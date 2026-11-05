"""What collectors receive and return."""

import subprocess
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from storage_cleanup.scan.measure import SkipRule
from storage_cleanup.scan.model import Item, Skipped

PRIVACY_SKIP_REASON: Final = "privacy-protected app data (not scanned)"
CLOUD_SKIP_REASON: Final = (
    "cloud storage folder (not scanned: files live online and listing them "
    "waits on the network)"
)


@dataclass(frozen=True)
class CommandResult:
    returncode: int
    stdout: str
    stderr: str


type Runner = Callable[[Sequence[str]], CommandResult]


def subprocess_runner(args: Sequence[str]) -> CommandResult:
    """Run a read-only command and capture its output."""
    try:
        done = subprocess.run(
            list(args), text=True, capture_output=True, timeout=120, check=False
        )
    except FileNotFoundError as exc:
        return CommandResult(127, "", str(exc))
    except subprocess.TimeoutExpired:
        return CommandResult(124, "", "timed out")
    return CommandResult(done.returncode, done.stdout, done.stderr)


@dataclass(frozen=True)
class ScanContext:
    home: Path
    app_dirs: tuple[Path, ...]
    workspace_roots: tuple[Path, ...]
    brew_prefix: Path | None
    run: Runner
    now: float
    include_app_data: bool = False

    def privacy_skip(self) -> SkipRule:
        """Skip cloud-storage mirrors, and other apps' data unless opted in."""
        protected = {
            self.home / "Library" / "Containers",
            self.home / "Library" / "Group Containers",
        }
        cloud = self.home / "Library" / "CloudStorage"
        include = self.include_app_data

        def rule(path: Path) -> str | None:
            if path == cloud:
                return CLOUD_SKIP_REASON
            if not include and path in protected:
                return PRIVACY_SKIP_REASON
            return None

        return rule


@dataclass(frozen=True)
class CollectorResult:
    items: tuple[Item, ...] = ()
    skipped: tuple[Skipped, ...] = ()


type Collector = Callable[[ScanContext], CollectorResult]
