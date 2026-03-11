from __future__ import annotations

import io
import os
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError, URLError

from gh_project_offline.config import AppConfig, GitHubConfig, StorageConfig, SyncConfig
from gh_project_offline.github_api import (
    GitHubApiError,
    GitHubClient,
    build_url,
    extract_next_cursor,
    split_repository_name,
)


def build_test_config() -> AppConfig:
    return AppConfig(
        config_path=Path("gh-project-offline.toml"),
        github=GitHubConfig(
            owner="octocat",
            owner_type="user",
            project_number=12,
            view_number=3,
            token_env="GITHUB_TOKEN",
        ),
        storage=StorageConfig(database_path=Path("cache.sqlite3"), logs_dir=Path("logs")),
        sync=SyncConfig(
            interval="15m",
            timeout_seconds=30,
            user_agent="gh-project-offline/test",
            include_closed_items=False,
        ),
    )


class GitHubApiTests(unittest.TestCase):
    def test_build_url_handles_repeated_field_parameters(self) -> None:
        url = build_url("/users/octocat/projectsV2/12/views/3/items", {"fields[]": ["1", "2"], "per_page": 100})

        self.assertIn("fields%5B%5D=1", url)
        self.assertIn("fields%5B%5D=2", url)
        self.assertIn("per_page=100", url)

    def test_extract_next_cursor_reads_after_cursor_from_link_header(self) -> None:
        link_header = '<https://api.github.com/resource?after=opaque-cursor>; rel="next"'

        self.assertEqual(extract_next_cursor(link_header), "opaque-cursor")

    def test_fetch_view_items_uses_cursor_pagination_and_requested_fields(self) -> None:
        with patch.dict(os.environ, {"GITHUB_TOKEN": "test-token"}):
            client = GitHubClient(build_test_config())

        with patch.object(
            client,
            "_request_json",
            side_effect=[
                (
                    {"items": [{"id": "item-1"}]},
                    {"Link": '<https://api.github.com/next?after=cursor-2>; rel="next"'},
                ),
                (
                    {"items": [{"id": "item-2"}]},
                    {},
                ),
            ],
        ) as mock_request:
            items = client.fetch_view_items(["11", "22"])

        self.assertEqual(items, [{"id": "item-1"}, {"id": "item-2"}])
        self.assertEqual(mock_request.call_args_list[0].kwargs["query"], {"per_page": 100, "fields[]": ["11", "22"]})
        self.assertEqual(
            mock_request.call_args_list[1].kwargs["query"],
            {"per_page": 100, "fields[]": ["11", "22"], "after": "cursor-2"},
        )

    def test_fetch_project_fields_views_issue_and_comments_delegate_to_request_layer(self) -> None:
        with patch.dict(os.environ, {"GITHUB_TOKEN": "test-token"}):
            client = GitHubClient(build_test_config())

        with patch.object(
            client,
            "_request_json",
            side_effect=[
                ({"id": "project-1"}, {}),
                ({"fields": [{"id": 11}]}, {}),
                ({"views": [{"number": 3}]}, {}),
                ({"title": "Issue 42"}, {}),
                ([{"id": 1001}], {}),
            ],
        ) as mock_request:
            project_payload = client.fetch_project()
            field_payloads = client.fetch_fields()
            view_payloads = client.fetch_views()
            issue_payload = client.fetch_issue("octocat/hello-world", 42)
            comment_payloads = client.fetch_issue_comments("octocat/hello-world", 42)

        self.assertEqual(project_payload["id"], "project-1")
        self.assertEqual(field_payloads, [{"id": 11}])
        self.assertEqual(view_payloads, [{"number": 3}])
        self.assertEqual(issue_payload["title"], "Issue 42")
        self.assertEqual(comment_payloads, [{"id": 1001}])
        self.assertEqual(mock_request.call_count, 5)

    def test_request_json_success_http_error_and_url_error(self) -> None:
        with patch.dict(os.environ, {"GITHUB_TOKEN": "test-token"}):
            client = GitHubClient(build_test_config())

        class FakeResponse:
            def __init__(self, payload: bytes) -> None:
                self._payload = payload
                self.headers = {"Link": ""}

            def read(self) -> bytes:
                return self._payload

            def __enter__(self) -> FakeResponse:
                return self

            def __exit__(self, exc_type, exc, tb) -> None:
                return None

        with patch("gh_project_offline.github_api.request.urlopen", return_value=FakeResponse(b'{"ok": true}')):
            payload, headers = client._request_json("/users/octocat/projectsV2/12")

        self.assertEqual(payload, {"ok": True})
        self.assertEqual(headers, {"Link": ""})

        http_error = HTTPError(
            url="https://api.github.com/test",
            code=404,
            msg="Not Found",
            hdrs={},
            fp=io.BytesIO(b'{"message":"missing"}'),
        )
        with patch("gh_project_offline.github_api.request.urlopen", side_effect=http_error):
            with self.assertRaises(GitHubApiError) as ctx:
                client._request_json("/users/octocat/projectsV2/12")

        self.assertEqual(ctx.exception.status_code, 404)
        self.assertIn("404", str(ctx.exception))

        with patch(
            "gh_project_offline.github_api.request.urlopen",
            side_effect=URLError("offline"),
        ):
            with self.assertRaisesRegex(GitHubApiError, "offline"):
                client._request_json("/users/octocat/projectsV2/12")

    def test_split_repository_name_validates_shape(self) -> None:
        self.assertEqual(split_repository_name("octocat/hello-world"), ("octocat", "hello-world"))
        with self.assertRaises(ValueError):
            split_repository_name("hello-world")


if __name__ == "__main__":
    unittest.main()
