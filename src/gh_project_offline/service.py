# SPDX-License-Identifier: GPL-3.0-only

from __future__ import annotations

import sqlite3
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from .config import AppConfig
from .db import (
    connect,
    fetch_cached_comment_index,
    fetch_cached_issue_index,
    finish_sync_run,
    get_cache_meta,
    project_key,
    replace_issue_cache,
    replace_project_fields,
    replace_project_snapshot,
    replace_project_views,
    replace_view_items,
    set_cache_meta,
    start_sync_run,
)
from .github_api import GitHubApiError, GitHubClient


@dataclass(slots=True)
class SyncSummary:
    fields_count: int
    views_count: int
    items_count: int
    issues_count: int
    comments_count: int
    reused_issue_records: int
    skipped_comment_fetches: int
    added_issue_records: int
    updated_issue_records: int
    removed_issue_records: int


def run_sync(
    config: AppConfig,
    *,
    progress: Callable[[str], None] | None = None,
    sync_mode: str = "manual_sync",
) -> SyncSummary:
    client = GitHubClient(config)
    key = project_key(config.github.owner_type, config.github.owner, config.github.project_number)
    emit = progress or (lambda _message: None)

    with connect(config.storage.database_path) as connection:
        run_id = start_sync_run(connection)
        try:
            rate_limit = client.fetch_rate_limit_status()
            emit(format_rate_limit_message(rate_limit))
            emit(describe_sync_mode(sync_mode))
            emit(step_message(sync_mode, "project snapshot", call_hint="1 endpoint call"))
            project_payload = client.fetch_project()
            emit(step_done_message(sync_mode, "project snapshot"))
            emit(step_message(sync_mode, "project fields"))
            field_payloads = client.fetch_fields()
            emit(f"Fetched {len(field_payloads)} project field(s).")
            field_ids = collect_field_ids(field_payloads)
            try:
                emit(step_message(sync_mode, "project views"))
                view_payloads = client.fetch_views()
                emit(f"Fetched {len(view_payloads)} project view definition(s).")
            except GitHubApiError as exc:
                if exc.status_code not in {404, 405}:
                    raise
                view_payloads = []
                emit("Project views endpoint unavailable; continuing without cached view definitions.")
            emit(step_message(sync_mode, "view items for the configured board view"))
            item_payloads = client.fetch_view_items(field_ids)
            open_item_payloads, skipped_closed_count = filter_item_payloads(
                item_payloads,
                include_closed_items=config.sync.include_closed_items,
            )
            emit(
                f"Fetched {len(item_payloads)} view item(s); "
                f"keeping {len(open_item_payloads)} and skipping {skipped_closed_count} closed item(s)."
            )
            emit(
                f"{hydration_prefix(sync_mode)} linked issues and comments "
                f"for {len(open_item_payloads)} remaining board item(s)..."
            )
            cached_issue_index = fetch_cached_issue_index(connection, project_key_value=key)
            cached_comment_index = fetch_cached_comment_index(connection, project_key_value=key)
            issue_snapshots, reused_issue_records, skipped_comment_fetches = fetch_issue_snapshots_with_progress(
                client,
                open_item_payloads,
                cached_issue_index=cached_issue_index,
                cached_comment_index=cached_comment_index,
                progress=emit,
            )
            added_issue_records, updated_issue_records, removed_issue_records = summarize_issue_deltas(
                cached_issue_index,
                issue_snapshots,
            )

            emit("Writing cache to SQLite...")
            replace_project_snapshot(
                connection,
                project_key_value=key,
                owner=config.github.owner,
                owner_type=config.github.owner_type,
                project_number=config.github.project_number,
                payload=project_payload,
            )
            replace_project_fields(connection, project_key_value=key, payloads=field_payloads)
            replace_project_views(connection, project_key_value=key, payloads=view_payloads)
            replace_view_items(
                connection,
                project_key_value=key,
                view_number=config.github.view_number,
                payloads=open_item_payloads,
            )
            replace_issue_cache(connection, project_key_value=key, snapshots=issue_snapshots)
            finish_sync_run(connection, run_id, "success")
            emit("Sync complete.")
            emit(
                "Hydration reuse summary: "
                f"reused_issue_records={reused_issue_records} "
                f"skipped_comment_fetches={skipped_comment_fetches}"
            )
            emit(
                "Cache delta summary: "
                f"added={added_issue_records} "
                f"updated={updated_issue_records} "
                f"removed={removed_issue_records}"
            )
            set_cache_meta(
                connection,
                "last_cache_delta_summary",
                f"added={added_issue_records} updated={updated_issue_records} removed={removed_issue_records}",
            )
            return SyncSummary(
                fields_count=len(field_payloads),
                views_count=len(view_payloads),
                items_count=len(open_item_payloads),
                issues_count=len(issue_snapshots),
                comments_count=sum(len(snapshot["comment_payloads"]) for snapshot in issue_snapshots),
                reused_issue_records=reused_issue_records,
                skipped_comment_fetches=skipped_comment_fetches,
                added_issue_records=added_issue_records,
                updated_issue_records=updated_issue_records,
                removed_issue_records=removed_issue_records,
            )
        except Exception as exc:
            finish_sync_run(connection, run_id, "failed", str(exc))
            connection.commit()
            raise


def watch_sync(config: AppConfig, interval_seconds: int) -> None:
    while True:
        run_sync(config)
        time.sleep(interval_seconds)


def fetch_status_rows(connection: sqlite3.Connection) -> dict[str, sqlite3.Row | None]:
    return {
        "last_run": connection.execute(
            "select * from sync_runs order by id desc limit 1"
        ).fetchone(),
        "recent_runs": connection.execute(
            "select * from sync_runs order by id desc limit 5"
        ).fetchall(),
        "project": connection.execute(
            "select * from project_snapshot order by updated_at desc limit 1"
        ).fetchone(),
        "field_count": connection.execute("select count(*) as count from project_fields").fetchone(),
        "view_count": connection.execute("select count(*) as count from project_views").fetchone(),
        "item_count": connection.execute("select count(*) as count from cached_view_items").fetchone(),
        "issue_count": connection.execute("select count(*) as count from cached_issue_details").fetchone(),
        "comment_count": connection.execute("select count(*) as count from cached_issue_comments").fetchone(),
        "last_cache_delta_summary": get_cache_meta(connection, "last_cache_delta_summary"),
    }


def collect_field_ids(field_payloads: list[dict[str, Any]]) -> list[str]:
    field_ids: list[str] = []
    for payload in field_payloads:
        value = payload.get("id") or payload.get("node_id")
        if value is not None:
            field_ids.append(str(value))
    return field_ids


def fetch_issue_snapshots(client: GitHubClient, item_payloads: list[dict[str, Any]]) -> list[dict[str, Any]]:
    snapshots, _, _ = fetch_issue_snapshots_with_progress(client, item_payloads)
    return snapshots


def fetch_issue_snapshots_with_progress(
    client: GitHubClient,
    item_payloads: list[dict[str, Any]],
    *,
    cached_issue_index: dict[tuple[str, int], sqlite3.Row] | None = None,
    cached_comment_index: dict[tuple[str, int], list[dict]] | None = None,
    progress: Callable[[str], None] | None = None,
) -> tuple[list[dict[str, Any]], int, int]:
    snapshots: list[dict[str, Any]] = []
    seen: set[tuple[str, int]] = set()
    emit = progress or (lambda _message: None)
    unique_targets: list[tuple[str, int]] = []
    reused_issue_records = 0
    skipped_comment_fetches = 0
    cached_index = cached_issue_index or {}
    cached_comments = cached_comment_index or {}

    for payload in item_payloads:
        content = payload.get("content") or {}
        repository = content.get("repository") or {}
        repository_name = repository.get("full_name")
        issue_number = content.get("number")
        if not repository_name or issue_number is None:
            continue
        key = (str(repository_name), int(issue_number))
        if key in seen:
            continue
        unique_targets.append(key)
        seen.add(key)

    seen.clear()
    total = len(unique_targets)
    emit(f"Hydration scope: {total} unique issue or pull request item(s).")
    for payload in item_payloads:
        content = payload.get("content") or {}
        repository = content.get("repository") or {}
        repository_name = repository.get("full_name")
        issue_number = content.get("number")
        if not repository_name or issue_number is None:
            continue
        key = (str(repository_name), int(issue_number))
        if key in seen:
            continue
        seen.add(key)
        emit(f"Hydrating issue {len(seen)}/{total}: {repository_name}#{issue_number}")
        issue_payload = client.fetch_issue(str(repository_name), int(issue_number))
        cached_row = cached_index.get(key)
        issue_unchanged = is_issue_unchanged(cached_row, issue_payload)
        if issue_unchanged:
            reused_issue_records += 1
            emit(f"Issue unchanged since last cache: {repository_name}#{issue_number}")
        emit(f"Fetching comments for {repository_name}#{issue_number}")
        if issue_unchanged and cached_row is not None:
            skipped_comment_fetches += 1
            comment_payloads = list(cached_comments.get(key, []))
            emit(f"Reusing cached comments for {repository_name}#{issue_number}")
        else:
            comment_payloads = client.fetch_issue_comments(str(repository_name), int(issue_number))
        snapshots.append(
            {
                "item_key": str(payload.get("id") or payload.get("node_id") or issue_number),
                "repository_name": str(repository_name),
                "issue_number": int(issue_number),
                "issue_payload": issue_payload,
                "comment_payloads": comment_payloads,
            }
        )

    return snapshots, reused_issue_records, skipped_comment_fetches


def filter_item_payloads(
    item_payloads: list[dict[str, Any]],
    *,
    include_closed_items: bool,
) -> tuple[list[dict[str, Any]], int]:
    if include_closed_items:
        return item_payloads, 0

    kept: list[dict[str, Any]] = []
    skipped_closed_count = 0
    for payload in item_payloads:
        state = ((payload.get("content") or {}).get("state") or "").lower()
        if state == "closed":
            skipped_closed_count += 1
            continue
        kept.append(payload)
    return kept, skipped_closed_count


def format_rate_limit_message(payload: dict[str, Any]) -> str:
    resources = payload.get("resources") or {}
    core = resources.get("core") or {}
    remaining = core.get("remaining")
    limit = core.get("limit")
    reset_epoch = core.get("reset")
    if remaining is None or limit is None:
        return "GitHub rate limit status unavailable."
    reset_text = "unknown"
    if isinstance(reset_epoch, (int, float)):
        reset_text = datetime.fromtimestamp(reset_epoch).strftime("%Y-%m-%d %H:%M:%S")
    return f"GitHub rate limit: remaining={remaining}/{limit} reset_local={reset_text}"


def describe_sync_mode(sync_mode: str) -> str:
    labels = {
        "initial_load": "Sync mode: initial load from GitHub into an empty or reset local cache.",
        "recheck": "Sync mode: recheck existing local cache against GitHub and refresh what changed.",
        "manual_sync": "Sync mode: manual sync refresh against GitHub.",
    }
    return labels.get(sync_mode, "Sync mode: refresh against GitHub.")


def step_message(sync_mode: str, target: str, *, call_hint: str | None = None) -> str:
    verb = "Checking" if sync_mode == "recheck" else "Fetching"
    suffix = f" ({call_hint})" if call_hint else ""
    return f"{verb} {target}{suffix}..."


def step_done_message(sync_mode: str, target: str) -> str:
    verb = "Checked" if sync_mode == "recheck" else "Fetched"
    return f"{verb} {target}."


def hydration_prefix(sync_mode: str) -> str:
    if sync_mode == "recheck":
        return "Rechecking"
    return "Hydrating"


def is_issue_unchanged(cached_row: sqlite3.Row | None, issue_payload: dict[str, Any]) -> bool:
    if cached_row is None:
        return False
    return (
        cached_row["remote_updated_at"] == issue_payload.get("updated_at")
        and int(cached_row["comments_count"] or 0) == int(issue_payload.get("comments") or 0)
    )


def summarize_issue_deltas(
    cached_issue_index: dict[tuple[str, int], sqlite3.Row],
    issue_snapshots: list[dict[str, Any]],
) -> tuple[int, int, int]:
    cached_keys = set(cached_issue_index.keys())
    new_keys = {
        (snapshot["repository_name"], snapshot["issue_number"])
        for snapshot in issue_snapshots
    }
    added = len(new_keys - cached_keys)
    removed = len(cached_keys - new_keys)
    updated = 0
    for snapshot in issue_snapshots:
        key = (snapshot["repository_name"], snapshot["issue_number"])
        cached_row = cached_issue_index.get(key)
        if cached_row is None:
            continue
        if not is_issue_unchanged(cached_row, snapshot["issue_payload"]):
            updated += 1
    return added, updated, removed
