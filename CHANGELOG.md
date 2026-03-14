# Changelog

All notable changes to this project will be documented in this file.

The format is based on Keep a Changelog and this project follows Semantic Versioning once it reaches stable public releases.

## [0.2.0] - Unreleased

### Added

- Added `summary`, `labels`, and `milestones` commands for cached reporting views.
- Expanded `find` with sorting, table/json/csv output, and selectable output fields.
- Expanded `status` with recent sync history and the last persisted cache delta summary.
- Enriched capability export with agent guidance, command behavior notes, and examples for key workflows.
- Expanded cached metadata with milestone details, richer status metadata, and issue timestamps used by reporting and sorting.
- Redesigned `start` so initial setup and existing-cache routing are clearly separated.

## [0.1.2] - 2026-03-14

### Added

- Added `gh-project-offline capabilities` to export machine-readable CLI command and flag metadata for agent tools.
- Added YAML and JSON capability export formats with configurable output path support.

## [0.1.1] - 2026-03-14

### Changed

- Added session log creation for `sync` and `watch`, not just `start`.
- Improved runtime failure logging so session logs capture exception context and traceback details.
- Made `watch` wait until GitHub rate limit reset by default, then resume the normal cycle.
- Added `--no-rate-limit-wait` to `watch`, `start`, and `setup` for fail-fast behavior.
- Improved `start` watch handoff wording so initial freshness waits and later watch cadence are clearer.
- Clarified runtime log location, command roles, and PAT-handling guidance in the docs.

## [0.1.0] - 2026-03-11

### Added

- Initial alpha CLI for caching a GitHub Project v2 view into SQLite.
- `start`, `sync`, `watch`, `status`, `items`, `issues`, `issue`, `query`, and `doctor` commands.
- Read-only local cache with hydrated issue and comment data.
- Interactive `start` and `setup` onboarding flow.
- Timestamped session logging.
- Incremental issue/comment reuse during later syncs.
- Closed-item skipping by default.
- Rate-limit stop guidance.
- `find` command with direct flags and interactive filtering.
- Rich-based tables and interactive selection menus.
- Rich live sync and watch progress rendering.
- Shorter local runtime layout under `.ghpo/`.
- One-time standalone migration script at `scripts/migrate_runtime_layout.py`.
- Release scaffolding docs for contributors and maintainers.
- Genericized public config and example defaults.
- Added basic lint and security checks to the maintainer workflow.
