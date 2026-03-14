# gh-project-offline

Local-first cache for a GitHub Project v2 view so humans and agent tools can inspect board data without live GitHub calls on every read.

## Alpha scope

This repo is now ready for alpha testing against a real board with:

- read-only sync from one configured GitHub Project v2 view into SQLite
- manual sync and periodic sync
- board item cache plus hydrated issue or pull request details
- cached issue comments, labels, milestone, assignees, state, and raw JSON payloads
- CLI commands for setup checks and offline inspection
- SQL access for human or agent workflows

## Not implemented yet

This is still a CLI-first alpha. It does **not** yet provide:

- a GitHub-like web UI
- dark theme board rendering
- multi-project sync in one config

## Quick start

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e .[dev]
$env:GITHUB_TOKEN = "your-classic-pat"
gh-project-offline start --project-url https://github.com/users/YOUR-OWNER/projects/123/views/1
```

For end users after publication, prefer:

```powershell
pipx install gh-project-offline
gh-project-offline start --project-url https://github.com/users/YOUR-OWNER/projects/123/views/1
```

`start` is the main onboarding command. It will:

- prompt for the project URL when needed
- prompt for the PAT when the configured env var is missing
- validate access to the target project
- perform the first sync only when the local cache does not already exist
- explicitly ask whether you want to continue into watch mode after initial setup completes
- route existing setups to an explicit next action: one-time sync, watch, or exit
- let you keep or override the sync interval before watch starts
- write runtime files under `.ghpo/`

Command roles:

- `start`: guided setup and routing command; it only performs initial load when cache does not already exist
- `sync`: one manual sync run, then exit
- `watch`: continuous periodic sync until stopped

Run periodic sync every 15 minutes:

```powershell
gh-project-offline watch --interval 15m
```

If `GITHUB_TOKEN` is missing and you run `sync` or `watch` from an interactive terminal, the CLI will prompt for it once for that process.

## Config

Example config:

```toml
[github]
project_url = "https://github.com/users/YOUR-OWNER/projects/123/views/1"
token_env = "GITHUB_TOKEN"

[storage]
database_path = "data/cache.db"
logs_dir = "logs"

[sync]
interval = "15m"
timeout_seconds = 30
user_agent = "gh-project-offline/0.2.0"
include_closed_items = false
```

You can still set `owner`, `owner_type`, `project_number`, and `view_number` explicitly, but `project_url` is the easiest way to get started.

## CLI

- `gh-project-offline start --project-url <board-or-view-url>`
- `gh-project-offline start --force`
- `gh-project-offline setup`
- `gh-project-offline init --project-url <board-or-view-url>`
- `gh-project-offline doctor`
- `gh-project-offline sync`
- `gh-project-offline watch --interval 15m`
- `gh-project-offline status`
- `gh-project-offline summary --by status`
- `gh-project-offline labels`
- `gh-project-offline milestones --format json`
- `gh-project-offline items`
- `gh-project-offline issues`
- `gh-project-offline find --label bug --state open --status "Todo"`
- `gh-project-offline find --format table --sort updated --show repo,number,status,updated`
- `gh-project-offline find --interactive`
- `gh-project-offline issue owner/repo 123`
- `gh-project-offline query "select * from cached_issue_details limit 5"`
- `gh-project-offline capabilities --format yaml`
- `gh-project-offline capabilities --format json --output .ghpo/agent-capabilities.json`

The `capabilities` command exports the installed CLI's current commands, arguments, and usage in an agent-friendly format so a repo can reference the generated file from `AGENTS.md` instead of rediscovering flags from `--help` every time.

## Cached data

The SQLite cache stores:

- project snapshot JSON
- project fields
- project views when the API exposes them
- items for the configured view
- hydrated issue or pull request details for repo-backed board items
- milestone title, due date, description, and state when GitHub provides them
- issue timestamps such as created, updated, and closed time
- issue or pull request descriptions or bodies
- issue comments
- raw JSON payloads alongside normalized columns

Runtime artifacts are isolated under one local app folder by default:

- config: `.ghpo/config.toml`
- SQLite cache: `.ghpo/data/cache.db`
- per-session logs: `.ghpo/logs/session-YYYYMMDD-HHMMSS.log`

If you already used an older root-level layout during local development, run `python scripts/migrate_runtime_layout.py` once to move `gh-project-offline.toml`, `data/`, and `logs/` into `.ghpo/`.

By default, sync skips closed issues and pull requests during local caching. Set `include_closed_items = true` in config if you want the cache to include them too.

If GitHub rate-limits a one-shot sync, the tool stops and tells you the suggested cooldown.
During `watch`, the default behavior is to wait until the reset window passes and then resume the normal cycle.
Use `--no-rate-limit-wait` with `watch` or `start` if you prefer fail-fast behavior instead.
Incremental sync also reuses cached issue/comment state when GitHub reports the item unchanged, so later syncs are much lighter than the first hydration run.

## Test with your board

Use the step-by-step guide in [docs/TESTING.md](docs/TESTING.md) for setup, smoke checks, SQL examples, and troubleshooting.
For a newcomer-friendly command guide, see [docs/GETTING_STARTED.md](docs/GETTING_STARTED.md).

## Token note

For user-owned Project v2 view endpoints, GitHub currently documents that a compatible classic-style personal access token is required rather than a fine-grained token or GitHub App token.

For security, the app does not automatically persist the PAT into system-wide environment variables, `.env` files, or other repository-local files. The safer default is to prompt for it when needed and use it only in the current process unless the user explicitly chooses their own persistence method.

This is primarily a security choice. It is even more important here because the GitHub endpoints used for some user-owned Project v2 flows may require a classic PAT rather than a narrower fine-grained token.

## Release docs

- [CHANGELOG.md](CHANGELOG.md)
- [SECURITY.md](SECURITY.md)
- [PRIVACY.md](PRIVACY.md)
- [CONTRIBUTING.md](CONTRIBUTING.md)
- [VISION.md](VISION.md)
- [docs/RELEASING.md](docs/RELEASING.md)

## Credits

This CLI builds on:

- Python standard library
- [Rich](https://github.com/Textualize/rich) for terminal UX
- [pytest](https://pytest.org/) and `pytest-cov` for tests

## License

GPL-3.0-only
