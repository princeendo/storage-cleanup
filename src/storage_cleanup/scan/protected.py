"""Paths the tool must never remove, whatever a rule, finding, or user says."""

import os
from pathlib import PurePath

_SYSTEM_PREFIXES: tuple[tuple[str, ...], ...] = (
    ("/", "system"),
    ("/", "bin"),
    ("/", "sbin"),
    ("/", "usr"),
    ("/", "private", "var", "db"),
    ("/", "library", "apple"),
)
_SYSTEM_EXCEPTIONS: tuple[tuple[str, ...], ...] = (("/", "usr", "local"),)

_HOME_STORES: tuple[tuple[tuple[str, ...], str], ...] = (
    (("library", "mail"), "Mail"),
    (("library", "messages"), "Messages"),
    (("library", "keychains"), "Keychains"),
    (("library", "mobile documents"), "iCloud Drive"),
)


def _parts(path: PurePath) -> tuple[str, ...]:
    return tuple(p.lower() for p in PurePath(os.path.abspath(path)).parts)


def _starts_with(parts: tuple[str, ...], prefix: tuple[str, ...]) -> bool:
    return parts[: len(prefix)] == prefix


def protected_reason(path: PurePath, home: PurePath) -> str | None:
    """Return why ``path`` is protected, or ``None`` if it is not."""
    parts = _parts(path)
    if any(_starts_with(parts, p) for p in _SYSTEM_PREFIXES) and not any(
        _starts_with(parts, p) for p in _SYSTEM_EXCEPTIONS
    ):
        return "system location"
    if any(p.endswith(".photoslibrary") for p in parts):
        return "Photos library"
    home_parts = _parts(home)
    if _starts_with(parts, home_parts):
        rest = parts[len(home_parts) :]
        for store, reason in _HOME_STORES:
            if _starts_with(rest, store):
                return reason
    if any(
        p.endswith((".backupbundle", ".sparsebundle")) or p == "backups.backupdb"
        for p in parts
    ) or _starts_with(parts, ("/", "volumes", ".timemachine")):
        return "Time Machine"
    if ".git" in parts:
        return "inside a .git folder"
    return None
