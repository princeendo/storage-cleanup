"""Carry out a confirmed plan, one entry at a time, and log every outcome."""

import json
import os
import re
import shutil
import stat
import subprocess
import tempfile
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Final

from storage_cleanup.actions.model import (
    FREES_NOW,
    ActionPlan,
    Method,
    PlanEntry,
    plan_id,
)
from storage_cleanup.actions.verify import recheck_path, verify_entry
from storage_cleanup.scan.context import CommandResult, Runner
from storage_cleanup.scan.model import Scan

TRASH_COMMAND: Final = "/usr/bin/trash"
DETAIL_LIMIT: Final = 300
ACTION_TIMEOUT_SECONDS: Final = 900
_PACKAGE_NAME: Final = re.compile(r"^[A-Za-z0-9][A-Za-z0-9@._+-]*$")
_SUDO_HINTS: Final = ("sudo:", "a terminal is required", "password is required")


@dataclass(frozen=True)
class EntryResult:
    item_id: str
    name: str
    path: str | None
    method: Method
    status: str  # "done" | "skipped" | "failed"
    detail: str
    size_bytes: int


@dataclass(frozen=True)
class ActionResult:
    plan_id: str
    permanent: bool
    started_at: str
    finished_at: str
    results: tuple[EntryResult, ...]
    free_before: int
    free_after: int
    moved_to_trash_bytes: int
    freed_bytes: int
    log_path: str
    archived_bytes: int = 0
    archive_batch: str | None = None


def action_runner(args: Sequence[str]) -> CommandResult:
    """Run a removal command with no terminal, so nothing can ask for a password."""
    try:
        done = subprocess.run(
            list(args),
            text=True,
            capture_output=True,
            stdin=subprocess.DEVNULL,
            start_new_session=True,
            timeout=ACTION_TIMEOUT_SECONDS,
            check=False,
        )
    except FileNotFoundError as exc:
        return CommandResult(127, "", str(exc))
    except subprocess.TimeoutExpired:
        return CommandResult(124, "", "timed out")
    return CommandResult(done.returncode, done.stdout, done.stderr)


def delete_path(path: Path) -> None:
    """Permanently remove a file or folder. Links are removed, never followed."""
    st = os.lstat(path)
    if stat.S_ISDIR(st.st_mode):
        shutil.rmtree(path)
    else:
        os.unlink(path)


def _gone(path: str) -> bool:
    try:
        os.lstat(path)
    except FileNotFoundError:
        return True
    return False


def _free(home: Path) -> int:
    try:
        return shutil.disk_usage(home).free
    except OSError:
        return shutil.disk_usage("/").free


def _stamp(t: float) -> str:
    return datetime.fromtimestamp(t, UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _plain_failure(result: CommandResult) -> str:
    return result.stderr.strip() or f"exited {result.returncode}"


def _command_failure(result: CommandResult, entry: PlanEntry) -> str:
    error = _plain_failure(result)
    if any(hint in error for hint in _SUDO_HINTS):
        flag = "--cask " if entry.method == Method.BREW_CASK else ""
        return (
            "Needs administrator rights. Uninstall it in Terminal with: "
            f"brew uninstall {flag}{entry.name}"
        )
    return error


def trash_path(
    path: str,
    runner: Runner,
    describe_failure: Callable[[CommandResult], str] = _plain_failure,
) -> tuple[str, str]:
    """Recheck, move to the Trash, and confirm it is gone; return (status, detail)."""
    reason = recheck_path(path)  # nothing may have changed since verify
    if reason is not None:
        return "skipped", reason
    result = runner([TRASH_COMMAND, path])
    if result.returncode != 0:
        return "failed", describe_failure(result)
    if not _gone(path):
        return "failed", "The trash command finished but the item is still there."
    return "done", "Moved to the Trash."


def _execute(
    entry: PlanEntry, scan: Scan, runner: Runner, brew_prefix: Path | None
) -> tuple[str, str]:
    """Run one entry; return (status, detail)."""
    if entry.method == Method.TRASH:
        assert entry.path is not None
        return trash_path(
            entry.path, runner, lambda result: _command_failure(result, entry)
        )
    if entry.method == Method.DELETE:
        assert entry.path is not None
        reason = recheck_path(entry.path)  # nothing may have changed since verify
        if reason is not None:
            return "skipped", reason
        try:
            delete_path(Path(entry.path))
        except OSError as exc:
            return "failed", f"{exc.strerror or exc}. Part of it may already be gone."
        if not _gone(entry.path):
            return "failed", "Deleting finished but the item is still there."
        return "done", "Deleted."

    item = next((i for i in scan.items if i.id == entry.item_id), None)
    if entry.method == Method.DOCKER_IMAGE:
        image_id = item.details.get("image_id") if item else None
        if not isinstance(image_id, str) or not _PACKAGE_NAME.match(
            image_id.replace(":", "")
        ):
            return "failed", "The scan has no usable image ID for this image."
        args = ["docker", "image", "rm", "--", image_id]
    elif entry.method == Method.DOCKER_BUILD_CACHE:
        args = ["docker", "builder", "prune", "--force"]
    else:
        if brew_prefix is None:
            return "failed", "Homebrew is not installed."
        if not _PACKAGE_NAME.match(entry.name):
            return "failed", f"Unexpected package name {entry.name!r}."
        brew = str(brew_prefix / "bin" / "brew")
        kind = "--cask" if entry.method == Method.BREW_CASK else "--formula"
        args = [brew, "uninstall", kind, entry.name]
    result = runner(args)
    if result.returncode != 0:
        return "failed", _command_failure(result, entry)
    return "done", "Removed."


def _log_path(log_dir: Path, started: float) -> Path:
    stamp = datetime.fromtimestamp(started, UTC).strftime("%Y%m%dT%H%M%S%fZ")
    target = log_dir / f"{stamp}.json"
    counter = 1
    while target.exists():
        target = log_dir / f"{stamp}-{counter}.json"
        counter += 1
    return target


def _write_log(target: Path, body: Mapping[str, object]) -> None:
    fd, tmp = tempfile.mkstemp(dir=target.parent, prefix=".log-", suffix=".tmp")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(body, f, indent=1, ensure_ascii=True, default=str)
    os.replace(tmp, target)


def run_plan(
    plan: ActionPlan,
    scan: Scan,
    *,
    runner: Runner,
    brew_prefix: Path | None,
    log_dir: Path,
    now: Callable[[], float] = time.time,
) -> ActionResult:
    """Verify and run every entry; a failure never stops the entries after it.

    The log is written before the first action and rewritten after each one,
    so an interrupted run still leaves a record of what happened.
    """
    home = Path(scan.home)
    started = now()
    free_before = _free(home)
    log_dir.mkdir(parents=True, exist_ok=True)
    log = _log_path(log_dir, started)
    results: list[EntryResult] = []

    def record(state: str) -> None:
        _write_log(
            log,
            {
                "state": state,
                "plan_id": plan_id(plan),
                "scan": plan.scan,
                "permanent": plan.permanent,
                "started_at": _stamp(started),
                "free_before": free_before,
                "entries": [asdict(e) for e in plan.entries],
                "refused": [asdict(r) for r in plan.refused],
                "results": [asdict(r) for r in results],
            },
        )

    record("running")
    for entry in plan.entries:
        try:
            reason = verify_entry(entry, scan, brew_prefix=brew_prefix)
            if reason is not None:
                status, detail = "skipped", reason
            else:
                status, detail = _execute(entry, scan, runner, brew_prefix)
        except Exception as exc:  # record it and keep going
            status, detail = "failed", f"{type(exc).__name__}: {exc}"
        results.append(
            EntryResult(
                item_id=entry.item_id,
                name=entry.name,
                path=entry.path,
                method=entry.method,
                status=status,
                detail=detail[:DETAIL_LIMIT],
                size_bytes=entry.size_bytes,
            )
        )
        record("running")

    done = [r for r in results if r.status == "done"]
    result = ActionResult(
        plan_id=plan_id(plan),
        permanent=plan.permanent,
        started_at=_stamp(started),
        finished_at=_stamp(now()),
        results=tuple(results),
        free_before=free_before,
        free_after=_free(home),
        moved_to_trash_bytes=sum(
            r.size_bytes for r in done if r.method == Method.TRASH
        ),
        freed_bytes=sum(r.size_bytes for r in done if r.method in FREES_NOW),
        log_path=str(log),
    )
    _write_log(log, {"state": "finished", **asdict(result), "scan": plan.scan})
    return result
