# SPDX-License-Identifier: GPL-3.0-only

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

SCHEMA = """
create table if not exists sync_runs (
    id integer primary key autoincrement,
    started_at text not null,
    finished_at text,
    status text not null,
    error_message text
);

create table if not exists cache_meta (
    key text primary key,
    value text not null
);

create table if not exists project_snapshot (
    project_key text primary key,
    owner text not null,
    owner_type text not null,
    project_number integer not null,
    project_json text not null,
    updated_at text not null
);

create table if not exists project_fields (
    project_key text not null,
    field_key text not null,
    field_name text,
    field_type text,
    field_json text not null,
    updated_at text not null,
    primary key (project_key, field_key)
);

create table if not exists project_views (
    project_key text not null,
    view_number integer not null,
    view_name text,
    layout text,
    view_json text not null,
    updated_at text not null,
    primary key (project_key, view_number)
);

create table if not exists cached_view_items (
    project_key text not null,
    view_number integer not null,
    item_key text not null,
    issue_number integer,
    issue_title text,
    status_name text,
    status_field_name text,
    status_option_id text,
    repository_name text,
    item_json text not null,
    updated_at text not null,
    primary key (project_key, view_number, item_key)
);

create table if not exists cached_issue_details (
    project_key text not null,
    repository_name text not null,
    issue_number integer not null,
    item_key text,
    issue_type text,
    state text,
    state_reason text,
    title text,
    body text,
    html_url text,
    api_url text,
    author_login text,
    milestone_title text,
    milestone_description text,
    milestone_due_on text,
    milestone_state text,
    labels_json text not null,
    assignees_json text not null,
    comments_count integer not null,
    created_at text,
    closed_at text,
    remote_updated_at text,
    issue_json text not null,
    updated_at text not null,
    primary key (project_key, repository_name, issue_number)
);

create table if not exists cached_issue_comments (
    project_key text not null,
    repository_name text not null,
    issue_number integer not null,
    comment_id integer not null,
    author_login text,
    created_at text,
    updated_at text,
    body text,
    html_url text,
    comment_json text not null,
    synced_at text not null,
    primary key (project_key, comment_id)
);
"""


def utc_now() -> str:
    return datetime.now(UTC).isoformat()


def set_cache_meta(connection: sqlite3.Connection, key: str, value: str) -> None:
    connection.execute(
        """
        insert into cache_meta(key, value)
        values(?, ?)
        on conflict(key) do update set value = excluded.value
        """,
        (key, value),
    )


def get_cache_meta(connection: sqlite3.Connection, key: str) -> str | None:
    row = connection.execute("select value from cache_meta where key = ?", (key,)).fetchone()
    return None if row is None else str(row["value"])


def project_key(owner_type: str, owner: str, project_number: int) -> str:
    return f"{owner_type}:{owner}:{project_number}"


@contextmanager
def connect(path: Path) -> Iterator[sqlite3.Connection]:
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    try:
        connection.executescript(SCHEMA)
        ensure_schema_upgrades(connection)
        yield connection
        connection.commit()
    finally:
        connection.close()


def ensure_schema_upgrades(connection: sqlite3.Connection) -> None:
    add_column_if_missing(connection, "cached_issue_details", "remote_updated_at text")
    add_column_if_missing(connection, "cached_view_items", "status_field_name text")
    add_column_if_missing(connection, "cached_view_items", "status_option_id text")
    add_column_if_missing(connection, "cached_issue_details", "milestone_description text")
    add_column_if_missing(connection, "cached_issue_details", "milestone_due_on text")
    add_column_if_missing(connection, "cached_issue_details", "milestone_state text")
    add_column_if_missing(connection, "cached_issue_details", "created_at text")
    add_column_if_missing(connection, "cached_issue_details", "closed_at text")


def add_column_if_missing(connection: sqlite3.Connection, table_name: str, column_sql: str) -> None:
    column_name = column_sql.split()[0]
    existing_columns = {
        row["name"]
        for row in connection.execute(f"pragma table_info({table_name})").fetchall()
    }
    if column_name in existing_columns:
        return
    connection.execute(f"alter table {table_name} add column {column_sql}")


def start_sync_run(connection: sqlite3.Connection) -> int:
    cursor = connection.execute(
        "insert into sync_runs(started_at, status) values(?, ?)",
        (utc_now(), "running"),
    )
    return int(cursor.lastrowid)


def finish_sync_run(connection: sqlite3.Connection, run_id: int, status: str, error_message: str | None = None) -> None:
    connection.execute(
        "update sync_runs set finished_at = ?, status = ?, error_message = ? where id = ?",
        (utc_now(), status, error_message, run_id),
    )


def replace_project_snapshot(
    connection: sqlite3.Connection,
    *,
    project_key_value: str,
    owner: str,
    owner_type: str,
    project_number: int,
    payload: dict,
) -> None:
    connection.execute(
        """
        insert into project_snapshot(project_key, owner, owner_type, project_number, project_json, updated_at)
        values(?, ?, ?, ?, ?, ?)
        on conflict(project_key) do update set
            owner = excluded.owner,
            owner_type = excluded.owner_type,
            project_number = excluded.project_number,
            project_json = excluded.project_json,
            updated_at = excluded.updated_at
        """,
        (project_key_value, owner, owner_type, project_number, json.dumps(payload), utc_now()),
    )


def replace_project_fields(connection: sqlite3.Connection, *, project_key_value: str, payloads: list[dict]) -> None:
    connection.execute("delete from project_fields where project_key = ?", (project_key_value,))
    updated_at = utc_now()
    for payload in payloads:
        field_key = str(payload.get("id") or payload.get("node_id") or payload.get("name"))
        connection.execute(
            """
            insert into project_fields(project_key, field_key, field_name, field_type, field_json, updated_at)
            values(?, ?, ?, ?, ?, ?)
            """,
            (
                project_key_value,
                field_key,
                payload.get("name"),
                payload.get("data_type") or payload.get("type"),
                json.dumps(payload),
                updated_at,
            ),
        )


def replace_project_views(connection: sqlite3.Connection, *, project_key_value: str, payloads: list[dict]) -> None:
    connection.execute("delete from project_views where project_key = ?", (project_key_value,))
    updated_at = utc_now()
    for payload in payloads:
        connection.execute(
            """
            insert into project_views(project_key, view_number, view_name, layout, view_json, updated_at)
            values(?, ?, ?, ?, ?, ?)
            """,
            (
                project_key_value,
                int(payload["number"]),
                payload.get("name"),
                payload.get("layout"),
                json.dumps(payload),
                updated_at,
            ),
        )


def replace_view_items(
    connection: sqlite3.Connection,
    *,
    project_key_value: str,
    view_number: int,
    payloads: list[dict],
) -> None:
    connection.execute(
        "delete from cached_view_items where project_key = ? and view_number = ?",
        (project_key_value, view_number),
    )
    updated_at = utc_now()
    for payload in payloads:
        content = payload.get("content") or {}
        repository = content.get("repository") or {}
        status_name, status_field_name, status_option_id = extract_status_info(payload)
        item_key = str(payload.get("id") or payload.get("node_id") or content.get("id") or content.get("number"))
        connection.execute(
            """
            insert into cached_view_items(
                project_key, view_number, item_key, issue_number, issue_title,
                status_name, status_field_name, status_option_id, repository_name, item_json, updated_at
            )
            values(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                project_key_value,
                view_number,
                item_key,
                content.get("number"),
                content.get("title"),
                status_name,
                status_field_name,
                status_option_id,
                repository.get("full_name"),
                json.dumps(payload),
                updated_at,
            ),
        )


def replace_issue_cache(
    connection: sqlite3.Connection,
    *,
    project_key_value: str,
    snapshots: list[dict],
) -> None:
    synced_at = utc_now()
    seen_issue_keys: set[tuple[str, int]] = set()
    seen_comment_ids: set[int] = set()
    for snapshot in snapshots:
        issue_payload = snapshot["issue_payload"]
        labels = issue_payload.get("labels") or []
        assignees = issue_payload.get("assignees") or []
        milestone = issue_payload.get("milestone") or {}
        issue_key = (snapshot["repository_name"], snapshot["issue_number"])
        seen_issue_keys.add(issue_key)
        connection.execute(
            """
                insert into cached_issue_details(
                    project_key, repository_name, issue_number, item_key, issue_type, state, state_reason,
                    title, body, html_url, api_url, author_login, milestone_title, milestone_description,
                    milestone_due_on, milestone_state, labels_json, assignees_json, comments_count,
                    created_at, closed_at, remote_updated_at, issue_json, updated_at
                )
            values(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            on conflict(project_key, repository_name, issue_number) do update set
                item_key = excluded.item_key,
                issue_type = excluded.issue_type,
                state = excluded.state,
                state_reason = excluded.state_reason,
                title = excluded.title,
                body = excluded.body,
                html_url = excluded.html_url,
                api_url = excluded.api_url,
                author_login = excluded.author_login,
                milestone_title = excluded.milestone_title,
                milestone_description = excluded.milestone_description,
                milestone_due_on = excluded.milestone_due_on,
                milestone_state = excluded.milestone_state,
                labels_json = excluded.labels_json,
                assignees_json = excluded.assignees_json,
                comments_count = excluded.comments_count,
                created_at = excluded.created_at,
                closed_at = excluded.closed_at,
                remote_updated_at = excluded.remote_updated_at,
                issue_json = excluded.issue_json,
                updated_at = excluded.updated_at
            """,
            (
                project_key_value,
                snapshot["repository_name"],
                snapshot["issue_number"],
                snapshot["item_key"],
                infer_issue_type(issue_payload),
                issue_payload.get("state"),
                issue_payload.get("state_reason"),
                issue_payload.get("title"),
                issue_payload.get("body"),
                issue_payload.get("html_url"),
                issue_payload.get("url"),
                (issue_payload.get("user") or {}).get("login"),
                milestone.get("title"),
                milestone.get("description"),
                milestone.get("due_on"),
                milestone.get("state"),
                json.dumps(labels),
                json.dumps(assignees),
                int(issue_payload.get("comments") or 0),
                issue_payload.get("created_at"),
                issue_payload.get("closed_at"),
                issue_payload.get("updated_at"),
                json.dumps(issue_payload),
                synced_at,
            ),
        )
        for comment in snapshot["comment_payloads"]:
            seen_comment_ids.add(int(comment["id"]))
            connection.execute(
                """
                insert into cached_issue_comments(
                    project_key, repository_name, issue_number, comment_id, author_login,
                    created_at, updated_at, body, html_url, comment_json, synced_at
                )
                values(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                on conflict(project_key, comment_id) do update set
                    repository_name = excluded.repository_name,
                    issue_number = excluded.issue_number,
                    author_login = excluded.author_login,
                    created_at = excluded.created_at,
                    updated_at = excluded.updated_at,
                    body = excluded.body,
                    html_url = excluded.html_url,
                    comment_json = excluded.comment_json,
                    synced_at = excluded.synced_at
                """,
                (
                    project_key_value,
                    snapshot["repository_name"],
                    snapshot["issue_number"],
                    int(comment["id"]),
                    (comment.get("user") or {}).get("login"),
                    comment.get("created_at"),
                    comment.get("updated_at"),
                    comment.get("body"),
                    comment.get("html_url"),
                    json.dumps(comment),
                    synced_at,
                ),
            )

    for row in connection.execute(
        "select repository_name, issue_number from cached_issue_details where project_key = ?",
        (project_key_value,),
    ).fetchall():
        issue_key = (row["repository_name"], row["issue_number"])
        if issue_key not in seen_issue_keys:
            connection.execute(
                """
                delete from cached_issue_comments
                where project_key = ? and repository_name = ? and issue_number = ?
                """,
                (project_key_value, row["repository_name"], row["issue_number"]),
            )
            connection.execute(
                """
                delete from cached_issue_details
                where project_key = ? and repository_name = ? and issue_number = ?
                """,
                (project_key_value, row["repository_name"], row["issue_number"]),
            )

    for row in connection.execute(
        "select comment_id from cached_issue_comments where project_key = ?",
        (project_key_value,),
    ).fetchall():
        if row["comment_id"] not in seen_comment_ids:
            connection.execute(
                "delete from cached_issue_comments where project_key = ? and comment_id = ?",
                (project_key_value, row["comment_id"]),
            )


def fetch_cached_issue_index(connection: sqlite3.Connection, *, project_key_value: str) -> dict[tuple[str, int], sqlite3.Row]:
    rows = connection.execute(
        """
        select repository_name, issue_number, comments_count, remote_updated_at, updated_at
        from cached_issue_details
        where project_key = ?
        """,
        (project_key_value,),
    ).fetchall()
    return {
        (row["repository_name"], row["issue_number"]): row
        for row in rows
    }


def fetch_cached_comment_index(
    connection: sqlite3.Connection,
    *,
    project_key_value: str,
) -> dict[tuple[str, int], list[dict]]:
    rows = connection.execute(
        """
        select repository_name, issue_number, comment_json
        from cached_issue_comments
        where project_key = ?
        order by repository_name, issue_number, created_at asc, comment_id asc
        """,
        (project_key_value,),
    ).fetchall()
    index: dict[tuple[str, int], list[dict]] = {}
    for row in rows:
        key = (row["repository_name"], row["issue_number"])
        index.setdefault(key, []).append(json.loads(row["comment_json"]))
    return index


def extract_status_info(payload: dict) -> tuple[str | None, str | None, str | None]:
    field_values = payload.get("field_values") or payload.get("fieldValues") or []
    for value in field_values:
        field = value.get("field") or {}
        if (field.get("name") or "").lower() == "status":
            option = value.get("option") or {}
            return option.get("name") or value.get("name"), field.get("name"), option.get("id") or option.get("node_id")
    return None, None, None


def extract_status_name(payload: dict) -> str | None:
    name, _, _ = extract_status_info(payload)
    return name


def infer_issue_type(issue_payload: dict) -> str:
    return "pull_request" if issue_payload.get("pull_request") else "issue"
