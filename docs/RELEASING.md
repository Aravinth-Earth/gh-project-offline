# Releasing

## 1. Verify the project

```powershell
python -m pytest --cov=gh_project_offline --cov-report=term-missing --cov-fail-under=85
python -m ruff check .
python -m bandit -q -r src
python -m build
python -m twine check dist/*
```

If you are upgrading a local dev checkout from the older root-level runtime layout, run this one-time helper before release testing:

```powershell
python scripts/migrate_runtime_layout.py
```

## 2. Update release metadata

- bump `version` in `pyproject.toml`
- update `CHANGELOG.md`
- update docs if commands, config, paths, or security guidance changed

## 3. Publish

Prefer `pipx` in release notes for CLI users. `pip` still works for library-style installs.

Example:

```powershell
python -m twine upload dist/*
```

## 4. After release

- create release notes from `CHANGELOG.md`
- tag the release in git
- announce install instructions using `pip` and `pipx`
- smoke-test a fresh `pipx install gh-project-offline` flow in a clean folder
