from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from gh_project_offline.config import AppConfig, GitHubConfig, StorageConfig, SyncConfig
from gh_project_offline.db import connect
from gh_project_offline.service import (
    collect_field_ids,
    fetch_issue_snapshots,
    fetch_issue_snapshots_with_progress,
    run_sync,
    watch_sync,
)


class FakeGitHubClient:
    def __init__(self) -> None:
        self.issue_calls: list[tuple[str, int]] = []
        self.comment_calls: list[tuple[str, int]] = []

    def fetch_issue(self, repository_name: str, issue_number: int) -> dict:
        self.issue_calls.append((repository_name, issue_number))
        return {"title": f"Issue {issue_number}", "comments": 1, "updated_at": "2026-03-11T10:00:00Z"}

    def fetch_issue_comments(self, repository_name: str, issue_number: int) -> list[dict]:
        self.comment_calls.append((repository_name, issue_number))
        return [{"id": issue_number * 100, "body": "cached"}]


class SyncGitHubClient:
    def fetch_rate_limit_status(self) -> dict:
        return {"resources": {"core": {"remaining": 4999, "limit": 5000, "reset": 1773250000}}}

    def fetch_project(self) -> dict:
        return {"id": "project-1", "title": "Offline Test Board"}

    def fetch_fields(self) -> list[dict]:
        return [{"id": 11, "name": "Status", "type": "single_select"}]

    def fetch_views(self) -> list[dict]:
        return [{"number": 3, "name": "Board", "layout": "board"}]

    def fetch_view_items(self, field_ids: list[str]) -> list[dict]:
        assert field_ids == ["11"]
        return [
            {
                "id": "item-1",
                "content": {
                    "number": 42,
                    "title": "Offline sync item",
                    "repository": {"full_name": "octocat/hello-world"},
                    "state": "open",
                },
                "field_values": [
                    {
                        "field": {"name": "Status"},
                        "option": {"name": "Todo"},
                    }
                ],
            }
        ]

    def fetch_issue(self, repository_name: str, issue_number: int) -> dict:
        return {
            "title": "Offline sync item",
            "body": "Hydrated body",
            "html_url": "https://github.com/octocat/hello-world/issues/42",
            "url": "https://api.github.com/repos/octocat/hello-world/issues/42",
            "state": "open",
            "state_reason": None,
            "comments": 1,
            "updated_at": "2026-03-11T10:00:00Z",
            "user": {"login": "octocat"},
            "labels": [{"name": "bug"}],
            "assignees": [{"login": "hubot"}],
            "milestone": {"title": "Sprint 1"},
        }

    def fetch_issue_comments(self, repository_name: str, issue_number: int) -> list[dict]:
        return [
            {
                "id": 1001,
                "body": "Cached comment",
                "user": {"login": "hubot"},
                "created_at": "2026-03-11T10:00:00Z",
                "updated_at": "2026-03-11T10:00:00Z",
                "html_url": "https://github.com/octocat/hello-world/issues/42#issuecomment-1001",
            }
        ]


class FailingGitHubClient:
    def fetch_rate_limit_status(self) -> dict:
        return {"resources": {"core": {"remaining": 4999, "limit": 5000, "reset": 1773250000}}}

    def fetch_project(self) -> dict:
        raise RuntimeError("network boom")


def build_config(database_path: Path) -> AppConfig:
    return AppConfig(
        config_path=Path("gh-project-offline.toml"),
        github=GitHubConfig(
            owner="octocat",
            owner_type="user",
            project_number=12,
            view_number=3,
            token_env="GITHUB_TOKEN",
        ),
        storage=StorageConfig(database_path=database_path, logs_dir=database_path.parent / "logs"),
        sync=SyncConfig(
            interval="15m",
            timeout_seconds=30,
            user_agent="gh-project-offline/test",
            include_closed_items=False,
        ),
    )


class ServiceTests(unittest.TestCase):
    def test_collect_field_ids_ignores_missing_ids(self) -> None:
        field_ids = collect_field_ids(
            [
                {"id": 11},
                {"node_id": "PVTSSF_22"},
                {"name": "Status"},
            ]
        )

        self.assertEqual(field_ids, ["11", "PVTSSF_22"])

    def test_fetch_issue_snapshots_deduplicates_same_issue(self) -> None:
        client = FakeGitHubClient()
        item_payloads = [
            {
                "id": "item-1",
                "content": {
                    "number": 42,
                    "repository": {"full_name": "octocat/hello-world"},
                },
            },
            {
                "id": "item-2",
                "content": {
                    "number": 42,
                    "repository": {"full_name": "octocat/hello-world"},
                },
            },
        ]

        snapshots = fetch_issue_snapshots(client, item_payloads)

        self.assertEqual(len(snapshots), 1)
        self.assertEqual(snapshots[0]["repository_name"], "octocat/hello-world")
        self.assertEqual(snapshots[0]["issue_number"], 42)
        self.assertEqual(client.issue_calls, [("octocat/hello-world", 42)])
        self.assertEqual(client.comment_calls, [("octocat/hello-world", 42)])

    def test_fetch_issue_snapshots_reports_progress(self) -> None:
        client = FakeGitHubClient()
        messages: list[str] = []
        item_payloads = [
            {
                "id": "item-1",
                "content": {
                    "number": 42,
                    "repository": {"full_name": "octocat/hello-world"},
                },
            }
        ]

        fetch_issue_snapshots_with_progress(client, item_payloads, progress=messages.append)

        self.assertIn("Hydrating issue 1/1: octocat/hello-world#42", messages)
        self.assertIn("Fetching comments for octocat/hello-world#42", messages)

    def test_run_sync_persists_project_items_and_issue_cache(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            database_path = Path(tmp_dir) / "cache.sqlite3"
            config = build_config(database_path)
            messages: list[str] = []

            with patch("gh_project_offline.service.GitHubClient", return_value=SyncGitHubClient()):
                summary = run_sync(config, progress=messages.append)

            self.assertEqual(summary.fields_count, 1)
            self.assertEqual(summary.views_count, 1)
            self.assertEqual(summary.items_count, 1)
            self.assertEqual(summary.issues_count, 1)
            self.assertEqual(summary.comments_count, 1)
            self.assertEqual(summary.reused_issue_records, 0)
            self.assertEqual(summary.skipped_comment_fetches, 0)
            self.assertEqual(summary.added_issue_records, 1)
            self.assertEqual(summary.updated_issue_records, 0)
            self.assertEqual(summary.removed_issue_records, 0)

            with connect(database_path) as connection:
                item_count = connection.execute("select count(*) as count from cached_view_items").fetchone()["count"]
                issue_count = connection.execute("select count(*) as count from cached_issue_details").fetchone()["count"]
                comment_count = connection.execute("select count(*) as count from cached_issue_comments").fetchone()["count"]
                last_run = connection.execute("select status from sync_runs order by id desc limit 1").fetchone()["status"]

            self.assertEqual(item_count, 1)
            self.assertEqual(issue_count, 1)
            self.assertEqual(comment_count, 1)
            self.assertEqual(last_run, "success")
            self.assertTrue(any("GitHub rate limit: remaining=4999/5000" in message for message in messages))
            self.assertIn("Fetched 1 project field(s).", messages)
            self.assertIn("Fetched 1 project view definition(s).", messages)
            self.assertIn("Fetched 1 view item(s); keeping 1 and skipping 0 closed item(s).", messages)
            self.assertIn("Hydration scope: 1 unique issue or pull request item(s).", messages)
            self.assertIn("Cache delta summary: added=1 updated=0 removed=0", messages)

    def test_run_sync_reuses_cached_comments_for_unchanged_issue(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            database_path = Path(tmp_dir) / "cache.sqlite3"
            config = build_config(database_path)

            with connect(database_path) as connection:
                from gh_project_offline.db import replace_issue_cache

                replace_issue_cache(
                    connection,
                    project_key_value="user:octocat:12",
                    snapshots=[
                        {
                            "item_key": "item-1",
                            "repository_name": "octocat/hello-world",
                            "issue_number": 42,
                            "issue_payload": SyncGitHubClient().fetch_issue("octocat/hello-world", 42),
                            "comment_payloads": SyncGitHubClient().fetch_issue_comments("octocat/hello-world", 42),
                        }
                    ],
                )

            messages: list[str] = []
            with patch("gh_project_offline.service.GitHubClient", return_value=SyncGitHubClient()):
                summary = run_sync(config, progress=messages.append)

            self.assertEqual(summary.reused_issue_records, 1)
            self.assertEqual(summary.skipped_comment_fetches, 1)
            self.assertEqual(summary.added_issue_records, 0)
            self.assertEqual(summary.updated_issue_records, 0)
            self.assertEqual(summary.removed_issue_records, 0)
            self.assertIn("Reusing cached comments for octocat/hello-world#42", messages)

    def test_run_sync_reports_removed_issue_when_board_item_disappears(self) -> None:
        class EmptyBoardClient(SyncGitHubClient):
            def fetch_view_items(self, field_ids: list[str]) -> list[dict]:
                return []

        with tempfile.TemporaryDirectory() as tmp_dir:
            database_path = Path(tmp_dir) / "cache.sqlite3"
            config = build_config(database_path)
            with connect(database_path) as connection:
                from gh_project_offline.db import replace_issue_cache

                replace_issue_cache(
                    connection,
                    project_key_value="user:octocat:12",
                    snapshots=[
                        {
                            "item_key": "item-1",
                            "repository_name": "octocat/hello-world",
                            "issue_number": 42,
                            "issue_payload": SyncGitHubClient().fetch_issue("octocat/hello-world", 42),
                            "comment_payloads": SyncGitHubClient().fetch_issue_comments("octocat/hello-world", 42),
                        }
                    ],
                )

            with patch("gh_project_offline.service.GitHubClient", return_value=EmptyBoardClient()):
                summary = run_sync(config)

            self.assertEqual(summary.added_issue_records, 0)
            self.assertEqual(summary.updated_issue_records, 0)
            self.assertEqual(summary.removed_issue_records, 1)

    def test_run_sync_skips_closed_items_by_default(self) -> None:
        class MixedStateClient(SyncGitHubClient):
            def fetch_view_items(self, field_ids: list[str]) -> list[dict]:
                return [
                    {
                        "id": "item-open",
                        "content": {
                            "number": 42,
                            "title": "Open item",
                            "repository": {"full_name": "octocat/hello-world"},
                            "state": "open",
                        },
                    },
                    {
                        "id": "item-closed",
                        "content": {
                            "number": 43,
                            "title": "Closed item",
                            "repository": {"full_name": "octocat/hello-world"},
                            "state": "closed",
                        },
                    },
                ]

        with tempfile.TemporaryDirectory() as tmp_dir:
            database_path = Path(tmp_dir) / "cache.sqlite3"
            config = build_config(database_path)

            with patch("gh_project_offline.service.GitHubClient", return_value=MixedStateClient()):
                summary = run_sync(config)

            self.assertEqual(summary.items_count, 1)
            self.assertEqual(summary.issues_count, 1)

    def test_run_sync_marks_failed_runs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            database_path = Path(tmp_dir) / "cache.sqlite3"
            config = build_config(database_path)

            with patch("gh_project_offline.service.GitHubClient", return_value=FailingGitHubClient()):
                with self.assertRaisesRegex(RuntimeError, "network boom"):
                    run_sync(config)

            with connect(database_path) as connection:
                last_run = connection.execute(
                    "select status, error_message from sync_runs order by id desc limit 1"
                ).fetchone()

            self.assertEqual(last_run["status"], "failed")
            self.assertIn("network boom", last_run["error_message"])

    def test_watch_sync_runs_then_sleeps(self) -> None:
        config = build_config(Path("cache.sqlite3"))

        with patch("gh_project_offline.service.run_sync") as mock_run_sync, patch(
            "gh_project_offline.service.time.sleep",
            side_effect=RuntimeError("stop loop"),
        ) as mock_sleep:
            with self.assertRaisesRegex(RuntimeError, "stop loop"):
                watch_sync(config, 123)

        mock_run_sync.assert_called_once_with(config)
        mock_sleep.assert_called_once_with(123)

    def test_filter_item_payloads_and_rate_limit_message_helpers(self) -> None:
        from gh_project_offline.service import filter_item_payloads, format_rate_limit_message

        kept, skipped = filter_item_payloads(
            [
                {"content": {"state": "open"}},
                {"content": {"state": "closed"}},
                {"content": {}},
            ],
            include_closed_items=False,
        )
        self.assertEqual(len(kept), 2)
        self.assertEqual(skipped, 1)

        message = format_rate_limit_message(
            {"resources": {"core": {"remaining": 4999, "limit": 5000, "reset": 1773250000}}}
        )
        self.assertIn("remaining=4999/5000", message)


if __name__ == "__main__":
    unittest.main()
