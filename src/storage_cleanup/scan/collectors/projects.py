"""Git repositories: your code against regenerable folders, and unsaved work."""

import os
from pathlib import Path
from typing import Final

from storage_cleanup.scan.categories import PROJECTS
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

COLLECTOR: Final = "projects"
MAX_REPO_DEPTH: Final = 4
REGENERABLE_MIN_BYTES: Final = 1024**2
REGENERABLE_DIRS: Final = frozenset(
    {
        "node_modules",
        ".venv",
        "venv",
        ".next",
        ".nuxt",
        "dist",
        "build",
        ".gradle",
        ".tox",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
        "__pycache__",
        "DerivedData",
        "target",
    }
)


def _subdirs(path: Path) -> list[Path]:
    try:
        return sorted(
            Path(e.path) for e in os.scandir(path) if e.is_dir(follow_symlinks=False)
        )
    except OSError:
        return []


def find_repos(root: Path) -> list[Path]:
    """Folders containing ``.git`` up to MAX_REPO_DEPTH, without descending into one."""
    repos: list[Path] = []
    stack: list[tuple[Path, int]] = [(root, 0)]
    while stack:
        current, depth = stack.pop()
        if (current / ".git").exists():
            repos.append(current)
            continue
        if depth >= MAX_REPO_DEPTH:
            continue
        for child in _subdirs(current):
            if child.name.startswith(".") or child.name == "node_modules":
                continue
            stack.append((child, depth + 1))
    return sorted(repos)


def _regenerable(repo: Path) -> list[Path]:
    found: list[Path] = []
    stack = [repo]
    while stack:
        current = stack.pop()
        for child in _subdirs(current):
            name = child.name
            if name == ".git":
                continue
            if name in REGENERABLE_DIRS and (
                name != "target" or (current / "Cargo.toml").exists()
            ):
                found.append(child)
                continue
            stack.append(child)
    return sorted(found)


def _git(ctx: ScanContext, repo: Path, *args: str) -> str | None:
    result = ctx.run(_git_args(repo, *args))
    return result.stdout.strip() if result.returncode == 0 else None


def _git_args(repo: Path, *args: str) -> list[str]:
    # --no-optional-locks stops `git status` from refreshing .git/index, so the
    # scan never writes inside a repository or blocks a concurrent commit.
    return ["git", "--no-optional-locks", "-C", str(repo), *args]


def _is_ignored(ctx: ScanContext, repo: Path, folder: Path) -> bool:
    """True when git ignores ``folder``; committed folders are not regenerable."""
    result = ctx.run(_git_args(repo, "check-ignore", "-q", "--", str(folder)))
    return result.returncode == 0


def collect_projects(ctx: ScanContext) -> CollectorResult:
    items: list[Item] = []
    skipped: list[Skipped] = []
    for root in ctx.workspace_roots:
        for repo in find_repos(root):
            m, sk = measure(repo, collector=COLLECTOR)
            skipped.extend(sk)
            regenerable_bytes = 0
            for folder in _regenerable(repo):
                if not _is_ignored(ctx, repo, folder):
                    continue
                rm, rsk = measure(folder, collector=COLLECTOR)
                skipped.extend(rsk)
                regenerable_bytes += rm.size_bytes
                if rm.size_bytes >= REGENERABLE_MIN_BYTES:
                    items.append(
                        Item(
                            id=item_id(ItemKind.REGENERABLE, str(folder)),
                            kind=ItemKind.REGENERABLE,
                            name=f"{repo.name}/{folder.relative_to(repo)}",
                            path=str(folder),
                            size_bytes=rm.size_bytes,
                            size_source=SizeSource.ALLOCATED,
                            categories=(PROJECTS,),
                            age_signal=AgeSignal.LAST_MODIFIED
                            if rm.newest_mtime
                            else None,
                            age_timestamp=rm.newest_mtime,
                            file_count=rm.file_count,
                            details={"pattern": folder.name},
                        )
                    )
            last_commit = _git(ctx, repo, "log", "-1", "--format=%ct")
            age: tuple[AgeSignal, float] | None = None
            if last_commit and last_commit.isdigit():
                age = (AgeSignal.LAST_COMMIT, float(last_commit))
            elif m.newest_mtime is not None:
                age = (AgeSignal.LAST_MODIFIED, m.newest_mtime)
            unpushed = _git(
                ctx, repo, "rev-list", "--count", "--branches", "--not", "--remotes"
            )
            details: dict[str, JsonValue] = {
                "dirty": bool(_git(ctx, repo, "status", "--porcelain")),
                "unpushed_commits": int(unpushed)
                if unpushed and unpushed.isdigit()
                else None,
                "has_remote": bool(_git(ctx, repo, "remote")),
                "regenerable_bytes": regenerable_bytes,
            }
            items.append(
                Item(
                    id=item_id(ItemKind.REPO, str(repo)),
                    kind=ItemKind.REPO,
                    name=repo.name,
                    path=str(repo),
                    size_bytes=m.size_bytes,
                    size_source=SizeSource.ALLOCATED,
                    categories=(PROJECTS,),
                    age_signal=age[0] if age else None,
                    age_timestamp=age[1] if age else None,
                    file_count=m.file_count,
                    details=details,
                )
            )
    return CollectorResult(items=tuple(items), skipped=tuple(skipped))
