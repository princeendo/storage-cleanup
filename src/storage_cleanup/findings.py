"""What an agent writes after studying a scan, and how it is checked.

Findings are untrusted input: they are parsed leniently, then validated
against the scan they name. Nothing here acts on the findings.
"""

import json
import math
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final

from storage_cleanup.scan.categories import DISPLAY_NAMES
from storage_cleanup.scan.model import Scan
from storage_cleanup.scan.recommend import ACTIONS, Recommendation

FINDINGS_SCHEMA_VERSION: Final = 1
MAX_HEADLINES: Final = 8
MAX_TITLE: Final = 80
MAX_BODY: Final = 300
MAX_REASON: Final = 200
_CATEGORY_ID: Final = re.compile(r"^[a-z0-9-]{1,40}$")
_RESERVED_CATEGORY_IDS: Final = frozenset({*DISPLAY_NAMES, "system-other"})


@dataclass(frozen=True)
class Headline:
    title: str
    body: str
    item_ids: tuple[str, ...]


@dataclass(frozen=True)
class AgentCategory:
    id: str
    name: str
    description: str
    item_ids: tuple[str, ...]


@dataclass(frozen=True)
class Findings:
    schema_version: int
    scan: str
    created_at: str
    agent: str
    headlines: tuple[Headline, ...]
    categories: tuple[AgentCategory, ...]
    recommendations: tuple[Recommendation, ...]


def findings_dir(root: Path) -> Path:
    return root / "reports" / "findings"


def findings_path_for(scan_path: Path) -> Path:
    """Findings sit beside the scans folder, named after their scan file."""
    return scan_path.parent.parent / "findings" / scan_path.name


def _field(obj: Any, key: str, where: str) -> Any:
    if not isinstance(obj, dict) or key not in obj:
        raise ValueError(f"Missing field {where}{key!r}.")
    return obj[key]


def _list(obj: Any, key: str, where: str) -> list[Any]:
    value = _field(obj, key, where)
    if not isinstance(value, list):
        raise ValueError(f"Field {where}{key!r} must be a list.")
    return value


def _ids(obj: Any, where: str) -> tuple[str, ...]:
    return tuple(str(i) for i in _list(obj, "item_ids", where))


def parse_findings(text: str) -> Findings:
    """Read a findings file; raise ValueError naming the first broken field."""
    try:
        data = json.loads(text)
    except (ValueError, RecursionError) as exc:
        raise ValueError(f"Not valid JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise ValueError("Findings must be a JSON object.")
    headlines = tuple(
        Headline(
            title=str(_field(h, "title", f"headlines[{n}].")),
            body=str(_field(h, "body", f"headlines[{n}].")),
            item_ids=_ids(h, f"headlines[{n}]."),
        )
        for n, h in enumerate(_list(data, "headlines", ""))
    )
    categories = tuple(
        AgentCategory(
            id=str(_field(c, "id", f"categories[{n}].")),
            name=str(_field(c, "name", f"categories[{n}].")),
            description=str(c.get("description", "")) if isinstance(c, dict) else "",
            item_ids=_ids(c, f"categories[{n}]."),
        )
        for n, c in enumerate(_list(data, "categories", ""))
    )
    recommendations = tuple(
        Recommendation(
            item_id=str(_field(r, "item_id", f"recommendations[{n}].")),
            action=str(_field(r, "action", f"recommendations[{n}].")),
            # Kept as given; validate_findings checks it is a number in 0..1.
            confidence=_field(r, "confidence", f"recommendations[{n}]."),
            reason=str(_field(r, "reason", f"recommendations[{n}].")),
            source="agent",
            rule="agent",
        )
        for n, r in enumerate(_list(data, "recommendations", ""))
    )
    return Findings(
        schema_version=_field(data, "schema_version", ""),
        scan=str(_field(data, "scan", "")),
        created_at=str(_field(data, "created_at", "")),
        agent=str(_field(data, "agent", "")),
        headlines=headlines,
        categories=categories,
        recommendations=recommendations,
    )


def _is_confidence(value: object) -> bool:
    return (
        isinstance(value, int | float)
        and not isinstance(value, bool)
        and math.isfinite(value)
        and 0 <= value <= 1
    )


def validate_findings(findings: Findings, scan: Scan, scan_name: str) -> list[str]:
    """Return one plain sentence per problem; an empty list means valid."""
    problems: list[str] = []
    if findings.schema_version != FINDINGS_SCHEMA_VERSION:
        problems.append(
            f"schema_version must be {FINDINGS_SCHEMA_VERSION}, "
            f"not {findings.schema_version!r}."
        )
    if findings.scan != scan_name:
        problems.append(
            f"These findings are for scan {findings.scan}, not {scan_name}."
        )
    items = {i.id: i for i in scan.items}

    def check_ids(ids: tuple[str, ...]) -> None:
        for item_id in ids:
            if item_id not in items:
                problems.append(f"Unknown item ID: {item_id}")

    if len(findings.headlines) > MAX_HEADLINES:
        problems.append(f"Write at most {MAX_HEADLINES} headlines.")
    for headline in findings.headlines:
        if not headline.title.strip() or len(headline.title) > MAX_TITLE:
            problems.append(
                f"Headline title must be 1 to {MAX_TITLE} characters: "
                f"{headline.title[:40]!r}"
            )
        if len(headline.body) > MAX_BODY:
            problems.append(
                f"Headline body must be at most {MAX_BODY} characters: "
                f"{headline.title[:40]!r}"
            )
        check_ids(headline.item_ids)

    for category in findings.categories:
        if not _CATEGORY_ID.match(category.id):
            problems.append(
                f"Invalid category ID {category.id!r}: use 1 to 40 lowercase letters, "
                "digits, or hyphens."
            )
        elif category.id in _RESERVED_CATEGORY_IDS:
            problems.append(f"Category ID {category.id!r} is built-in; pick another.")
        check_ids(category.item_ids)

    seen: set[str] = set()
    for rec in findings.recommendations:
        if rec.item_id in seen:
            problems.append(f"Item {rec.item_id} is recommended more than once.")
        seen.add(rec.item_id)
        item = items.get(rec.item_id)
        if item is None:
            problems.append(f"Unknown item ID: {rec.item_id}")
        elif item.protected_reason is not None:
            problems.append(
                f"Item {rec.item_id} is protected ({item.protected_reason}) "
                "and cannot be recommended."
            )
        if rec.action not in ACTIONS:
            problems.append(
                f"Item {rec.item_id}: action must be one of "
                f"{', '.join(sorted(ACTIONS))}, not {rec.action!r}."
            )
        if not _is_confidence(rec.confidence):
            problems.append(
                f"Item {rec.item_id}: confidence must be a number from 0 to 1, "
                f"not {rec.confidence!r}."
            )
        if not rec.reason.strip() or len(rec.reason) > MAX_REASON:
            problems.append(
                f"Item {rec.item_id}: reason must be 1 to {MAX_REASON} characters."
            )
    return problems
