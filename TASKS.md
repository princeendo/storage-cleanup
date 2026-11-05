# Tasks

## Current State

2026-11-05: The read-only scanner is built (`uv run storage-cleanup scan`;
plan `docs/superpowers/plans/2026-11-05-scanner.md`). It covers home folders,
Downloads, applications and their data, AI tools, Homebrew and other
packages, Docker, developer caches, and git projects, and writes one JSON
file under `reports/scans/`. A full real scan of this Mac took about 3 minutes (2026-11-05). `~/Library/CloudStorage` is never walked: listing cloud-drive mirrors waits on the network and stalled the first run. Next: `PAGE-001`.

The overall design is `docs/superpowers/specs/2026-11-05-storage-explorer-design.md`.
Each build step gets its own plan in `docs/superpowers/plans/`.

## Tasks

| ID | Status | Task | Acceptance criteria |
| --- | --- | --- | --- |
| SETUP-001 | DONE | Scaffold the uv project, README, and agent guidance | `uv run pytest`, `ruff`, and `mypy` pass |
| SCAN-001 | DONE | Read-only scanner (design build step 1) | `storage-cleanup scan` writes a scan file covering all six blind spots; changes nothing else on disk; tests, ruff, mypy pass |
| SCAN-002 | DONE | Identify known-safe cache and build-output locations | `KNOWN_CACHES` in `scan/collectors/dev_caches.py` documents why each is safe and how it regenerates |
| PAGE-001 | READY | Local page: Overview, category and location lenses, reclaim-zone scatter, project dead weight (design build step 2) | Page renders a real scan offline with vendored ECharts; plan written and approved first |
| AGENT-001 | BACKLOG | Agent playbook, findings schema, findings cards, scores, Recommendations tab (step 3) | Any coding agent can follow the playbook to produce a valid findings file the page shows |
| DELETE-001 | BACKLOG | Delete: Trash, Empty Trash, permanent delete, Docker, Homebrew; safety-rule updates (step 4) | Confirmation, re-verification, and protected-path tests pass; nothing removed without confirmation |
| ARCHIVE-001 | BLOCKED (homelab SSH account) | Archive to the homelab (step 5) | Unblock: a dedicated SSH key and account exist, created by a task in `remote_server_maintenance` |
| TARGET-001 | BACKLOG | Target mode and "what grew since last time" (step 6) | Target pre-checks by score; growth compares with the previous saved scan |
