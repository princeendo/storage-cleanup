import json
import shutil
from collections.abc import Sequence
from pathlib import Path

import pytest

from storage_cleanup.actions.model import Method
from storage_cleanup.actions.plan import build_plan
from storage_cleanup.actions.run import delete_path, run_plan
from storage_cleanup.scan.context import CommandResult
from storage_cleanup.scan.model import Item, Scan
from tests.actions.test_plan import CACHE, IMAGE, TOTAL
from tests.actions.test_verify import scan_paths
from tests.helpers import FakeRunner, make_file, ok, path_item

TRASH = "/usr/bin/trash"


class MovingTrash:
    """Pretends to be /usr/bin/trash: records calls and removes the path."""

    def __init__(self, fail: set[str] | None = None) -> None:
        self.calls: list[tuple[str, ...]] = []
        self.fail = fail or set()

    def __call__(self, args: Sequence[str]) -> CommandResult:
        self.calls.append(tuple(args))
        if args[0] == TRASH:
            if args[1] in self.fail:
                return CommandResult(1, "", "trash: permission denied")
            delete_path(Path(args[1]))
            return ok("")
        return ok("")


def setup(tmp_path: Path) -> tuple[Path, Scan, list[Path]]:
    home = tmp_path / "h"
    paths = [
        make_file(home / "Downloads" / "a.dmg", 1000),
        make_file(home / "Downloads" / "b.dmg", 2000),
        make_file(home / "Movies" / "m" / "c.mov", 3000).parent,
    ]
    return home, scan_paths(home, paths), paths


def ids_for(scan: Scan, paths: Sequence[Path]) -> list[str]:
    by_path = {i.path: i.id for i in scan.items}
    return [by_path[str(p)] for p in paths]


def test_trash_success_requires_path_gone(tmp_path: Path) -> None:
    _home, scan, paths = setup(tmp_path)
    plan = build_plan(scan, "S.json", ids_for(scan, paths[:1]), permanent=False)
    runner = FakeRunner({(TRASH, str(paths[0])): ok("")})  # says ok, moves nothing
    result = run_plan(
        plan, scan, runner=runner, brew_prefix=None, log_dir=tmp_path / "log"
    )
    [entry] = result.results
    assert entry.status == "failed"
    assert entry.detail == "The trash command finished but the item is still there."
    assert paths[0].exists()


def test_trash_moves_and_counts(tmp_path: Path) -> None:
    _home, scan, paths = setup(tmp_path)
    plan = build_plan(scan, "S.json", ids_for(scan, paths), permanent=False)
    runner = MovingTrash()
    result = run_plan(
        plan, scan, runner=runner, brew_prefix=None, log_dir=tmp_path / "log"
    )
    assert [r.status for r in result.results] == ["done", "done", "done"]
    assert result.moved_to_trash_bytes == plan.trash_bytes
    assert result.freed_bytes == 0
    assert [c[1] for c in runner.calls] == [str(p) for p in paths]


def test_permanent_delete_removes_files_and_dirs_but_skips_links(
    tmp_path: Path,
) -> None:
    home = tmp_path / "h"
    outside = make_file(tmp_path / "keep" / "precious.txt", 100)
    link = home / "Downloads" / "link"
    link.parent.mkdir(parents=True)
    link.symlink_to(outside)
    folder = make_file(home / "Old" / "deep" / "x", 500).parent.parent
    single = make_file(home / "Downloads" / "z.zip", 50)
    scan = scan_paths(home, [link, folder, single])
    plan = build_plan(
        scan, "S.json", ids_for(scan, [link, folder, single]), permanent=True
    )
    runner = FakeRunner()
    result = run_plan(
        plan, scan, runner=runner, brew_prefix=None, log_dir=tmp_path / "log"
    )
    assert [r.status for r in result.results] == ["skipped", "done", "done"]
    assert result.results[0].detail == "Is a link; there is nothing here to free."
    assert link.is_symlink() and not folder.exists() and not single.exists()
    assert outside.read_text() == "x" * 100
    assert runner.calls == []


def test_delete_path_refuses_nothing_outside(tmp_path: Path) -> None:
    target = make_file(tmp_path / "t" / "keep", 10).parent
    link = tmp_path / "dirlink"
    link.symlink_to(target)
    delete_path(link)
    assert target.exists() and not link.exists()


def docker_brew_scan(tmp_path: Path) -> tuple[Scan, list[Item]]:
    home = tmp_path / "h"
    home.mkdir(parents=True)
    formula = path_item(
        "/opt/homebrew/Cellar/jq", 10, ("homebrew",), kind="brew-formula"
    )
    cask = path_item(
        "/opt/homebrew/Caskroom/affine", 10, ("homebrew",), kind="brew-cask"
    )
    items = [TOTAL, IMAGE, CACHE, formula, cask]
    from tests.web.test_views import scan_of

    return scan_of(items, used=10**9), items


def test_docker_and_brew_commands(tmp_path: Path) -> None:
    scan, items = docker_brew_scan(tmp_path)
    prefix = tmp_path / "brew"
    plan = build_plan(scan, "S.json", [i.id for i in items[1:]], permanent=False)
    runner = FakeRunner(
        {
            ("docker", "image", "rm", "--", "sha256:abc"): ok(""),
            ("docker", "builder", "prune", "--force"): ok(""),
            (str(prefix / "bin" / "brew"), "uninstall", "--formula", "jq"): ok(""),
            (str(prefix / "bin" / "brew"), "uninstall", "--cask", "affine"): ok(""),
        }
    )
    result = run_plan(
        plan, scan, runner=runner, brew_prefix=prefix, log_dir=tmp_path / "log"
    )
    assert [r.status for r in result.results] == ["done"] * 4
    assert {r.method for r in result.results} == {
        Method.DOCKER_IMAGE,
        Method.DOCKER_BUILD_CACHE,
        Method.BREW_FORMULA,
        Method.BREW_CASK,
    }
    assert result.freed_bytes == plan.freed_bytes


def test_brew_without_prefix_fails_cleanly(tmp_path: Path) -> None:
    scan, items = docker_brew_scan(tmp_path)
    plan = build_plan(scan, "S.json", [items[3].id], permanent=False)
    result = run_plan(
        plan, scan, runner=FakeRunner(), brew_prefix=None, log_dir=tmp_path / "log"
    )
    [entry] = result.results
    assert (entry.status, entry.detail) == ("failed", "Homebrew is not installed.")


def test_failure_does_not_stop_later_entries(tmp_path: Path) -> None:
    _home, scan, paths = setup(tmp_path)
    plan = build_plan(scan, "S.json", ids_for(scan, paths), permanent=False)
    runner = MovingTrash(fail={str(paths[0])})
    result = run_plan(
        plan, scan, runner=runner, brew_prefix=None, log_dir=tmp_path / "log"
    )
    assert [r.status for r in result.results] == ["failed", "done", "done"]
    assert result.results[0].detail == "trash: permission denied"


def test_skipped_entries_are_not_touched(tmp_path: Path) -> None:
    _home, scan, paths = setup(tmp_path)
    plan = build_plan(scan, "S.json", ids_for(scan, paths), permanent=False)
    shutil.rmtree(paths[2])
    runner = MovingTrash()
    result = run_plan(
        plan, scan, runner=runner, brew_prefix=None, log_dir=tmp_path / "log"
    )
    assert [r.status for r in result.results] == ["done", "done", "skipped"]
    assert result.results[2].detail == "No longer exists."
    assert str(paths[2]) not in [c[1] for c in runner.calls]


def test_log_written_and_totals(tmp_path: Path) -> None:
    _home, scan, paths = setup(tmp_path)
    plan = build_plan(scan, "S.json", ids_for(scan, paths), permanent=False)
    result = run_plan(
        plan, scan, runner=MovingTrash(), brew_prefix=None, log_dir=tmp_path / "log"
    )
    log = json.loads(Path(result.log_path).read_text())
    assert log["plan_id"] == result.plan_id
    assert [r["status"] for r in log["results"]] == ["done", "done", "done"]
    assert log["free_before"] > 0 and log["free_after"] > 0
    assert Path(result.log_path).parent == tmp_path / "log"


# --- Final review fixes -----------------------------------------------------


def test_path_is_rechecked_right_before_acting(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from storage_cleanup.actions import run as run_mod

    home = tmp_path / "h"
    target = make_file(home / "proj" / "sub" / "data.bin", 5000).parent
    scan = scan_paths(home, [target])
    plan = build_plan(scan, "S.json", ids_for(scan, [target]), permanent=True)
    outside = make_file(tmp_path / "outside" / "proj" / "sub" / "PRECIOUS.bin", 5000)
    shutil.rmtree(home / "proj")
    (home / "proj").symlink_to(tmp_path / "outside" / "proj")
    monkeypatch.setattr(run_mod, "verify_entry", lambda *a, **k: None)  # the race
    result = run_plan(
        plan, scan, runner=FakeRunner(), brew_prefix=None, log_dir=tmp_path / "log"
    )
    [entry] = result.results
    assert (entry.status, entry.detail) == (
        "skipped",
        "A folder in its path is now a link.",
    )
    assert outside.exists()


def test_log_exists_before_the_first_action(tmp_path: Path) -> None:
    _home, scan, paths = setup(tmp_path)
    plan = build_plan(scan, "S.json", ids_for(scan, paths[:1]), permanent=False)
    seen: list[str] = []
    trash = MovingTrash()

    def runner(args: Sequence[str]) -> CommandResult:
        logs = list((tmp_path / "log").glob("*.json"))
        seen.append(json.loads(logs[0].read_text())["state"] if logs else "missing")
        return trash(args)

    result = run_plan(
        plan, scan, runner=runner, brew_prefix=None, log_dir=tmp_path / "log"
    )
    assert seen == ["running"]
    assert json.loads(Path(result.log_path).read_text())["state"] == "finished"


def test_unexpected_errors_are_recorded_and_logged(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from storage_cleanup.actions import run as run_mod

    _home, scan, paths = setup(tmp_path)
    plan = build_plan(scan, "S.json", ids_for(scan, paths[:2]), permanent=False)

    def boom(*args: object, **kwargs: object) -> str | None:
        raise RuntimeError("disk on fire")

    monkeypatch.setattr(run_mod, "verify_entry", boom)
    result = run_plan(
        plan, scan, runner=MovingTrash(), brew_prefix=None, log_dir=tmp_path / "log"
    )
    assert [r.status for r in result.results] == ["failed", "failed"]
    assert result.results[0].detail == "RuntimeError: disk on fire"
    assert Path(result.log_path).exists()


def test_two_runs_in_the_same_second_keep_both_logs(tmp_path: Path) -> None:
    _home, scan, paths = setup(tmp_path)
    log_dir = tmp_path / "log"
    for path in paths[:2]:
        plan = build_plan(scan, "S.json", ids_for(scan, [path]), permanent=False)
        run_plan(
            plan,
            scan,
            runner=MovingTrash(),
            brew_prefix=None,
            log_dir=log_dir,
            now=lambda: 1_800_000_000.0,
        )
    assert len(list(log_dir.glob("*.json"))) == 2


def test_cask_needing_sudo_says_so(tmp_path: Path) -> None:
    scan, items = docker_brew_scan(tmp_path)
    prefix = tmp_path / "brew"
    plan = build_plan(scan, "S.json", [items[4].id], permanent=False)
    sudo = CommandResult(1, "", "sudo: a terminal is required to read the password")
    runner = FakeRunner(
        {(str(prefix / "bin" / "brew"), "uninstall", "--cask", "affine"): sudo}
    )
    [entry] = run_plan(
        plan, scan, runner=runner, brew_prefix=prefix, log_dir=tmp_path / "log"
    ).results
    assert entry.status == "failed"
    assert entry.detail == (
        "Needs administrator rights. Uninstall it in Terminal with: "
        "brew uninstall --cask affine"
    )


def test_odd_package_names_are_refused(tmp_path: Path) -> None:
    from dataclasses import replace as dc_replace

    scan, items = docker_brew_scan(tmp_path)
    evil = dc_replace(items[3], name="--force")
    scan = dc_replace(
        scan, items=tuple(evil if i.id == evil.id else i for i in scan.items)
    )
    plan = build_plan(scan, "S.json", [evil.id], permanent=False)
    runner = FakeRunner()
    [entry] = run_plan(
        plan,
        scan,
        runner=runner,
        brew_prefix=tmp_path / "brew",
        log_dir=tmp_path / "log",
    ).results
    assert (entry.status, entry.detail) == (
        "failed",
        "Unexpected package name '--force'.",
    )
    assert runner.calls == []


def test_action_runner_never_reads_the_terminal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import subprocess

    from storage_cleanup.actions.run import action_runner

    captured: dict[str, object] = {}

    def fake_run(args: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        captured.update(kwargs)
        return subprocess.CompletedProcess(args, 0, "", "")

    monkeypatch.setattr(subprocess, "run", fake_run)
    assert action_runner(["true"]).returncode == 0
    assert captured["stdin"] is subprocess.DEVNULL
    assert captured["start_new_session"] is True
    assert captured["timeout"] == 900


def test_delete_results_have_no_notes(tmp_path: Path) -> None:
    _home, scan, paths = setup(tmp_path)
    plan = build_plan(scan, "S.json", ids_for(scan, paths[:1]), permanent=False)
    result = run_plan(
        plan, scan, runner=MovingTrash(), brew_prefix=None, log_dir=tmp_path / "log"
    )
    assert result.notes == ()
