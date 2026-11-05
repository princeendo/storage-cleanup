"""The scan file's data model and its JSON form."""

import json
from dataclasses import asdict, dataclass, field
from enum import StrEnum
from typing import Any, Final

SCHEMA_VERSION: Final = 1

type JsonValue = (
    str | int | float | bool | list[JsonValue] | dict[str, JsonValue] | None
)


class ItemKind:
    FOLDER: Final = "folder"
    FILE: Final = "file"
    APP: Final = "app"
    APP_DATA: Final = "app-data"
    BREW_FORMULA: Final = "brew-formula"
    BREW_CASK: Final = "brew-cask"
    PACKAGE: Final = "package"
    DOCKER_TOTAL: Final = "docker-total"
    DOCKER_IMAGE: Final = "docker-image"
    DOCKER_VOLUME: Final = "docker-volume"
    DOCKER_BUILD_CACHE: Final = "docker-build-cache"
    AI_TOOL_PATH: Final = "ai-tool-path"
    AI_SESSIONS: Final = "ai-sessions"
    CACHE: Final = "cache"
    REPO: Final = "repo"
    REGENERABLE: Final = "regenerable"


class AgeSignal(StrEnum):
    LAST_OPENED = "last-opened"
    LAST_MODIFIED = "last-modified"
    LAST_COMMIT = "last-commit"
    INSTALLED = "installed"
    CREATED = "created"
    LAST_CONTAINER_USE = "last-container-use"


class SizeSource(StrEnum):
    ALLOCATED = "allocated"
    TOOL_REPORTED = "tool-reported"


@dataclass(frozen=True)
class Item:
    id: str
    kind: str
    name: str
    path: str | None
    size_bytes: int
    size_source: SizeSource
    categories: tuple[str, ...]
    age_signal: AgeSignal | None = None
    age_timestamp: float | None = None
    file_count: int | None = None
    parent_id: str | None = None
    location: str | None = None
    protected_reason: str | None = None
    details: dict[str, JsonValue] = field(default_factory=dict)


@dataclass(frozen=True)
class Skipped:
    path: str
    reason: str
    collector: str


@dataclass(frozen=True)
class DiskUsage:
    volume: str
    total_bytes: int
    used_bytes: int
    free_bytes: int


@dataclass(frozen=True)
class Scan:
    schema_version: int
    created_at: str
    home: str
    disk: DiskUsage
    trash_bytes: int | None
    local_snapshots: tuple[str, ...]
    items: tuple[Item, ...]
    skipped: tuple[Skipped, ...]
    totals_by_category: dict[str, int]
    totals_by_location: dict[str, int]


def scan_to_json(scan: Scan) -> str:
    return json.dumps(asdict(scan), indent=1, ensure_ascii=True, sort_keys=True)


def _item_from_dict(data: dict[str, Any]) -> Item:
    age = data.get("age_signal")
    return Item(
        id=data["id"],
        kind=data["kind"],
        name=data["name"],
        path=data.get("path"),
        size_bytes=data["size_bytes"],
        size_source=SizeSource(data["size_source"]),
        categories=tuple(data["categories"]),
        age_signal=AgeSignal(age) if age is not None else None,
        age_timestamp=data.get("age_timestamp"),
        file_count=data.get("file_count"),
        parent_id=data.get("parent_id"),
        location=data.get("location"),
        protected_reason=data.get("protected_reason"),
        details=data.get("details", {}),
    )


def scan_from_json(text: str) -> Scan:
    data = json.loads(text)
    version = data.get("schema_version")
    if version != SCHEMA_VERSION:
        raise ValueError(
            f"Unsupported scan schema_version {version!r}; expected {SCHEMA_VERSION}"
        )
    return Scan(
        schema_version=version,
        created_at=data["created_at"],
        home=data["home"],
        disk=DiskUsage(**data["disk"]),
        trash_bytes=data["trash_bytes"],
        local_snapshots=tuple(data["local_snapshots"]),
        items=tuple(_item_from_dict(i) for i in data["items"]),
        skipped=tuple(Skipped(**s) for s in data["skipped"]),
        totals_by_category=data["totals_by_category"],
        totals_by_location=data["totals_by_location"],
    )
