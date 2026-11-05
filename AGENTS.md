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

## Scope

- macOS only for now. Do not add support for other platforms (Linux, Windows,
  iOS, Android) until a task in `TASKS.md` calls for it.
- Python 3.12+ is the implementation language. Bash is fine for small glue.
- Prefer the standard library. Ask before adding a runtime dependency, and
  record why it is needed.

## Safety Rules for the Tool

These rules describe how the tool itself must behave.

1. **Report first.** By default every command only reports what it found. It
   changes nothing on disk.
2. **Trash, not delete.** Removal moves items to the user's Trash
   (`~/.Trash`) so they can be restored. Permanent deletion (`unlink`,
   `rmtree`, `rm`) never happens by default. If it is added later, it needs its
   own explicit flag and its own confirmation.
3. **Confirm before acting.** Before removing anything, show the user every
   path, its size, and the total, and wait for confirmation. A `--yes` flag may
   skip the prompt only when the user passes it.
4. **Allowlist, not denylist.** A cleaner only touches paths it knows are safe
   to remove (caches, build output, old downloads the user picks). Unknown paths
   are reported, never removed.
5. **Protected paths are never removed**, even if a rule matches them:
   - System locations: `/System`, `/bin`, `/sbin`, `/usr` (except
     `/usr/local`), `/private/var/db`, `/Library/Apple`.
   - User data stores: Photos libraries, Mail, Messages, Keychains,
     `~/Library/Mobile Documents` (iCloud Drive), and Time Machine backups or
     local snapshots.
   - Anything inside a `.git` directory.
6. **No elevated rights.** Do not use `sudo` or ask for Full Disk Access. If a
   path needs either, report it and skip it.
7. **Measure, don't guess.** Report sizes that were measured on disk (allocated
   blocks), not estimates. Record before and after free space for any real
   cleanup.

## Safety Rules for Agents

1. Never run a removing command against the real filesystem while you develop or
   test. Tests use temporary directories (`tmp_path`) and synthetic fixtures.
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
