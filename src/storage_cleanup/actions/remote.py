"""Talk to the homelab over SSH: a fixed set of programs, never a shell string."""

import re
import shlex
import subprocess
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Final

from storage_cleanup.actions.archive_config import ArchiveConfig
from storage_cleanup.scan.context import CommandResult

# "sh" is added by the listing task, for one exact script only.
REMOTE_PROGRAMS: Final = frozenset(
    {"hostname", "findmnt", "df", "install", "mkdir", "mv", "chown", "find", "rmdir"}
)
_UNREACHABLE: Final = (
    "Could not resolve hostname",
    "Connection refused",
    "timed out",
    "No route to host",
    "Network is unreachable",
)
_HOST_KEY: Final = (
    "Host key verification failed",
    "REMOTE HOST IDENTIFICATION HAS CHANGED",
)
_DEST: Final = re.compile(r"^[A-Za-z0-9._/-]+$")
_SSH_OPTIONS: Final = ("-o", "IdentitiesOnly=yes", "-o", "BatchMode=yes")
GIB: Final = 1024**3

_FIND_RUNNERS: Final = frozenset({"-exec", "-execdir", "-ok", "-okdir"})
_FIND_WRITERS: Final = frozenset({"-fprint", "-fprint0", "-fprintf", "-fls"})

type TimedRunner = Callable[[Sequence[str], float], CommandResult]


def ssh_runner(args: Sequence[str], timeout: float) -> CommandResult:
    """Run ssh or rsync with no terminal, so nothing can ask for a password."""
    try:
        done = subprocess.run(
            list(args),
            capture_output=True,
            encoding="utf-8",
            errors="surrogateescape",  # remote names need not be UTF-8
            stdin=subprocess.DEVNULL,
            start_new_session=True,
            timeout=timeout,
            check=False,
        )
    except FileNotFoundError as exc:
        return CommandResult(127, "", str(exc))
    except subprocess.TimeoutExpired:
        return CommandResult(124, "", "timed out")
    return CommandResult(done.returncode, done.stdout, done.stderr)


def _check_argv(argv: Sequence[str]) -> None:
    """Refuse anything but the allowed programs, and find's removing/writing forms."""
    if not argv or argv[0] not in REMOTE_PROGRAMS:
        raise ValueError(f"Not an allowed remote program: {list(argv)!r}")
    if "-delete" in argv:
        raise ValueError("-delete is never allowed on the homelab.")
    if argv[0] != "find":
        return
    for i, arg in enumerate(argv):
        if arg in _FIND_WRITERS:
            raise ValueError(f"find {arg} writes files and is not allowed.")
        if arg in _FIND_RUNNERS and argv[i + 1 : i + 2] != ["chmod"]:
            raise ValueError(f"find {arg} may only run chmod.")


class RemoteError(Exception):
    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


@dataclass(frozen=True)
class Remote:
    config: ArchiveConfig
    host: str
    run: TimedRunner

    def ssh_args(self, argv: Sequence[str]) -> list[str]:
        _check_argv(argv)
        command = list(argv) if list(argv) == ["hostname"] else ["sudo", "-n", *argv]
        return [
            "/usr/bin/ssh",
            "-o",
            "IdentitiesOnly=yes",
            "-o",
            "BatchMode=yes",
            "-o",
            "ConnectTimeout=10",
            "-i",
            str(self.config.key),
            f"{self.config.user}@{self.host}",
            "--",
            shlex.join(command),
        ]

    def call(self, argv: Sequence[str], timeout: float = 60) -> CommandResult:
        return self.run(self.ssh_args(argv), timeout)

    def rsync_args(self, source: str, dest_dir: str) -> list[str]:
        if not _DEST.fullmatch(dest_dir):
            raise ValueError(f"Unsafe destination folder: {dest_dir!r}")
        ssh = " ".join(["/usr/bin/ssh", *_SSH_OPTIONS, "-i", str(self.config.key)])
        return [
            "/usr/bin/rsync",
            "-a",
            "--rsync-path=sudo -n rsync",
            "-e",
            ssh,
            "--",
            source,
            f"{self.config.user}@{self.host}:{dest_dir}/",
        ]


def connect(config: ArchiveConfig, run: TimedRunner) -> Remote:
    """Return a Remote for the first host that answers as the expected machine."""
    for host in config.hosts:
        remote = Remote(config, host, run)
        result = remote.call(["hostname"], timeout=30)
        if result.returncode == 0:
            name = result.stdout.strip()
            if name != config.expected_hostname:
                raise RemoteError(f"Unexpected host {name}")
            return remote
        error = result.stderr
        if result.returncode in (255, 124) and any(s in error for s in _UNREACHABLE):
            continue
        if "Permission denied" in error:
            raise RemoteError("The homelab refused the SSH key.")
        if any(s in error for s in _HOST_KEY):
            raise RemoteError(
                "The homelab's host key is unknown or changed; "
                "check it by hand before archiving."
            )
        detail = error.strip() or f"exited {result.returncode}"
        raise RemoteError(f"Could not talk to {host}: {detail}")
    raise RemoteError("Could not reach the homelab on any configured address.")


def _gib(size: int) -> str:
    return f"{size / GIB:.1f} GiB"


def check_storage(remote: Remote, need_bytes: int) -> int:
    """Check the archive disk is mounted, is the right one, and has room.

    Returns the free bytes so a caller can report them.
    """
    config = remote.config
    found = remote.call(["findmnt", "-no", "UUID", "--mountpoint", config.mount_point])
    uuid = found.stdout.strip()
    if found.returncode != 0 and found.stderr.strip():
        raise RemoteError(f"Could not check the archive disk: {found.stderr.strip()}")
    if found.returncode != 0 or not uuid:
        raise RemoteError("The archive disk is not mounted")
    if uuid != config.fs_uuid:
        raise RemoteError("The archive disk is not the expected one")
    usage = remote.call(["df", "-B1", "--output=avail", "--", config.mount_point])
    try:
        if usage.returncode != 0:
            raise ValueError
        free = int(usage.stdout.split()[-1])
    except (ValueError, IndexError):
        detail = usage.stderr.strip() or "unreadable output"
        raise RemoteError(f"Could not read the free space: {detail}") from None
    if free < need_bytes:
        raise RemoteError(
            f"Not enough free space on the homelab ({_gib(free)} free, "
            f"{_gib(need_bytes)} needed)"
        )
    return free
