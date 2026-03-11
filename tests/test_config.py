from __future__ import annotations

import tempfile
import textwrap
import unittest
from pathlib import Path

from gh_project_offline.config import (
    APP_DIR_NAME,
    DEFAULT_CONFIG_PATH,
    DEFAULT_PROJECT_URL,
    load_config,
    parse_project_url,
    render_default_config,
)


class ConfigTests(unittest.TestCase):
    def test_parse_project_url_for_user_view(self) -> None:
        parsed = parse_project_url("https://github.com/users/octocat/projects/12/views/3")

        self.assertEqual(parsed.owner, "octocat")
        self.assertEqual(parsed.owner_type, "user")
        self.assertEqual(parsed.project_number, 12)
        self.assertEqual(parsed.view_number, 3)

    def test_parse_project_url_for_org_without_view(self) -> None:
        parsed = parse_project_url("https://github.com/orgs/openai/projects/44")

        self.assertEqual(parsed.owner, "openai")
        self.assertEqual(parsed.owner_type, "org")
        self.assertEqual(parsed.project_number, 44)
        self.assertIsNone(parsed.view_number)

    def test_load_config_uses_project_url_when_explicit_fields_are_omitted(self) -> None:
        config_text = textwrap.dedent(
            """
            [github]
            project_url = "https://github.com/orgs/openai/projects/44/views/9"
            token_env = "BOARD_PAT"

            [storage]
            database_path = "cache.sqlite3"
            logs_dir = "logs"

            [sync]
            interval = "30m"
            timeout_seconds = 60
            user_agent = "gh-project-offline/test"
            include_closed_items = true
            """
        ).strip()

        with tempfile.TemporaryDirectory() as tmp_dir:
            config_path = Path(tmp_dir) / "gh-project-offline.toml"
            config_path.write_text(config_text, encoding="utf-8")

            config = load_config(config_path)

        self.assertEqual(config.github.owner, "openai")
        self.assertEqual(config.github.owner_type, "org")
        self.assertEqual(config.github.project_number, 44)
        self.assertEqual(config.github.view_number, 9)
        self.assertEqual(config.github.token_env, "BOARD_PAT")
        self.assertEqual(config.github.project_web_url, "https://github.com/orgs/openai/projects/44/views/9")
        self.assertEqual(config.storage.logs_dir, (config_path.parent / "logs").resolve())
        self.assertTrue(config.sync.include_closed_items)

    def test_render_default_config_accepts_project_url_override(self) -> None:
        rendered = render_default_config(
            project_url="https://github.com/users/octocat/projects/1/views/2",
            token_env="GH_BOARD_TOKEN",
        )

        self.assertIn('project_url = "https://github.com/users/octocat/projects/1/views/2"', rendered)
        self.assertIn('token_env = "GH_BOARD_TOKEN"', rendered)
        self.assertIn('database_path = "data/cache.db"', rendered)
        self.assertIn('logs_dir = "logs"', rendered)
        self.assertIn('include_closed_items = false', rendered)

    def test_default_config_path_uses_local_app_folder(self) -> None:
        self.assertEqual(DEFAULT_CONFIG_PATH, Path(APP_DIR_NAME) / "config.toml")

    def test_default_project_url_is_generic(self) -> None:
        self.assertEqual(DEFAULT_PROJECT_URL, "https://github.com/users/YOUR-OWNER/projects/123/views/1")


if __name__ == "__main__":
    unittest.main()
