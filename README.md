# storage-cleanup

A tool for finding what is using disk space on a Mac and reclaiming it safely.

The project is at an early stage: the read-only scanner and the local page
work; the agent playbook and cleanup actions come next. See `TASKS.md` and the design in
`docs/superpowers/specs/2026-11-05-storage-explorer-design.md`.

## Principles

- **Report first.** Scanning and the page change nothing until you confirm a
  removal.
- **Trash by default.** Removed files and folders go to the Trash, so you can
  put them back. Permanent deletion and Empty Trash each need a typed
  confirmation.
- **You choose, the tool checks.** Rules and agents only recommend. The tool
  acts only on items you queued and confirmed, and checks each one again just
  before acting.
- **Protected things stay put.** System folders, Photos, Mail, Messages, iCloud
  Drive, Keychains, Time Machine data, anything inside `.git`, and any folder
  that contains one of these are never removed. Docker volumes are never
  removed.
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

## Viewing a scan

```bash
uv run storage-cleanup serve
```

This opens the latest scan in your browser as a local page: headline numbers,
a "where your disk went" treemap you can drill into (by category or by
location), a "big and forgotten" chart of size against age, and the space
your git projects spend on rebuildable folders such as `node_modules`.

- The page is read-only. It changes nothing on disk.
- It listens only on this Mac (`127.0.0.1`) and needs the private link that
  `serve` prints; other web pages and programs cannot read it.
- `--scan FILE` shows a specific scan, `--port N` picks the port, and
  `--no-open` prints the link without opening a browser.

## Asking an agent to analyze your Mac

Open a coding agent (Claude Code, Codex, OpenCode, …) in this repository and
ask it to "analyze my device". It follows [`AGENT_PLAYBOOK.md`](AGENT_PLAYBOOK.md):

1. It runs the scan and studies it with `storage-cleanup inspect`, which
   answers focused questions without reading the whole scan file.
2. It looks closer with read-only commands, so it can say what things *are*.
3. It writes a findings file under `reports/findings/`: headline cards, its
   own categories, and recommendations with a confidence and a one-line reason.
4. It checks the file with `storage-cleanup findings check` and opens the page.

The agent never removes anything. Without an agent, the page still shows
rule-based recommendations for well-known safe items such as developer caches
and installers for apps you already have.

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

## Third-party code

| Library | Version | License | Where | Why |
| --- | --- | --- | --- | --- |
| [Apache ECharts](https://echarts.apache.org/) | 5.6.0 | Apache-2.0 (`ECHARTS_LICENSE`, `ECHARTS_NOTICE`) | `src/storage_cleanup/web/static/vendor/` | Treemap, scatter, and drill-down charts on the local page. Vendored so the page works offline; approved by Colin on 2026-11-05 (design section 10). Its SHA-256 is pinned in `storage_cleanup/web/__init__.py` and checked by a test. |
