"""Docker images, volumes, and build cache, as Docker itself reports them."""

import json
import re
from datetime import datetime
from typing import Any, Final

from storage_cleanup.scan.categories import DOCKER
from storage_cleanup.scan.context import CollectorResult, ScanContext
from storage_cleanup.scan.ids import item_id
from storage_cleanup.scan.model import (
    AgeSignal,
    Item,
    ItemKind,
    JsonValue,
    SizeSource,
    Skipped,
)

COLLECTOR: Final = "docker"
_UNITS: Final = {"b": 1, "kb": 10**3, "mb": 10**6, "gb": 10**9, "tb": 10**12}
_SIZE: Final = re.compile(r"^\s*([0-9.]+)\s*([kmgt]?b)", re.IGNORECASE)


def parse_docker_size(text: str) -> int:
    """Parse Docker's decimal sizes such as ``1.2GB`` or ``512kB (12%)``."""
    match = _SIZE.match(text)
    if not match:
        return 0
    return round(float(match.group(1)) * _UNITS[match.group(2).lower()])


def _parse_time(text: str) -> float | None:
    parts = text.split()
    try:
        return datetime.strptime(
            " ".join(parts[:3]), "%Y-%m-%d %H:%M:%S %z"
        ).timestamp()
    except (ValueError, IndexError):
        return None


def _json_lines(text: str) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for line in text.splitlines():
        try:
            record = json.loads(line)
        except ValueError:
            continue
        if isinstance(record, dict):
            records.append(record)
    return records


def _item(
    kind: str,
    key: str,
    name: str,
    size: int,
    parent: str | None,
    details: dict[str, JsonValue],
    age: tuple[AgeSignal, float] | None = None,
) -> Item:
    return Item(
        id=item_id(kind, key),
        kind=kind,
        name=name,
        path=None,
        size_bytes=size,
        size_source=SizeSource.TOOL_REPORTED,
        categories=(DOCKER,),
        age_signal=age[0] if age else None,
        age_timestamp=age[1] if age else None,
        parent_id=parent,
        details=details,
    )


def _image_refs(image: dict[str, Any]) -> set[str]:
    repo, tag, image_id = image.get("Repository"), image.get("Tag"), image.get("ID", "")
    refs = {image_id}
    if repo and repo != "<none>":
        refs.add(f"{repo}:{tag}")
        if tag == "latest":
            refs.add(repo)
    return refs


def collect_docker(ctx: ScanContext) -> CollectorResult:
    if ctx.run(["docker", "info", "--format", "{{json .ServerVersion}}"]).returncode:
        return CollectorResult(
            skipped=(Skipped("", "Docker not running or not installed", COLLECTOR),)
        )
    skipped: list[Skipped] = []

    def run(*args: str) -> str:
        result = ctx.run(["docker", *args])
        if result.returncode != 0:
            reason = (
                f"docker {' '.join(args[:2])} failed: {result.stderr.strip()[:200]}"
            )
            skipped.append(Skipped("", reason, COLLECTOR))
            return ""
        return result.stdout

    df = {
        r.get("Type"): r
        for r in _json_lines(run("system", "df", "--format", "{{json .}}"))
    }
    total = _item(
        ItemKind.DOCKER_TOTAL,
        "docker",
        "Docker (as reported by Docker)",
        sum(parse_docker_size(r.get("Size", "")) for r in df.values()),
        None,
        {
            str(t): {"size": r.get("Size"), "reclaimable": r.get("Reclaimable")}
            for t, r in df.items()
        },
    )
    items: list[Item] = [total]

    containers = _json_lines(run("ps", "--all", "--format", "{{json .}}"))
    for image in _json_lines(run("image", "ls", "--all", "--format", "{{json .}}")):
        refs = _image_refs(image)
        using = [
            c
            for c in containers
            if c.get("Image") in refs
            or any(
                r and str(c.get("Image", "")).startswith(r) for r in refs if len(r) >= 6
            )
        ]
        used_times = [
            t for c in using if (t := _parse_time(c.get("CreatedAt", ""))) is not None
        ]
        created = _parse_time(image.get("CreatedAt", ""))
        age: tuple[AgeSignal, float] | None = None
        if used_times:
            age = (AgeSignal.LAST_CONTAINER_USE, max(used_times))
        elif created is not None:
            age = (AgeSignal.CREATED, created)
        repo, tag = image.get("Repository", "<none>"), image.get("Tag", "<none>")
        name = f"{repo}:{tag}" if repo != "<none>" else str(image.get("ID"))
        items.append(
            _item(
                ItemKind.DOCKER_IMAGE,
                str(image.get("ID")),
                name,
                parse_docker_size(image.get("Size", "")),
                total.id,
                {
                    "image_id": image.get("ID"),
                    "containers": len(using),
                    "running": any(c.get("State") == "running" for c in using),
                },
                age,
            )
        )

    volumes_text = run("system", "df", "-v", "--format", "{{json .Volumes}}")
    try:
        volumes = json.loads(volumes_text) if volumes_text.strip() else []
    except ValueError:
        volumes = []
    for volume in volumes if isinstance(volumes, list) else []:
        items.append(
            _item(
                ItemKind.DOCKER_VOLUME,
                str(volume.get("Name")),
                str(volume.get("Name")),
                parse_docker_size(str(volume.get("Size", ""))),
                total.id,
                {"links": volume.get("Links"), "report_only": True},
            )
        )

    build_cache = df.get("Build Cache")
    if build_cache:
        items.append(
            _item(
                ItemKind.DOCKER_BUILD_CACHE,
                "build-cache",
                "Docker build cache",
                parse_docker_size(build_cache.get("Size", "")),
                total.id,
                {"reclaimable": build_cache.get("Reclaimable")},
            )
        )
    return CollectorResult(items=tuple(items), skipped=tuple(skipped))
