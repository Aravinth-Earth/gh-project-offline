# SPDX-License-Identifier: GPL-3.0-only

from __future__ import annotations

import argparse
import getpass
import json
import os
import sqlite3
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from rich.console import Console
from rich.progress import (
    BarColumn,
    Progress,
    SpinnerColumn,
    TaskProgressColumn,
    TextColumn,
    TimeElapsedColumn,
)
from rich.table import Table

from .config import DEFAULT_CONFIG_PATH, AppConfig, load_config, write_default_config
from .db import connect
from .github_api import GitHubApiError, GitHubClient
from .runtime import create_run_logger
from .service import fetch_status_rows, run_sync

CONSOLE = Console()


def console_print(message: str) -> None:
    CONSOLE.print(message, markup=False)


class SyncProgressRenderer:
    def __init__(self) -> None:
        self._progress = Progress(
            SpinnerColumn(),
            TextColumn("{task.description}"),
            BarColumn(),
            TaskProgressColumn(),
            TimeElapsedColumn(),
            console=CONSOLE,
            transient=True,
        )
        self._task_id: int | None = None
        self._started = False
        self._phase_label: str | None = None
        self._phase_started_at: float | None = None
        self._hydration_total: int | None = None
        self._cache_delta_summary: str | None = None

    def __enter__(self) -> SyncProgressRenderer:
        self._progress.__enter__()
        self._task_id = self._progress.add_task("Preparing sync...", total=None)
        self._started = True
        self._phase_started_at = time.perf_counter()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        if self._started:
            self._finish_phase("Interrupted." if exc_type is KeyboardInterrupt else None)
            self._progress.__exit__(exc_type, exc, tb)
            self._started = False

    def emit(self, message: str) -> None:
        if self._task_id is None:
            return
        phase_label = detect_phase_label(message)
        self._switch_phase(phase_label)
        if message.startswith("Cache delta summary: "):
            self._cache_delta_summary = message.removeprefix("Cache delta summary: ").strip()
            return
        hydration_scope = parse_hydration_scope(message)
        hydration_step = parse_hydration_step(message)
        if hydration_scope is not None:
            self._hydration_total = hydration_scope
            self._progress.update(self._task_id, total=hydration_scope, completed=0, description=message)
            return
        if hydration_step is not None:
            completed, total = hydration_step
            self._progress.update(self._task_id, total=total, completed=completed, description=message)
            return
        if message == "Sync complete.":
            self._finish_phase("Done.")
        self._progress.update(self._task_id, description=message)

    def _switch_phase(self, phase_label: str | None) -> None:
        if phase_label is None:
            return
        if self._phase_label == phase_label:
            return
        self._finish_phase()
        self._phase_label = phase_label
        self._phase_started_at = time.perf_counter()

    def _finish_phase(self, forced_suffix: str | None = None) -> None:
        if self._phase_label is None or self._phase_started_at is None:
            return
        elapsed = max(time.perf_counter() - self._phase_started_at, 0)
        suffix = forced_suffix or "Done."
        if self._phase_label == "Hydrating issues":
            count_text = f"{self._hydration_total or 0} item(s)"
            console_print(f"{self._phase_label}: {count_text} {suffix} in {format_duration(int(round(elapsed)))}")
        elif self._phase_label == "Sync complete" and self._cache_delta_summary:
            console_print(
                f"{self._phase_label}: {suffix} in {format_duration(int(round(elapsed)))} "
                f"({self._cache_delta_summary})"
            )
        else:
            console_print(f"{self._phase_label}: {suffix} in {format_duration(int(round(elapsed)))}")
        self._phase_label = None
        self._phase_started_at = None


def parse_hydration_scope(message: str) -> int | None:
    prefix = "Hydration scope: "
    if not message.startswith(prefix):
        return None
    count_text = message[len(prefix):].split(" ", 1)[0]
    return int(count_text) if count_text.isdigit() else None


def parse_hydration_step(message: str) -> tuple[int, int] | None:
    prefix = "Hydrating issue "
    if not message.startswith(prefix):
        return None
    progress_text = message[len(prefix):].split(":", 1)[0].strip()
    if "/" not in progress_text:
        return None
    completed_text, total_text = progress_text.split("/", 1)
    if completed_text.isdigit() and total_text.isdigit():
        return int(completed_text), int(total_text)
    return None


def detect_phase_label(message: str) -> str | None:
    phase_prefixes = [
        ("GitHub rate limit:", "Rate limit check"),
        ("Fetching project snapshot", "Project snapshot"),
        ("Checking project snapshot", "Project snapshot"),
        ("Fetching project fields", "Project fields"),
        ("Checking project fields", "Project fields"),
        ("Fetching project views", "Project views"),
        ("Checking project views", "Project views"),
        ("Fetching view items", "View items"),
        ("Checking view items", "View items"),
        ("Hydrating linked issues and comments", "Hydrating issues"),
        ("Rechecking linked issues and comments", "Hydrating issues"),
        ("Writing cache to SQLite", "Writing cache"),
        ("Sync complete.", "Sync complete"),
    ]
    for prefix, label in phase_prefixes:
        if message.startswith(prefix):
            return label
    if message.startswith("Hydrating issue "):
        return "Hydrating issues"
    return None


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="gh-project-offline")
    parser.add_argument(
        "--config",
        type=Path,
        default=DEFAULT_CONFIG_PATH,
        help="Path to the TOML config file.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    init_parser = subparsers.add_parser("init", help="Write a starter config file.")
    init_parser.add_argument("--force", action="store_true", help="Overwrite an existing config file.")
    init_parser.add_argument("--project-url", help="GitHub project or view URL to seed the config.")
    init_parser.add_argument("--token-env", default="GITHUB_TOKEN", help="Environment variable that stores the PAT.")

    start_parser = subparsers.add_parser("start", help="Interactive setup, validation, and first sync.")
    start_parser.add_argument("--force", action="store_true", help="Prompt again and rebuild the cache from scratch.")
    start_parser.add_argument("--project-url", help="GitHub project or view URL to use for setup.")
    start_parser.add_argument("--token-env", default="GITHUB_TOKEN", help="Environment variable that stores the PAT.")

    setup_parser = subparsers.add_parser("setup", help="Alias for start.")
    setup_parser.add_argument("--force", action="store_true", help="Prompt again and rebuild the cache from scratch.")
    setup_parser.add_argument("--project-url", help="GitHub project or view URL to use for setup.")
    setup_parser.add_argument("--token-env", default="GITHUB_TOKEN", help="Environment variable that stores the PAT.")

    subparsers.add_parser("sync", help="Run a single sync now.")

    watch_parser = subparsers.add_parser("watch", help="Run sync on an interval.")
    watch_parser.add_argument("--interval", help="Override config interval, like 15m or 1h.")

    subparsers.add_parser("status", help="Show cache and sync status.")
    subparsers.add_parser("doctor", help="Validate local config and token setup without syncing.")

    items_parser = subparsers.add_parser("items", help="Show cached items from the configured view.")
    items_parser.add_argument("--limit", type=int, default=20, help="Maximum number of items to print.")

    issues_parser = subparsers.add_parser("issues", help="Show hydrated issue details from the cache.")
    issues_parser.add_argument("--limit", type=int, default=20, help="Maximum number of issues to print.")

    find_parser = subparsers.add_parser("find", help="Find cached issues or pull requests by offline filters.")
    find_parser.add_argument("--label", action="append", default=[], help="Filter by label name. Repeat to add more labels.")
    find_parser.add_argument("--milestone", help="Filter by milestone title.")
    find_parser.add_argument("--state", choices=["open", "closed"], help="Filter by GitHub state.")
    find_parser.add_argument("--status", "--column", dest="status", help="Filter by board status or column name.")
    find_parser.add_argument("--repo", help="Filter by repository full name, like owner/repo.")
    find_parser.add_argument("--assignee", action="append", default=[], help="Filter by assignee login. Repeat to add more.")
    find_parser.add_argument("--type", dest="issue_type", choices=["issue", "pull_request"], help="Filter by cached item type.")
    find_parser.add_argument("--text", help="Filter by text in title, body, or board title.")
    find_parser.add_argument("--match", choices=["all", "any"], default="all", help="How repeated label or assignee filters should match.")
    find_parser.add_argument("--limit", type=int, default=20, help="Maximum number of matches to print.")
    find_parser.add_argument("--interactive", action="store_true", help="Build filters through prompts instead of flags alone.")

    issue_parser = subparsers.add_parser("issue", help="Show one cached issue plus recent comments.")
    issue_parser.add_argument("repository", help="Repository full name, like owner/repo.")
    issue_parser.add_argument("number", type=int, help="Issue or pull request number.")
    issue_parser.add_argument("--comments", type=int, default=5, help="How many cached comments to print.")

    query_parser = subparsers.add_parser("query", help="Run a read-only SQL query against the cache.")
    query_parser.add_argument("sql", help="A read-only SELECT query.")

    return parser


def parse_interval(value: str) -> int:
    value = value.strip().lower()
    if value.endswith("m"):
        return int(value[:-1]) * 60
    if value.endswith("h"):
        return int(value[:-1]) * 3600
    if value.endswith("s"):
        return int(value[:-1])
    return int(value)


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command == "init":
        if args.config.exists() and not args.force:
            print(f"Config already exists at {args.config}. Use --force to overwrite.", file=sys.stderr)
            return 1
        write_default_config(args.config, project_url=args.project_url, token_env=args.token_env)
        print(f"Wrote starter config to {args.config}")
        return 0

    if args.command in {"start", "setup"}:
        try:
            return run_start_flow(args)
        except GitHubApiError as exc:
            if is_rate_limit_error(exc):
                print_rate_limit_guidance(exc)
            print(f"Start failed: {exc}", file=sys.stderr)
            return 1
        except KeyboardInterrupt:
            print("Start interrupted.", file=sys.stderr)
            return 130
        except ValueError as exc:
            print(f"Config error: {exc}", file=sys.stderr)
            return 1
        except OSError as exc:
            print(f"I/O error: {exc}", file=sys.stderr)
            return 1

    try:
        config = load_config(args.config)

        if args.command == "doctor":
            run_doctor(config)
            return 0

        if args.command == "sync":
            ensure_token_available(config)
            with SyncProgressRenderer() as renderer:
                summary = run_sync(
                    config,
                    progress=renderer.emit,
                    sync_mode="manual_sync",
                )
            console_print(
                "Sync complete. "
                f"fields={summary.fields_count} views={summary.views_count} "
                f"items={summary.items_count} issues={summary.issues_count} comments={summary.comments_count}"
            )
            return 0

        if args.command == "watch":
            ensure_token_available(config)
            interval_seconds = parse_interval(args.interval or config.sync.interval)
            console_print(f"Watching with interval={interval_seconds}s against {config.storage.database_path}")
            try:
                run_watch_loop(config, interval_seconds)
            except KeyboardInterrupt:
                console_print("Stopped.")
                return 0
            return 0

        if args.command == "status":
            with connect(config.storage.database_path) as connection:
                rows = fetch_status_rows(connection)
            print_status(rows, config.storage.database_path)
            return 0

        if args.command == "items":
            with connect(config.storage.database_path) as connection:
                rows = connection.execute(
                    """
                    select
                        item.issue_number,
                        item.issue_title,
                        item.status_name,
                        detail.state as issue_state,
                        item.repository_name,
                        item.updated_at
                    from cached_view_items as item
                    left join cached_issue_details as detail
                      on detail.project_key = item.project_key
                     and detail.repository_name = item.repository_name
                     and detail.issue_number = item.issue_number
                    order by coalesce(item.status_name, ''), item.issue_number
                    limit ?
                    """,
                    (args.limit,),
                ).fetchall()
            for row in rows:
                print(
                    f"#{row['issue_number'] or '?'} [{row['status_name'] or 'unknown'}] "
                    f"[{row['issue_state'] or 'unknown'}] "
                    f"{row['issue_title'] or '<no title>'} ({row['repository_name'] or 'unknown repo'})"
                )
            print(f"Printed {len(rows)} item(s).")
            return 0

        if args.command == "issues":
            with connect(config.storage.database_path) as connection:
                rows = connection.execute(
                    """
                    select repository_name, issue_number, issue_type, state, title,
                           milestone_title, labels_json, comments_count
                    from cached_issue_details
                    order by repository_name, issue_number
                    limit ?
                    """,
                    (args.limit,),
                ).fetchall()
            for row in rows:
                labels = ", ".join(label.get("name", "?") for label in json.loads(row["labels_json"])) or "-"
                milestone = row["milestone_title"] or "-"
                print(
                    f"{row['repository_name']}#{row['issue_number']} [{row['issue_type']}/{row['state']}] "
                    f"{row['title'] or '<no title>'} milestone={milestone} labels={labels} comments={row['comments_count']}"
                )
            print(f"Printed {len(rows)} issue(s).")
            return 0

        if args.command == "find":
            with connect(config.storage.database_path) as connection:
                filters = gather_find_filters(args, connection=connection)
                rows = fetch_find_rows(connection, limit=args.limit)
            matched_rows = apply_find_filters(rows, filters)[: args.limit]
            for row in matched_rows:
                print_found_issue(row)
            console_print(f"Printed {len(matched_rows)} match(es).")
            return 0

        if args.command == "issue":
            with connect(config.storage.database_path) as connection:
                issue_row = connection.execute(
                    """
                    select repository_name, issue_number, issue_type, state, state_reason, title, body,
                           html_url, author_login, milestone_title, labels_json, assignees_json, comments_count
                    from cached_issue_details
                    where repository_name = ? and issue_number = ?
                    """,
                    (args.repository, args.number),
                ).fetchone()
                if issue_row is None:
                    print(f"No cached issue found for {args.repository}#{args.number}.", file=sys.stderr)
                    return 1
                comment_rows = connection.execute(
                    """
                    select author_login, created_at, body, html_url
                    from cached_issue_comments
                    where repository_name = ? and issue_number = ?
                    order by created_at asc
                    limit ?
                    """,
                    (args.repository, args.number, args.comments),
                ).fetchall()
            print_issue(issue_row, comment_rows)
            return 0

        if args.command == "query":
            sql = args.sql.strip()
            if not sql.lower().startswith("select"):
                print("Only SELECT queries are allowed.", file=sys.stderr)
                return 1
            with connect(config.storage.database_path) as connection:
                cursor = connection.execute(sql)
                print_rows(cursor)
            return 0
    except GitHubApiError as exc:
        if is_rate_limit_error(exc):
            print_rate_limit_guidance(exc)
        print(f"Sync failed: {exc}", file=sys.stderr)
        return 1
    except ValueError as exc:
        print(f"Config error: {exc}", file=sys.stderr)
        return 1
    except OSError as exc:
        print(f"I/O error: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("Interrupted.", file=sys.stderr)
        return 130

    parser.error(f"Unknown command: {args.command}")
    return 1


def print_status(rows: dict[str, sqlite3.Row | None], database_path: Path) -> None:
    console_print(f"Database: {database_path}")
    last_run = rows["last_run"]
    if last_run:
        console_print(f"Last sync: {last_run['status']} started={last_run['started_at']} finished={last_run['finished_at']}")
        if last_run["error_message"]:
            console_print(f"Last error: {last_run['error_message']}")
    else:
        console_print("Last sync: none")
    console_print(f"Projects cached: {1 if rows['project'] else 0}")
    console_print(f"Fields cached: {rows['field_count']['count'] if rows['field_count'] else 0}")
    console_print(f"Views cached: {rows['view_count']['count'] if rows['view_count'] else 0}")
    console_print(f"Items cached: {rows['item_count']['count'] if rows['item_count'] else 0}")
    console_print(f"Issues cached: {rows['issue_count']['count'] if rows['issue_count'] else 0}")
    console_print(f"Comments cached: {rows['comment_count']['count'] if rows['comment_count'] else 0}")


def print_rows(cursor: sqlite3.Cursor) -> None:
    headers = [column[0] for column in cursor.description or []]
    if not headers:
        console_print("No columns returned.")
        return
    table = Table(show_header=True, header_style="bold")
    for header in headers:
        table.add_column(header)
    row_count = 0
    for row in cursor.fetchall():
        table.add_row(*["" if value is None else str(value) for value in row])
        row_count += 1
    CONSOLE.print(table)
    console_print(f"{row_count} row(s).")


def fetch_find_rows(connection: sqlite3.Connection, *, limit: int) -> list[sqlite3.Row]:
    query_limit = max(limit * 10, 200)
    return connection.execute(
        """
        select
            detail.repository_name,
            detail.issue_number,
            detail.issue_type,
            detail.state,
            detail.title,
            detail.body,
            detail.milestone_title,
            detail.labels_json,
            detail.assignees_json,
            detail.comments_count,
            item.status_name,
            item.issue_title
        from cached_issue_details as detail
        left join cached_view_items as item
          on item.project_key = detail.project_key
         and item.repository_name = detail.repository_name
         and item.issue_number = detail.issue_number
        order by detail.repository_name, detail.issue_number
        limit ?
        """,
        (query_limit,),
    ).fetchall()


def gather_find_filters(args: argparse.Namespace, *, connection: sqlite3.Connection | None = None) -> dict[str, Any]:
    filters = {
        "labels": [value.strip() for value in args.label if value.strip()],
        "milestone": clean_optional_text(args.milestone),
        "state": clean_optional_text(args.state),
        "status": clean_optional_text(args.status),
        "repo": clean_optional_text(args.repo),
        "assignees": [value.strip() for value in args.assignee if value.strip()],
        "issue_type": clean_optional_text(args.issue_type),
        "text": clean_optional_text(args.text),
        "match": args.match,
    }
    if args.interactive:
        interactive_filters = prompt_find_filters(connection)
        filters["labels"] = interactive_filters["labels"] or filters["labels"]
        filters["milestone"] = interactive_filters["milestone"] or filters["milestone"]
        filters["state"] = interactive_filters["state"] or filters["state"]
        filters["status"] = interactive_filters["status"] or filters["status"]
        filters["repo"] = interactive_filters["repo"] or filters["repo"]
        filters["assignees"] = interactive_filters["assignees"] or filters["assignees"]
        filters["issue_type"] = interactive_filters["issue_type"] or filters["issue_type"]
        filters["text"] = interactive_filters["text"] or filters["text"]
        if interactive_filters["labels"] or interactive_filters["assignees"]:
            filters["match"] = interactive_filters["match"]
    return filters


def prompt_find_filters(connection: sqlite3.Connection | None) -> dict[str, Any]:
    console_print("Interactive issue finder. Choose from cached values or press Enter to skip.")
    options = fetch_find_options(connection) if connection is not None else {}
    labels = prompt_menu_values("Labels", options.get("labels", []), allow_multiple=True)
    milestone = prompt_menu_value("Milestone", options.get("milestones", []))
    state = prompt_menu_value("State", options.get("states", []))
    status = prompt_menu_value("Board status or column", options.get("statuses", []))
    repo = prompt_menu_value("Repository", options.get("repos", []))
    assignees = prompt_menu_values("Assignees", options.get("assignees", []), allow_multiple=True)
    issue_type = prompt_menu_value("Type", options.get("issue_types", []))
    text = clean_optional_text(input("Text search (free text, optional): "))
    match = "all"
    if len(labels) > 1 or len(assignees) > 1:
        match = prompt_menu_value("Match mode", ["all", "any"]) or "all"
    return {
        "labels": labels,
        "milestone": milestone,
        "state": state,
        "status": status,
        "repo": repo,
        "assignees": assignees,
        "issue_type": issue_type,
        "text": text,
        "match": match,
    }


def apply_find_filters(rows: list[sqlite3.Row], filters: dict[str, Any]) -> list[sqlite3.Row]:
    matched_rows: list[sqlite3.Row] = []
    wanted_labels = [value.lower() for value in filters["labels"]]
    wanted_assignees = [value.lower() for value in filters["assignees"]]
    match_mode = filters["match"]
    for row in rows:
        label_names = [label.get("name", "").lower() for label in json.loads(row["labels_json"] or "[]")]
        assignee_names = [assignee.get("login", "").lower() for assignee in json.loads(row["assignees_json"] or "[]")]
        if filters["milestone"] and (row["milestone_title"] or "").lower() != filters["milestone"].lower():
            continue
        if filters["state"] and (row["state"] or "").lower() != filters["state"].lower():
            continue
        if filters["status"] and (row["status_name"] or "").lower() != filters["status"].lower():
            continue
        if filters["repo"] and (row["repository_name"] or "").lower() != filters["repo"].lower():
            continue
        if filters["issue_type"] and (row["issue_type"] or "").lower() != filters["issue_type"].lower():
            continue
        if wanted_labels and not values_match(label_names, wanted_labels, match_mode):
            continue
        if wanted_assignees and not values_match(assignee_names, wanted_assignees, match_mode):
            continue
        if filters["text"]:
            haystack = " ".join(
                [
                    row["title"] or "",
                    row["body"] or "",
                    row["issue_title"] or "",
                ]
            ).lower()
            if filters["text"].lower() not in haystack:
                continue
        matched_rows.append(row)
    return matched_rows


def values_match(actual_values: list[str], wanted_values: list[str], match_mode: str) -> bool:
    actual = set(actual_values)
    if match_mode == "any":
        return any(value in actual for value in wanted_values)
    return all(value in actual for value in wanted_values)


def print_found_issue(row: sqlite3.Row) -> None:
    labels = ", ".join(label.get("name", "?") for label in json.loads(row["labels_json"] or "[]")) or "-"
    assignees = ", ".join(assignee.get("login", "?") for assignee in json.loads(row["assignees_json"] or "[]")) or "-"
    milestone = row["milestone_title"] or "-"
    status = row["status_name"] or "-"
    console_print(
        f"{row['repository_name']}#{row['issue_number']} "
        f"[{row['issue_type']}/{row['state']}] "
        f"[{status}] "
        f"{row['title'] or '<no title>'} "
        f"milestone={milestone} assignees={assignees} labels={labels} comments={row['comments_count']}"
    )


def clean_optional_text(value: str | None) -> str | None:
    if value is None:
        return None
    cleaned = value.strip()
    return cleaned or None


def parse_csv_values(value: str) -> list[str]:
    return [part.strip() for part in value.split(",") if part.strip()]


def parse_optional_choice(value: str, allowed: set[str]) -> str | None:
    cleaned = value.strip().lower()
    if not cleaned:
        return None
    if cleaned in allowed:
        return cleaned
    console_print(f"Ignoring unsupported value '{value.strip()}'. Expected one of: {', '.join(sorted(allowed))}.")
    return None


def fetch_find_options(connection: sqlite3.Connection) -> dict[str, list[str]]:
    rows = fetch_find_rows(connection, limit=10000)
    labels: set[str] = set()
    assignees: set[str] = set()
    milestones: set[str] = set()
    statuses: set[str] = set()
    repos: set[str] = set()
    states: set[str] = set()
    issue_types: set[str] = set()
    for row in rows:
        for label in json.loads(row["labels_json"] or "[]"):
            name = (label.get("name") or "").strip()
            if name:
                labels.add(name)
        for assignee in json.loads(row["assignees_json"] or "[]"):
            login = (assignee.get("login") or "").strip()
            if login:
                assignees.add(login)
        for value, bucket in [
            (row["milestone_title"], milestones),
            (row["status_name"], statuses),
            (row["repository_name"], repos),
            (row["state"], states),
            (row["issue_type"], issue_types),
        ]:
            cleaned = (value or "").strip()
            if cleaned:
                bucket.add(cleaned)
    return {
        "labels": sorted(labels, key=str.lower),
        "assignees": sorted(assignees, key=str.lower),
        "milestones": sorted(milestones, key=str.lower),
        "statuses": sorted(statuses, key=str.lower),
        "repos": sorted(repos, key=str.lower),
        "states": sorted(states, key=str.lower),
        "issue_types": sorted(issue_types, key=str.lower),
    }


def prompt_menu_value(title: str, options: list[str]) -> str | None:
    if not options:
        console_print(f"{title}: no cached options available. Press Enter to skip.")
        input(f"{title}: ")
        return None
    render_option_table(title, options)
    while True:
        entered = input(f"Choose {title} number or press Enter to skip: ").strip()
        if not entered:
            return None
        if entered.isdigit():
            choice = int(entered)
            if 1 <= choice <= len(options):
                return options[choice - 1]
        console_print(f"Please enter a number between 1 and {len(options)}, or press Enter to skip.")


def prompt_menu_values(title: str, options: list[str], *, allow_multiple: bool) -> list[str]:
    if not options:
        console_print(f"{title}: no cached options available. Press Enter to skip.")
        input(f"{title}: ")
        return []
    render_option_table(title, options)
    prompt = f"Choose {title} number"
    if allow_multiple:
        prompt += "s (comma-separated)"
    prompt += ", or press Enter to skip: "
    while True:
        entered = input(prompt).strip()
        if not entered:
            return []
        parts = [part.strip() for part in entered.split(",") if part.strip()]
        if all(part.isdigit() and 1 <= int(part) <= len(options) for part in parts):
            selected_indexes: list[int] = []
            for part in parts:
                index = int(part)
                if index not in selected_indexes:
                    selected_indexes.append(index)
            return [options[index - 1] for index in selected_indexes]
        console_print(f"Please enter number values between 1 and {len(options)}, or press Enter to skip.")


def render_option_table(title: str, options: list[str]) -> None:
    table = Table(title=title, show_header=True, header_style="bold")
    table.add_column("#", justify="right", style="cyan")
    table.add_column("Value")
    for index, option in enumerate(options, start=1):
        table.add_row(str(index), option)
    CONSOLE.print(table)


def run_doctor(config) -> None:
    console_print(f"Config: {config.config_path}")
    console_print(f"Project: {config.github.project_web_url}")
    console_print(f"Token env: {config.github.token_env}")
    console_print(f"Token present: {'yes' if os.getenv(config.github.token_env) else 'no'}")
    console_print(f"Database: {config.storage.database_path}")
    console_print(f"Logs dir: {config.storage.logs_dir}")
    console_print(f"Sync interval: {config.sync.interval}")
    with connect(config.storage.database_path):
        pass
    console_print("SQLite: ok")


def emit_sync_feedback(message: str, *, logger=None, renderer: SyncProgressRenderer | None = None) -> None:
    if logger is not None:
        if renderer is not None:
            logger.write_only(message)
        else:
            logger.emit(message)
    else:
        console_print(message)
    if renderer is not None:
        renderer.emit(message)


def run_watch_loop(
    config: AppConfig,
    interval_seconds: int,
    *,
    logger=None,
    wait_before_first_cycle: bool = False,
    first_wait_seconds: int | None = None,
) -> None:
    cycle_number = 0
    if wait_before_first_cycle:
        wait_seconds = first_wait_seconds if first_wait_seconds is not None else interval_seconds
        if logger is not None:
            logger.emit(f"Watch handoff: waiting {wait_seconds}s before the next sync cycle.")
        else:
            console_print(f"Waiting {wait_seconds}s before the first watch sync.")
        render_wait_countdown(wait_seconds)
    while True:
        cycle_number += 1
        if logger is not None:
            logger.emit(f"Watch cycle {cycle_number}: starting recheck sync.")
        else:
            console_print(f"Watch cycle {cycle_number}: starting recheck sync.")
        with SyncProgressRenderer() as renderer:
            summary = run_sync(
                config,
                progress=lambda message: emit_sync_feedback(message, logger=logger, renderer=renderer),
                sync_mode="recheck",
            )
        done_message = (
            f"Watch cycle {cycle_number}: sync complete "
            f"items={summary.items_count} issues={summary.issues_count} comments={summary.comments_count}"
        )
        if logger is not None:
            logger.emit(done_message)
            logger.emit(f"Watch cycle {cycle_number}: waiting {interval_seconds}s until next sync.")
        else:
            console_print(done_message)
            console_print(f"Waiting {interval_seconds}s until next sync.")
        render_wait_countdown(interval_seconds)


def render_wait_countdown(interval_seconds: int) -> None:
    with Progress(
        SpinnerColumn(),
        TextColumn("Watch wait"),
        BarColumn(),
        TaskProgressColumn(),
        TimeElapsedColumn(),
        console=CONSOLE,
        transient=True,
    ) as progress:
        task_id = progress.add_task("Watch wait", total=interval_seconds)
        for second in range(interval_seconds):
            time.sleep(1)
            progress.update(task_id, completed=second + 1, description=f"Watch wait {interval_seconds - second - 1}s remaining")


def run_start_flow(args: argparse.Namespace) -> int:
    config_exists = args.config.exists()
    existing_config: AppConfig | None = None
    if config_exists:
        try:
            existing_config = load_config(args.config)
        except ValueError as exc:
            if not args.force:
                raise
            console_print(f"Ignoring existing config error during forced reset: {exc}")

    console_print("Starting interactive setup.")
    project_url = (
        args.project_url
        or prompt_project_url(existing_config, force=args.force)
    )
    token_env = args.token_env or (existing_config.github.token_env if existing_config else "GITHUB_TOKEN")
    write_default_config(args.config, project_url=project_url, token_env=token_env)
    console_print(f"Saved config to {args.config}")

    config = load_config(args.config)
    logger = create_run_logger(config.storage.logs_dir)
    logger.emit(f"Session log created at {logger.log_path}")
    token = prompt_token(config, force=args.force)
    os.environ[config.github.token_env] = token

    sync_mode = "initial_load"
    if args.force:
        reset_database(config.storage.database_path)
        logger.emit("Forced reset requested. Local cache was cleared before sync.")
    elif existing_config and has_existing_cache(config.storage.database_path):
        sync_mode = "recheck"
        logger.emit(f"Reusing existing project board cache at {config.storage.database_path}")
    else:
        logger.emit("No reusable local board cache found. Starting initial load.")

    logger.emit(f"Validating access to {config.github.project_web_url}...")
    client = GitHubClient(config)
    try:
        client.fetch_project()
    except GitHubApiError as exc:
        print_pat_guidance(config, exc)
        raise
    logger.emit("GitHub access check passed.")

    interval_seconds = parse_interval(config.sync.interval)
    recent_sync = get_recent_successful_sync_age(config.storage.database_path)
    should_run_sync = True
    wait_before_first_watch_cycle = True
    first_watch_wait_seconds: int | None = None
    if sync_mode == "recheck" and recent_sync is not None and recent_sync < interval_seconds:
        remaining_seconds = max(interval_seconds - recent_sync, 0)
        logger.emit(
            f"Local cache freshness: last successful sync was {format_duration(recent_sync)} ago, "
            f"inside the configured {config.sync.interval} interval."
        )
        should_wait = prompt_yes_no(
            f"Local cache is fresh. Wait about {format_duration(remaining_seconds)} before the next recheck?",
            default=True,
        )
        should_run_sync = not should_wait
        if should_wait:
            logger.emit(f"Skipping immediate recheck. Cache is recent; about {format_duration(remaining_seconds)} remains in this sync window.")
            wait_before_first_watch_cycle = True
            first_watch_wait_seconds = remaining_seconds

    if should_run_sync:
        with SyncProgressRenderer() as renderer:
            summary = run_sync(
                config,
                progress=lambda message: emit_sync_feedback(message, logger=logger, renderer=renderer),
                sync_mode=sync_mode,
            )
        logger.emit(
            "Ready. "
            f"fields={summary.fields_count} views={summary.views_count} "
            f"items={summary.items_count} issues={summary.issues_count} comments={summary.comments_count}"
        )
    else:
        logger.emit("Ready. Using the existing local cache without an immediate recheck.")
    logger.emit("Next: use `gh-project-offline items`, `issues`, or `issue owner/repo 123`.")
    if prompt_yes_no("Start watch mode now?", default=True):
        interval_value = prompt_interval_override(config.sync.interval)
        interval_seconds = parse_interval(interval_value)
        logger.emit(f"Starting watch mode with interval={interval_seconds}s")
        try:
            run_watch_loop(
                config,
                interval_seconds,
                logger=logger,
                wait_before_first_cycle=wait_before_first_watch_cycle,
                first_wait_seconds=first_watch_wait_seconds,
            )
        except KeyboardInterrupt:
            logger.emit("Watch mode stopped.")
    return 0


def prompt_project_url(existing_config: AppConfig | None, *, force: bool) -> str:
    if existing_config and not force and existing_config.github.project_url:
        console_print(f"Using existing project URL: {existing_config.github.project_web_url}")
        return existing_config.github.project_web_url

    default_value = existing_config.github.project_web_url if existing_config else ""
    while True:
        suffix = f" [{default_value}]" if default_value else ""
        entered = input(f"GitHub project view URL{suffix}: ").strip()
        candidate = entered or default_value
        if candidate:
            return candidate
        console_print("A project view URL is required.")


def prompt_token(config: AppConfig, *, force: bool) -> str:
    existing_token = os.getenv(config.github.token_env)
    if existing_token and not force:
        console_print(f"Using token from {config.github.token_env}.")
        return existing_token

    while True:
        prompt = f"Personal access token for {config.github.token_env}: "
        token = getpass.getpass(prompt).strip()
        if token:
            return token
        console_print("A personal access token is required.")


def ensure_token_available(config: AppConfig) -> None:
    if os.getenv(config.github.token_env):
        return
    if not sys.stdin.isatty():
        return
    token = getpass.getpass(f"Personal access token for {config.github.token_env}: ").strip()
    if token:
        os.environ[config.github.token_env] = token


def reset_database(database_path: Path) -> None:
    if database_path.exists():
        database_path.unlink()
        console_print(f"Removed existing database at {database_path}")
    shm_path = database_path.with_name(database_path.name + "-shm")
    wal_path = database_path.with_name(database_path.name + "-wal")
    if shm_path.exists():
        shm_path.unlink()
    if wal_path.exists():
        wal_path.unlink()


def prompt_yes_no(prompt: str, *, default: bool) -> bool:
    suffix = " [Y/n]: " if default else " [y/N]: "
    while True:
        entered = input(prompt + suffix).strip().lower()
        if not entered:
            return default
        if entered in {"y", "yes"}:
            return True
        if entered in {"n", "no"}:
            return False
        console_print("Please answer yes or no.")


def prompt_interval_override(default_interval: str) -> str:
    entered = input(f"Watch interval [{default_interval}]: ").strip()
    return entered or default_interval


def has_existing_cache(database_path: Path) -> bool:
    if not database_path.exists():
        return False
    with connect(database_path) as connection:
        row = connection.execute(
            """
            select
                (select count(*) from cached_view_items) as item_count,
                (select count(*) from cached_issue_details) as issue_count
            """
        ).fetchone()
    return bool(row and ((row["item_count"] or 0) > 0 or (row["issue_count"] or 0) > 0))


def get_recent_successful_sync_age(database_path: Path) -> int | None:
    if not database_path.exists():
        return None
    with connect(database_path) as connection:
        row = connection.execute(
            """
            select finished_at
            from sync_runs
            where status = 'success' and finished_at is not null
            order by id desc
            limit 1
            """
        ).fetchone()
    if row is None or not row["finished_at"]:
        return None
    finished_at = datetime.fromisoformat(row["finished_at"])
    if finished_at.tzinfo is None:
        finished_at = finished_at.replace(tzinfo=UTC)
    age_seconds = int((datetime.now(UTC) - finished_at).total_seconds())
    return max(age_seconds, 0)


def format_duration(total_seconds: int) -> str:
    if total_seconds < 60:
        return f"{total_seconds}s"
    minutes, seconds = divmod(total_seconds, 60)
    if minutes < 60:
        return f"{minutes}m {seconds}s" if seconds else f"{minutes}m"
    hours, minutes = divmod(minutes, 60)
    if minutes == 0 and seconds == 0:
        return f"{hours}h"
    if seconds == 0:
        return f"{hours}h {minutes}m"
    return f"{hours}h {minutes}m {seconds}s"


def print_pat_guidance(config: AppConfig, exc: GitHubApiError) -> None:
    print("")
    print("GitHub token check failed.")
    if exc.status_code == 401:
        print("What this usually means: GitHub rejected the token itself, not just the scope.")
        print("Check for: typo while pasting, expired or revoked token, wrong GitHub account, or hidden whitespace.")
        if config.github.owner_type == "user":
            print("For user-owned Project v2 views, use a classic personal access token.")
            print("Fine-grained personal access tokens and GitHub App tokens do not work for this path.")
            print("Practical starting point: create a classic PAT with `project` scope.")
            print("If the board contains private repository issues or pull requests, also add `repo`.")
        else:
            print("For org-owned projects, use a token that can read the organization project and the linked repositories.")
            print("A fine-grained token typically needs organization Projects read access plus repository read access for board items.")
        return

    if exc.status_code in {403, 404}:
        print("What this usually means: the token is valid, but GitHub will not let it read this project.")
        if config.github.owner_type == "user":
            print("For user-owned Project v2 views, switch to a classic PAT if you used a fine-grained token.")
            print("Recommended baseline: classic PAT with `project`; add `repo` when board items come from private repositories.")
        else:
            print("For org-owned projects, check organization access to Projects and repository read access for the board's repos.")
            print("If you use a fine-grained PAT, include organization Projects read plus repository read permissions as needed.")
        print("Also verify that the project URL points to the correct owner, project number, and view.")
    else:
        print("GitHub returned an unexpected auth-related response.")

    sso_header = exc.response_headers.get("X-GitHub-SSO")
    if sso_header:
        print("This token may also need SSO authorization for the organization.")
        print(f"GitHub SSO hint: {sso_header}")


def print_rate_limit_guidance(exc: GitHubApiError) -> None:
    reset_header = exc.response_headers.get("X-RateLimit-Reset")
    retry_after = exc.response_headers.get("Retry-After")
    print("")
    print("GitHub rate limit reached. Sync was stopped without automatic retry.")
    if retry_after:
        print(f"Suggested cooldown: wait about {retry_after} second(s), then rerun `gh-project-offline start` or `sync`.")
    elif reset_header and reset_header.isdigit():
        reset_local = datetime.fromtimestamp(int(reset_header)).strftime("%Y-%m-%d %H:%M:%S")
        print(f"Suggested cooldown: wait until about {reset_local} local time, then rerun `gh-project-offline start` or `sync`.")
    else:
        print("Suggested cooldown: wait a few minutes, then rerun `gh-project-offline start` or `sync`.")
    print("This tool does not auto-retry rate-limited requests by default.")


def is_rate_limit_error(exc: GitHubApiError) -> bool:
    if exc.status_code not in {403, 429}:
        return False
    body = (exc.response_body or "").lower()
    return "rate limit" in body or "secondary rate limit" in body


def print_issue(issue_row: sqlite3.Row, comment_rows: list[sqlite3.Row]) -> None:
    labels = ", ".join(label.get("name", "?") for label in json.loads(issue_row["labels_json"])) or "-"
    assignees = ", ".join(user.get("login", "?") for user in json.loads(issue_row["assignees_json"])) or "-"
    print(
        f"{issue_row['repository_name']}#{issue_row['issue_number']} "
        f"[{issue_row['issue_type']}/{issue_row['state']}] {issue_row['title'] or '<no title>'}"
    )
    print(f"Author: {issue_row['author_login'] or '-'}")
    print(f"Milestone: {issue_row['milestone_title'] or '-'}")
    print(f"Labels: {labels}")
    print(f"Assignees: {assignees}")
    print(f"Comments cached: {issue_row['comments_count']}")
    print(f"URL: {issue_row['html_url'] or '-'}")
    if issue_row["state_reason"]:
        print(f"State reason: {issue_row['state_reason']}")
    print("")
    print("Body:")
    print(issue_row["body"] or "<empty>")
    if comment_rows:
        print("")
        print("Comments:")
        for row in comment_rows:
            print(f"- {row['author_login'] or 'unknown'} @ {row['created_at'] or '?'}")
            print(f"  {row['body'] or '<empty>'}")
            if row["html_url"]:
                print(f"  {row['html_url']}")


if __name__ == "__main__":
    raise SystemExit(main())
