# Tasks

## Current State

2026-11-05: Repository set up (uv project, CLI stub, agent guidance). No
scanners or cleaners exist yet. Next: `SCAN-001`.

## Tasks

| ID | Status | Task | Acceptance criteria |
| --- | --- | --- | --- |
| SETUP-001 | DONE | Scaffold the uv project, README, and agent guidance | `uv run pytest`, `ruff`, and `mypy` pass |
| SCAN-001 | READY | Report-only disk usage overview | Shows free space and the largest top-level folders in the home directory; changes nothing on disk |
| SCAN-002 | BACKLOG | Identify known-safe cache and build-output locations | Each location is documented with why it is safe to remove and how it regenerates |
| CLEAN-001 | BLOCKED (SCAN-002) | Move selected items to the Trash after confirmation | Protected-path and allowlist tests pass; nothing is permanently deleted |
