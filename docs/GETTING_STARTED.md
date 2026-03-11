# Getting Started

This guide is the quickest way to start using `gh-project-offline` as a read-only offline mirror for one GitHub Project v2 view.

## Install

For development:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e .[dev]
```

For end users after publication:

```powershell
pipx install gh-project-offline
```

## First Run

Use `start` for the normal first-time flow:

```powershell
gh-project-offline start
```

What `start` does:

- reuses the configured project URL when one already exists
- prompts for the project URL when needed
- prompts for the PAT when the configured token env var is missing
- validates access to the board
- runs the first sync
- offers to continue into watch mode
- lets you keep or override the watch interval

Useful `start` flags:

- `--project-url`: seed setup with a board or view URL
- `--token-env`: change the env var name used for the PAT
- `--force`: prompt again and rebuild the local cache from scratch

Example fresh reset:

```powershell
gh-project-offline start --force
```

If you want to seed setup in one command without typing the board URL interactively:

```powershell
gh-project-offline start --project-url https://github.com/users/YOUR-OWNER/projects/123/views/1
```

## Main Commands

`doctor`

```powershell
gh-project-offline doctor
```

Shows config path, project URL, token env var, token presence, database path, logs path, and sync interval.

`sync`

```powershell
gh-project-offline sync
```

Runs one manual sync now.

`watch`

```powershell
gh-project-offline watch --interval 15m
```

Runs sync in a loop. If the token is missing and you are in an interactive terminal, the CLI prompts once for that process.

Useful `watch` flags:

- `--interval`: override the configured interval, like `15m`, `30m`, `1h`, or `45s`

`status`

```powershell
gh-project-offline status
```

Shows cache counts and the most recent sync result.

`items`

```powershell
gh-project-offline items --limit 20
```

Shows cached board items from the configured view.

Useful `items` flags:

- `--limit`: cap the printed rows

`issues`

```powershell
gh-project-offline issues --limit 20
```

Shows cached issue and pull request details with milestone, labels, and comment count.

Useful `issues` flags:

- `--limit`: cap the printed rows

`issue`

```powershell
gh-project-offline issue owner/repo 123 --comments 5
```

Shows one cached issue or pull request plus recent cached comments.

Useful `issue` flags:

- `--comments`: how many cached comments to print

`find`

Use `find` when you want to filter cached issues or pull requests by their saved attributes.

```powershell
gh-project-offline find --label bug --state open --status "Todo"
```

Supported `find` flags:

- `--label`: filter by label name, repeat to add more
- `--milestone`: filter by milestone title
- `--state`: filter by GitHub state, `open` or `closed`
- `--status`: filter by board status or column name
- `--column`: alias for `--status`
- `--repo`: filter by repository full name, like `owner/repo`
- `--assignee`: filter by assignee login, repeat to add more
- `--type`: filter by cached type, `issue` or `pull_request`
- `--text`: case-insensitive text match against title, body, or board title
- `--match`: choose how repeated labels or assignees match, `all` or `any`
- `--limit`: cap the printed rows
- `--interactive`: build filters through prompts

Examples:

```powershell
gh-project-offline find --milestone "Sprint 3" --state open
gh-project-offline find --label bug --label regression --match any
gh-project-offline find --repo octocat/hello-world --assignee hubot
gh-project-offline find --status "In Progress" --text offline
gh-project-offline find --interactive
```

`query`

```powershell
gh-project-offline query "select repository_name, issue_number, state from cached_issue_details limit 10"
```

Runs a read-only `SELECT` query against the local SQLite cache.

## Data and Logs

Default runtime layout:

- config: `.ghpo/config.toml`
- database: `.ghpo/data/cache.db`
- logs: `.ghpo/logs/session-YYYYMMDD-HHMMSS.log`

This keeps all app runtime files in one project-local folder so users can clearly see what the tool created.

## Token Notes

- the tool does not silently store your PAT into system environment variables
- the safest default is process-local use unless you choose your own persistence method
- for user-owned Project v2 endpoints, a compatible classic personal access token is the safest starting point
- if your board references private repos, you may also need repo read access

## Good First Workflow

```powershell
gh-project-offline start
gh-project-offline status
gh-project-offline items
gh-project-offline find --state open --status "Todo"
gh-project-offline issue owner/repo 123
```
