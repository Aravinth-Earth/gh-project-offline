# Privacy

## What this tool stores locally

By design, `gh-project-offline` stores project data on the local machine, including:

- project metadata
- project fields and views
- board items
- issue or pull request titles and bodies
- labels, milestone, assignees, state
- issue comments
- raw GitHub API payloads for cached entities
- sync logs

Default locations:

- SQLite cache: `.ghpo/data/cache.db`
- logs: `.ghpo/logs/`

## What this tool does not do

- It does not push changes back to GitHub.
- It does not sync data to a hosted backend.
- It does not intentionally write PAT values into logs.

## User responsibility

If the cached project contains sensitive data, the local machine and filesystem permissions become part of the trust boundary. Users should consider:

- disk encryption
- profile/user isolation
- local backup policies
- excluding cache/log directories from unwanted sync or sharing tools

## Token guidance

Do not store PATs in repository files or commit them.

Prefer:

- entering the token interactively when prompted
- or explicitly managing persistence via your own shell or OS secret tooling

Avoid:

- hardcoding tokens in config files
- silently promoting tokens into machine-wide environment variables
