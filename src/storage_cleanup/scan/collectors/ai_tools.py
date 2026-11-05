"""AI coding tools: how much each stores, and which sessions point at gone folders."""

import json
import os
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Final

from storage_cleanup.scan.categories import AI_TOOLING
from storage_cleanup.scan.collectors.home import path_item
from storage_cleanup.scan.context import CollectorResult, ScanContext
from storage_cleanup.scan.ids import item_id
from storage_cleanup.scan.measure import measure, measure_tree
from storage_cleanup.scan.model import (
    AgeSignal,
    Item,
    ItemKind,
    JsonValue,
    SizeSource,
    Skipped,
)


@dataclass(frozen=True)
class AiTool:
    key: str
    display_name: str
    paths: tuple[str, ...]


AI_TOOLS: Final[tuple[AiTool, ...]] = (
    AiTool("claude-code", "Claude Code", (".claude",)),
    AiTool("claude-desktop", "Claude desktop", ("Library/Application Support/Claude",)),
    AiTool("codex", "Codex", (".codex",)),
    AiTool("opencode", "OpenCode", (".local/share/opencode", ".config/opencode")),
    AiTool("cursor", "Cursor", (".cursor", "Library/Application Support/Cursor")),
)
CHILD_MIN_BYTES: Final = 1024**2
MAX_LINES: Final = 200


def _read_json_lines(path: Path, limit: int) -> list[Any]:
    records: list[Any] = []
    try:
        with path.open(encoding="utf-8", errors="replace") as f:
            for index, line in enumerate(f):
                if index >= limit:
                    break
                try:
                    records.append(json.loads(line))
                except ValueError:
                    continue
    except OSError:
        pass
    return records


def _claude_cwd(project: Path) -> str | None:
    for session in sorted(project.glob("*.jsonl")):
        for record in _read_json_lines(session, MAX_LINES):
            if isinstance(record, dict) and isinstance(record.get("cwd"), str):
                return str(record["cwd"])
    return None


def _codex_cwd(rollout: Path) -> str | None:
    records = _read_json_lines(rollout, 1)
    if records and isinstance(records[0], dict):
        payload = records[0].get("payload")
        if isinstance(payload, dict) and isinstance(payload.get("cwd"), str):
            return str(payload["cwd"])
    return None


def _cwd_details(cwd: str | None) -> dict[str, JsonValue]:
    return {"cwd": cwd, "cwd_exists": cwd is not None and Path(cwd).exists()}


def _claude_sessions(ctx: ScanContext, details: dict[str, JsonValue]) -> list[Item]:
    projects = ctx.home / ".claude" / "projects"
    items: list[Item] = []
    for project in sorted(p for p in projects.glob("*") if p.is_dir()):
        m, _ = measure(project, collector="ai-tools")
        items.append(
            Item(
                id=item_id(ItemKind.AI_SESSIONS, str(project)),
                kind=ItemKind.AI_SESSIONS,
                name=project.name,
                path=str(project),
                size_bytes=m.size_bytes,
                size_source=SizeSource.ALLOCATED,
                categories=(AI_TOOLING,),
                age_signal=AgeSignal.LAST_MODIFIED if m.newest_mtime else None,
                age_timestamp=m.newest_mtime,
                file_count=m.file_count,
                details={**details, **_cwd_details(_claude_cwd(project))},
            )
        )
    return items


def _codex_sessions(ctx: ScanContext, details: dict[str, JsonValue]) -> list[Item]:
    root = ctx.home / ".codex"
    groups: dict[str | None, list[os.stat_result]] = {}
    for rollout in sorted((root / "sessions").rglob("rollout-*.jsonl")):
        try:
            st = os.lstat(rollout)
        except OSError:
            continue
        groups.setdefault(_codex_cwd(rollout), []).append(st)
    items: list[Item] = []
    for cwd, stats in groups.items():
        newest = max(s.st_mtime for s in stats)
        items.append(
            Item(
                id=item_id(ItemKind.AI_SESSIONS, f"codex:{cwd}"),
                kind=ItemKind.AI_SESSIONS,
                name=f"Codex sessions in {cwd or 'an unknown folder'}",
                path=None,
                size_bytes=sum(s.st_blocks * 512 for s in stats),
                size_source=SizeSource.ALLOCATED,
                categories=(AI_TOOLING,),
                age_signal=AgeSignal.LAST_MODIFIED,
                age_timestamp=newest,
                file_count=len(stats),
                parent_id=item_id(ItemKind.AI_TOOL_PATH, str(root)),
                details={**details, **_cwd_details(cwd)},
            )
        )
    return items


def _opencode_projects(path: Path) -> dict[str, JsonValue]:
    count = 0
    missing: list[JsonValue] = []
    for project in sorted((path / "storage" / "project").glob("*.json")):
        try:
            worktree = json.loads(project.read_text()).get("worktree")
        except (OSError, ValueError, AttributeError):
            continue
        if not isinstance(worktree, str):
            continue
        count += 1
        if not Path(worktree).exists():
            missing.append(worktree)
    return {"projects": count, "missing_worktrees": missing}


def collect_ai_tools(ctx: ScanContext) -> CollectorResult:
    items: list[Item] = []
    skipped: list[Skipped] = []
    for tool in AI_TOOLS:
        details: dict[str, JsonValue] = {
            "tool": tool.key,
            "tool_name": tool.display_name,
        }
        for rel in tool.paths:
            path = ctx.home / rel
            if not path.exists():
                continue
            measured, sk = measure_tree(
                path,
                collector="ai-tools",
                max_item_depth=1,
                min_item_bytes=CHILD_MIN_BYTES,
            )
            skipped.extend(sk)
            m = measured.pop(path)
            tool_details = dict(details)
            if tool.key == "opencode" and rel == ".local/share/opencode":
                tool_details.update(_opencode_projects(path))
            items.append(
                Item(
                    id=item_id(ItemKind.AI_TOOL_PATH, str(path)),
                    kind=ItemKind.AI_TOOL_PATH,
                    name=f"{tool.display_name} ({rel})",
                    path=str(path),
                    size_bytes=m.size_bytes,
                    size_source=SizeSource.ALLOCATED,
                    categories=(AI_TOOLING,),
                    age_signal=AgeSignal.LAST_MODIFIED if m.newest_mtime else None,
                    age_timestamp=m.newest_mtime,
                    file_count=m.file_count,
                    details=tool_details,
                )
            )
            for child, cm in measured.items():
                items.append(
                    replace(path_item(child, cm, (AI_TOOLING,)), details=dict(details))
                )
        if tool.key == "claude-code":
            items.extend(_claude_sessions(ctx, details))
        if tool.key == "codex":
            items.extend(_codex_sessions(ctx, details))
    return CollectorResult(items=tuple(items), skipped=tuple(skipped))
