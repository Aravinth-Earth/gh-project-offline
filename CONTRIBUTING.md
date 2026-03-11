# Contributing

## Development setup

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e .[dev]
python -m ruff check .
python -m bandit -q -r src
python -m pytest --cov=gh_project_offline --cov-report=term-missing --cov-fail-under=85
```

## Project expectations

- keep sync read-only
- prefer incremental, low-churn network behavior
- avoid secret persistence by default
- preserve the CLI-first workflow
- update docs when user-facing behavior changes
- keep shipped defaults and examples generic, not tied to a maintainer's real board

## Before opening a PR

- run the test suite
- run the lint and security checks
- update `CHANGELOG.md` if behavior changed
- update docs if commands, config, security, or storage paths changed

## Roadmap

See [VISION.md](VISION.md) for planned directions and longer-term enhancements.
