# Testing Guide

## Goal

Use this guide to validate that `gh-project-offline` can read your GitHub Project v2 view, cache it locally, and make the data inspectable offline.

## 1. Prepare the local environment

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e .[dev]
```

## 2. Create config from the board URL

Use the single interactive command:

```powershell
$env:GITHUB_TOKEN = "your-classic-pat"
gh-project-offline start --project-url https://github.com/users/YOUR-OWNER/projects/123/views/1
```

That writes `.ghpo/config.toml`, validates access, runs the first sync, creates a timestamped session log under `.ghpo/logs/`, and then explicitly asks whether you want to continue into watch mode.

If the token is not already present in the configured env var, `start` will prompt for it without echoing it back to the terminal.

## 3. Reset and resync from scratch when needed

```powershell
gh-project-offline start --force
```

`--force` prompts again for setup values and deletes the local SQLite cache before rebuilding it.
The rebuilt cache goes to `.ghpo/data/cache.db` by default, and each run writes a log file under `.ghpo/logs/`.
By default, closed issues and pull requests are skipped during caching to keep sync focused on active work.

## 4. Run a local setup check

```powershell
gh-project-offline doctor
```

Expected result:

- the config path is shown
- the derived project URL is shown
- `Token present: yes`
- the logs directory is shown
- SQLite opens successfully

## 5. Run the first sync

```powershell
gh-project-offline status
```

Expected result:

- `start` reports counts for fields, views, items, issues, and comments
- `status` shows the last sync as `success`
- item and issue counts are greater than zero for a populated board

## 6. Inspect the offline cache

Board items:

```powershell
gh-project-offline items --limit 20
```

Hydrated issues or pull requests:

```powershell
gh-project-offline issues --limit 20
gh-project-offline issue owner/repo 123
```

Ad hoc SQL for humans or agents:

```powershell
gh-project-offline query "select repository_name, issue_number, title, state from cached_issue_details order by repository_name, issue_number limit 20"
gh-project-offline query "select repository_name, issue_number, author_login, created_at from cached_issue_comments order by created_at desc limit 20"
gh-project-offline query "select issue_number, issue_title, status_name, repository_name from cached_view_items order by status_name, issue_number limit 20"
```

## 7. Try periodic sync

```powershell
gh-project-offline watch --interval 15m
```

Stop it with `Ctrl+C`.
If the token env var is missing, `watch` will prompt for it when run interactively in a terminal.
By default, if GitHub rate-limits `watch`, it waits until reset and then resumes the normal cycle.
Use `--no-rate-limit-wait` if you prefer fail-fast behavior.

## Suggested acceptance checklist

- `doctor` passes locally
- first sync succeeds
- expected board items appear in `items`
- labels, milestone, assignees, and comments appear in `issues` or `issue`
- SQL queries return stable offline data after disconnecting from GitHub

## Troubleshooting

`Token present: no`

Export the token in the shell before running `start`, `doctor`, `sync`, or `watch`, or let `start` prompt you for it.

`sync` fails with an API error

Double-check that the board URL points to a Project v2 view and that the token can read the target project and repositories.

`start` looks stuck during hydration

Open the latest file in `.ghpo/logs/`. The CLI and log now include timestamped hydration messages like `Hydrating issue 12/87` and `Fetching comments for owner/repo#123`, which help pinpoint the current item.

GitHub reports a rate limit

For one-shot `sync`, the tool stops and shows the cooldown guidance.
For `watch`, the default behavior is to wait until the rate limit resets and then continue the normal cycle.
If you use `--no-rate-limit-wait`, `watch` will fail fast instead.

`Views cached: 0` but items synced

Some environments expose item sync without returning view metadata cleanly. This is acceptable for alpha testing if `items`, `issues`, and `issue` work.

`Items cached: 0`

Check that the view URL points to the board view you actually want to mirror and that the board is not empty.
