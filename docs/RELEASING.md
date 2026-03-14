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

Current recommended path:

- GitHub release -> PyPI via Trusted Publishing

Before relying on the workflow, configure Trusted Publishing on PyPI:

- configure the PyPI trusted publisher for this GitHub repository and the `pypi` environment

Manual fallback:

```powershell
python -m twine upload dist/*
```

Suggested rollout:

1. configure the PyPI trusted publisher for this repository and environment
2. create a normal GitHub release
3. let the workflow publish to PyPI
4. verify installation from PyPI

## 4. After release

- create release notes from `CHANGELOG.md`
- tag the release in git
- announce install instructions using `pip` and `pipx`
- smoke-test a fresh `pipx install gh-project-offline` flow in a clean folder
