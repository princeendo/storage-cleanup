import subprocess
from pathlib import Path

from storage_cleanup.scan.collectors.projects import collect_projects
from storage_cleanup.scan.context import subprocess_runner
from storage_cleanup.scan.model import AgeSignal, Item
from tests.helpers import make_ctx, make_file

MIB = 1024**2


def git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@t", *args],
        check=True,
        capture_output=True,
        text=True,
        env={
            "GIT_AUTHOR_DATE": "2025-01-02T03:04:05Z",
            "GIT_COMMITTER_DATE": "2025-01-02T03:04:05Z",
            "PATH": "/usr/bin:/bin:/opt/homebrew/bin",
        },
    ).stdout


def make_repo(path: Path) -> Path:
    path.mkdir(parents=True)
    git(path, "init", "-q")
    make_file(path / "README.md", 100)
    git(path, "add", ".")
    git(path, "commit", "-q", "-m", "init")
    return path


def items_for(home: Path) -> tuple[Item, ...]:
    return collect_projects(make_ctx(home, subprocess_runner)).items


def test_repo_facts(tmp_path: Path) -> None:
    home = tmp_path / "h"
    repo = make_repo(home / "workspace" / "personal" / "app")
    make_file(repo / "untracked.txt", 10)
    [item] = [i for i in items_for(home) if i.kind == "repo"]
    assert item.path == str(repo)
    assert item.categories == ("projects",)
    assert item.age_signal is AgeSignal.LAST_COMMIT
    assert item.age_timestamp == 1735787045
    assert item.details["dirty"] is True
    assert item.details["unpushed_commits"] == 1
    assert item.details["has_remote"] is False


def test_regenerable_dirs_summed_not_descended(tmp_path: Path) -> None:
    home = tmp_path / "h"
    repo = make_repo(home / "workspace" / "web")
    make_file(repo / "node_modules" / "a" / "big.js", 2 * MIB)
    make_file(repo / "node_modules" / "a" / "node_modules" / "b" / "x.js", 10)
    make_file(repo / "target" / "debug" / "bin", 2 * MIB)
    make_file(repo / "src" / "__pycache__" / "m.pyc", 1000)
    items = items_for(home)
    [repo_item] = [i for i in items if i.kind == "repo"]
    regenerable = [i for i in items if i.kind == "regenerable"]
    assert [i.path for i in regenerable] == [str(repo / "node_modules")]
    assert regenerable[0].details["pattern"] == "node_modules"
    assert regenerable[0].categories == ("projects",)
    nm = regenerable[0].size_bytes
    assert repo_item.details["regenerable_bytes"] > nm  # includes small __pycache__
    assert repo_item.details["regenerable_bytes"] < nm + MIB  # target/ not counted


def test_cargo_target_counted(tmp_path: Path) -> None:
    home = tmp_path / "h"
    repo = make_repo(home / "workspace" / "rusty")
    make_file(repo / "Cargo.toml", 10)
    make_file(repo / "target" / "debug" / "bin", 2 * MIB)
    regenerable = [i for i in items_for(home) if i.kind == "regenerable"]
    assert [i.details["pattern"] for i in regenerable] == ["target"]


def test_nested_repo_not_descended(tmp_path: Path) -> None:
    home = tmp_path / "h"
    outer = make_repo(home / "workspace" / "outer")
    make_repo(outer / "vendor" / "inner")
    repos = [i.path for i in items_for(home) if i.kind == "repo"]
    assert repos == [str(outer)]


def test_repo_without_commits_uses_mtime(tmp_path: Path) -> None:
    home = tmp_path / "h"
    repo = home / "workspace" / "empty"
    repo.mkdir(parents=True)
    git(repo, "init", "-q")
    make_file(repo / "a.txt", 10, mtime=5000)
    [item] = [i for i in items_for(home) if i.kind == "repo"]
    assert item.age_signal is AgeSignal.LAST_MODIFIED


def test_no_workspace_is_empty(tmp_path: Path) -> None:
    assert items_for(tmp_path / "h") == ()
