"""Homebrew formulae, casks, and download cache."""

import json
from pathlib import Path
from typing import Any

from storage_cleanup.scan.categories import DEV_CACHES, HOMEBREW
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

COLLECTOR = "homebrew"


def _item(
    kind: str,
    name: str,
    path: Path,
    categories: tuple[str, ...],
    details: dict[str, JsonValue],
    installed: float | None,
) -> tuple[Item, tuple[Skipped, ...]]:
    m, skipped = measure(path, collector=COLLECTOR)
    age = AgeSignal.INSTALLED if installed is not None else None
    if age is None and m.newest_mtime is not None:
        age, installed = AgeSignal.LAST_MODIFIED, m.newest_mtime
    item = Item(
        id=item_id(kind, str(path)),
        kind=kind,
        name=name,
        path=str(path),
        size_bytes=m.size_bytes,
        size_source=SizeSource.ALLOCATED,
        categories=categories,
        age_signal=age,
        age_timestamp=installed,
        file_count=m.file_count,
        details=details,
    )
    return item, skipped


def _cask_apps(cask: dict[str, Any]) -> list[JsonValue]:
    apps: list[JsonValue] = []
    for artifact in cask.get("artifacts") or []:
        if isinstance(artifact, dict):
            apps.extend(a for a in artifact.get("app", []) if isinstance(a, str))
    return apps


def collect_homebrew(ctx: ScanContext) -> CollectorResult:
    prefix = ctx.brew_prefix
    brew = prefix / "bin" / "brew" if prefix else None
    if prefix is None or brew is None or not brew.exists():
        return CollectorResult(
            skipped=(Skipped("", "Homebrew not installed", COLLECTOR),)
        )

    items: list[Item] = []
    skipped: list[Skipped] = []

    def run(*args: str) -> str | None:
        result = ctx.run([str(brew), *args])
        if result.returncode != 0:
            reason = f"brew {' '.join(args)} failed: {result.stderr.strip()[:200]}"
            skipped.append(Skipped("", reason, COLLECTOR))
            return None
        return result.stdout

    info_text = run("info", "--json=v2", "--installed")
    leaves_text = run("leaves")
    leaves = set(leaves_text.split()) if leaves_text else set()
    if info_text is not None:
        try:
            info = json.loads(info_text)
        except ValueError:
            skipped.append(Skipped("", "brew info returned unreadable JSON", COLLECTOR))
            info = {}
        for formula in info.get("formulae", []):
            installed = (formula.get("installed") or [{}])[0]
            item, sk = _item(
                ItemKind.BREW_FORMULA,
                formula["name"],
                prefix / "Cellar" / formula["name"],
                (HOMEBREW,),
                {
                    "version": installed.get("version"),
                    "installed_on_request": installed.get("installed_on_request"),
                    "leaf": formula["name"] in leaves,
                },
                installed.get("time"),
            )
            items.append(item)
            skipped.extend(sk)
        for cask in info.get("casks", []):
            item, sk = _item(
                ItemKind.BREW_CASK,
                cask["token"],
                prefix / "Caskroom" / cask["token"],
                (HOMEBREW,),
                {"version": cask.get("installed"), "apps": _cask_apps(cask)},
                cask.get("installed_time"),
            )
            items.append(item)
            skipped.extend(sk)

    cache_text = run("--cache")
    if cache_text and cache_text.strip():
        cache = Path(cache_text.strip())
        if cache.exists():
            item, sk = _item(
                ItemKind.CACHE,
                "Homebrew download cache",
                cache,
                (HOMEBREW, DEV_CACHES),
                {
                    "why_safe": "Holds downloaded bottles and installers only.",
                    "regenerates": "Downloaded again on the next brew install/upgrade.",
                },
                None,
            )
            items.append(item)
            skipped.extend(sk)
    return CollectorResult(items=tuple(items), skipped=tuple(skipped))
