"""run_archive against a fake homelab that lives in tmp_path.

FakeLab answers each remote command by matching its argv (or a prefix of it).
By default it acts on a folder under tmp_path the way the homelab would, so
the copy, the listing and the move really happen, but only inside tmp_path.
"""

import json
import os
import shlex
import shutil
from collections.abc import Callable, Sequence
from pathlib import Path

from storage_cleanup.actions.archive import (
    ARCHIVE_TIMEOUT_SECONDS,
    FREE_MARGIN_BYTES,
    run_archive,
)
from storage_cleanup.actions.manifest import LIST_SCRIPT, local_manifest
from storage_cleanup.actions.model import ActionPlan
from storage_cleanup.actions.plan import build_plan
from storage_cleanup.actions.remote import REMOTE_PROGRAMS
from storage_cleanup.actions.run import ActionResult
from storage_cleanup.scan.context import CommandResult
from storage_cleanup.scan.model import Scan
from tests.actions.test_remote import UUID, make_config
from tests.actions.test_run import MovingTrash, ids_for, setup
from tests.helpers import make_file, ok

NOW = 1_793_900_000.0
LOCATION = "/mnt/HD/Disk1/mac-archive/laptop"
BATCH = f"{LOCATION}/20261105T173320Z"
OWNER = ["-o", "archiver", "-g", "users", "-m", "2770"]
type Answer = CommandResult | Callable[[list[str]], CommandResult]


def missing(path: str) -> CommandResult:
    return CommandResult(1, "", f"find: '{path}': No such file or directory")


def listing_of(folder: Path) -> str:
    """What LIST_SCRIPT prints for a folder, built from local_manifest."""
    records: list[str] = []
    hashes: list[str] = []
    for name in sorted(os.listdir(folder)):
        m = local_manifest(folder / name)
        records += [f"D\0{p}\0" for p in sorted(m.dirs)]
        records += [f"L\0{p}\0{t}\0" for p, t in sorted(m.links.items())]
        records += [f"F\0{p}\0{s}\0" for p, (s, _) in sorted(m.files.items())]
        hashes += [f"{h}  ./{p}\0" for p, (_, h) in sorted(m.files.items())]
    return "".join(records) + "H\0" + "".join(hashes)


class FakeLab:
    """A homelab stand-in rooted at a tmp_path folder; records every command."""

    def __init__(self, root: Path, *, hostname: str = "nas", uuid: str = UUID):
        self.root = root
        self.hostname = hostname
        self.uuid = uuid
        self.calls: list[list[str]] = []  # ssh argv after "sudo -n"; rsync as 3
        self.raw: list[tuple[list[str], float]] = []
        self.answers: list[tuple[list[str], Answer]] = []
        self.lost = False
        self.uploaded: dict[str, str] = {}  # batch files sent with rsync

    def on(self, prefix: list[str], answer: Answer) -> None:
        self.answers.insert(0, (prefix, answer))

    def at(self, remote_path: str) -> Path:
        return self.root / remote_path.lstrip("/")

    def __call__(self, args: Sequence[str], timeout: float) -> CommandResult:
        args = list(args)
        self.raw.append((args, timeout))
        if args[0] == "/usr/bin/rsync":
            argv = ["rsync", args[-2], args[-1]]
        else:
            assert args[0] == "/usr/bin/ssh"
            words = shlex.split(args[-1])
            argv = words[2:] if words[:2] == ["sudo", "-n"] else words
        self.calls.append(argv)
        if self.lost:
            return CommandResult(255, "", "Connection closed by remote host")
        for prefix, answer in self.answers:
            if argv[: len(prefix)] == prefix:
                return answer(argv) if callable(answer) else answer
        return self.default(argv)

    def default(self, argv: list[str]) -> CommandResult:
        program = argv[0]
        if program == "hostname":
            return ok(f"{self.hostname}\n")
        if program == "findmnt":
            return ok(f"{self.uuid}\n")
        if program == "df":
            return ok(f"Avail\n{10**13}\n")
        if program in ("install", "mkdir"):
            for path in argv[argv.index("--") + 1 :]:
                self.at(path).mkdir(parents=True, exist_ok=True)
            return ok("")
        if program == "rsync":
            return self.copy(argv[1], argv[2].split(":", 1)[1])
        if program == "sh":
            return ok(listing_of(self.at(argv[4])))
        if program == "find" and argv[2:] == ["-maxdepth", "0"]:
            exists = os.path.lexists(self.at(argv[1]))
            return ok(argv[1] + "\n") if exists else missing(argv[1])
        if program == "mv":
            source, target = self.at(argv[-2]), self.at(argv[-1])
            if not os.path.lexists(target):  # old coreutils: -n says 0 either way
                source.rename(target)
            return ok("")
        if program == "rmdir":
            try:
                self.at(argv[-1]).rmdir()
            except OSError as exc:
                return CommandResult(1, "", f"rmdir: {exc.strerror}")
            return ok("")
        return ok("")  # chown and find -exec chmod change nothing here

    def copy(self, source: str, dest_dir: str) -> CommandResult:
        target = self.at(dest_dir) / Path(source).name
        if ".incoming" not in dest_dir:
            self.uploaded[target.name] = Path(source).read_text()
        if os.path.islink(source):
            os.symlink(os.readlink(source), target)
        elif os.path.isdir(source):
            shutil.copytree(source, target, symlinks=True)
        else:
            shutil.copy2(source, target)
        return ok("")


def archive_plan(scan: Scan, paths: Sequence[Path]) -> ActionPlan:
    return build_plan(
        scan,
        "S.json",
        ids_for(scan, paths),
        permanent=False,
        action="archive",
        location=LOCATION,
    )


def run(
    tmp_path: Path, plan: ActionPlan, scan: Scan, lab: FakeLab, trash: MovingTrash
) -> ActionResult:
    return run_archive(
        plan,
        scan,
        config=make_config(),
        remote_runner=lab,
        runner=trash,
        brew_prefix=None,
        log_dir=tmp_path / "reports" / "actions",
        manifest_dir=tmp_path / "reports" / "archives",
        now=lambda: NOW,
    )


def final_of(path: Path) -> str:
    return f"{BATCH}/{str(path).removeprefix('/')}"


def guard() -> list[list[str]]:
    return [
        ["findmnt", "-no", "UUID", "--mountpoint", "/mnt/HD/Disk1"],
        ["df", "-B1", "--output=avail", "--", "/mnt/HD/Disk1"],
    ]


def item_calls(n: int, path: Path) -> list[list[str]]:
    holder = f"{BATCH}/.incoming/{n}"
    incoming = f"{holder}/{path.name}"
    final = final_of(path)
    return [
        *guard(),
        ["install", "-d", *OWNER, "--", holder],
        ["rsync", str(path), f"archiver@one.example:{holder}/"],
        ["sh", "-c", LIST_SCRIPT, "sh", holder],
        ["mkdir", "-p", "--", os.path.dirname(final)],
        ["find", final, "-maxdepth", "0"],
        ["mv", "-n", "-T", "--", incoming, final],
        ["find", incoming, "-maxdepth", "0"],
        ["find", final, "-maxdepth", "0"],
        ["rmdir", "--", holder],
    ]


def finish_calls() -> list[list[str]]:
    return [
        ["rsync", "MANIFEST.json", f"archiver@one.example:{BATCH}/"],
        ["rsync", "README.md", f"archiver@one.example:{BATCH}/"],
        ["chown", "-R", "--", "archiver:users", BATCH],
        [
            "find",
            BATCH,
            "(",
            "-type",
            "d",
            "-exec",
            "chmod",
            "2770",
            "{}",
            "+",
            ")",
            "-o",
            "(",
            "-type",
            "f",
            "-exec",
            "chmod",
            "660",
            "{}",
            "+",
            ")",
        ],
        ["rmdir", "--", f"{BATCH}/.incoming"],
    ]


def comparable(calls: list[list[str]]) -> list[list[str]]:
    """Temp-folder uploads are compared by file name only."""
    out = []
    for argv in calls:
        if argv[0] == "rsync" and argv[1].endswith((".json", ".md")):
            argv = ["rsync", Path(argv[1]).name, argv[2]]
        out.append(argv)
    return out


def start_calls() -> list[list[str]]:
    return [
        ["hostname"],
        *guard(),
        ["install", "-d", *OWNER, "--", LOCATION, BATCH, f"{BATCH}/.incoming"],
    ]


# --- Happy path ---------------------------------------------------------------


def test_happy_path_exact_sequence(tmp_path: Path) -> None:
    _home, scan, paths = setup(tmp_path)
    plan = archive_plan(scan, paths)
    lab, trash = FakeLab(tmp_path / "lab"), MovingTrash()
    result = run(tmp_path, plan, scan, lab, trash)

    assert [r.status for r in result.results] == ["done", "done", "done"]
    assert {r.detail for r in result.results} == {
        "Copied to the homelab, checked, and moved to the Trash."
    }
    expected = start_calls()
    for n, path in enumerate(paths, start=1):
        expected += item_calls(n, path)
    expected += finish_calls()
    assert comparable(lab.calls) == expected
    assert trash.calls == [("/usr/bin/trash", str(p)) for p in paths]
    assert not any(p.exists() for p in paths)
    for path in paths:
        assert os.path.lexists(lab.at(final_of(path)))
    assert result.archived_bytes == plan.total_bytes
    assert result.moved_to_trash_bytes == plan.total_bytes
    assert result.freed_bytes == 0
    assert result.archive_batch == BATCH


def test_happy_path_timeouts(tmp_path: Path) -> None:
    _home, scan, paths = setup(tmp_path)
    lab = FakeLab(tmp_path / "lab")
    run(tmp_path, archive_plan(scan, paths[:1]), scan, lab, MovingTrash())
    by_call = dict(
        zip([" ".join(c[:2]) for c in lab.calls], [t for _, t in lab.raw], strict=True)
    )
    assert by_call[f"rsync {paths[0]}"] == ARCHIVE_TIMEOUT_SECONDS
    assert by_call["sh -c"] == ARCHIVE_TIMEOUT_SECONDS


def test_storage_guard_amounts(tmp_path: Path) -> None:
    _home, scan, paths = setup(tmp_path)
    plan = archive_plan(scan, paths[:2])
    lab = FakeLab(tmp_path / "lab")
    # Free space just covers item 1 plus the margin, but not the batch total.
    free = plan.entries[0].size_bytes + FREE_MARGIN_BYTES
    lab.on(["df"], ok(f"Avail\n{free}\n"))
    result = run(tmp_path, plan, scan, lab, MovingTrash())
    assert {r.status for r in result.results} == {"skipped"}
    assert result.results[0].detail.startswith("Not enough free space")


def test_log_and_manifest_are_written(tmp_path: Path) -> None:
    _home, scan, paths = setup(tmp_path)
    folder = paths[2]
    (folder / "link").symlink_to("c.mov")  # adds no size worth a rescan
    plan = archive_plan(scan, [paths[0], folder])
    lab = FakeLab(tmp_path / "lab")
    result = run(tmp_path, plan, scan, lab, MovingTrash())
    assert [r.status for r in result.results] == ["done", "done"]

    log = json.loads(Path(result.log_path).read_text())
    assert log["state"] == "finished"
    assert log["archive_batch"] == BATCH
    assert Path(result.log_path).parent == tmp_path / "reports" / "actions"

    saved = tmp_path / "reports" / "archives" / "20261105T173320Z.json"
    manifest = json.loads(saved.read_text())
    assert json.loads(lab.uploaded["MANIFEST.json"]) == manifest
    assert manifest["batch"] == BATCH
    [first, second] = manifest["items"]
    assert first["source"] == str(paths[0])
    assert first["path"] == final_of(paths[0])
    [row] = first["contents"]
    assert row["path"] == final_of(paths[0])
    assert row["kind"] == "file" and row["size"] == 1000 and len(row["sha256"]) == 64
    kinds = {r["path"]: r["kind"] for r in second["contents"]}
    assert kinds == {
        final_of(folder): "dir",
        final_of(folder / "c.mov"): "file",
        final_of(folder / "link"): "link",
    }

    readme = lab.uploaded["README.md"]
    parent = shlex.quote(str(paths[0].parent) + "/")
    assert (
        'rsync -a --rsync-path="sudo -n rsync" -e "ssh -i /keys/k" '
        f"archiver@one.example:{shlex.quote(final_of(paths[0]))} {parent}"
    ) in readme
    assert "extended attributes" in readme and "Finder tags" in readme


def test_readme_quotes_awkward_names(tmp_path: Path) -> None:
    home = tmp_path / "h"
    odd = make_file(home / "Downloads" / "it's $x.dmg", 100)
    from tests.actions.test_verify import scan_paths

    scan = scan_paths(home, [odd])
    plan = archive_plan(scan, [odd])
    lab = FakeLab(tmp_path / "lab")
    result = run(tmp_path, plan, scan, lab, MovingTrash())
    assert [r.status for r in result.results] == ["done"]
    final = final_of(odd)
    assert ["mv", "-n", "-T", "--", f"{BATCH}/.incoming/1/{odd.name}", final] in (
        lab.calls
    )
    assert f"archiver@one.example:{shlex.quote(final)} " in lab.uploaded["README.md"]


# --- Every remote command is on the allowlist ---------------------------------


def assert_allowed(lab: FakeLab) -> None:
    for args, _ in lab.raw:
        if args[0] == "/usr/bin/rsync":
            assert args[:4] == [
                "/usr/bin/rsync",
                "-a",
                "--rsync-path=sudo -n rsync",
                "-e",
            ]
            continue
        words = shlex.split(args[-1])
        if words == ["hostname"]:
            continue
        assert words[:2] == ["sudo", "-n"]
        argv = words[2:]
        assert argv[0] in REMOTE_PROGRAMS | {"sh"}
        assert argv[0] != "rm"
        assert "-delete" not in argv
        if argv[0] == "sh":
            assert argv == ["sh", "-c", LIST_SCRIPT, "sh", argv[4]]
        for i, word in enumerate(argv):
            if word in ("-exec", "-execdir", "-ok", "-okdir"):
                assert argv[i + 1] in ("chmod", "sha256sum")


def test_every_remote_argv_is_allowed(tmp_path: Path) -> None:
    _home, scan, paths = setup(tmp_path)
    lab = FakeLab(tmp_path / "lab")
    plan = archive_plan(scan, paths)
    lab.on(
        ["sh", "-c", LIST_SCRIPT, "sh", f"{BATCH}/.incoming/2"],
        CommandResult(1, "", "boom"),
    )
    run(tmp_path, plan, scan, lab, MovingTrash())
    assert len(lab.raw) > 30
    assert_allowed(lab)


# --- Failures -----------------------------------------------------------------


def test_storage_guard_failure_writes_nothing(tmp_path: Path) -> None:
    _home, scan, paths = setup(tmp_path)
    lab = FakeLab(tmp_path / "lab", uuid="something-else")
    trash = MovingTrash()
    result = run(tmp_path, archive_plan(scan, paths), scan, lab, trash)
    assert [r.status for r in result.results] == ["skipped"] * 3
    assert {r.detail for r in result.results} == {
        "The archive disk is not the expected one"
    }
    assert lab.calls == [
        ["hostname"],
        ["findmnt", "-no", "UUID", "--mountpoint", "/mnt/HD/Disk1"],
    ]
    assert trash.calls == []
    assert all(p.exists() for p in paths)
    assert result.archive_batch is None
    assert result.archived_bytes == 0
    assert json.loads(Path(result.log_path).read_text())["state"] == "finished"
    assert not (tmp_path / "reports" / "archives").exists()


def test_wrong_host_writes_nothing(tmp_path: Path) -> None:
    _home, scan, paths = setup(tmp_path)
    lab = FakeLab(tmp_path / "lab", hostname="impostor")
    trash = MovingTrash()
    result = run(tmp_path, archive_plan(scan, paths), scan, lab, trash)
    assert {r.detail for r in result.results} == {"Unexpected host impostor"}
    assert lab.calls == [["hostname"]]
    assert trash.calls == []


def test_checksum_mismatch_on_item_one_of_two(tmp_path: Path) -> None:
    _home, scan, paths = setup(tmp_path)
    lab = FakeLab(tmp_path / "lab")
    trash = MovingTrash()

    def corrupt(argv: list[str]) -> CommandResult:
        done = lab.copy(argv[1], argv[2].split(":", 1)[1])
        (lab.at(f"{BATCH}/.incoming/1") / paths[0].name).write_bytes(b"y" * 1000)
        return done

    lab.on(["rsync", str(paths[0])], corrupt)
    result = run(tmp_path, archive_plan(scan, paths[:2]), scan, lab, trash)
    first, second = result.results
    assert first.status == "failed"
    assert first.detail == (
        f"{paths[0].name}: checksum differs; the homelab copy is in "
        f"{BATCH}/.incoming/1/{paths[0].name}"
    )
    assert paths[0].exists()
    assert second.status == "done"
    assert trash.calls == [("/usr/bin/trash", str(paths[1]))]
    assert ["mv", "-n", "-T", "--", f"{BATCH}/.incoming/1/{paths[0].name}"] not in [
        c[:5] for c in lab.calls
    ]
    assert result.archived_bytes == second.size_bytes
    # The failed copy stays in .incoming, so .incoming cannot be removed.
    assert os.path.exists(lab.at(f"{BATCH}/.incoming/1/{paths[0].name}"))


def test_listing_exit_status_counts_even_with_a_matching_listing(
    tmp_path: Path,
) -> None:
    _home, scan, paths = setup(tmp_path)
    lab = FakeLab(tmp_path / "lab")
    trash = MovingTrash()

    def listing_then_fail(argv: list[str]) -> CommandResult:
        return CommandResult(1, listing_of(lab.at(argv[4])), "find: oops")

    lab.on(["sh"], listing_then_fail)
    result = run(tmp_path, archive_plan(scan, paths[:1]), scan, lab, trash)
    [entry] = result.results
    assert entry.status == "failed"
    assert entry.detail.startswith("Could not check the copy on the homelab")
    assert trash.calls == []
    assert paths[0].exists()


def test_failed_copy_is_reported_and_kept_local(tmp_path: Path) -> None:
    _home, scan, paths = setup(tmp_path)
    lab = FakeLab(tmp_path / "lab")
    trash = MovingTrash()
    lab.on(["rsync", str(paths[0])], CommandResult(23, "", "rsync error: partial"))
    result = run(tmp_path, archive_plan(scan, paths[:2]), scan, lab, trash)
    first, second = result.results
    assert first.status == "failed"
    assert first.detail == (
        "The copy failed: rsync error: partial; the homelab copy is in "
        f"{BATCH}/.incoming/1/{paths[0].name}"
    )
    assert second.status == "done"
    # The probe ran after the failed step, and the connection was fine.
    rsync_at = lab.calls.index(
        ["rsync", str(paths[0]), f"archiver@one.example:{BATCH}/.incoming/1/"]
    )
    assert lab.calls[rsync_at + 1] == ["hostname"]


def test_connection_lost_on_item_two_of_three(tmp_path: Path) -> None:
    _home, scan, paths = setup(tmp_path)
    lab = FakeLab(tmp_path / "lab")
    trash = MovingTrash()

    def drop(argv: list[str]) -> CommandResult:
        lab.lost = True
        return CommandResult(255, "", "Connection reset by peer")

    lab.on(["rsync", str(paths[1])], drop)
    result = run(tmp_path, archive_plan(scan, paths), scan, lab, trash)
    assert [r.status for r in result.results] == ["done", "failed", "skipped"]
    assert result.results[1].detail.startswith("The copy failed")
    assert result.results[2].detail == "Connection lost"
    assert trash.calls == [("/usr/bin/trash", str(paths[0]))]
    assert paths[1].exists() and paths[2].exists()
    assert lab.calls[-1] == ["hostname"]  # the probe; nothing after it
    assert result.archive_batch == BATCH
    assert result.archived_bytes == result.results[0].size_bytes
    saved = tmp_path / "reports" / "archives" / "20261105T173320Z.json"
    assert [i["source"] for i in json.loads(saved.read_text())["items"]] == [
        str(paths[0])
    ]


def test_final_path_already_exists(tmp_path: Path) -> None:
    _home, scan, paths = setup(tmp_path)
    lab = FakeLab(tmp_path / "lab")
    trash = MovingTrash()
    taken = lab.at(final_of(paths[0]))
    taken.parent.mkdir(parents=True)
    taken.write_text("older copy")
    result = run(tmp_path, archive_plan(scan, paths[:2]), scan, lab, trash)
    first, second = result.results
    assert first.status == "failed"
    assert first.detail == (
        "Could not move it into place on the homelab; the copy is in "
        f"{BATCH}/.incoming/1/{paths[0].name}"
    )
    assert taken.read_text() == "older copy"
    assert not any(
        c[0] == "mv" and c[4].endswith("/1/" + paths[0].name) for c in lab.calls
    )
    assert paths[0].exists()
    assert second.status == "done"
    assert trash.calls == [("/usr/bin/trash", str(paths[1]))]


def test_mv_that_silently_keeps_the_target_is_caught(tmp_path: Path) -> None:
    """The final path appears between the check and mv; mv -n exits 0 anyway."""
    _home, scan, paths = setup(tmp_path)
    lab = FakeLab(tmp_path / "lab")
    trash = MovingTrash()
    final = final_of(paths[0])

    checks = 0

    def appear_after_first_check(argv: list[str]) -> CommandResult:
        nonlocal checks
        checks += 1
        if checks == 1:
            lab.at(final).write_text("raced in")
            return missing(final)
        return lab.default(argv)

    lab.on(["find", final, "-maxdepth", "0"], appear_after_first_check)
    result = run(tmp_path, archive_plan(scan, paths[:1]), scan, lab, trash)
    [entry] = result.results
    assert entry.status == "failed"
    assert entry.detail.startswith("Could not move it into place on the homelab")
    assert lab.at(final).read_text() == "raced in"
    assert trash.calls == []
    assert paths[0].exists()


def test_absence_needs_a_clear_no_such_file(tmp_path: Path) -> None:
    """A find error that is not 'No such file' is doubt, so nothing moves."""
    _home, scan, paths = setup(tmp_path)
    lab = FakeLab(tmp_path / "lab")
    trash = MovingTrash()
    final = final_of(paths[0])
    lab.on(["find", final, "-maxdepth", "0"], CommandResult(1, "", "find: weird"))
    result = run(tmp_path, archive_plan(scan, paths[:1]), scan, lab, trash)
    assert result.results[0].status == "failed"
    assert not any(c[0] == "mv" for c in lab.calls)
    assert trash.calls == []


def test_item_replaced_by_link_after_verify_is_skipped(tmp_path: Path) -> None:
    _home, scan, paths = setup(tmp_path)
    lab = FakeLab(tmp_path / "lab")
    trash = MovingTrash()
    elsewhere = make_file(tmp_path / "elsewhere" / "keep.dmg", 10)

    def swap(argv: list[str]) -> CommandResult:
        answer = lab.default(argv)
        paths[0].unlink()
        paths[0].symlink_to(elsewhere)
        return answer

    lab.on(["mv"], swap)
    result = run(tmp_path, archive_plan(scan, paths[:1]), scan, lab, trash)
    [entry] = result.results
    assert entry.status == "skipped"
    assert entry.detail == (
        "Is a link; there is nothing here to free. "
        f"The homelab copy is in {final_of(paths[0])}."
    )
    assert trash.calls == []
    assert paths[0].is_symlink() and elsewhere.exists()
    assert result.archived_bytes == 0


def test_verify_failure_skips_without_remote_writes(tmp_path: Path) -> None:
    _home, scan, paths = setup(tmp_path)
    plan = archive_plan(scan, paths[:1])
    paths[0].unlink()
    lab = FakeLab(tmp_path / "lab")
    trash = MovingTrash()
    result = run(tmp_path, plan, scan, lab, trash)
    assert [(r.status, r.detail) for r in result.results] == [
        ("skipped", "No longer exists.")
    ]
    assert not any(c[0] == "rsync" and c[1] == str(paths[0]) for c in lab.calls)
    assert trash.calls == []


def test_trash_failure_leaves_item_archived_but_not_done(tmp_path: Path) -> None:
    _home, scan, paths = setup(tmp_path)
    lab = FakeLab(tmp_path / "lab")
    trash = MovingTrash(fail={str(paths[0])})
    result = run(tmp_path, archive_plan(scan, paths[:1]), scan, lab, trash)
    [entry] = result.results
    assert entry.status == "failed"
    assert entry.detail == (
        f"trash: permission denied. The homelab copy is in {final_of(paths[0])}."
    )
    assert result.archived_bytes == 0 and result.moved_to_trash_bytes == 0


def test_no_location_skips_everything(tmp_path: Path) -> None:
    from dataclasses import replace

    _home, scan, paths = setup(tmp_path)
    plan = replace(archive_plan(scan, paths[:1]), location="/etc/x")
    lab = FakeLab(tmp_path / "lab")
    result = run(tmp_path, plan, scan, lab, MovingTrash())
    assert result.results[0].status == "skipped"
    assert lab.calls == []
