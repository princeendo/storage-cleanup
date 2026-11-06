# Tasks

## Current State

2026-11-05: Build steps 1–4 and 6 are done: the read-only scanner (`scan`),
the local page (`serve`), the agent workflow (`AGENT_PLAYBOOK.md`, `inspect`,
`findings`), removal from the page (queue, exact review, re-verification,
Trash by default, typed confirmation for permanent delete and Empty Trash,
Docker and Homebrew removal, action logs in `reports/actions/`), and target
mode with "what grew since last time" (a free-space goal that checks the best
items for you, the path-to-target bar, and a comparison with the previous saved
scan). The safety rules in `AGENTS.md` and `README.md` follow design section 9.
Removal was checked end to end on a scratch sandbox, and one scratch file was
moved to the real Trash with Colin's OK. Only `ARCHIVE-001` remains. It is
BLOCKED: it needs a dedicated SSH key and account, set up by a task in the
`remote_server_maintenance` repository.

The overall design is `docs/superpowers/specs/2026-11-05-storage-explorer-design.md`.
Each build step gets its own plan in `docs/superpowers/plans/`.

## Tasks

| ID | Status | Task | Acceptance criteria |
| --- | --- | --- | --- |
| SETUP-001 | DONE | Scaffold the uv project, README, and agent guidance | `uv run pytest`, `ruff`, and `mypy` pass |
| SCAN-001 | DONE | Read-only scanner (design build step 1) | `storage-cleanup scan` writes a scan file covering all six blind spots; changes nothing else on disk; tests, ruff, mypy pass |
| SCAN-002 | DONE | Identify known-safe cache and build-output locations | `KNOWN_CACHES` in `scan/collectors/dev_caches.py` documents why each is safe and how it regenerates |
| PAGE-001 | DONE | Local page: Overview, category and location lenses, reclaim-zone scatter, project dead weight (design build step 2) | Page renders a real scan offline with vendored ECharts (`docs/superpowers/plans/2026-11-05-page.md`) |
| AGENT-001 | DONE | Agent playbook, findings schema, findings cards, scores, Recommendations tab (step 3) | Any coding agent can follow the playbook to produce a valid findings file the page shows (`docs/superpowers/plans/2026-11-05-agent.md`) |
| DELETE-001 | DONE | Delete: Trash, Empty Trash, permanent delete, Docker, Homebrew; safety-rule updates (step 4) | Confirmation, re-verification, and protected-path tests pass; nothing removed without confirmation (`docs/superpowers/plans/2026-11-05-delete.md`) |
| ARCHIVE-001 | BLOCKED (homelab SSH account) | Archive to the homelab (step 5) | Unblock: a dedicated SSH key and account exist, created by a task in `remote_server_maintenance` |
| TARGET-001 | DONE | Target mode and "what grew since last time" (step 6) | Target pre-checks by score; growth compares with the previous saved scan |
