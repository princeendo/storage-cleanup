from dataclasses import replace

from storage_cleanup.actions.model import Method, plan_id, plan_to_json
from storage_cleanup.actions.plan import build_plan
from storage_cleanup.scan.ids import item_id
from storage_cleanup.scan.model import Item, Scan, SizeSource
from tests.helpers import path_item
from tests.web.test_views import scan_of

GIB = 1024**3
MIB = 1024**2

FILE = path_item("/h/Downloads/a.dmg", GIB, ("downloads",), kind="file")
FOLDER = path_item("/h/Movies", 3 * GIB)
CHILD = path_item("/h/Movies/clip.mov", GIB, kind="file")
MAIL = path_item("/h/Library/Mail", GIB)
REPO = path_item(
    "/h/workspace/app",
    2 * GIB,
    ("projects",),
    kind="repo",
    details={"dirty": True, "unpushed_commits": 2, "has_remote": False},
)
FORMULA = path_item(
    "/opt/homebrew/Cellar/jq",
    10 * MIB,
    ("homebrew",),
    kind="brew-formula",
    details={"leaf": False},
)
CASK = path_item(
    "/opt/homebrew/Caskroom/affine", 50 * MIB, ("homebrew",), kind="brew-cask"
)


def virtual(
    kind: str, key: str, size: int, parent: str | None = None, **details: object
) -> Item:
    return Item(
        id=item_id(kind, key),
        kind=kind,
        name=key,
        path=None,
        size_bytes=size,
        size_source=SizeSource.TOOL_REPORTED,
        categories=("docker",),
        parent_id=parent,
        details=dict(details),  # type: ignore[arg-type]
    )


TOTAL = virtual("docker-total", "docker", 5 * GIB)
IMAGE = virtual("docker-image", "img", 2 * GIB, TOTAL.id, image_id="sha256:abc")
CACHE = virtual("docker-build-cache", "bc", GIB, TOTAL.id)
VOLUME = virtual("docker-volume", "pg", GIB, TOTAL.id)
SESSIONS = replace(virtual("ai-sessions", "codex:/x", MIB), categories=("ai-tooling",))


def scan() -> Scan:
    return scan_of(
        [
            FILE,
            FOLDER,
            CHILD,
            MAIL,
            REPO,
            FORMULA,
            CASK,
            TOTAL,
            IMAGE,
            CACHE,
            VOLUME,
            SESSIONS,
        ],
        used=100 * GIB,
    )


def methods(ids: list[str], permanent: bool = False) -> dict[str, Method]:
    plan = build_plan(scan(), "S.json", ids, permanent=permanent)
    return {e.item_id: e.method for e in plan.entries}


def test_methods_by_kind() -> None:
    ids = [FILE.id, IMAGE.id, CACHE.id, FORMULA.id, CASK.id]
    assert methods(ids) == {
        FILE.id: Method.TRASH,
        IMAGE.id: Method.DOCKER_IMAGE,
        CACHE.id: Method.DOCKER_BUILD_CACHE,
        FORMULA.id: Method.BREW_FORMULA,
        CASK.id: Method.BREW_CASK,
    }
    assert methods([FILE.id], permanent=True) == {FILE.id: Method.DELETE}


def test_refusals_each_have_a_reason() -> None:
    plan = build_plan(
        scan(),
        "S.json",
        ["file:nope", MAIL.id, VOLUME.id, TOTAL.id, SESSIONS.id, FILE.id, FILE.id],
        permanent=False,
    )
    reasons = {r.item_id: r.reason for r in plan.refused}
    assert reasons["file:nope"] == "Not in this scan."
    assert reasons[MAIL.id] == "Protected: Mail"
    assert reasons[VOLUME.id].startswith("Docker volumes can hold data")
    assert reasons[TOTAL.id] == "Not removable from the page."
    assert reasons[SESSIONS.id] == "Not removable from the page."
    assert [e.item_id for e in plan.entries] == [FILE.id]
    assert any(r.reason == "Listed twice." for r in plan.refused)


def test_child_of_queued_parent_is_refused() -> None:
    plan = build_plan(scan(), "S.json", [CHILD.id, FOLDER.id], permanent=False)
    assert [e.item_id for e in plan.entries] == [FOLDER.id]
    [refusal] = plan.refused
    assert refusal.item_id == CHILD.id
    assert refusal.reason == "Already included in Movies."


def test_repo_warnings() -> None:
    plan = build_plan(scan(), "S.json", [REPO.id], permanent=False)
    [entry] = plan.entries
    assert entry.warnings == (
        "Has uncommitted changes.",
        "Has 2 commits that are not pushed.",
        "No remote: this may be the only copy.",
    )


def test_formula_with_dependents_warns() -> None:
    [entry] = build_plan(scan(), "S.json", [FORMULA.id], permanent=False).entries
    assert entry.warnings == ("Other formulae depend on this; Homebrew may refuse.",)
    assert entry.undo == "Reinstall with brew install jq."


def test_undo_text() -> None:
    plan = build_plan(
        scan(), "S.json", [FILE.id, IMAGE.id, CACHE.id, CASK.id], permanent=False
    )
    undo = {e.item_id: e.undo for e in plan.entries}
    assert undo[FILE.id] == "Put Back from the Trash in Finder."
    assert undo[IMAGE.id] == "Pull or build the image again."
    assert undo[CACHE.id] == "Rebuilt the next time images are built."
    assert undo[CASK.id] == "Reinstall with brew install --cask affine."
    [deleted] = build_plan(scan(), "S.json", [FILE.id], permanent=True).entries
    assert deleted.undo == "Cannot be undone."


def test_totals_split_trash_and_freed() -> None:
    plan = build_plan(scan(), "S.json", [FILE.id, IMAGE.id], permanent=False)
    assert (plan.total_bytes, plan.trash_bytes, plan.freed_bytes) == (
        3 * GIB,
        GIB,
        2 * GIB,
    )
    permanent = build_plan(scan(), "S.json", [FILE.id], permanent=True)
    assert (permanent.trash_bytes, permanent.freed_bytes) == (0, GIB)


def test_plan_id_changes_with_entries_and_mode() -> None:
    a = build_plan(scan(), "S.json", [FILE.id], permanent=False)
    b = build_plan(scan(), "S.json", [FILE.id, FOLDER.id], permanent=False)
    c = build_plan(scan(), "S.json", [FILE.id], permanent=True)
    d = build_plan(scan(), "T.json", [FILE.id], permanent=False)
    assert len({plan_id(a), plan_id(b), plan_id(c), plan_id(d)}) == 4


def test_plan_id_ignores_request_order() -> None:
    a = build_plan(scan(), "S.json", [FILE.id, FOLDER.id], permanent=False)
    b = build_plan(scan(), "S.json", [FOLDER.id, FILE.id], permanent=False)
    assert plan_id(a) == plan_id(b)


def test_plan_json_has_id_and_entries() -> None:
    plan = build_plan(scan(), "S.json", [FILE.id, MAIL.id], permanent=False)
    data = plan_to_json(plan)
    assert data["plan_id"] == plan_id(plan)
    assert data["permanent"] is False
    assert isinstance(data["entries"], list) and isinstance(data["refused"], list)


def test_plan_id_changes_when_a_size_changes() -> None:
    a = build_plan(scan(), "S.json", [FILE.id], permanent=False)
    bigger = replace(FILE, size_bytes=40 * GIB)
    s = scan_of([bigger, FOLDER], used=100 * GIB)
    b = build_plan(s, "S.json", [FILE.id], permanent=False)
    assert plan_id(a) != plan_id(b)


def test_roots_and_paths_outside_them_are_refused_at_plan_time() -> None:
    home = path_item("/h", 10 * GIB)
    apps = path_item("/Applications", GIB)
    elsewhere = path_item("/Volumes/USB/stuff", GIB)
    s = scan_of([home, apps, elsewhere, FILE, FORMULA], used=100 * GIB)
    plan = build_plan(
        s,
        "S.json",
        [home.id, apps.id, elsewhere.id, FILE.id, FORMULA.id],
        permanent=False,
    )
    reasons = {r.item_id: r.reason for r in plan.refused}
    outside = "Outside the folders this tool may change."
    assert reasons == {home.id: outside, apps.id: outside, elsewhere.id: outside}
    assert {e.item_id for e in plan.entries} == {FILE.id, FORMULA.id}
