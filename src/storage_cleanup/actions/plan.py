"""Turn the user's queue into an exact plan, refusing what may not be removed."""

from collections.abc import Sequence
from pathlib import Path

from storage_cleanup.actions.model import (
    FREES_NOW,
    ActionPlan,
    Method,
    PlanEntry,
    Refusal,
)
from storage_cleanup.actions.verify import allowed_roots
from storage_cleanup.scan.model import Item, ItemKind, Scan

NOT_REMOVABLE = "Not removable from the page."
OUTSIDE_ROOTS = "Outside the folders this tool may change."
VOLUME_REASON = (
    "Docker volumes can hold data that cannot be recreated; remove them with "
    "Docker yourself."
)


def removal_method(item: Item, permanent: bool) -> Method | None:
    """How an item would be removed, or None if the page never removes it."""
    if item.kind == ItemKind.DOCKER_IMAGE:
        return Method.DOCKER_IMAGE
    if item.kind == ItemKind.DOCKER_BUILD_CACHE:
        return Method.DOCKER_BUILD_CACHE
    if item.kind == ItemKind.BREW_FORMULA:
        return Method.BREW_FORMULA
    if item.kind == ItemKind.BREW_CASK:
        return Method.BREW_CASK
    if item.kind in (ItemKind.DOCKER_VOLUME, ItemKind.DOCKER_TOTAL):
        return None
    if item.path is None:
        return None
    return Method.DELETE if permanent else Method.TRASH


def _undo(item: Item, method: Method) -> str:
    match method:
        case Method.TRASH:
            return "Put Back from the Trash in Finder."
        case Method.DELETE:
            return "Cannot be undone."
        case Method.DOCKER_IMAGE:
            return "Pull or build the image again."
        case Method.DOCKER_BUILD_CACHE:
            return "Rebuilt the next time images are built."
        case Method.BREW_FORMULA:
            return f"Reinstall with brew install {item.name}."
        case Method.BREW_CASK:
            return f"Reinstall with brew install --cask {item.name}."


def _warnings(item: Item) -> tuple[str, ...]:
    d = item.details
    warnings: list[str] = []
    if item.kind == ItemKind.REPO:
        if d.get("dirty"):
            warnings.append("Has uncommitted changes.")
        unpushed = d.get("unpushed_commits")
        if isinstance(unpushed, int) and unpushed > 0:
            noun = "commit that is" if unpushed == 1 else "commits that are"
            warnings.append(f"Has {unpushed} {noun} not pushed.")
        if d.get("has_remote") is False:
            warnings.append("No remote: this may be the only copy.")
    if item.kind == ItemKind.BREW_FORMULA and d.get("leaf") is False:
        warnings.append("Other formulae depend on this; Homebrew may refuse.")
    return tuple(warnings)


def _ancestor_in(item: Item, by_id: dict[str, Item], wanted: set[str]) -> Item | None:
    seen: set[str] = set()
    parent_id = item.parent_id
    while parent_id is not None and parent_id not in seen:
        if parent_id in wanted:
            return by_id[parent_id]
        seen.add(parent_id)
        parent = by_id.get(parent_id)
        parent_id = parent.parent_id if parent is not None else None
    return None


def _outside_roots(item: Item, scan: Scan, brew_prefix: Path | None) -> bool:
    if item.path is None:
        return False
    path = Path(item.path)
    return not any(
        path != root and path.is_relative_to(root)
        for root in allowed_roots(scan, brew_prefix)
    )


def build_plan(
    scan: Scan,
    scan_name: str,
    item_ids: Sequence[str],
    *,
    permanent: bool,
    brew_prefix: Path | None = None,
) -> ActionPlan:
    """The exact plan for these items: entries to act on, and refusals with reasons."""
    by_id = {i.id: i for i in scan.items}
    entries: list[PlanEntry] = []
    refused: list[Refusal] = []
    seen: set[str] = set()
    candidates: list[tuple[Item, Method]] = []

    for item_id in item_ids:
        if item_id in seen:
            refused.append(
                Refusal(
                    item_id,
                    by_id[item_id].name if item_id in by_id else item_id,
                    "Listed twice.",
                )
            )
            continue
        seen.add(item_id)
        item = by_id.get(item_id)
        if item is None:
            refused.append(Refusal(item_id, item_id, "Not in this scan."))
            continue
        if item.protected_reason is not None:
            refused.append(
                Refusal(item_id, item.name, f"Protected: {item.protected_reason}")
            )
            continue
        if item.kind == ItemKind.DOCKER_VOLUME:
            refused.append(Refusal(item_id, item.name, VOLUME_REASON))
            continue
        method = removal_method(item, permanent)
        if method is None:
            refused.append(Refusal(item_id, item.name, NOT_REMOVABLE))
            continue
        if method in (Method.TRASH, Method.DELETE) and _outside_roots(
            item, scan, brew_prefix
        ):
            refused.append(Refusal(item_id, item.name, OUTSIDE_ROOTS))
            continue
        candidates.append((item, method))

    wanted = {item.id for item, _ in candidates}
    for item, method in candidates:
        ancestor = _ancestor_in(item, by_id, wanted)
        if ancestor is not None:
            refused.append(
                Refusal(item.id, item.name, f"Already included in {ancestor.name}.")
            )
            continue
        entries.append(
            PlanEntry(
                item_id=item.id,
                name=item.name,
                path=item.path,
                size_bytes=item.size_bytes,
                method=method,
                undo=_undo(item, method),
                warnings=_warnings(item),
            )
        )

    trash = sum(e.size_bytes for e in entries if e.method == Method.TRASH)
    freed = sum(e.size_bytes for e in entries if e.method in FREES_NOW)
    return ActionPlan(
        scan=scan_name,
        permanent=permanent,
        entries=tuple(entries),
        refused=tuple(refused),
        total_bytes=trash + freed,
        trash_bytes=trash,
        freed_bytes=freed,
    )
