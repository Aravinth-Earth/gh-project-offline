# Repository Guidelines

## Project Structure & Module Organization

- Source code lives in `src/gh_project_offline/`.
- CLI entrypoint and most user-facing behavior are in `src/gh_project_offline/cli.py`.
- Sync, cache, and GitHub integration logic live in:
  - `service.py`
  - `db.py`
  - `github_api.py`
  - `config.py`
- Tests live in `tests/` and mirror the module layout, for example `tests/test_cli.py`.
- Contributor and release docs live in `docs/`.
- Runtime artifacts are local-only under `.ghpo/` and should not be treated as source files.

## Build, Test, and Development Commands

- Create a dev environment:
  - `python -m venv .venv`
  - `.\.venv\Scripts\Activate.ps1`
  - `python -m pip install -e .[dev]`
- Run the CLI locally:
  - `python -m gh_project_offline.cli status`
  - `python -m gh_project_offline.cli capabilities --format yaml`
- Run tests:
  - `python -m pytest`
- Run coverage gate:
  - `python -m pytest --cov=gh_project_offline --cov-report=term-missing --cov-fail-under=85`
- Run lint:
  - `python -m ruff check src tests`

## Coding Style & Naming Conventions

- Use Python 3.11+ compatible code and 4-space indentation.
- Follow Ruff-enforced import ordering and keep changes ASCII unless a file already requires otherwise.
- Prefer clear snake_case names for functions and variables; use descriptive command names in `argparse`.
- Keep CLI help text short and behavior-specific.

## Testing Guidelines

- Tests use `pytest`.
- Add or update tests for every behavior change, especially CLI flags, sync logic, and cache handling.
- Keep test names descriptive, for example `test_find_supports_json_output_with_sort_and_show`.
- Do not lower the coverage gate without explicit approval.

## Commit & Pull Request Guidelines

- Prefer short imperative commit messages, e.g. `Prepare 0.1.2 release` or `Configure Dependabot for pip and GitHub Actions`.
- Use feature branches and PRs; do not commit release work directly to `main`.
- PRs should include:
  - a short summary of user-visible changes
  - verification commands run
  - notes on config, release, or security impact when relevant

## Security & Configuration Tips

- Do not commit personal access tokens, `.env` secrets, or runtime cache/log data from `.ghpo/`.
- Publishing is triggered by a GitHub release, not by a normal push.
- Prefer the generated capability file for agent/tool discovery when available:
  - `.ghpo/agent-capabilities.yaml`
