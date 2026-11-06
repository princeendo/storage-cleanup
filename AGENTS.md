# Agent Operating Contract

This file is authoritative for every AI-agent session in this repository. It
applies to every agent tool (Claude Code, opencode, Codex, and so on).
`CLAUDE.md` only points here.

The project builds a tool that finds and reclaims disk space on a Mac. A tool
that deletes files can destroy data the user cannot get back, so the safety
rules below take priority over speed and convenience.

## Before Each Session

1. Read `local_agent_guidance.md` if it exists. It holds this machine's git
   identity, user preferences, and local paths. Git ignores it and it is never
   committed.
2. Read the "Current State" section of `TASKS.md`.
3. Run `git status` and `git pull --ff-only`. If the pull is not clean, stop
   and ask before doing anything else.

## Analyzing a Device

When the user asks you to analyze their Mac, follow `AGENT_PLAYBOOK.md`. It is
read-only: you write a findings file under `reports/findings/`, and the user
acts in the page.

## Scope

- macOS only for now. Do not add support for other platforms (Linux, Windows,
  iOS, Android) until a task in `TASKS.md` calls for it.
- Python 3.12+ is the implementation language. Bash is fine for small glue.
- Prefer the standard library. Ask before adding a runtime dependency, and
  record why it is needed.

## Safety Rules for the Tool

These rules describe how the tool itself must behave. They follow section 9 of
the approved design (`docs/superpowers/specs/2026-11-05-storage-explorer-design.md`).

1. **Report first.** `scan`, `inspect`, `findings`, and the page's views only
   report. Nothing changes on disk until the user confirms a removal.
2. **Trash by default.** Files and folders move to the Trash with macOS's
   `/usr/bin/trash`, so Finder's "Put Back" works. **Permanent deletion** is an
   explicit per-run choice with its own second confirmation (the user types
   `delete permanently`). **Empty Trash** is permanent too and has its own typed
   confirmation (`empty trash`).
3. **Archive** copies an item to the homelab over the existing `opencode` SSH
   setup from `remote_server_maintenance`, with `sudo -n` on the homelab. It
   sends only allowlisted commands and never removes anything there. The copy
   must match by SHA-256 on both ends, and the item and the disk are
   re-checked before the local copy goes to the Trash.
4. **Confirm an exact plan.** Before acting, the page shows every path, its
   size, how it will be removed, how to undo it, any warnings, every refusal,
   and the totals. The server rebuilds the plan itself and acts only if it
   matches the plan the user reviewed.
5. **Recommend narrowly, act only on what the user chose.** The tool
   recommends only through built-in rules or validated agent findings. It acts
   only on items present in the scan that the user queued and confirmed.
   Unknown paths are never acted on automatically.
6. **Re-verify before every action.** Immediately before acting, the item must
   still exist, still be the same kind of thing, be about the measured size,
   lie under the home folder, `/Applications`, or the Homebrew prefix, and not
   be protected. No folder in its path may have become a link, and it must not
   contain a protected store at any depth, another mounted disk, or online-only
   cloud files. Links themselves are skipped, never followed. Anything that
   fails is skipped with a reason, never forced. The path checks repeat in the
   instant before acting.
7. **Protected paths are never removed**, whatever a rule, finding, or user
   selection says:
   - System locations: `/System`, `/bin`, `/sbin`, `/usr` (except
     `/usr/local`), `/private/var/db`, `/Library/Apple`.
   - User data stores: Photos libraries, Mail, Messages, Keychains,
     `~/Library/Mobile Documents` (iCloud Drive), and Time Machine backups or
     local snapshots.
   - Anything inside a `.git` directory.
   - Any folder that *contains* one of the stores above. A whole repository
     (a folder containing `.git`) may be archived or trashed as one unit; the
     review warns when it has uncommitted or unpushed work.
   - Docker volumes are report-only.
8. **No elevated rights.** Do not use `sudo` or ask for Full Disk Access. If a
   path needs either, report it and skip it. Empty Trash goes through Finder
   and may show a one-time macOS Automation prompt; the tool never changes
   that setting itself.
9. **Local server safety.** The page listens only on `127.0.0.1`, needs the
   per-session token, rejects foreign `Host` headers, and accepts changes only
   as JSON `POST` requests with a matching `Origin`. One run at a time.
10. **Measure, don't guess, and keep a record.** Sizes are allocated blocks on
    disk. Every run records free space before and after and writes a log under
    `reports/actions/`.

## Safety Rules for Agents

1. Never run a removing command against the real filesystem while you develop or
   test. Tests use temporary directories (`tmp_path`) and synthetic fixtures. Tests never touch the real
   homelab.
2. Read-only commands may run at any time: `df`, `du`, `diskutil info`,
   `diskutil apfs list`, `tmutil listlocalsnapshots`, `ls`, `stat`, `find`
   without `-delete`.
3. Ask the user, for each action, before anything that removes, moves, or
   changes files outside this repository. Say what will change, how much space
   it frees, and how to undo it.
4. Never change system settings, Time Machine settings, or Spotlight settings.
5. Scan reports contain real file paths from this machine. Keep them under
   `reports/` or `local/`, which git ignores. Never commit them.
6. Never commit secrets, `.env` files, or `local_agent_guidance.md`.

## Development

- Use `uv` for everything. Do not rely on an activated virtual environment.
  - Set up: `uv sync`
  - Run: `uv run storage-cleanup ...`
  - Test: `uv run pytest`
  - Lint and format: `uv run ruff check .` and `uv run ruff format .`
  - Type check: `uv run mypy`
- Every new cleaner gets tests that prove it ignores protected paths and paths
  outside its allowlist.

## Task Discipline

Allowed statuses in `TASKS.md` are `BACKLOG`, `BLOCKED`, `READY`,
`IN_PROGRESS`, `DONE`, and `REJECTED`. Code alone does not make a task `DONE`;
its acceptance criteria must be met and verified. Do not bypass a blocker.

## Session Closeout

Before ending substantial work:

1. Run tests, lint, formatting, and type checks.
2. Update `TASKS.md` ("Current State" and task statuses).
3. Check that `git config user.email` matches the identity in
   `local_agent_guidance.md`. If it does not, stop and fix the configuration
   before committing.
4. Inspect `git status` and the full diff. Stage only files that belong to the
   task. Check that no scan report, secret, or machine-local file is staged.
5. Create a concise commit and push it to the upstream branch. Never
   force-push. Report a failed push as a blocker.
