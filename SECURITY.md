# Security Policy

## Supported versions

This project is currently in alpha. The latest release line is the only supported line for security fixes.

## Reporting a vulnerability

Please avoid filing public issues for sensitive security problems.

Until a dedicated private channel is published, contact the maintainer directly and include:

- affected version
- reproduction steps
- potential impact
- whether the issue involves token leakage, local data exposure, or unsafe filesystem behavior

## Security posture

Current design goals:

- read-only sync from GitHub to local storage
- avoid persisting the PAT automatically into system-wide environment variables
- keep logs and cache local to the machine
- avoid writing secrets into logs

Current limitations:

- token handling is process-local by default and depends on the host terminal/session
- local SQLite and log files are not encrypted by the application
- this project does not yet integrate with OS keychains or secret vaults

## Token handling guidance

For public release, the safest default is:

- prompt the user for a PAT when needed
- use it only for the running process unless the user explicitly manages persistence outside the app

The app should not silently write PATs into user or machine environment variables. That is convenient, but it expands the blast radius of a leaked token and makes accidental reuse more likely.
