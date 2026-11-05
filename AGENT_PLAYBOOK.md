# Agent Playbook: Analyze This Mac

Follow these steps when the user asks you to "analyze my device," "find what
is using my disk," or similar. Any coding agent can follow them. You need a
shell in this repository and `uv`.

Your job is to **explain and recommend**. The user decides and acts in the
page. Read `AGENTS.md` first; its safety rules apply here.

## 1. Rules

1. **Change nothing.** Do not remove, move, rename, compress, or empty
   anything. Do not run `brew cleanup`, `docker prune`, `rm`, or `trash`.
2. **Read-only commands only.** `ls`, `stat`, `du`, `df`, `find` (never
   `-delete`), `mdls`, `file`, `head`, `brew info`, `brew leaves`, `docker
   image ls`, `git log`, `git status`, plus this tool's own `scan`,
   `inspect`, and `findings` commands.
3. **No `sudo`, and no Full Disk Access.** If something is unreadable, say so
   and move on.
4. **Never recommend a protected item.** `inspect` hides protected items by
   default; the validator rejects them anyway.
5. **Keep personal data private.** Findings stay under `reports/`, which git
   ignores. Do not paste file lists into other tools or services.

## 2. Scan

```bash
uv run storage-cleanup scan
```

This takes about 3 minutes and writes `reports/scans/<time>.json`. A recent
scan (from today) is fine to reuse. To measure one folder in detail and add it
to the latest scan:

```bash
uv run storage-cleanup scan --path ~/Downloads/SomeFolder
```

## 3. Orient

The scan file is large; do not read it whole. Ask focused questions instead:

```bash
uv run storage-cleanup inspect --summary
```

Then work through each area. Each command prints at most 50 rows (raise
`--limit` if you need more) with item IDs you will cite later.

| Area | Command |
| --- | --- |
| Downloads, oldest first | `uv run storage-cleanup inspect --location Downloads --sort age` |
| Big, old Downloads | `uv run storage-cleanup inspect --category downloads --min-size 500M --older-than 365` |
| AI tooling | `uv run storage-cleanup inspect --category ai-tooling --min-size 50M` |
| Sessions for gone folders | `uv run storage-cleanup inspect --kind ai-sessions --json` (look at `cwd_exists` with `--id`) |
| Apps not opened in a year | `uv run storage-cleanup inspect --kind app --older-than 365` |
| Data from removed apps | `uv run storage-cleanup inspect --category leftover-app-data` |
| Homebrew | `uv run storage-cleanup inspect --category homebrew --min-size 100M` |
| Docker | `uv run storage-cleanup inspect --category docker` |
| Developer caches | `uv run storage-cleanup inspect --category dev-caches` |
| Projects | `uv run storage-cleanup inspect --kind repo --sort age` and `--kind regenerable` |
| Stray home folders | `uv run storage-cleanup inspect --category home --min-size 1G` |

`uv run storage-cleanup inspect --id <ITEM_ID>` shows one item with every
detail the scanner recorded (for example `installer_for`, `last_commit`,
`dirty`, `unpushed_commits`, `cwd`, `bundle_id`).

## 4. Investigate

Look harder at anything large, old, or unexplained. Find out **what it is**
before you judge it:

- `ls -la` and `du -sh` inside a folder to see what makes it big.
- `mdls -name kMDItemLastUsedDate <app>` for when an app was last opened.
- `brew info <name>` and `brew uses --installed <name>` for what a formula is
  and what depends on it.
- `git -C <repo> log -1 --format=%cr` and `git -C <repo> status --short` for
  a project.
- `file <path>` or `head -c 200 <path>` for an unknown file.

Stop when you can explain the biggest items in plain words. You do not need
to look at everything.

## 5. Decide confidence

| Confidence | Use when |
| --- | --- |
| 0.9 or more | It rebuilds or downloads again by itself, or it duplicates something the user keeps. |
| 0.7 | Very likely unneeded, and easy to get back. |
| 0.5 | Probably unneeded, but the user should look first. |
| Below 0.5 | Do not recommend it. Mention it in a headline instead if it is interesting. |

Choose `archive` (copy to the homelab, then Trash) over `delete` (Trash) for
anything personal or hard to recreate: photos, videos, documents, old
projects, game or ROM collections. Choose `delete` for caches, installers,
build output, and other things that come back on their own.

Recommend the **largest sensible unit**. Recommend a folder, not each file in
it, when the whole folder can go. The page also rolls up three or more
sibling recommendations with the same action.

Built-in rules already recommend the obvious items: known developer caches,
installers for apps you already have, Docker build cache, unused Docker images,
rebuildable folders in stale projects, AI sessions for gone folders, and data
from removed apps. Recommend one of those yourself only when you know more
(your recommendation replaces the rule's).

## 6. Write headlines

Write at most 8 headline cards. Each is a **checkable fact with numbers**,
and the user should learn something from it:

- Good: "Your Downloads is mostly installers you already ran: 38 files, 21 GB."
- Good: "Claude Code keeps sessions for 9 projects that no longer exist: 2.3 GB."
- Bad: "You have a lot of files." (no numbers, nothing to check)

A title is at most 80 characters and a body at most 300. List the item IDs
the card is about, so the page can show them. The page shows the total size
of the items you cite, so **cite every item behind a number** (all 38
environments, not the largest six), or the card will contradict itself.

You may also group items into your own categories (for example
`"id": "rom-collections"`) when that helps the user see a pattern.

## 7. Write the findings file

Find where it belongs:

```bash
uv run storage-cleanup findings path
```

Write JSON in exactly this shape (item IDs come from `inspect`):

```json
{
  "schema_version": 1,
  "scan": "20261105T221259Z.json",
  "created_at": "2026-11-05T23:30:00Z",
  "agent": "Claude Code (claude-opus-5-5)",
  "headlines": [
    {
      "title": "18 GB of game ROMs sit in Downloads",
      "body": "One folder of arcade ROMs (FBNeo, 18.1 GB) was last changed 9 months ago. It is the largest thing in Downloads.",
      "item_ids": ["folder:0123456789abcdef"]
    }
  ],
  "categories": [
    {
      "id": "rom-collections",
      "name": "ROM collections",
      "description": "Game ROM sets kept in Downloads.",
      "item_ids": ["folder:0123456789abcdef"]
    }
  ],
  "recommendations": [
    {
      "item_id": "folder:0123456789abcdef",
      "action": "archive",
      "confidence": 0.7,
      "reason": "18 GB ROM set, untouched for 9 months; archive keeps it on the homelab."
    }
  ]
}
```

- `scan` is the scan file's name (not its path).
- `reason` is one line, at most 200 characters, that the user can check.
- Each item appears in at most one recommendation.

## 8. Validate

```bash
uv run storage-cleanup findings check reports/findings/<scan file name>
```

Fix everything it reports and run it again until it says
`Findings are valid`.

## 9. Show the user

```bash
uv run storage-cleanup serve
```

Then tell the user, in plain words, the three findings that free the most
space or surprise them most, and that nothing has been changed.
