from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from gh_project_offline.db import (
    connect,
    extract_status_info,
    extract_status_name,
    infer_issue_type,
    replace_issue_cache,
    replace_project_fields,
    replace_project_snapshot,
    replace_project_views,
    replace_view_items,
)
from gh_project_offline.service import fetch_status_rows


class DatabaseTests(unittest.TestCase):
    def test_replace_issue_cache_stores_issue_details_and_comments(self) -> None:
        snapshots = [
            {
                "item_key": "item-1",
                "repository_name": "octocat/hello-world",
                "issue_number": 42,
                "issue_payload": {
                    "title": "Offline sync test",
                    "body": "Need the whole issue body offline.",
                    "html_url": "https://github.com/octocat/hello-world/issues/42",
                    "url": "https://api.github.com/repos/octocat/hello-world/issues/42",
                    "created_at": "2026-03-01T09:00:00Z",
                    "updated_at": "2026-03-11T10:30:00Z",
                    "closed_at": None,
                    "state": "open",
                    "state_reason": None,
                    "comments": 2,
                    "user": {"login": "octocat"},
                    "labels": [{"name": "bug"}],
                    "assignees": [{"login": "hubot"}],
                    "milestone": {
                        "title": "Sprint 1",
                        "description": "Need this finished soon",
                        "due_on": "2026-03-20T00:00:00Z",
                        "state": "open",
                    },
                },
                "comment_payloads": [
                    {
                        "id": 1001,
                        "user": {"login": "hubot"},
                        "created_at": "2026-03-11T10:00:00Z",
                        "updated_at": "2026-03-11T10:00:00Z",
                        "body": "First cached comment",
                        "html_url": "https://github.com/octocat/hello-world/issues/42#issuecomment-1001",
                    },
                    {
                        "id": 1002,
                        "user": {"login": "monalisa"},
                        "created_at": "2026-03-11T10:05:00Z",
                        "updated_at": "2026-03-11T10:05:00Z",
                        "body": "Second cached comment",
                        "html_url": "https://github.com/octocat/hello-world/issues/42#issuecomment-1002",
                    },
                ],
            }
        ]

        with tempfile.TemporaryDirectory() as tmp_dir:
            database_path = Path(tmp_dir) / "cache.sqlite3"
            with connect(database_path) as connection:
                replace_issue_cache(connection, project_key_value="user:octocat:1", snapshots=snapshots)

            with connect(database_path) as connection:
                status_rows = fetch_status_rows(connection)
                issue_row = connection.execute(
                    """
                    select repository_name, issue_number, title, milestone_title, milestone_description,
                           milestone_due_on, milestone_state, created_at, closed_at, comments_count
                    from cached_issue_details
                    """
                ).fetchone()
                comment_rows = connection.execute(
                    "select author_login, body from cached_issue_comments order by comment_id"
                ).fetchall()

        self.assertEqual(status_rows["issue_count"]["count"], 1)
        self.assertEqual(status_rows["comment_count"]["count"], 2)
        self.assertEqual(issue_row["repository_name"], "octocat/hello-world")
        self.assertEqual(issue_row["issue_number"], 42)
        self.assertEqual(issue_row["title"], "Offline sync test")
        self.assertEqual(issue_row["milestone_title"], "Sprint 1")
        self.assertEqual(issue_row["milestone_description"], "Need this finished soon")
        self.assertEqual(issue_row["milestone_due_on"], "2026-03-20T00:00:00Z")
        self.assertEqual(issue_row["milestone_state"], "open")
        self.assertEqual(issue_row["created_at"], "2026-03-01T09:00:00Z")
        self.assertIsNone(issue_row["closed_at"])
        self.assertEqual(issue_row["comments_count"], 2)
        self.assertEqual([row["author_login"] for row in comment_rows], ["hubot", "monalisa"])

    def test_project_snapshot_view_item_helpers_store_normalized_rows(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            database_path = Path(tmp_dir) / "cache.sqlite3"
            with connect(database_path) as connection:
                replace_project_snapshot(
                    connection,
                    project_key_value="user:octocat:1",
                    owner="octocat",
                    owner_type="user",
                    project_number=1,
                    payload={"id": "project-1", "title": "Board"},
                )
                replace_project_fields(
                    connection,
                    project_key_value="user:octocat:1",
                    payloads=[{"id": 11, "name": "Status", "type": "single_select"}],
                )
                replace_project_views(
                    connection,
                    project_key_value="user:octocat:1",
                    payloads=[{"number": 3, "name": "Board", "layout": "board"}],
                )
                replace_view_items(
                    connection,
                    project_key_value="user:octocat:1",
                    view_number=3,
                    payloads=[
                        {
                            "id": "item-1",
                            "content": {
                                "id": "issue-node-1",
                                "number": 42,
                                "title": "Offline item",
                                "repository": {"full_name": "octocat/hello-world"},
                            },
                            "field_values": [
                                {
                                    "field": {"name": "Status"},
                                    "option": {"id": "option-1", "name": "Todo"},
                                }
                            ],
                        }
                    ],
                )
                item_row = connection.execute(
                    "select issue_number, issue_title, status_name, status_field_name, status_option_id, repository_name from cached_view_items"
                ).fetchone()
                project_row = connection.execute("select owner, project_number from project_snapshot").fetchone()

        self.assertEqual(project_row["owner"], "octocat")
        self.assertEqual(project_row["project_number"], 1)
        self.assertEqual(item_row["issue_number"], 42)
        self.assertEqual(item_row["issue_title"], "Offline item")
        self.assertEqual(item_row["status_name"], "Todo")
        self.assertEqual(item_row["status_field_name"], "Status")
        self.assertEqual(item_row["status_option_id"], "option-1")
        self.assertEqual(item_row["repository_name"], "octocat/hello-world")
        self.assertEqual(
            extract_status_info(
                {
                    "fieldValues": [
                        {"field": {"name": "Status"}, "option": {"id": "option-2", "name": "In Progress"}},
                    ]
                }
            ),
            ("In Progress", "Status", "option-2"),
        )
        self.assertEqual(
            extract_status_name(
                {
                    "fieldValues": [
                        {"field": {"name": "Status"}, "name": "In Progress"},
                    ]
                }
            ),
            "In Progress",
        )
        self.assertEqual(infer_issue_type({"pull_request": {"url": "https://api.github.com"}}), "pull_request")
        self.assertEqual(infer_issue_type({}), "issue")


if __name__ == "__main__":
    unittest.main()
