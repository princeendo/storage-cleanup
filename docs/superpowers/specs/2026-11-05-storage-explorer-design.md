# Storage Explorer: Design

Date: 2026-11-05
Status: Approved by Colin, 2026-11-05

## 1. Intent

Colin wants to see where the disk space on his Mac has gone, sorted into groups
that mean something to him, and then archive or remove what he no longer needs.
Established disk tools show sizes. They do not tell him what a thing is, how
old it is, or whether it is safe to remove, so he does not know where to look
and does not have the energy to sort through the results.

This tool is different because it is **LLM-assisted without an API key**. The
repository holds deterministic scanning code, a local web page, and a written
playbook. Colin points any coding agent (Claude Code, Codex, OpenCode) at the
repository and says "analyze my device." The agent runs the scanner, studies
the results, and writes findings that the page shows next to the measured data.
A plain scanner cannot do the agent's part: explain what something is, notice
patterns, and write a short explanation for each recommendation.

### Colin's blind spots (the problems this must solve)

1. **Hidden tool data.** Docker images and build cache, package caches, and
   other things he does not think to look for.
2. **Downloads triage.** `~/Downloads` holds a lot that should go, but going
   through it by hand takes more energy than he has.
3. **AI agent footprint.** How much Claude Code, OpenCode, Codex, and similar
   tools each store.
4. **Stale applications.** Apps he has not opened in a long time, with dates.
5. **Stale command-line tools.** Homebrew formulae and casks (and similar)
   that never appear in `/Applications`.
6. **Stray home-folder data.** He only uses `~/Downloads` actively, but data
   lands elsewhere in his home folder. He wants to see it and how old it is.

The common thread is **size plus age plus "what is this."**

## 2. Goals, non-goals, anti-goals

### Goals

- A rich, visually elegant local web page that shows what is on the Mac and
  where, through two lenses: **category** (for example "AI tooling," "Docker,"
  "Applications") and **location** (for example "Downloads" for `~/Downloads`,
  "Your personal data" for `~/workspace/personal`).
- Interesting insights, not only charts (see section 6.2).
- Recommendations, ranked by a score that weighs confidence against space
  reclaimed.
- Archive (to the homelab) and Delete (to the Trash by default, or
  permanently) run from the page, after explicit confirmation.
- A target mode: "get me to 15% free" or "free 10 GB" pre-checks the
  best-scoring items until the target is met.
- Works with any coding agent that can read files and run commands. No API key.

### Non-goals (for now)

- Platforms other than macOS.
- Calling an LLM API from the tool itself. This may come later and must not be
  designed out, but it is not built now.
- Running continuously, in the background, or on a schedule.
- Hosting the page anywhere except on the local machine.

### Anti-goals (failure even if it "works")

- Losing data the user did not clearly choose to lose. Every removal is
  confirmed, re-checked, and undoable by default.
- Touching protected locations (section 9) under any circumstances.
- An agent or a page action removing something that is not in the measured
  scan. The agent recommends; only the user's confirmation acts.
- Sizes or savings that are guessed instead of measured.
- A dull page. Colin explicitly wants "really elegant, interesting insights."

## 3. How it works (overview)

```
  scanner (Python, deterministic)          agent (any coding agent)
  storage-cleanup scan ──► reports/scans/<timestamp>.json
                                   │
                                   └──► agent reads it, investigates,
                                        writes reports/findings/<timestamp>.json
                                   │
  storage-cleanup serve ──► local page reads scan + findings (+ previous scan)
                                   │
                         user checks items, confirms
                                   │
                    server re-verifies, then Trash / delete / archive
                                   │
                         reports/actions/<timestamp>.json (log)
```

Colin decided:

- The page performs actions itself (it is served by a local server). Handing
  a plan back to the agent is the fallback only if page-driven actions prove
  impractical.
- The agent's job is the findings: headline cards, categories, confidence, and
  reasons.

## 4. The scanner

A deterministic Python command (`storage-cleanup scan`) that measures facts and
writes one JSON file under `reports/scans/`. It changes nothing on disk except
writing that file.

### 4.1 What it measures

| Area | Facts to collect |
| --- | --- |
| Disk | Total, used, and free space for the boot volume |
| Home folder | Every top-level folder, then a bounded walk into each: allocated size, file count, newest and oldest modification time |
| `~/Downloads` | Every top-level entry with size, type (by extension), and dates; whether an installer (`.dmg`, `.pkg`, `.zip` containing an `.app`) matches an app already in `/Applications` |
| AI tooling | Known storage locations for Claude Code, Claude desktop, Codex, OpenCode, and similar tools, broken down per tool; for session stores, which project folder each session refers to and whether that folder still exists |
| Applications | Every bundle in `/Applications` and `~/Applications`: size, version, last-used date (Spotlight metadata via `mdls`, read-only), and the size of its data in `~/Library` (Application Support, Containers, Caches, Group Containers) |
| Leftover app data | `~/Library` data whose owning app is no longer installed |
| Homebrew | Formulae and casks, installed size, install date, whether a formula is a leaf (nothing depends on it), and the best available "last used" signal; the Homebrew download cache |
| Other package managers | Builder's call on which to include (`pipx`, `npm -g`, `cargo install`, `uv` tools, and so on) |
| Docker | Images (size, created, last used by any container), containers, volumes, and build cache, through the `docker` CLI; skipped and reported if Docker is not running |
| Developer caches | Known regenerable locations (Xcode DerivedData, simulator runtimes, package-manager caches, and so on) |
| Projects | Git repositories under `~/workspace` (and other configured roots): total size, size of regenerable folders (`node_modules`, `.venv`, build output), and last commit date |

### 4.2 Measurement rules

- Size is **allocated blocks on disk** (`st_blocks * 512`), not apparent size.
  Hard links count once. The scanner cannot see APFS clone sharing; the page
  says so where it matters.
- Paths the scanner cannot read (privacy-protected folders and similar) are
  recorded as "skipped: no access." The scanner never uses `sudo` and never
  asks for Full Disk Access.
- Access times on APFS are not reliable as a "last used" signal. The builder
  picks the best signal per kind of item and records which signal was used,
  so the page can show it ("last opened," "last modified," "last commit").
- The scan must finish in a reasonable time on a home folder of several hundred
  gigabytes. The builder chooses walk depth limits and roll-up rules to meet
  this. My guess at a target is a few minutes, not tens of minutes.

### 4.3 Item identity

Every measured thing gets a stable ID in the scan file. Findings and actions
refer to items by ID. The page and server act only on items present in the
scan.

### 4.4 Targeted scans

`storage-cleanup scan --path <path>` measures one location in full detail and
adds its items to the current scan file, under the same measurement rules.
This is how the agent turns something it found while investigating (for
example one 30 GB file inside a rolled-up folder) into a measured item it can
recommend. The agent never invents items or sizes. My call.

### 4.5 Space the tool cannot reclaim directly

The scanner also reports, read-only:

- The size of the Trash (`~/.Trash`), shown as "waiting in the Trash."
- APFS local snapshots (`tmutil listlocalsnapshots /`), shown as possibly
  holding space. The tool never creates, deletes, or changes snapshots or Time
  Machine settings.

### 4.6 macOS privacy prompts

Reading `~/Downloads`, `~/Documents`, and `~/Desktop` can trigger the standard
macOS per-folder access prompt for the app running the scan. Allowing those
prompts is acceptable, and Downloads triage needs it. This is different from
Full Disk Access, which the tool never asks for. Other apps' sandboxed data
(`~/Library/Containers`, Group Containers) is measured only where access
already exists. Otherwise it is reported as "skipped: no access," and the
scanner avoids triggering a prompt for it where it can. My call.

## 5. The agent and its playbook

A playbook file in the repository (my call: `AGENT_PLAYBOOK.md`, linked from
`AGENTS.md`) tells any agent how to run an analysis:

1. Run the scan.
2. Read the scan file. Investigate further with read-only commands where
   something is large, old, or unexplained (the read-only command list in
   `AGENTS.md` applies).
3. Write a findings file that follows a documented JSON schema (committed in
   the repository, for example `schemas/findings.schema.json`):
   - **Category assignments** for items, using the built-in categories and any
     new ones the agent proposes (for example "Your personal data").
   - **Recommendations**: item IDs, proposed action (archive or delete),
     confidence from 0 to 1, and a one-line reason a person can check
     ("Docker image, no container has used it in 14 months").
   - **Headline findings**: short cards with a title, a one- or two-sentence
     explanation, and the item IDs behind it. Examples: "Your Downloads is
     mostly installers for apps you already have," "Claude Code keeps sessions
     for 9 repositories that no longer exist."
4. Start the page and give the user the link.

The agent never removes, moves, or archives anything itself. That stays the
user's action in the page.

The page must also work with **no findings file**: it then shows the scan and
rule-based recommendations for well-known safe locations (caches, build
output). My call, so the tool is useful before or without an agent.

## 6. The page

A local web page served by `storage-cleanup serve`. Four tabs.

### 6.1 Overview tab

- Headline numbers: used, free, recommended to reclaim, time of the scan.
- Rich charts of where the space is, by category and by location. The early
  mockups showed a drill-down treemap, a sunburst, category bars split by age,
  and a location table with an age strip. Colin called these a good first cut
  and asked for more. The builder picks the final set, aiming for elegant and
  interactive (drill-down, hover detail) over many plain charts.

### 6.2 Insights

Colin picked these five from the gallery mockup:

1. **Agent-written findings**: headline cards from the findings file. Each card
   opens the items behind it.
2. **Reclaim-zone scatter**: one dot per folder, app, or tool item. Horizontal
   position is how long since last use, vertical is size (log scale), and color
   is category. The big, forgotten corner is highlighted.
3. **What grew since last time**: compares this scan with the previous saved
   scan, listing the biggest growth and shrinkage, plus a used-space trend over
   all saved scans.
4. **Dead weight in your projects**: for each repository, your own files
   compared with regenerable folders, plus the last commit date.
5. **Path to your target**: the pre-checked recommendations stacked toward the
   target line. It updates live as items are checked or unchecked.

Not picked: "app icebergs" (app bundle compared with its hidden `~/Library`
data). The scanner still collects that data because Applications and
leftover-data items use it.

The builder decides where the insights live: on the Overview tab, on their
own tab, or next to the tab they relate to (the target chart belongs with
Recommendations).

### 6.3 Recommendations tab

- A list of recommended items, sorted by score (section 7), each showing
  name, path, category, size, age signal, confidence, reason, and proposed
  action.
- Check or uncheck items, and send checked items to Archive or Delete.
- Any measured item can also be queued from the Overview and location views
  (for example a folder in the treemap or a file in Downloads), not only
  recommended items. Colin's first description was a page that "allows for the
  selection of different items," and Downloads triage needs it. The same
  confirmation and protection checks apply. My call.
- Target mode: the user sets a goal (percent free, or an amount to free). The
  tool pre-checks items in score order until the goal is met, and the path
  chart shows progress.

### 6.4 Archive and Delete tabs

These are nearly identical lists of queued items, each with path, size, and
total. Differences:

- **Archive** has an "Archive location" field (default from local
  configuration).
- **Delete** has a "Move to Trash" option, **on by default**. Turning it off
  means permanent delete (section 8.2).

Both end in the same confirmation flow (section 8.1).

## 7. Score and roll-up

- **Score** combines confidence with space reclaimed. Colin's rule: a large,
  high-confidence item scores high, while a tiny item (for example 4 KB) scores
  low no matter how confident. Proposed shape (my call, builder may tune):
  `score = 100 × confidence × g(bytes)`, where `g` is a log-scaled curve that
  rises from 0 near a few kilobytes to 1 near tens of gigabytes. Tests should
  pin the properties: higher confidence never lowers the score, and larger size
  never lowers the score.
- **Roll-up**: when many items under one folder are recommended for the same
  reason, show them as a single folder item with the combined size (Colin's
  example: 10,000 files of 4 MB become one 40 GB item). The builder chooses
  the roll-up rule.

## 8. Actions

### 8.1 Confirmation and re-verification (all actions)

1. The user presses go on the Archive or Delete tab.
2. The page shows every path, its size, and the total, and states whether the
   action is undoable and how. Nothing happens until the user confirms.
3. For each item, the server checks again before acting: the item is still in
   the scan, still exists, is still about the measured size, and is not
   protected (section 9). Items that fail are skipped and reported. They are
   never forced.
4. The page shows a result: what was done, what was skipped and why, and free
   space before and after. The server writes an action log under
   `reports/actions/`.

### 8.2 Delete

- **To the Trash (default):** macOS's built-in `/usr/bin/trash`, so Finder's
  "Put Back" works.
- **Permanent delete:** off by default, switched on per run, and it requires a
  second, separate confirmation. This updates the repository's "Trash, not
  delete" rule deliberately (section 9).
- **Trash space is not freed yet (Colin decided).** The page shows "freed" and
  "waiting in the Trash" as separate numbers everywhere: headline numbers,
  results, and target mode. An **Empty Trash** action exists. It is permanent,
  so it shows the Trash's contents and size and needs its own confirmation.
  Target mode counts trashed items as "met once the Trash is emptied," which
  is shown separately from "met now." Free space may still lag behind if APFS
  local snapshots hold the blocks; the page says so when snapshots exist.
- **Items that are not plain files** (my call; the builder confirms the exact
  commands):
  - Applications: the `.app` bundle goes to the Trash. Its `~/Library` data is
    offered as separate items.
  - Docker images and build cache: removed with the `docker` CLI. They cannot
    go to the Trash; the page labels them "not undoable, can be pulled or
    rebuilt again."
  - Docker volumes: report only for now, because they can hold data that
    cannot be recreated.
  - Homebrew formulae and casks: removed with `brew uninstall`, labeled
    "reinstallable."

### 8.3 Archive to the homelab

The homelab is not mounted on the Mac. Access follows the guidance in the
`remote_server_maintenance` repository, checked out locally at
`~/workspace/personal/remote_server_maintenance` (see its `AGENTS.md` for SSH
rules and `README.md` for the storage guard and the Seagate disk). The
mount-guard identity, the dedicated account's write rights and file ownership
(so File Browser can read archived items), and the remote checksum command are
settled in the homelab task that creates the account:

- **Connection:** SSH, trying the homelab's endpoints in the order that
  repository's `AGENTS.md` lists them (local hostname, then LAN address, then
  public SSH name). The actual host list lives only in the git-ignored local
  configuration, never in this repository. Move to the next host only on DNS, routing, or
  connection-unavailable errors. Stop on an authentication failure, a host-key
  warning, or an unexpected host identity. Never bypass host-key checking.
- **Credentials (Colin decided, 2026-11-05):** the existing `opencode`
  account and key that the `remote_server_maintenance` repository already
  uses, not a dedicated account. That account is not in the File Browser
  group, so every remote command runs through `sudo -n`, the same way that
  repository's own archive transfers do. The tool sends only a fixed set of
  remote commands (read the mount, free space and file list; create
  folders; copy; move into place; set owner and mode) and never removes
  anything on the homelab.
- **Destination:** the Seagate 22 TB disk, under a dedicated folder such as
  `/mnt/HD/Seagate22TB/filebrowser-inbox/Archives/mac-storage-cleanup/<date>/`,
  keeping each item's original path layout below that (my guess, accepted in
  discussion). Items there are also visible in File Browser under
  `/srv/Inbox/Archives`.
- **Storage guard:** before writing, verify that the destination is on the
  real Seagate filesystem (the identity the homelab's mount guard expects) and
  has enough free space. Never write into an empty mountpoint on the root
  disk.
- **Transfer and proof:** copy with `rsync` over SSH (macOS ships `openrsync`;
  the builder confirms its flags), then compare SHA-256 checksums on both ends.
  Keep the manifest locally under `reports/`.
- **After verification:** the local copy moves to the Trash, so even a
  successful archive stays undoable for a while (my guess, accepted in
  discussion). If any checksum fails, nothing local is touched for that item.
- **Configuration:** host list, user, key path, and destination live in a
  git-ignored local configuration file, with a committed example.

## 9. Safety rules (updated)

The current rules in `AGENTS.md` and `README.md` still apply, with these
deliberate changes, which the implementation must write into both files:

- **Archive** is a new removal path: copy, verify, then Trash the local copy.
- **Permanent delete** exists, but only as an explicit per-run choice with its
  own second confirmation. The Trash stays the default.
- **"Allowlist, not denylist"** now means: the tool *recommends* only through
  known rules or agent findings, and acts only on items present in the scan
  that the user checked and confirmed. Unknown paths are still never acted on
  automatically. Target mode pre-checks items, but the final confirmation is
  still the user's choice.
- **Protected paths** are unchanged and checked by the server immediately
  before every action, whatever the scan, findings, or user selection say.
  They include system locations, Photos libraries, Mail, Messages, Keychains,
  iCloud Drive, Time Machine data, and anything inside a `.git` folder.
- **Whole repositories (Colin decided):** a folder that contains a `.git`
  folder may be archived or moved to the Trash as one unit. The confirmation
  warns if the repository has uncommitted changes or commits not pushed to any
  remote. Nothing inside `.git` is ever acted on by itself. Any other item that
  *contains* a protected data store (for example a folder holding a Photos
  library or iCloud Drive data) is blocked as a whole. My call.
- **Local server safety:** it listens only on `127.0.0.1`. Every request needs
  a random per-session token, and requests that change things require a POST
  with an Origin check, so no other web page or local program can trigger an
  action.
- **Development safety is unchanged:** tests use temporary directories and
  fakes, never the real filesystem, real Docker, real Homebrew, or the real
  homelab.

## 10. Technical constraints

- Python 3.12+, standard library first, `uv` for everything (existing repo
  rules).
- Page: plain HTML, CSS, and JavaScript with no build step, served by the
  standard-library HTTP server. The page must work offline.
- **ECharts** (Apache-2.0) is approved by Colin as the one charting library.
  It is vendored into the repository as a single pinned file, so the page
  works offline. Record the version, license, and reason in the repository
  (for example in `README.md`), as `AGENTS.md` requires for dependencies.
- The scan, findings, previous scans, and action logs contain real paths and
  stay under `reports/` (git-ignored). Never commit them.
- The design must leave room for a future option that calls an LLM API
  directly, but must not require one.

## 11. Build order

Each step is usable on its own:

1. **Scanner** covering all six blind spots, writing the scan file.
2. **Page** with the Overview tab, category and location lenses, and the
   reclaim-zone scatter and project dead-weight insights.
3. **Agent playbook, findings schema, findings cards, scores, and the
   Recommendations tab.**
4. **Delete** (Trash, permanent, Docker, Homebrew) with the confirmation flow
   and safety-rule updates.
5. **Archive** to the homelab (uses the existing `opencode` SSH setup).
6. **Target mode** and **what grew since last time**.

The existing `TASKS.md` entries (`SCAN-001`, `SCAN-002`, `CLEAN-001`) are
replaced by tasks that follow this order.

## 12. Who decided what

**Colin decided:** the purpose and the six blind spots; the category and
location lenses; the four tabs; the Archive and Delete tabs being nearly
identical; the score idea and folder roll-up; target mode; the five insights;
the page running actions itself; archiving over SSH following the
`remote_server_maintenance` guidance; Python, the local page, the Trash
mechanism, the agent playbook, the build order, and the safety-rule updates
(all accepted when played back).

**My calls (flagged for review):** the playbook file name and findings schema
location; the page working without a findings file; the score formula's
shape; how non-file items (Docker, Homebrew, apps) are removed, and Docker
volumes being report-only; protecting the local server with a token and
Origin check; targeted scans as the only way the agent adds items; reporting
the Trash and local snapshots; accepting the standard per-folder privacy
prompts but not prompting for other apps' sandboxed data; any measured item
being selectable, not only recommended ones.

The brainstorming mockups (Overview charts and the insights gallery) are in
`.superpowers/brainstorm/`, which git ignores. They are a visual reference,
not a specification.

## 13. Answers from the review round

A builder review raised three questions only Colin could answer. His answers
are written into the sections above:

1. Trash space: separate "freed" and "waiting in the Trash" numbers, plus an
   Empty Trash action with its own confirmation (section 8.2).
2. Charting library: ECharts, vendored (section 10).
3. Whole repositories may be archived or trashed as one unit, with a warning
   for unsaved work (section 9).

**Left to the builder:** the final Overview chart set and visual design;
where insights sit; scan depth and roll-up rules; the best "last used" signal
per item kind; which extra package managers to cover; exact file formats; the
`rsync` flags and checksum commands.
