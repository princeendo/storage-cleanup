# storage-cleanup

A tool for finding what is using disk space on a Mac and reclaiming it safely.

The project is at an early stage: the read-only scanner works; the web page,
agent playbook, and cleanup actions come next. See `TASKS.md` and the design in
`docs/superpowers/specs/2026-11-05-storage-explorer-design.md`.

## Principles

- **Report first.** Commands show what they found and change nothing unless you
  ask them to.
- **Trash, not delete.** Anything removed goes to the Trash, so you can put it
  back.
- **Only known-safe locations.** The tool removes things only from locations it
  knows regenerate (caches, build output) or that you pick yourself. System
  folders, Photos, Mail, iCloud Drive, Keychains, and Time Machine are never
  touched.
- **No `sudo`.** The tool runs as you and skips anything it cannot read.

## Requirements

- macOS
- [uv](https://docs.astral.sh/uv/) (`brew install uv`)

uv installs Python 3.12 or later for you if it is missing.

## Setup

```bash
uv sync
```

## Usage

```bash
uv run storage-cleanup --help
```

## Scanning

```bash
uv run storage-cleanup scan
```

The scan is read-only. It writes one JSON file to `reports/scans/` (git
ignores it, because it contains real paths from your Mac) and prints a
summary: free space, the largest items, and totals by category.

It measures allocated disk blocks (hard links count once) for:

- your home folder, three levels deep for anything over 50 MB;
- `~/Downloads`, including installers for apps you already have;
- applications, their last-opened date, and their data in `~/Library`;
- data left behind by apps you removed;
- AI tools (Claude Code, Claude desktop, Codex, OpenCode, Cursor), including
  sessions whose project folder no longer exists;
- Homebrew formulae and casks, pipx, uv tools, global npm packages, cargo;
- Docker images, volumes, and build cache (when Docker is running);
- known developer caches, each with why it is safe and how it regenerates;
- git projects under `~/workspace`: last commit, unsaved work, and
  regenerable folders such as `node_modules`.

Options:

- `--path PATH` measures one folder in detail and adds it to the latest scan.
- `--workspace-root PATH` searches another folder for git projects
  (repeatable).
- `--include-app-data` also measures other apps' sandboxed data in
  `~/Library/Containers`. Without it those folders are skipped, so macOS does
  not ask for access to other apps' data.

macOS may ask once whether your terminal can access Downloads, Documents, or
Desktop. Allowing that is enough; the tool never asks for Full Disk Access.
Anything it cannot read is listed as skipped in the scan file, including the
Trash (macOS blocks reading it without Full Disk Access).

## Development

```bash
uv run pytest
uv run ruff check .
uv run ruff format .
uv run mypy
```

## Repository Layout

| Path | Contents |
| --- | --- |
| `src/storage_cleanup/` | The Python package and CLI |
| `tests/` | Tests; they use temporary directories, never your real files |
| `AGENTS.md` | Rules for AI agents working in this repository |
| `TASKS.md` | Task list and current state |

Agents also read `local_agent_guidance.md` (machine-local, not committed) for
git identity and personal preferences.
