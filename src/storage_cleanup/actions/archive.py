"""Archive a confirmed plan: copy each item to the homelab, prove it, then trash it.

This is the gate that decides when a local copy may go to the Trash, so every
doubt fails closed: an item is trashed only after its homelab copy matches
file for file (SHA-256, link targets, folders) and has been moved into its
final place. Anything else leaves the local item exactly as it was, and the
homelab copy stays in the batch's `.incoming/` folder. Nothing on the homelab
is ever removed (see `remote.REMOTE_PROGRAMS`).
"""

import json
import posixpath
import shlex
import tempfile
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Final

from storage_cleanup.actions.archive_config import ArchiveConfig, check_location
from storage_cleanup.actions.manifest import (
    Manifest,
    UnsupportedItem,
    compare,
    list_command,
    local_manifest_and_snapshot,
    manifest_to_json,
    parse_listing,
    snapshot,
)
from storage_cleanup.actions.model import ActionPlan, Method, PlanEntry, plan_id
from storage_cleanup.actions.remote import (
    Remote,
    RemoteError,
    TimedRunner,
    check_storage,
    connect,
)
from storage_cleanup.actions.run import (
    DETAIL_LIMIT,
    ActionResult,
    EntryResult,
    _free,
    _log_path,
    _stamp,
    _write_log,
    trash_path,
)
from storage_cleanup.actions.verify import verify_entry
from storage_cleanup.scan.context import CommandResult, Runner
from storage_cleanup.scan.model import JsonValue, Scan

ARCHIVE_TIMEOUT_SECONDS: Final = 6 * 3600  # per rsync, and per remote listing
FREE_MARGIN_BYTES: Final = 1024**3
UPLOAD_TIMEOUT_SECONDS: Final = 600  # MANIFEST.json and README.md
PROBE_TIMEOUT_SECONDS: Final = 30
CONNECTION_LOST_EXIT: Final = 255  # what ssh exits with when it cannot connect
DONE_DETAIL: Final = "Copied to the homelab, checked, and moved to the Trash."
CONNECTION_LOST: Final = "Connection lost"
NOT_IN_PLACE: Final = "Could not move it into place on the homelab; the copy is in {}"
NO_SUCH_FILE: Final = "No such file or directory"
CHANGED: Final = "Changed while archiving; the homelab copy is in {}"


@dataclass(frozen=True)
class _Outcome:
    status: str
    detail: str
    remote_failed: bool = False  # probe the connection before the next item
    archived: tuple[str, Manifest] | None = None  # final path, what was copied


def _error(result: CommandResult) -> str:
    text = " ".join(result.stderr.split()) or f"exited {result.returncode}"
    return text[:DETAIL_LIMIT]


def _install(config: ArchiveConfig, *paths: str) -> list[str]:
    owner = ["-o", config.owner, "-g", config.group, "-m", "2770"]
    return ["install", "-d", *owner, "--", *paths]


def _exists(remote: Remote, path: str) -> bool | None:
    """True or False only when find says so clearly; None is doubt."""
    found = remote.call(["find", path, "-maxdepth", "0"])
    if found.returncode == 0:
        return True
    if found.returncode == 1 and NO_SUCH_FILE in found.stderr:
        return False
    return None


def _move_into_place(remote: Remote, incoming: str, final: str) -> bool:
    """mv -n's exit status for an existing target varies, so check both ends."""
    made = remote.call(["mkdir", "-p", "--", posixpath.dirname(final)])
    if made.returncode != 0 or _exists(remote, final) is not False:
        return False
    if remote.call(["mv", "-n", "-T", "--", incoming, final]).returncode != 0:
        return False
    return _exists(remote, incoming) is False and _exists(remote, final) is True


def _archive_one(
    entry: PlanEntry,
    n: int,
    *,
    remote: Remote,
    batch: str,
    scan: Scan,
    runner: Runner,
    brew_prefix: Path | None,
) -> _Outcome:
    if entry.method != Method.ARCHIVE or entry.path is None:
        return _Outcome("skipped", "Not an archive entry.")
    path = entry.path
    reason = verify_entry(entry, scan, brew_prefix=brew_prefix)
    if reason is not None:
        return _Outcome("skipped", reason)
    try:
        check_storage(remote, entry.size_bytes + FREE_MARGIN_BYTES)
    except RemoteError as exc:
        return _Outcome("failed", exc.message, remote_failed=True)

    holder = f"{batch}/.incoming/{n}"
    incoming = f"{holder}/{posixpath.basename(path)}"

    def kept(problem: str, *, remote_failed: bool) -> _Outcome:
        detail = f"{problem[:DETAIL_LIMIT]}; the homelab copy is in {incoming}"
        return _Outcome("failed", detail, remote_failed=remote_failed)

    made = remote.call(_install(remote.config, holder))
    if made.returncode != 0:
        return _Outcome(
            "failed",
            f"Could not make a folder on the homelab: {_error(made)}",
            remote_failed=True,
        )
    copied = remote.run(remote.rsync_args(path, holder), ARCHIVE_TIMEOUT_SECONDS)
    if copied.returncode != 0:
        return kept(f"The copy failed: {_error(copied)}", remote_failed=True)
    try:
        local, seen = local_manifest_and_snapshot(Path(path))
    except (UnsupportedItem, OSError) as exc:
        return kept(str(exc), remote_failed=False)
    listing = remote.call(list_command(holder), timeout=ARCHIVE_TIMEOUT_SECONDS)
    if listing.returncode != 0:
        return kept(
            f"Could not check the copy on the homelab: {_error(listing)}",
            remote_failed=True,
        )
    problems = compare(local, parse_listing(listing.stdout))
    if problems:
        return kept(problems[0], remote_failed=False)

    final = f"{batch}/{path.removeprefix('/')}"
    if not _move_into_place(remote, incoming, final):
        return _Outcome("failed", NOT_IN_PLACE.format(incoming), remote_failed=True)
    remote.call(["rmdir", "--", holder])  # an empty folder left behind is harmless

    # Hours may have passed since verify and the hashing: check everything again.
    try:
        check_storage(remote, 0)  # an unmounted disk means the copy is elsewhere
    except RemoteError as exc:
        detail = f"{exc.message[:DETAIL_LIMIT]}; the homelab copy is in {final}"
        return _Outcome("failed", detail, remote_failed=True)
    reason = verify_entry(entry, scan, brew_prefix=brew_prefix)
    if reason is not None:
        detail = f"{reason[:DETAIL_LIMIT].rstrip('.')}. The homelab copy is in {final}."
        return _Outcome("skipped", detail, archived=(final, local))
    try:
        unchanged = snapshot(Path(path)) == seen
    except OSError:
        unchanged = False
    if not unchanged:
        return _Outcome("failed", CHANGED.format(final), archived=(final, local))
    status, detail = trash_path(path, runner)
    if status == "done":
        return _Outcome("done", DONE_DETAIL, archived=(final, local))
    detail = f"{detail[:DETAIL_LIMIT].rstrip('.')}. The homelab copy is in {final}."
    return _Outcome(status, detail, archived=(final, local))


def _readme(
    remote: Remote, batch: str, stamp: str, restores: list[tuple[str, str]]
) -> str:
    config = remote.config
    lines = [
        f"# Archive batch {posixpath.basename(batch)}",
        "",
        f"Made by storage-cleanup on {stamp}. Each item was copied from a Mac,",
        "checked with SHA-256 against the original, and then moved to that Mac's",
        "Trash. MANIFEST.json lists every file with its size and SHA-256.",
        "",
        "macOS extended attributes and Finder tags were not copied. Afterwards,",
        f"everything here was given to {config.owner}:{config.group}, with folders",
        "set to 2770 and files to 660, so executable bits are not kept.",
        "",
        "## Restore",
        "",
        "Run on the Mac to copy an item back:",
        "",
    ]
    ssh = f'-e "ssh -i {config.key}"'
    for final, original in restores:
        source = f"{config.user}@{remote.host}:{shlex.quote(final)}"
        parent = shlex.quote(posixpath.dirname(original) + "/")
        lines.append(
            f'    rsync -a --rsync-path="sudo -n rsync" {ssh} {source} {parent}'
        )
    return "\n".join(lines) + "\n"


def _finish_batch(
    remote: Remote,
    batch: str,
    manifest: dict[str, JsonValue],
    readme: str,
) -> list[str]:
    """Upload MANIFEST.json and README.md, then fix ownership; return problems."""
    config = remote.config
    problems: list[str] = []
    with tempfile.TemporaryDirectory(prefix="archive-") as tmp:
        for name, text in (
            ("MANIFEST.json", json.dumps(manifest, indent=1, ensure_ascii=True)),
            ("README.md", readme),
        ):
            file = Path(tmp) / name
            file.write_text(text, encoding="utf-8")
            sent = remote.run(
                remote.rsync_args(str(file), batch), UPLOAD_TIMEOUT_SECONDS
            )
            if sent.returncode != 0:
                problems.append(f"Could not upload {name}: {_error(sent)}")
    steps: list[list[str]] = [
        ["chown", "-R", "--", f"{config.owner}:{config.group}", batch],
        # chmod runs only inside find -exec; see remote._check_argv.
        [
            "find",
            batch,
            "(",
            "-type",
            "d",
            "-exec",
            "chmod",
            "2770",
            "{}",
            "+",
            ")",
            "-o",
            "(",
            "-type",
            "f",
            "-exec",
            "chmod",
            "660",
            "{}",
            "+",
            ")",
        ],
    ]
    for argv in steps:
        done = remote.call(argv, timeout=ARCHIVE_TIMEOUT_SECONDS)
        if done.returncode != 0:
            problems.append(f"{argv[0]} failed on the homelab: {_error(done)}")
    # Fails while a failed item is still inside; that copy is kept on purpose.
    remote.call(["rmdir", "--", f"{batch}/.incoming"])
    return problems


def run_archive(
    plan: ActionPlan,
    scan: Scan,
    *,
    config: ArchiveConfig,
    remote_runner: TimedRunner,
    runner: Runner,
    brew_prefix: Path | None,
    log_dir: Path,
    manifest_dir: Path,
    now: Callable[[], float] = time.time,
) -> ActionResult:
    """Copy, check and trash every entry in order; the log is kept as in run_plan."""
    home = Path(scan.home)
    started = now()
    free_before = _free(home)
    log_dir.mkdir(parents=True, exist_ok=True)
    log = _log_path(log_dir, started)
    batch_name = datetime.fromtimestamp(started, UTC).strftime("%Y%m%dT%H%M%SZ")
    results: list[EntryResult] = []
    archived: list[tuple[PlanEntry, str, Manifest, bool]] = []
    notes: list[str] = []
    remote: Remote | None = None
    batch: str | None = None

    def record(state: str) -> None:
        _write_log(
            log,
            {
                "state": state,
                "plan_id": plan_id(plan),
                "scan": plan.scan,
                "permanent": plan.permanent,
                "location": plan.location,
                "batch": batch,
                "host": remote.host if remote is not None else None,
                "started_at": _stamp(started),
                "free_before": free_before,
                "entries": [asdict(e) for e in plan.entries],
                "refused": [asdict(r) for r in plan.refused],
                "results": [asdict(r) for r in results],
                "notes": notes,
            },
        )

    def add(entry: PlanEntry, status: str, detail: str) -> None:
        results.append(
            EntryResult(
                item_id=entry.item_id,
                name=entry.name,
                path=entry.path,
                method=entry.method,
                status=status,
                detail=detail,
                size_bytes=entry.size_bytes,
            )
        )

    record("running")
    stop: str | None = None
    location = plan.location
    if location is None:
        stop = "No archive location was chosen."
    else:
        stop = check_location(config, location)
    if stop is None and location is not None and plan.entries:
        try:
            remote = connect(config, remote_runner)
            check_storage(remote, plan.total_bytes + FREE_MARGIN_BYTES)
        except RemoteError as exc:
            stop = exc.message
        else:
            wanted = f"{location}/{batch_name}"
            made = remote.call(
                _install(config, location, wanted, f"{wanted}/.incoming")
            )
            if made.returncode != 0:
                stop = f"Could not make the batch folder on the homelab: {_error(made)}"
            else:
                batch = wanted

    for n, entry in enumerate(plan.entries, start=1):
        if stop is not None or remote is None or batch is None:
            add(entry, "skipped", stop or CONNECTION_LOST)
            record("running")
            continue
        try:
            outcome = _archive_one(
                entry,
                n,
                remote=remote,
                batch=batch,
                scan=scan,
                runner=runner,
                brew_prefix=brew_prefix,
            )
        except Exception as exc:  # record it and keep going; nothing was trashed
            outcome = _Outcome("failed", f"{type(exc).__name__}: {exc}"[:DETAIL_LIMIT])
        if outcome.archived is not None:
            final, copied = outcome.archived
            archived.append((entry, final, copied, outcome.status == "done"))
        add(entry, outcome.status, outcome.detail)
        record("running")
        if outcome.remote_failed:
            probe = remote.call(["hostname"], timeout=PROBE_TIMEOUT_SECONDS)
            if probe.returncode == CONNECTION_LOST_EXIT:
                stop = CONNECTION_LOST

    if remote is not None and batch is not None and stop != CONNECTION_LOST:
        try:
            check_storage(remote, 0)  # never write into an empty mountpoint
        except RemoteError as exc:
            stop = exc.message
    if remote is not None and batch is not None:
        manifest: dict[str, JsonValue] = {
            "batch": batch,
            "host": remote.host,
            "created_at": _stamp(started),
            "items": [
                {
                    "source": entry.path,
                    "path": final,
                    "size_bytes": entry.size_bytes,
                    "trashed": trashed,
                    "contents": list(manifest_to_json(m, posixpath.dirname(final))),
                }
                for entry, final, m, trashed in archived
            ],
        }
        manifest_dir.mkdir(parents=True, exist_ok=True)
        _write_log(manifest_dir / f"{batch_name}.json", manifest)
        if stop is not None:
            notes.append(
                f"{stop}, so MANIFEST.json, README.md and the batch's ownership "
                "were not finished on the homelab."
            )
        else:
            restores = [
                (final, entry.path)
                for entry, final, _m, trashed in archived
                if trashed and entry.path is not None
            ]
            readme = _readme(remote, batch, _stamp(started), restores)
            notes.extend(_finish_batch(remote, batch, manifest, readme))

    done = sum(r.size_bytes for r in results if r.status == "done")
    result = ActionResult(
        plan_id=plan_id(plan),
        permanent=plan.permanent,
        started_at=_stamp(started),
        finished_at=_stamp(now()),
        results=tuple(results),
        free_before=free_before,
        free_after=_free(home),
        moved_to_trash_bytes=done,
        freed_bytes=0,
        log_path=str(log),
        archived_bytes=done,
        archive_batch=batch,
    )
    _write_log(
        log,
        {
            "state": "finished",
            **asdict(result),
            "scan": plan.scan,
            "location": plan.location,
            "host": remote.host if remote is not None else None,
            "notes": notes,
        },
    )
    return result
