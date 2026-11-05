"""Applications, the data they keep in ~/Library, and data left by removed apps."""

import os
import plistlib
import re
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Final

from storage_cleanup.scan.categories import APP_DATA, APPLICATIONS, LEFTOVER_APP_DATA
from storage_cleanup.scan.context import CollectorResult, ScanContext
from storage_cleanup.scan.ids import item_id
from storage_cleanup.scan.measure import measure
from storage_cleanup.scan.model import (
    AgeSignal,
    Item,
    ItemKind,
    JsonValue,
    SizeSource,
    Skipped,
)

APP_DATA_DIRS: Final = (
    "Application Support",
    "Caches",
    "Containers",
    "Group Containers",
    "HTTPStorages",
    "Logs",
    "Saved Application State",
    "WebKit",
)
LEFTOVER_DIRS: Final = frozenset(
    {
        "Application Support",
        "Caches",
        "HTTPStorages",
        "Saved Application State",
        "WebKit",
    }
)
LEFTOVER_MIN_BYTES: Final = 1024**2
_REVERSE_DNS: Final = re.compile(r"^[A-Za-z0-9-]+(\.[A-Za-z0-9-]+){2,}")
_SAVED_STATE: Final = ".savedState"


@dataclass(frozen=True)
class AppBundle:
    path: Path
    name: str
    bundle_id: str | None
    version: str | None


def find_app_bundles(app_dirs: Sequence[Path]) -> list[AppBundle]:
    """List top-level ``*.app`` bundles with their Info.plist facts."""
    bundles: list[AppBundle] = []
    for directory in app_dirs:
        try:
            entries = sorted(directory.iterdir())
        except OSError:
            continue
        for path in entries:
            if path.suffix != ".app" or not path.is_dir():
                continue
            bundle_id: str | None = None
            version: str | None = None
            try:
                with (path / "Contents" / "Info.plist").open("rb") as f:
                    info = plistlib.load(f)
                bundle_id = info.get("CFBundleIdentifier")
                version = info.get("CFBundleShortVersionString") or info.get(
                    "CFBundleVersion"
                )
            except (OSError, plistlib.InvalidFileException, ValueError):
                pass
            bundles.append(AppBundle(path, path.stem, bundle_id, version))
    return bundles


def _last_opened(ctx: ScanContext, path: Path) -> tuple[AgeSignal, float] | None:
    result = ctx.run(["mdls", "-raw", "-name", "kMDItemLastUsedDate", str(path)])
    text = result.stdout.strip()
    if result.returncode != 0 or not text or text == "(null)":
        return None
    try:
        return AgeSignal.LAST_OPENED, datetime.strptime(
            text, "%Y-%m-%d %H:%M:%S %z"
        ).timestamp()
    except ValueError:
        return None


def _data_item(
    path: Path,
    size: int,
    category: str,
    details: dict[str, JsonValue],
    mtime: float | None,
) -> Item:
    return Item(
        id=item_id(ItemKind.APP_DATA, str(path)),
        kind=ItemKind.APP_DATA,
        name=path.name,
        path=str(path),
        size_bytes=size,
        size_source=SizeSource.ALLOCATED,
        categories=(category,),
        age_signal=AgeSignal.LAST_MODIFIED if mtime is not None else None,
        age_timestamp=mtime,
        details=details,
    )


def collect_apps(ctx: ScanContext) -> CollectorResult:
    bundles = find_app_bundles(ctx.app_dirs)
    by_id = {b.bundle_id.lower(): b for b in bundles if b.bundle_id}
    by_name = {b.name.lower(): b for b in bundles}
    library_bytes: dict[Path, int] = {b.path: 0 for b in bundles}
    items: list[Item] = []
    skipped: list[Skipped] = []
    privacy = ctx.privacy_skip()

    for dirname in APP_DATA_DIRS:
        base = ctx.home / "Library" / dirname
        if privacy(base) is not None:
            continue
        try:
            entries = sorted(base.iterdir())
        except FileNotFoundError:
            continue
        except PermissionError:
            skipped.append(Skipped(str(base), "no access", "apps"))
            continue
        for entry in entries:
            key = entry.name.removesuffix(_SAVED_STATE).lower()
            owner = by_id.get(key) or by_name.get(key)
            is_leftover = (
                owner is None
                and dirname in LEFTOVER_DIRS
                and _REVERSE_DNS.match(key) is not None
                and not key.startswith("com.apple.")
            )
            if owner is None and not is_leftover:
                continue
            m, sk = measure(entry, collector="apps")
            skipped.extend(sk)
            if owner is not None:
                library_bytes[owner.path] += m.size_bytes
                items.append(
                    _data_item(
                        entry,
                        m.size_bytes,
                        APP_DATA,
                        {"owner_bundle_id": owner.bundle_id},
                        m.newest_mtime,
                    )
                )
            elif m.size_bytes >= LEFTOVER_MIN_BYTES:
                guessed = entry.name.removesuffix(_SAVED_STATE)
                items.append(
                    _data_item(
                        entry,
                        m.size_bytes,
                        LEFTOVER_APP_DATA,
                        {"guessed_owner": guessed},
                        m.newest_mtime,
                    )
                )

    for bundle in bundles:
        m, sk = measure(bundle.path, collector="apps")
        skipped.extend(sk)
        age = _last_opened(ctx, bundle.path)
        if age is None:
            try:
                age = (AgeSignal.LAST_MODIFIED, os.lstat(bundle.path).st_mtime)
            except OSError:
                age = None
        items.append(
            Item(
                id=item_id(ItemKind.APP, str(bundle.path)),
                kind=ItemKind.APP,
                name=bundle.name,
                path=str(bundle.path),
                size_bytes=m.size_bytes,
                size_source=SizeSource.ALLOCATED,
                categories=(APPLICATIONS,),
                age_signal=age[0] if age else None,
                age_timestamp=age[1] if age else None,
                file_count=m.file_count,
                details={
                    "bundle_id": bundle.bundle_id,
                    "version": bundle.version,
                    "library_bytes": library_bytes[bundle.path],
                },
            )
        )
    return CollectorResult(items=tuple(items), skipped=tuple(skipped))
