"""What a removal run will do, and what it did."""

import hashlib
import json
from dataclasses import dataclass
from enum import StrEnum

from storage_cleanup.scan.model import JsonValue


class Method(StrEnum):
    TRASH = "trash"
    DELETE = "delete"
    ARCHIVE = "archive"
    DOCKER_IMAGE = "docker-image-rm"
    DOCKER_BUILD_CACHE = "docker-builder-prune"
    BREW_FORMULA = "brew-uninstall"
    BREW_CASK = "brew-uninstall-cask"


FREES_NOW = frozenset(
    {
        Method.DELETE,
        Method.DOCKER_IMAGE,
        Method.DOCKER_BUILD_CACHE,
        Method.BREW_FORMULA,
        Method.BREW_CASK,
    }
)


@dataclass(frozen=True)
class PlanEntry:
    item_id: str
    name: str
    path: str | None
    size_bytes: int
    method: Method
    undo: str
    warnings: tuple[str, ...]


@dataclass(frozen=True)
class Refusal:
    item_id: str
    name: str
    reason: str


@dataclass(frozen=True)
class ActionPlan:
    scan: str
    permanent: bool
    entries: tuple[PlanEntry, ...]
    refused: tuple[Refusal, ...]
    total_bytes: int
    trash_bytes: int
    freed_bytes: int
    location: str | None = None


def plan_id(plan: ActionPlan) -> str:
    """A fingerprint of what will happen; request order does not matter."""
    body = {
        "scan": plan.scan,
        "permanent": plan.permanent,
        "location": plan.location,
        # Path and size too: an in-place targeted rescan must invalidate a review.
        "entries": sorted(
            [e.item_id, e.method.value, e.path or "", e.size_bytes]
            for e in plan.entries
        ),
    }
    return hashlib.sha256(json.dumps(body, sort_keys=True).encode()).hexdigest()


def plan_to_json(plan: ActionPlan) -> dict[str, JsonValue]:
    return {
        "plan_id": plan_id(plan),
        "scan": plan.scan,
        "permanent": plan.permanent,
        "location": plan.location,
        "entries": [
            {
                "item_id": e.item_id,
                "name": e.name,
                "path": e.path,
                "size": e.size_bytes,
                "method": e.method.value,
                "undo": e.undo,
                "warnings": list(e.warnings),
            }
            for e in plan.entries
        ],
        "refused": [
            {"item_id": r.item_id, "name": r.name, "reason": r.reason}
            for r in plan.refused
        ],
        "total_bytes": plan.total_bytes,
        "trash_bytes": plan.trash_bytes,
        "freed_bytes": plan.freed_bytes,
    }
