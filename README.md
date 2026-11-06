# storage-cleanup

A tool for finding what is using disk space on a Mac and reclaiming it safely.

The scanner, the local page, the agent playbook, and removal (Trash,
permanent delete, Docker, Homebrew), target mode, and the "what grew" card
work. Archiving to a homelab comes next. See `TASKS.md` and the design in
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

## Removing things

Nothing is removed until you confirm an exact plan.

1. **Queue.** Check items on the Recommendations tab (a grouped row selects
   everything inside it), or open any item's details and choose "Add to
   Delete queue". The queue is kept for this browser tab.
2. **Review.** On the Delete tab, "Review…" asks the server for the exact plan:
   every path and size, how each item is removed, how to undo it, warnings
   (for example a repository with unsaved work), and anything that will be
   left out and why.
3. **Confirm.** "Move to Trash" is the default, so Finder's Put Back works.
   Turning it off deletes permanently and needs a second confirmation where
   you type `delete permanently`. Docker images and build cache are removed
   with Docker, and Homebrew packages with `brew uninstall`; Docker volumes are
   never removed.
4. **Check and run.** Just before acting, each item is checked again. It must
   still exist, be about the scanned size, lie inside your home folder,
   `/Applications`, or Homebrew's folder, and not be (or contain, at any
   depth) anything protected. Folders that hold another mounted disk or
   online-only iCloud files are skipped, and so are links (they are never
   followed). Anything that fails is skipped with the reason.
5. **Results.** You see what was done, skipped, or failed, the space moved to
   the Trash (freed once you empty it) and freed now, and free space before
   and after. A log is written to `reports/actions/`. Run a new scan to refresh
   the charts.

**Empty Trash** is a separate button that needs you to type `empty trash`.
It asks Finder to empty the Trash, so macOS may ask once whether your terminal
may control Finder. If you say no, empty the Trash in Finder instead.

## Archiving to the homelab

Archive copies an item to your homelab, proves the copy matches, and only then
moves the local item to the Trash. It is optional and off until you set it up.

### Setup

1. Copy `archive.example.toml` to `local/archive.toml` (git ignores `local/`)
   and fill it in:
   - `hosts`: addresses to try in order, for example a LAN name and then a VPN
     address. The first one that answers is used.
   - `user` and `key`: the homelab account and its SSH key (see Access below).
   - `expected_hostname`: what `hostname` must print on the homelab. If it
     differs, the run stops.
   - `mount_point` and `fs_uuid`: where the archive disk mounts and its
     filesystem UUID. The disk must be mounted and have that UUID, or the run
     stops.
   - `allowed_root`: the only folder Archive writes under. `location` is this
     Mac's folder inside it.
   - `owner` and `group`: who owns the archived files on the homelab.
2. Run `uv run storage-cleanup archive check`. It is read-only: it connects,
   checks the host name and the disk, and prints the free space.

### What happens

1. **Queue.** Check items on the Recommendations tab (archive recommendations
   go to the Archive queue), or open an item's details and choose "Add to
   Archive queue".
2. **Review.** On the Archive tab, "Review…" shows every path, its size, and
   the location on the homelab. Nothing is copied until you confirm.
3. **Copy.** Each item is copied with rsync into the batch folder
   `<location>/<UTC timestamp>/`, under its original path. The copy is
   listed on the homelab and compared with the original by SHA-256 on both
   ends, including link targets and folders, and only then moved into place.
4. **Trash.** Right before the Trash, the item and the disk are checked again.
   Only then does the local copy go to the Trash, so Put Back still works.
5. **Failures.** A copy that does not verify stays in the batch's `.incoming/`
   folder and is reported. The local item is left untouched.

### Restoring

The batch's `README.md` on the homelab has a restore command for each item
(`rsync -rl`). File modes and modification times are not restored.
`MANIFEST.json` lists every file with its size and SHA-256; a copy is kept in
`reports/archives/`.

### What is not archived

- Docker and Homebrew items.
- Names with control characters.
- Sockets, FIFOs, and devices.
- Extended attributes and Finder tags are not copied.
- Files land with mode 660 (folders 2770), so executable bits are not kept.

### Access

- The tool uses the homelab's existing maintenance account and SSH key, the
  setup from the `remote_server_maintenance` repository.
- Every remote command except `hostname` runs with `sudo -n`.
- It sends only these commands: `hostname`, `findmnt`, `df`, `install -d`,
  `mkdir -p`, `mv -n`, `chown`, `find` (only with `-exec chmod`), `rmdir`, one
  fixed listing script, and `rsync`.
- It never removes anything on the homelab.
- It always uses `IdentitiesOnly` and `BatchMode`, and stops on any host-key or
  identity problem.

## Reaching a free-space target

On the Recommendations tab, target mode helps you pick what to remove to reach
a goal. It never removes anything itself: you still review and confirm in the
Delete tab, exactly as above.

1. **Set a goal.** Choose "Reach N % free" or "Free up N GiB". The amount is in
   GiB (1024 x 1024 x 1024 bytes), like every other number on the page. The
   page remembers your goal in this browser.
2. **Check the best items.** "Check the best items" adds the best-scoring
   removable recommendations to the Delete queue until the goal is met. It
   keeps what you already queued, never removes anything from the queue, and
   never picks an item together with a folder that holds it.
3. **Read the bar.** "Path to your target" shows the space that is free today,
   the queued items that free space as soon as they run (Docker and Homebrew
   items; the bar does not see the permanent-delete switch), and the queued items that wait in the Trash (hatched), with
   a dashed marker at the target. Below it you see "Met now", "Met once you
   empty the Trash", or "Not met yet". If the scan saw local snapshots, it adds
   that snapshots may keep some space in use until macOS removes them.
4. **Review and confirm.** Open the Delete tab, review the exact plan, and
   confirm.

## What grew since last time

The Overview has a "What grew since last time" card. It compares the scan with
the previous saved scan in the same folder and lists the items that grew or
shrank by at least 100 MiB, with "new" and "gone" labels. A sparkline shows used
space across up to your last 60 saved scans. With only one scan, the card says
"Run another scan later to see what grew."

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
