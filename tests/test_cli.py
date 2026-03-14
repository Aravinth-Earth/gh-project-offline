from __future__ import annotations

import io
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch

from gh_project_offline import cli
from gh_project_offline.config import (
    APP_DIR_NAME,
    AppConfig,
    GitHubConfig,
    StorageConfig,
    SyncConfig,
    render_default_config,
)
from gh_project_offline.db import connect, finish_sync_run, replace_issue_cache, start_sync_run
from gh_project_offline.github_api import GitHubApiError
from gh_project_offline.service import SyncSummary


def make_sync_summary(**overrides) -> SyncSummary:
    values = {
        "fields_count": 1,
        "views_count": 1,
        "items_count": 2,
        "issues_count": 2,
        "comments_count": 3,
        "reused_issue_records": 0,
        "skipped_comment_fetches": 0,
        "added_issue_records": 0,
        "updated_issue_records": 0,
        "removed_issue_records": 0,
    }
    values.update(overrides)
    return SyncSummary(**values)


def build_config(config_path: Path, database_path: Path) -> AppConfig:
    logs_dir = (config_path.parent / "logs") if str(config_path.parent) not in {"", "."} else Path("logs")
    return AppConfig(
        config_path=config_path,
        github=GitHubConfig(
            owner="octocat",
            owner_type="user",
            project_number=12,
            view_number=3,
            token_env="GITHUB_TOKEN",
            project_url="https://github.com/users/octocat/projects/12/views/3",
        ),
        storage=StorageConfig(database_path=database_path, logs_dir=logs_dir),
        sync=SyncConfig(
            interval="15m",
            timeout_seconds=30,
            user_agent="gh-project-offline/test",
            include_closed_items=False,
        ),
    )


def write_config_file(config_path: Path, database_name: str = "data/cache.db") -> None:
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(
        "\n".join(
            [
                '[github]',
                'project_url = "https://github.com/users/octocat/projects/12/views/3"',
                'token_env = "GITHUB_TOKEN"',
                "",
                "[storage]",
                f'database_path = "{database_name}"',
                'logs_dir = "logs"',
                "",
                "[sync]",
                'interval = "15m"',
                "timeout_seconds = 30",
                'user_agent = "gh-project-offline/test"',
                "",
            ]
        ),
        encoding="utf-8",
    )


def seed_cache(database_path: Path) -> None:
    with connect(database_path) as connection:
        run_id = start_sync_run(connection)
        finish_sync_run(connection, run_id, "success")
        connection.execute(
            """
            insert into cached_view_items(
                project_key, view_number, item_key, issue_number, issue_title,
                status_name, repository_name, item_json, updated_at
            )
            values(?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "user:octocat:12",
                3,
                "item-1",
                42,
                "Offline board entry",
                "Todo",
                "octocat/hello-world",
                "{}",
                "2026-03-11T12:00:00Z",
            ),
        )
        replace_issue_cache(
            connection,
            project_key_value="user:octocat:12",
            snapshots=[
                {
                    "item_key": "item-1",
                    "repository_name": "octocat/hello-world",
                    "issue_number": 42,
                    "issue_payload": {
                        "title": "Offline board entry",
                        "body": "Issue body for offline inspection.",
                        "html_url": "https://github.com/octocat/hello-world/issues/42",
                        "url": "https://api.github.com/repos/octocat/hello-world/issues/42",
                        "state": "open",
                        "state_reason": None,
                        "comments": 1,
                        "user": {"login": "octocat"},
                        "labels": [{"name": "bug"}],
                        "assignees": [{"login": "hubot"}],
                        "milestone": {"title": "Sprint 1"},
                    },
                    "comment_payloads": [
                        {
                            "id": 1001,
                            "user": {"login": "hubot"},
                            "created_at": "2026-03-11T10:00:00Z",
                            "updated_at": "2026-03-11T10:00:00Z",
                            "body": "Cached comment",
                            "html_url": "https://github.com/octocat/hello-world/issues/42#issuecomment-1001",
                        }
                    ],
                },
                {
                    "item_key": "item-2",
                    "repository_name": "octocat/hello-world",
                    "issue_number": 43,
                    "issue_payload": {
                        "title": "Closed enhancement",
                        "body": "Completed work for the next release.",
                        "html_url": "https://github.com/octocat/hello-world/issues/43",
                        "url": "https://api.github.com/repos/octocat/hello-world/issues/43",
                        "state": "closed",
                        "state_reason": "completed",
                        "comments": 0,
                        "user": {"login": "octocat"},
                        "labels": [{"name": "enhancement"}, {"name": "release"}],
                        "assignees": [{"login": "monalisa"}],
                        "milestone": {"title": "Sprint 2"},
                    },
                    "comment_payloads": [],
                },
            ],
        )
        connection.execute(
            """
            insert into cached_view_items(
                project_key, view_number, item_key, issue_number, issue_title,
                status_name, repository_name, item_json, updated_at
            )
            values(?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "user:octocat:12",
                3,
                "item-2",
                43,
                "Closed enhancement",
                "Done",
                "octocat/hello-world",
                "{}",
                "2026-03-11T12:05:00Z",
            ),
        )


class CliTests(unittest.TestCase):
    def test_parse_interval_supports_common_units(self) -> None:
        self.assertEqual(cli.parse_interval("15m"), 900)
        self.assertEqual(cli.parse_interval("2h"), 7200)
        self.assertEqual(cli.parse_interval("30s"), 30)
        self.assertEqual(cli.parse_interval("45"), 45)

    def test_format_duration_handles_common_ranges(self) -> None:
        self.assertEqual(cli.format_duration(45), "45s")
        self.assertEqual(cli.format_duration(120), "2m")
        self.assertEqual(cli.format_duration(125), "2m 5s")
        self.assertEqual(cli.format_duration(3660), "1h 1m")

    def test_get_recent_successful_sync_age_reads_latest_success(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            database_path = Path(tmp_dir) / "cache.sqlite3"
            with connect(database_path) as connection:
                run_id = start_sync_run(connection)
                finish_sync_run(connection, run_id, "success")

            age = cli.get_recent_successful_sync_age(database_path)

            self.assertIsNotNone(age)
            self.assertGreaterEqual(age, 0)

    def test_get_recent_successful_sync_age_returns_none_without_db(self) -> None:
        self.assertIsNone(cli.get_recent_successful_sync_age(Path("missing-cache.sqlite3")))

    def test_detect_phase_label_maps_sync_messages(self) -> None:
        self.assertEqual(cli.detect_phase_label("Fetching project snapshot (1 endpoint call)..."), "Project snapshot")
        self.assertEqual(cli.detect_phase_label("Rechecking linked issues and comments for 10 remaining board item(s)..."), "Hydrating issues")
        self.assertEqual(cli.detect_phase_label("Writing cache to SQLite..."), "Writing cache")

    def test_parse_hydration_helpers(self) -> None:
        self.assertEqual(cli.parse_hydration_scope("Hydration scope: 91 unique issue or pull request item(s)."), 91)
        self.assertEqual(cli.parse_hydration_step("Hydrating issue 4/91: owner/repo#123"), (4, 91))
        self.assertIsNone(cli.parse_hydration_scope("Something else"))
        self.assertIsNone(cli.parse_hydration_step("Something else"))

    def test_rate_limit_wait_helpers(self) -> None:
        exc = GitHubApiError(
            "rate limited",
            status_code=403,
            response_body="API rate limit exceeded",
            response_headers={"Retry-After": "12", "X-RateLimit-Reset": "1773250000"},
        )
        self.assertEqual(cli.compute_rate_limit_wait_seconds(exc), 12)
        self.assertIn("Waiting 12s", cli.describe_rate_limit_wait(12, exc))

    def test_emit_sync_feedback_uses_log_only_when_renderer_is_present(self) -> None:
        logger = unittest.mock.Mock()
        renderer = unittest.mock.Mock()

        cli.emit_sync_feedback("Checking project fields...", logger=logger, renderer=renderer)

        logger.write_only.assert_called_once()
        logger.emit.assert_not_called()
        renderer.emit.assert_called_once_with("Checking project fields...")

    def test_sync_progress_renderer_emits_phase_summaries(self) -> None:
        outputs: list[str] = []
        with patch("gh_project_offline.cli.console_print", side_effect=outputs.append):
            renderer = cli.SyncProgressRenderer()
            with renderer:
                renderer.emit("Fetching project snapshot (1 endpoint call)...")
                renderer.emit("Checked project snapshot.")
                renderer.emit("Hydration scope: 2 unique issue or pull request item(s).")
                renderer.emit("Hydrating issue 1/2: owner/repo#1")
                renderer.emit("Cache delta summary: added=1 updated=2 removed=3")
                renderer.emit("Sync complete.")

        self.assertTrue(any("Project snapshot:" in line for line in outputs))
        self.assertTrue(any("Hydrating issues:" in line for line in outputs))
        self.assertTrue(any("added=1 updated=2 removed=3" in line for line in outputs))

    def test_log_runtime_failure_records_traceback_to_logger(self) -> None:
        logger = unittest.mock.Mock()
        try:
            raise OSError("timed out")
        except OSError as exc:
            cli.log_runtime_failure(logger, "Sync command failed", exc)

        logger.emit.assert_called_once_with("Sync command failed: OSError: timed out")
        logger.write_exception.assert_called_once()

    def test_main_init_writes_project_url_config(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            config_path = Path(tmp_dir) / APP_DIR_NAME / "config.toml"

            stdout = io.StringIO()
            with redirect_stdout(stdout):
                exit_code = cli.main(
                    [
                        "--config",
                        str(config_path),
                        "init",
                        "--project-url",
                        "https://github.com/orgs/openai/projects/77/views/5",
                        "--token-env",
                        "BOARD_PAT",
                    ]
                )

            self.assertEqual(exit_code, 0)
            self.assertIn("Wrote starter config", stdout.getvalue())
            rendered = config_path.read_text(encoding="utf-8")
            self.assertIn('project_url = "https://github.com/orgs/openai/projects/77/views/5"', rendered)
            self.assertIn('token_env = "BOARD_PAT"', rendered)

    def test_main_init_refuses_existing_config_without_force(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            config_path = Path(tmp_dir) / APP_DIR_NAME / "config.toml"
            config_path.parent.mkdir(parents=True, exist_ok=True)
            config_path.write_text(render_default_config(), encoding="utf-8")

            stderr = io.StringIO()
            with redirect_stderr(stderr):
                exit_code = cli.main(["--config", str(config_path), "init"])

            self.assertEqual(exit_code, 1)
            self.assertIn("Config already exists", stderr.getvalue())

    def test_main_doctor_reports_local_setup(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            config_path = Path(tmp_dir) / APP_DIR_NAME / "config.toml"
            database_path = config_path.parent / "cache.sqlite3"
            write_config_file(config_path, database_name="cache.sqlite3")

            stdout = io.StringIO()
            with redirect_stdout(stdout):
                exit_code = cli.main(["--config", str(config_path), "doctor"])

            self.assertEqual(exit_code, 0)
            self.assertIn("Project: https://github.com/users/octocat/projects/12/views/3", stdout.getvalue())
            self.assertIn("Token present: no", stdout.getvalue())
            self.assertIn(str(database_path), stdout.getvalue())

    def test_main_sync_and_watch_paths_report_success(self) -> None:
        config = build_config(Path(".ghpo/config.toml"), Path("cache.sqlite3"))

        with patch("gh_project_offline.cli.load_config", return_value=config), patch(
                "gh_project_offline.cli.run_sync",
                return_value=make_sync_summary(
                    fields_count=4,
                    views_count=1,
                    items_count=10,
                    issues_count=8,
                    comments_count=22,
                ),
        ):
            stdout = io.StringIO()
            with redirect_stdout(stdout):
                exit_code = cli.main(["sync"])

        self.assertEqual(exit_code, 0)
        self.assertIn("issues=8 comments=22", stdout.getvalue())

        with patch("gh_project_offline.cli.load_config", return_value=config), patch(
            "gh_project_offline.cli.run_watch_loop",
            side_effect=KeyboardInterrupt,
        ):
            stdout = io.StringIO()
            with redirect_stdout(stdout):
                exit_code = cli.main(["watch", "--interval", "30m"])

        self.assertEqual(exit_code, 0)
        self.assertIn("Watching with interval=1800s", stdout.getvalue())
        self.assertIn("Stopped.", stdout.getvalue())

    def test_sync_and_watch_prompt_for_token_when_env_is_missing(self) -> None:
        config = build_config(Path(".ghpo/config.toml"), Path("cache.sqlite3"))

        with patch.dict("os.environ", {}, clear=True), patch(
            "gh_project_offline.cli.load_config",
            return_value=config,
        ), patch(
            "gh_project_offline.cli.sys.stdin.isatty",
            return_value=True,
        ), patch(
            "gh_project_offline.cli.getpass.getpass",
            return_value="prompted-token",
        ) as mock_getpass, patch(
            "gh_project_offline.cli.run_sync",
            return_value=make_sync_summary(),
        ):
            exit_code = cli.main(["sync"])

        self.assertEqual(exit_code, 0)
        mock_getpass.assert_called_once()

        with patch.dict("os.environ", {}, clear=True), patch(
            "gh_project_offline.cli.load_config",
            return_value=config,
        ), patch(
            "gh_project_offline.cli.sys.stdin.isatty",
            return_value=True,
        ), patch(
            "gh_project_offline.cli.getpass.getpass",
            return_value="prompted-token",
        ) as mock_getpass, patch(
            "gh_project_offline.cli.run_watch_loop",
            side_effect=KeyboardInterrupt,
        ):
            exit_code = cli.main(["watch"])

        self.assertEqual(exit_code, 0)
        mock_getpass.assert_called_once()

    def test_start_flow_uses_existing_config_and_token_then_runs_sync(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            config_path = Path(tmp_dir) / APP_DIR_NAME / "config.toml"
            database_path = config_path.parent / "data" / "cache.db"
            write_config_file(config_path)
            config = build_config(config_path, database_path)

            stdout = io.StringIO()
            with redirect_stdout(stdout), patch.dict("os.environ", {"GITHUB_TOKEN": "env-token"}, clear=False), patch(
                "gh_project_offline.cli.load_config",
                side_effect=[config, config],
            ), patch(
                "gh_project_offline.cli.write_default_config"
            ) as mock_write, patch(
                "gh_project_offline.cli.prompt_yes_no",
                return_value=False,
            ), patch(
                "gh_project_offline.cli.create_run_logger"
            ) as mock_create_logger, patch(
                "gh_project_offline.cli.GitHubClient"
            ) as mock_client_cls, patch(
                "gh_project_offline.cli.run_sync",
                return_value=make_sync_summary(),
            ) as mock_run_sync:
                logger = mock_create_logger.return_value
                logger.log_path = Path("logs/session-test.log")
                mock_client = mock_client_cls.return_value
                mock_client.fetch_project.return_value = {"id": "project-1"}
                exit_code = cli.main(["--config", str(config_path), "start"])

            self.assertEqual(exit_code, 0)
            mock_write.assert_called_once()
            mock_client.fetch_project.assert_called_once()
            mock_run_sync.assert_called_once()
            self.assertIn("Using existing project URL", stdout.getvalue())
            self.assertIn("Using token from GITHUB_TOKEN.", stdout.getvalue())
            logger.emit.assert_any_call("Ready. fields=1 views=1 items=2 issues=2 comments=3")

    def test_start_reuses_existing_cache_and_switches_to_recheck_mode(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            config_path = Path(tmp_dir) / APP_DIR_NAME / "config.toml"
            database_path = config_path.parent / "data" / "cache.db"
            write_config_file(config_path)
            seed_cache(database_path)
            config = build_config(config_path, database_path)

            with patch.dict("os.environ", {"GITHUB_TOKEN": "env-token"}, clear=False), patch(
                "gh_project_offline.cli.load_config",
                side_effect=[config, config],
            ), patch(
                "gh_project_offline.cli.write_default_config"
            ), patch(
                "gh_project_offline.cli.get_recent_successful_sync_age",
                return_value=3600,
            ), patch(
                "gh_project_offline.cli.prompt_yes_no",
                return_value=False,
            ), patch(
                "gh_project_offline.cli.create_run_logger"
            ) as mock_create_logger, patch(
                "gh_project_offline.cli.GitHubClient"
            ) as mock_client_cls, patch(
                "gh_project_offline.cli.run_sync",
                return_value=make_sync_summary(),
            ) as mock_run_sync:
                logger = mock_create_logger.return_value
                logger.log_path = Path("logs/session-test.log")
                mock_client = mock_client_cls.return_value
                mock_client.fetch_project.return_value = {"id": "project-1"}
                exit_code = cli.main(["--config", str(config_path), "start"])

            self.assertEqual(exit_code, 0)
            logger.emit.assert_any_call(f"Reusing existing project board cache at {database_path}")
            self.assertEqual(mock_run_sync.call_count, 1)
            self.assertEqual(mock_run_sync.call_args.args[0], config)
            self.assertEqual(mock_run_sync.call_args.kwargs["sync_mode"], "recheck")

    def test_start_can_skip_immediate_recheck_when_cache_is_fresh(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            config_path = Path(tmp_dir) / APP_DIR_NAME / "config.toml"
            database_path = config_path.parent / "data" / "cache.db"
            write_config_file(config_path)
            seed_cache(database_path)
            config = build_config(config_path, database_path)

            with patch.dict("os.environ", {"GITHUB_TOKEN": "env-token"}, clear=False), patch(
                "gh_project_offline.cli.load_config",
                side_effect=[config, config],
            ), patch(
                "gh_project_offline.cli.write_default_config"
            ), patch(
                "gh_project_offline.cli.get_recent_successful_sync_age",
                return_value=120,
            ), patch(
                "gh_project_offline.cli.prompt_yes_no",
                side_effect=[True, True],
            ), patch(
                "gh_project_offline.cli.prompt_interval_override",
                return_value="15m",
            ) as mock_prompt_interval, patch(
                "gh_project_offline.cli.create_run_logger"
            ) as mock_create_logger, patch(
                "gh_project_offline.cli.GitHubClient"
            ) as mock_client_cls, patch(
                "gh_project_offline.cli.run_sync",
            ) as mock_run_sync, patch(
                "gh_project_offline.cli.run_watch_loop",
                side_effect=KeyboardInterrupt,
            ) as mock_watch:
                logger = mock_create_logger.return_value
                logger.log_path = Path("logs/session-test.log")
                mock_client = mock_client_cls.return_value
                mock_client.fetch_project.return_value = {"id": "project-1"}
                exit_code = cli.main(["--config", str(config_path), "start"])

            self.assertEqual(exit_code, 0)
            mock_run_sync.assert_not_called()
            logger.emit.assert_any_call("Ready. Using the existing local cache without an immediate recheck.")
            mock_prompt_interval.assert_called_once_with("Watch interval after the initial 13m wait", "15m")
            mock_watch.assert_called_once_with(
                config,
                900,
                logger=logger,
                wait_before_first_cycle=True,
                first_wait_seconds=780,
                auto_wait_on_rate_limit=True,
            )

    def test_start_can_force_recheck_even_when_cache_is_fresh(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            config_path = Path(tmp_dir) / APP_DIR_NAME / "config.toml"
            database_path = config_path.parent / "data" / "cache.db"
            write_config_file(config_path)
            seed_cache(database_path)
            config = build_config(config_path, database_path)

            with patch.dict("os.environ", {"GITHUB_TOKEN": "env-token"}, clear=False), patch(
                "gh_project_offline.cli.load_config",
                side_effect=[config, config],
            ), patch(
                "gh_project_offline.cli.write_default_config"
            ), patch(
                "gh_project_offline.cli.get_recent_successful_sync_age",
                return_value=120,
            ), patch(
                "gh_project_offline.cli.prompt_yes_no",
                side_effect=[False, False],
            ), patch(
                "gh_project_offline.cli.create_run_logger"
            ) as mock_create_logger, patch(
                "gh_project_offline.cli.GitHubClient"
            ) as mock_client_cls, patch(
                "gh_project_offline.cli.run_sync",
                return_value=make_sync_summary(),
            ) as mock_run_sync:
                logger = mock_create_logger.return_value
                logger.log_path = Path("logs/session-test.log")
                mock_client = mock_client_cls.return_value
                mock_client.fetch_project.return_value = {"id": "project-1"}
                exit_code = cli.main(["--config", str(config_path), "start"])

            self.assertEqual(exit_code, 0)
            self.assertEqual(mock_run_sync.call_count, 1)

    def test_start_can_enter_watch_mode_with_overridden_interval(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            config_path = Path(tmp_dir) / APP_DIR_NAME / "config.toml"
            database_path = config_path.parent / "data" / "cache.db"
            write_config_file(config_path)
            config = build_config(config_path, database_path)

            with patch.dict("os.environ", {"GITHUB_TOKEN": "env-token"}, clear=False), patch(
                "gh_project_offline.cli.load_config",
                side_effect=[config, config],
            ), patch(
                "gh_project_offline.cli.write_default_config"
            ), patch(
                "gh_project_offline.cli.prompt_yes_no",
                return_value=True,
            ), patch(
                "gh_project_offline.cli.prompt_interval_override",
                return_value="30m",
            ), patch(
                "gh_project_offline.cli.create_run_logger"
            ) as mock_create_logger, patch(
                "gh_project_offline.cli.GitHubClient"
            ) as mock_client_cls, patch(
                "gh_project_offline.cli.run_sync",
                return_value=make_sync_summary(),
            ), patch(
                "gh_project_offline.cli.run_watch_loop",
                side_effect=KeyboardInterrupt,
            ) as mock_watch:
                logger = mock_create_logger.return_value
                logger.log_path = Path("logs/session-test.log")
                mock_client = mock_client_cls.return_value
                mock_client.fetch_project.return_value = {"id": "project-1"}
                exit_code = cli.main(["--config", str(config_path), "start"])

            self.assertEqual(exit_code, 0)
            mock_watch.assert_called_once_with(
                config,
                1800,
                logger=logger,
                wait_before_first_cycle=True,
                first_wait_seconds=None,
                auto_wait_on_rate_limit=True,
            )
            logger.emit.assert_any_call("Starting watch mode with interval=1800s")
            logger.emit.assert_any_call("Watch mode stopped.")

    def test_start_shows_next_commands_only_when_watch_is_declined(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            config_path = Path(tmp_dir) / APP_DIR_NAME / "config.toml"
            database_path = config_path.parent / "data" / "cache.db"
            write_config_file(config_path)
            config = build_config(config_path, database_path)

            with patch.dict("os.environ", {"GITHUB_TOKEN": "env-token"}, clear=False), patch(
                "gh_project_offline.cli.load_config",
                side_effect=[config, config],
            ), patch(
                "gh_project_offline.cli.write_default_config"
            ), patch(
                "gh_project_offline.cli.prompt_yes_no",
                return_value=False,
            ), patch(
                "gh_project_offline.cli.create_run_logger"
            ) as mock_create_logger, patch(
                "gh_project_offline.cli.GitHubClient"
            ) as mock_client_cls, patch(
                "gh_project_offline.cli.run_sync",
                return_value=make_sync_summary(),
            ):
                logger = mock_create_logger.return_value
                logger.log_path = Path("logs/session-test.log")
                mock_client = mock_client_cls.return_value
                mock_client.fetch_project.return_value = {"id": "project-1"}
                exit_code = cli.main(["--config", str(config_path), "start"])

            self.assertEqual(exit_code, 0)
            logger.emit.assert_any_call("Next: use `gh-project-offline items`, `issues`, or `issue owner/repo 123`.")

    def test_watch_command_starts_immediately_but_start_handoff_waits_first(self) -> None:
        config = build_config(Path(".ghpo/config.toml"), Path("cache.sqlite3"))

        with patch("gh_project_offline.cli.load_config", return_value=config), patch(
            "gh_project_offline.cli.run_watch_loop",
            side_effect=KeyboardInterrupt,
        ) as mock_watch:
            stdout = io.StringIO()
            with redirect_stdout(stdout):
                exit_code = cli.main(["watch", "--interval", "15m"])

        self.assertEqual(exit_code, 0)
        mock_watch.assert_called_once()
        self.assertEqual(mock_watch.call_args.args[:2], (config, 900))
        self.assertIn("logger", mock_watch.call_args.kwargs)
        self.assertTrue(mock_watch.call_args.kwargs["auto_wait_on_rate_limit"])

    def test_watch_command_can_disable_rate_limit_wait(self) -> None:
        config = build_config(Path(".ghpo/config.toml"), Path("cache.sqlite3"))

        with patch("gh_project_offline.cli.load_config", return_value=config), patch(
            "gh_project_offline.cli.run_watch_loop",
            side_effect=KeyboardInterrupt,
        ) as mock_watch:
            exit_code = cli.main(["watch", "--no-rate-limit-wait"])

        self.assertEqual(exit_code, 0)
        self.assertFalse(mock_watch.call_args.kwargs["auto_wait_on_rate_limit"])

    def test_start_force_prompts_for_values_and_resets_database(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            config_path = Path(tmp_dir) / APP_DIR_NAME / "config.toml"
            database_path = config_path.parent / "data" / "cache.db"
            database_path.parent.mkdir(parents=True, exist_ok=True)
            database_path.write_text("stale", encoding="utf-8")
            config = build_config(config_path, database_path)

            stdout = io.StringIO()
            with redirect_stdout(stdout), patch(
                "builtins.input",
                return_value="https://github.com/users/octocat/projects/12/views/3",
            ), patch(
                "gh_project_offline.cli.getpass.getpass",
                return_value="prompted-token",
            ) as mock_getpass, patch(
                "gh_project_offline.cli.load_config",
                return_value=config,
            ), patch(
                "gh_project_offline.cli.prompt_yes_no",
                return_value=False,
            ), patch(
                "gh_project_offline.cli.create_run_logger"
            ) as mock_create_logger, patch(
                "gh_project_offline.cli.GitHubClient"
            ) as mock_client_cls, patch(
                "gh_project_offline.cli.run_sync",
                return_value=make_sync_summary(),
            ), patch.dict("os.environ", {}, clear=True):
                logger = mock_create_logger.return_value
                logger.log_path = Path("logs/session-test.log")
                mock_client = mock_client_cls.return_value
                mock_client.fetch_project.return_value = {"id": "project-1"}
                exit_code = cli.main(["--config", str(config_path), "start", "--force"])

            self.assertEqual(exit_code, 0)
            self.assertFalse(database_path.exists())
            mock_getpass.assert_called_once()
            self.assertIn("Removed existing database", stdout.getvalue())

    def test_setup_alias_uses_same_start_flow(self) -> None:
        with patch("gh_project_offline.cli.run_start_flow", return_value=0) as mock_start_flow:
            exit_code = cli.main(["setup"])

        self.assertEqual(exit_code, 0)
        mock_start_flow.assert_called_once()

    def test_start_force_ignores_invalid_existing_config_and_recovers(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            config_path = Path(tmp_dir) / APP_DIR_NAME / "config.toml"
            database_path = config_path.parent / "cache.db"
            config_path.parent.mkdir(parents=True, exist_ok=True)
            config_path.write_text(
                '\n'.join(
                    [
                        "[github]",
                        'project_url = "not-a-valid-url"',
                        'token_env = "GITHUB_TOKEN"',
                        "",
                        "[storage]",
                        'database_path = "cache.db"',
                        "",
                        "[sync]",
                        'interval = "15m"',
                        "timeout_seconds = 30",
                        'user_agent = "gh-project-offline/test"',
                        "",
                    ]
                ),
                encoding="utf-8",
            )
            config = build_config(config_path, database_path)

            stdout = io.StringIO()
            with redirect_stdout(stdout), patch(
                "builtins.input",
                return_value="https://github.com/users/octocat/projects/12/views/3",
            ), patch(
                "gh_project_offline.cli.getpass.getpass",
                return_value="prompted-token",
            ), patch(
                "gh_project_offline.cli.load_config",
                side_effect=[ValueError("bad existing config"), config],
            ), patch(
                "gh_project_offline.cli.prompt_yes_no",
                return_value=False,
            ), patch(
                "gh_project_offline.cli.create_run_logger"
            ) as mock_create_logger, patch(
                "gh_project_offline.cli.GitHubClient"
            ) as mock_client_cls, patch(
                "gh_project_offline.cli.run_sync",
                return_value=make_sync_summary(),
            ):
                logger = mock_create_logger.return_value
                logger.log_path = Path("logs/session-test.log")
                mock_client = mock_client_cls.return_value
                mock_client.fetch_project.return_value = {"id": "project-1"}
                exit_code = cli.main(["--config", str(config_path), "start", "--force"])

            self.assertEqual(exit_code, 0)
            self.assertIn("Ignoring existing config error during forced reset", stdout.getvalue())

    def test_main_status_items_issues_issue_and_query_read_cache(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            config_path = tmp_path / APP_DIR_NAME / "config.toml"
            database_path = config_path.parent / "cache.sqlite3"
            write_config_file(config_path, database_name="cache.sqlite3")
            seed_cache(database_path)

            stdout = io.StringIO()
            with redirect_stdout(stdout):
                status_code = cli.main(["--config", str(config_path), "status"])
                items_code = cli.main(["--config", str(config_path), "items", "--limit", "5"])
                issues_code = cli.main(["--config", str(config_path), "issues", "--limit", "5"])
                issue_code = cli.main(["--config", str(config_path), "issue", "octocat/hello-world", "42"])
                query_code = cli.main(
                    [
                        "--config",
                        str(config_path),
                        "query",
                        "select repository_name, issue_number from cached_issue_details",
                    ]
                )

            output = stdout.getvalue()
            self.assertEqual(status_code, 0)
            self.assertEqual(items_code, 0)
            self.assertEqual(issues_code, 0)
            self.assertEqual(issue_code, 0)
            self.assertEqual(query_code, 0)
            self.assertIn("Issues cached: 2", output)
            self.assertIn("#42 [Todo] [open] Offline board entry", output)
            self.assertIn("octocat/hello-world#42 [issue/open]", output)
            self.assertIn("Comments:", output)
            self.assertIn("repository_name", output)
            self.assertIn("issue_number", output)

    def test_find_supports_flag_filters(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            config_path = tmp_path / APP_DIR_NAME / "config.toml"
            database_path = config_path.parent / "cache.sqlite3"
            write_config_file(config_path, database_name="cache.sqlite3")
            seed_cache(database_path)

            stdout = io.StringIO()
            with redirect_stdout(stdout):
                exit_code = cli.main(
                    [
                        "--config",
                        str(config_path),
                        "find",
                        "--label",
                        "bug",
                        "--milestone",
                        "Sprint 1",
                        "--state",
                        "open",
                        "--status",
                        "Todo",
                        "--repo",
                        "octocat/hello-world",
                        "--assignee",
                        "hubot",
                        "--type",
                        "issue",
                        "--text",
                        "offline",
                    ]
                )

            output = stdout.getvalue()
            self.assertEqual(exit_code, 0)
            self.assertIn("octocat/hello-world#42", output)
            self.assertNotIn("octocat/hello-world#43", output)
            self.assertIn("Printed 1 match(es).", output)

    def test_find_supports_interactive_filters(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            config_path = tmp_path / APP_DIR_NAME / "config.toml"
            database_path = config_path.parent / "cache.sqlite3"
            write_config_file(config_path, database_name="cache.sqlite3")
            seed_cache(database_path)

            prompts = iter(
                [
                    "2,3",
                    "2",
                    "1",
                    "1",
                    "1",
                    "2",
                    "1",
                    "completed",
                    "1",
                ]
            )
            stdout = io.StringIO()
            with redirect_stdout(stdout), patch("builtins.input", side_effect=lambda _: next(prompts)):
                exit_code = cli.main(["--config", str(config_path), "find", "--interactive"])

            output = stdout.getvalue()
            self.assertEqual(exit_code, 0)
            self.assertIn("octocat/hello-world#43", output)
            self.assertNotIn("octocat/hello-world#42", output)
            self.assertIn("Printed 1 match(es).", output)
            self.assertIn("Labels", output)
            self.assertIn("Board status", output)

    def test_find_handles_keyboard_interrupt_cleanly(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            config_path = tmp_path / APP_DIR_NAME / "config.toml"
            database_path = config_path.parent / "cache.sqlite3"
            write_config_file(config_path, database_name="cache.sqlite3")
            seed_cache(database_path)

            stderr = io.StringIO()
            with redirect_stderr(stderr), patch("builtins.input", side_effect=KeyboardInterrupt):
                exit_code = cli.main(["--config", str(config_path), "find", "--interactive"])

            self.assertEqual(exit_code, 130)
            self.assertIn("Interrupted.", stderr.getvalue())

    def test_main_rejects_non_select_query_and_missing_issue(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            config_path = tmp_path / APP_DIR_NAME / "config.toml"
            database_path = config_path.parent / "cache.sqlite3"
            write_config_file(config_path, database_name="cache.sqlite3")
            with connect(database_path):
                pass

            stderr = io.StringIO()
            with redirect_stderr(stderr):
                bad_query_code = cli.main(["--config", str(config_path), "query", "delete from cached_issue_details"])
                missing_issue_code = cli.main(["--config", str(config_path), "issue", "octocat/hello-world", "99"])

            self.assertEqual(bad_query_code, 1)
            self.assertEqual(missing_issue_code, 1)
            self.assertIn("Only SELECT queries are allowed.", stderr.getvalue())
            self.assertIn("No cached issue found", stderr.getvalue())

    def test_main_handles_sync_and_config_failures(self) -> None:
        config = build_config(Path(".ghpo/config.toml"), Path("cache.sqlite3"))

        stderr = io.StringIO()
        with redirect_stderr(stderr), patch("gh_project_offline.cli.load_config", return_value=config), patch(
            "gh_project_offline.cli.create_run_logger"
        ) as mock_create_logger, patch(
            "gh_project_offline.cli.run_sync",
            side_effect=GitHubApiError("boom"),
        ):
            logger = mock_create_logger.return_value
            logger.log_path = Path("logs/session-test.log")
            exit_code = cli.main(["sync"])

        self.assertEqual(exit_code, 1)
        self.assertIn("Sync failed: boom", stderr.getvalue())
        logger.emit.assert_any_call(f"Session log created at {logger.log_path}")
        logger.emit.assert_any_call("Sync command failed: GitHubApiError: boom")

        stderr = io.StringIO()
        with redirect_stderr(stderr), patch(
            "gh_project_offline.cli.load_config",
            side_effect=ValueError("bad config"),
        ):
            exit_code = cli.main(["status"])

        self.assertEqual(exit_code, 1)
        self.assertIn("Config error: bad config", stderr.getvalue())

        stderr = io.StringIO()
        with redirect_stderr(stderr), patch(
            "gh_project_offline.cli.run_start_flow",
            side_effect=GitHubApiError("bad token"),
        ):
            exit_code = cli.main(["start"])

        self.assertEqual(exit_code, 1)
        self.assertIn("Start failed: bad token", stderr.getvalue())

    def test_start_logs_runtime_failure_before_returning_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            config_path = Path(tmp_dir) / APP_DIR_NAME / "config.toml"
            database_path = config_path.parent / "data" / "cache.db"
            write_config_file(config_path)
            config = build_config(config_path, database_path)

            stderr = io.StringIO()
            with redirect_stderr(stderr), patch.dict("os.environ", {"GITHUB_TOKEN": "env-token"}, clear=False), patch(
                "gh_project_offline.cli.load_config",
                side_effect=[config, config],
            ), patch(
                "gh_project_offline.cli.write_default_config"
            ), patch(
                "gh_project_offline.cli.create_run_logger"
            ) as mock_create_logger, patch(
                "gh_project_offline.cli.GitHubClient"
            ) as mock_client_cls, patch(
                "gh_project_offline.cli.run_sync",
                side_effect=OSError("The read operation timed out"),
            ):
                logger = mock_create_logger.return_value
                logger.log_path = Path("logs/session-test.log")
                mock_client = mock_client_cls.return_value
                mock_client.fetch_project.return_value = {"id": "project-1"}
                exit_code = cli.main(["--config", str(config_path), "start"])

            self.assertEqual(exit_code, 1)
            self.assertIn("I/O error: The read operation timed out", stderr.getvalue())
            logger.emit.assert_any_call("Start flow failed: OSError: The read operation timed out")

    def test_watch_logs_runtime_failure_before_returning_error(self) -> None:
        config = build_config(Path(".ghpo/config.toml"), Path("cache.sqlite3"))

        stderr = io.StringIO()
        with redirect_stderr(stderr), patch("gh_project_offline.cli.load_config", return_value=config), patch(
            "gh_project_offline.cli.create_run_logger"
        ) as mock_create_logger, patch(
            "gh_project_offline.cli.run_watch_loop",
            side_effect=OSError("socket read timed out"),
        ):
            logger = mock_create_logger.return_value
            logger.log_path = Path("logs/session-test.log")
            exit_code = cli.main(["watch"])

        self.assertEqual(exit_code, 1)
        self.assertIn("I/O error: socket read timed out", stderr.getvalue())
        logger.emit.assert_any_call("Watch command failed: OSError: socket read timed out")

    def test_start_watch_handoff_can_disable_rate_limit_wait(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            config_path = Path(tmp_dir) / APP_DIR_NAME / "config.toml"
            database_path = config_path.parent / "data" / "cache.db"
            write_config_file(config_path)
            config = build_config(config_path, database_path)

            with patch.dict("os.environ", {"GITHUB_TOKEN": "env-token"}, clear=False), patch(
                "gh_project_offline.cli.load_config",
                side_effect=[config, config],
            ), patch(
                "gh_project_offline.cli.write_default_config"
            ), patch(
                "gh_project_offline.cli.prompt_yes_no",
                return_value=True,
            ), patch(
                "gh_project_offline.cli.prompt_interval_override",
                return_value="15m",
            ), patch(
                "gh_project_offline.cli.create_run_logger"
            ) as mock_create_logger, patch(
                "gh_project_offline.cli.GitHubClient"
            ) as mock_client_cls, patch(
                "gh_project_offline.cli.run_sync",
                return_value=make_sync_summary(),
            ), patch(
                "gh_project_offline.cli.run_watch_loop",
                side_effect=KeyboardInterrupt,
            ) as mock_watch:
                logger = mock_create_logger.return_value
                logger.log_path = Path("logs/session-test.log")
                mock_client = mock_client_cls.return_value
                mock_client.fetch_project.return_value = {"id": "project-1"}
                exit_code = cli.main(["--config", str(config_path), "start", "--no-rate-limit-wait"])

            self.assertEqual(exit_code, 0)
            self.assertFalse(mock_watch.call_args.kwargs["auto_wait_on_rate_limit"])

    def test_run_watch_loop_waits_for_rate_limit_then_resumes(self) -> None:
        config = build_config(Path(".ghpo/config.toml"), Path("cache.sqlite3"))
        logger = unittest.mock.Mock()
        rate_limit_exc = GitHubApiError(
            "rate limited",
            status_code=403,
            response_body="secondary rate limit",
            response_headers={"Retry-After": "2", "X-RateLimit-Reset": "1773250000"},
        )

        with patch(
            "gh_project_offline.cli.run_sync",
            side_effect=[rate_limit_exc, make_sync_summary(), RuntimeError("done")],
        ), patch(
            "gh_project_offline.cli.render_wait_countdown",
            side_effect=[None, RuntimeError("done")],
        ) as mock_wait:
            with self.assertRaisesRegex(RuntimeError, "done"):
                cli.run_watch_loop(config, 900, logger=logger)

        self.assertEqual(mock_wait.call_args_list[0].args[0], 2)
        self.assertTrue(
            any(
                "GitHub rate limit reached during watch. Waiting 2s until about" in call.args[0]
                for call in logger.emit.call_args_list
            )
        )
        logger.emit.assert_any_call("Watch cycle 1: resuming after GitHub rate limit wait.")

    def test_run_watch_loop_can_fail_fast_on_rate_limit(self) -> None:
        config = build_config(Path(".ghpo/config.toml"), Path("cache.sqlite3"))
        rate_limit_exc = GitHubApiError(
            "rate limited",
            status_code=403,
            response_body="API rate limit exceeded",
            response_headers={"Retry-After": "5"},
        )

        with patch("gh_project_offline.cli.run_sync", side_effect=rate_limit_exc):
            with self.assertRaises(GitHubApiError):
                cli.run_watch_loop(config, 900, auto_wait_on_rate_limit=False)

    def test_start_prints_bad_credentials_guidance_for_user_project(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            config_path = Path(tmp_dir) / APP_DIR_NAME / "config.toml"
            database_path = config_path.parent / "data" / "cache.db"
            write_config_file(config_path)
            config = build_config(config_path, database_path)

            stdout = io.StringIO()
            stderr = io.StringIO()
            with redirect_stdout(stdout), redirect_stderr(stderr), patch(
                "gh_project_offline.cli.load_config",
                side_effect=[config, config],
            ), patch.dict("os.environ", {"GITHUB_TOKEN": "bad-token"}, clear=False), patch(
                "gh_project_offline.cli.write_default_config"
            ), patch(
                "gh_project_offline.cli.prompt_yes_no",
                return_value=False,
            ), patch(
                "gh_project_offline.cli.create_run_logger"
            ) as mock_create_logger, patch(
                "gh_project_offline.cli.GitHubClient"
            ) as mock_client_cls:
                logger = mock_create_logger.return_value
                logger.log_path = Path("logs/session-test.log")
                mock_client = mock_client_cls.return_value
                mock_client.fetch_project.side_effect = GitHubApiError(
                    "bad credentials",
                    status_code=401,
                )
                exit_code = cli.main(["--config", str(config_path), "start"])

            self.assertEqual(exit_code, 1)
            self.assertIn("GitHub token check failed.", stdout.getvalue())
            self.assertIn("use a classic personal access token", stdout.getvalue())
            self.assertIn("project` scope", stdout.getvalue())
            self.assertIn("Start failed: bad credentials", stderr.getvalue())

    def test_start_prints_access_guidance_and_sso_hint(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            config_path = Path(tmp_dir) / APP_DIR_NAME / "config.toml"
            config_path.parent.mkdir(parents=True, exist_ok=True)
            config_path.write_text(render_default_config(project_url="https://github.com/orgs/openai/projects/12/views/3"), encoding="utf-8")
            config = AppConfig(
                config_path=config_path,
                github=GitHubConfig(
                    owner="openai",
                    owner_type="org",
                    project_number=12,
                    view_number=3,
                    token_env="GITHUB_TOKEN",
                    project_url="https://github.com/orgs/openai/projects/12/views/3",
                ),
                storage=StorageConfig(database_path=config_path.parent / "data" / "cache.db", logs_dir=config_path.parent / "logs"),
                sync=SyncConfig(
                    interval="15m",
                    timeout_seconds=30,
                    user_agent="gh-project-offline/test",
                    include_closed_items=False,
                ),
            )

            stdout = io.StringIO()
            stderr = io.StringIO()
            with redirect_stdout(stdout), redirect_stderr(stderr), patch(
                "gh_project_offline.cli.load_config",
                side_effect=[config, config],
            ), patch.dict("os.environ", {"GITHUB_TOKEN": "valid-but-limited"}, clear=False), patch(
                "gh_project_offline.cli.write_default_config"
            ), patch(
                "gh_project_offline.cli.prompt_yes_no",
                return_value=False,
            ), patch(
                "gh_project_offline.cli.create_run_logger"
            ) as mock_create_logger, patch(
                "gh_project_offline.cli.GitHubClient"
            ) as mock_client_cls:
                logger = mock_create_logger.return_value
                logger.log_path = Path("logs/session-test.log")
                mock_client = mock_client_cls.return_value
                mock_client.fetch_project.side_effect = GitHubApiError(
                    "missing access",
                    status_code=403,
                    response_headers={"X-GitHub-SSO": "required; url=https://github.com/orgs/openai/sso"},
                )
                exit_code = cli.main(["--config", str(config_path), "start"])

            self.assertEqual(exit_code, 1)
            self.assertIn("organization Projects read plus repository read permissions", stdout.getvalue())
            self.assertIn("GitHub SSO hint", stdout.getvalue())
            self.assertIn("Start failed: missing access", stderr.getvalue())

    def test_start_prints_rate_limit_guidance_without_auto_retry(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            config_path = Path(tmp_dir) / APP_DIR_NAME / "config.toml"
            database_path = config_path.parent / "data" / "cache.db"
            write_config_file(config_path)
            config = build_config(config_path, database_path)

            stdout = io.StringIO()
            stderr = io.StringIO()
            with redirect_stdout(stdout), redirect_stderr(stderr), patch(
                "gh_project_offline.cli.load_config",
                side_effect=[config, config],
            ), patch.dict("os.environ", {"GITHUB_TOKEN": "valid-token"}, clear=False), patch(
                "gh_project_offline.cli.write_default_config"
            ), patch(
                "gh_project_offline.cli.prompt_yes_no",
                return_value=False,
            ), patch(
                "gh_project_offline.cli.create_run_logger"
            ) as mock_create_logger, patch(
                "gh_project_offline.cli.GitHubClient"
            ) as mock_client_cls:
                logger = mock_create_logger.return_value
                logger.log_path = Path("logs/session-test.log")
                mock_client = mock_client_cls.return_value
                mock_client.fetch_project.side_effect = GitHubApiError(
                    "secondary rate limit hit",
                    status_code=403,
                    response_body='{"message":"You have exceeded a secondary rate limit."}',
                    response_headers={"Retry-After": "60"},
                )
                exit_code = cli.main(["--config", str(config_path), "start"])

            self.assertEqual(exit_code, 1)
            self.assertIn("Sync was stopped without automatic retry", stdout.getvalue())
            self.assertIn("wait about 60 second(s)", stdout.getvalue())
            self.assertIn("Start failed: secondary rate limit hit", stderr.getvalue())


if __name__ == "__main__":
    unittest.main()
