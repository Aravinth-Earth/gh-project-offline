# Vision

## Current product shape

`gh-project-offline` is currently a Python package with a CLI-first workflow for read-only offline caching of GitHub Project v2 planning data.

## Near-term direction

- make the sync engine production-leaner
- improve resumability and delta sync behavior
- improve operator observability and diagnostics
- make package installation and release smoother

## Planned enhancements after initial public release

- local read-only cache UI
- long-running background sync host or service wrapper
- richer filtering and search over cached data
- stronger secret-storage integration
- more efficient resume/checkpoint behavior for large boards

## Contribution note

This file exists so future work can be tracked without blocking the current Python package release.
