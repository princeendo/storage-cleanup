"""Scores and rule-based recommendations (spec sections 5 and 7)."""

import math
from collections.abc import Callable
from dataclasses import dataclass
from typing import Final

from storage_cleanup.scan.categories import DEV_CACHES, DOWNLOADS, LEFTOVER_APP_DATA
from storage_cleanup.scan.model import Item, ItemKind, JsonValue, Scan

ACTIONS: Final = frozenset({"delete", "archive"})
_MIN_LOG2: Final = 12  # 4 KiB scores nothing
_MAX_LOG2: Final = 35  # 32 GiB and up gets the full size factor
_STALE_DAYS: Final = 180
_IN_USE_DAYS: Final = 30
_DAY: Final = 86_400.0


@dataclass(frozen=True)
class Recommendation:
    item_id: str
    action: str
    confidence: float
    reason: str
    source: str


def size_factor(size_bytes: int) -> float:
    """0 at or below 4 KiB, 1 at or above 32 GiB, log-scaled between."""
    if size_bytes <= 0:
        return 0.0
    value = (math.log2(size_bytes) - _MIN_LOG2) / (_MAX_LOG2 - _MIN_LOG2)
    return min(1.0, max(0.0, value))


def score(confidence: float, size_bytes: int) -> float:
    """How much a removal is worth: confidence weighted by space it frees."""
    return round(100 * confidence * size_factor(size_bytes), 1)


def format_age(days: float) -> str:
    """The page's age wording (format.js formatAge)."""
    if days < 1:
        return "today"
    if days < 2:
        return "1 day"
    if days < 45:
        return f"{math.floor(days)} days"
    if days < 365:
        months = round(days / 30.44)
        return "1 month" if months == 1 else f"{months} months"
    return f"{days / 365.25:.1f} years"


def _age_days(item: Item, now: float) -> float | None:
    if item.age_timestamp is None:
        return None
    return max(0.0, (now - item.age_timestamp) / _DAY)


def _text(value: JsonValue) -> str:
    return "" if value is None else str(value)


def rule_recommendations(scan: Scan, now: float) -> list[Recommendation]:
    """Recommendations that need no agent: well-known, regenerable, or orphaned."""
    by_id = {i.id: i for i in scan.items}

    def repo_of(item: Item) -> Item | None:
        parent = by_id.get(item.parent_id) if item.parent_id else None
        seen: set[str] = set()
        while parent is not None and parent.id not in seen:
            if parent.kind == ItemKind.REPO:
                return parent
            seen.add(parent.id)
            parent = by_id.get(parent.parent_id) if parent.parent_id else None
        return None

    def rule(item: Item) -> tuple[float, str] | None:
        d = item.details
        if item.kind == ItemKind.CACHE and DEV_CACHES in item.categories:
            return (
                0.9,
                f"{_text(d.get('why_safe'))} {_text(d.get('regenerates'))}".strip(),
            )
        if DOWNLOADS in item.categories and d.get("installer_for"):
            return (
                0.85,
                f"Installer for {d['installer_for']}, which is already installed.",
            )
        if item.kind == ItemKind.DOCKER_BUILD_CACHE:
            return 0.9, "Docker build cache; rebuilt when images are built again."
        if item.kind == ItemKind.DOCKER_IMAGE and d.get("containers") == 0:
            age = _age_days(item, now)
            if age is not None and age >= _STALE_DAYS:
                return 0.6, f"Docker image no container has used in {format_age(age)}."
            return None
        if item.kind == ItemKind.REGENERABLE:
            repo = repo_of(item)
            age = _age_days(repo, now) if repo is not None else None
            if age is not None and age >= _STALE_DAYS:
                return 0.7, (
                    f"{_text(d.get('pattern'))} in a project last committed "
                    f"{format_age(age)} ago; rebuilt by its tools."
                )
            return None
        if item.kind == ItemKind.AI_SESSIONS and d.get("cwd_exists") is False:
            return 0.6, (
                f"{_text(d.get('tool_name'))} sessions for a folder that no longer "
                f"exists ({_text(d.get('cwd'))})."
            )
        if LEFTOVER_APP_DATA in item.categories:
            age = _age_days(item, now)
            if age is not None and age < _IN_USE_DAYS:
                return None  # something still writes here: probably not leftover
            return 0.5, (
                f"Data left by {_text(d.get('guessed_owner'))}, which is no longer "
                "installed."
            )
        return None

    return _apply(scan, rule)


def _apply(
    scan: Scan, rule: Callable[[Item], tuple[float, str] | None]
) -> list[Recommendation]:
    recs: list[Recommendation] = []
    for item in scan.items:
        if item.protected_reason is not None:
            continue
        found = rule(item)
        if found is not None:
            confidence, reason = found
            recs.append(Recommendation(item.id, "delete", confidence, reason, "rule"))
    return recs
