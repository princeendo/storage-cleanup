# storage-cleanup

A tool for finding what is using disk space on a Mac and reclaiming it safely.

The project is at an early stage: the command-line tool exists but has no
scanners yet. See `TASKS.md` for what comes next.

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
