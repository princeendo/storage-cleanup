"""Downloads triage facts: type, age, and installers for apps already installed."""

import os
import re
import stat
import zipfile
from dataclasses import replace
from pathlib import Path
from typing import Final

from storage_cleanup.scan.categories import DOWNLOADS
from storage_cleanup.scan.collectors.apps import find_app_bundles
from storage_cleanup.scan.collectors.home import path_item
from storage_cleanup.scan.context import CollectorResult, ScanContext
from storage_cleanup.scan.measure import measure
from storage_cleanup.scan.model import Item, JsonValue, Skipped

_TYPES: Final[dict[str, frozenset[str]]] = {
    "installer": frozenset({".dmg", ".pkg", ".mpkg"}),
    "archive": frozenset({".zip", ".tar", ".gz", ".tgz", ".bz2", ".xz", ".7z", ".rar"}),
    "video": frozenset({".mov", ".mp4", ".m4v", ".mkv", ".avi", ".webm"}),
    "audio": frozenset({".mp3", ".m4a", ".wav", ".flac", ".aac", ".aiff"}),
    "image": frozenset(
        {".png", ".jpg", ".jpeg", ".gif", ".heic", ".webp", ".tiff", ".bmp", ".svg"}
    ),
    "document": frozenset(
        {
            ".pdf",
            ".doc",
            ".docx",
            ".xls",
            ".xlsx",
            ".ppt",
            ".pptx",
            ".pages",
            ".numbers",
            ".key",
            ".txt",
            ".md",
            ".csv",
        }
    ),
}
_PLATFORM_TOKENS: Final = frozenset(
    {"arm64", "x64", "x86", "universal", "aarch64", "mac", "macos", "osx", "darwin"}
)
_TOKEN: Final = re.compile(r"[a-z0-9]+")


def file_type(path: Path) -> str:
    try:
        if stat.S_ISDIR(os.lstat(path).st_mode):
            return "folder"
    except OSError:
        pass
    suffix = path.suffix.lower()
    for kind, suffixes in _TYPES.items():
        if suffix in suffixes:
            return kind
    return "other"


def normalize_app_name(name: str) -> str:
    stem = Path(name).stem if Path(name).suffix else name
    tokens = _TOKEN.findall(stem.lower())
    return "".join(t for t in tokens if not t.isdigit() and t not in _PLATFORM_TOKENS)


def _zip_apps(path: Path) -> set[str]:
    try:
        with zipfile.ZipFile(path) as z:
            names = z.namelist()
    except (OSError, zipfile.BadZipFile, ValueError):
        return set()
    return {n.split("/", 1)[0] for n in names if n.split("/", 1)[0].endswith(".app")}


def _installer_for(path: Path, kind: str, apps: dict[str, str]) -> str | None:
    if kind == "installer":
        return apps.get(normalize_app_name(path.name))
    if path.suffix.lower() == ".zip":
        for bundle in _zip_apps(path):
            match = apps.get(normalize_app_name(bundle))
            if match:
                return match
    return None


def collect_downloads(ctx: ScanContext) -> CollectorResult:
    downloads = ctx.home / "Downloads"
    try:
        entries = sorted(downloads.iterdir())
    except FileNotFoundError:
        return CollectorResult()
    except PermissionError:
        return CollectorResult(
            skipped=(Skipped(str(downloads), "no access", "downloads"),)
        )

    apps = {normalize_app_name(b.name): b.name for b in find_app_bundles(ctx.app_dirs)}
    items: list[Item] = []
    skipped: list[Skipped] = []
    for entry in entries:
        m, sk = measure(entry, collector="downloads")
        skipped.extend(sk)
        kind = file_type(entry)
        details: dict[str, JsonValue] = {"file_type": kind}
        installer = _installer_for(entry, kind, apps)
        if installer:
            details["installer_for"] = installer
        items.append(replace(path_item(entry, m, (DOWNLOADS,)), details=details))
    return CollectorResult(items=tuple(items), skipped=tuple(skipped))
