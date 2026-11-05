import json
from pathlib import Path

from storage_cleanup.scan.collectors.ai_tools import collect_ai_tools
from storage_cleanup.scan.ids import item_id
from storage_cleanup.scan.model import Item
from tests.helpers import make_ctx, make_file

MIB = 1024**2


def of_kind(items: tuple[Item, ...], kind: str) -> list[Item]:
    return [i for i in items if i.kind == kind]


def write_lines(path: Path, lines: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n")


def test_tool_paths_and_children(tmp_path: Path) -> None:
    home = tmp_path / "h"
    make_file(home / ".claude" / "projects" / "x" / "s.jsonl", 2 * MIB)
    make_file(home / ".claude" / "small", 10)
    items = collect_ai_tools(make_ctx(home)).items
    [tool] = of_kind(items, "ai-tool-path")
    assert tool.path == str(home / ".claude")
    assert tool.categories == ("ai-tooling",)
    assert tool.details == {"tool": "claude-code", "tool_name": "Claude Code"}
    children = of_kind(items, "folder")
    assert [c.path for c in children] == [str(home / ".claude" / "projects")]
    assert children[0].details["tool"] == "claude-code"


def test_claude_project_cwd_and_existence(tmp_path: Path) -> None:
    home = tmp_path / "h"
    live = tmp_path / "live-repo"
    live.mkdir()
    write_lines(
        home / ".claude" / "projects" / "-gone" / "a.jsonl",
        ['{"type":"x"}', '{"cwd":"/gone/repo"}'],
    )
    write_lines(
        home / ".claude" / "projects" / "-live" / "b.jsonl",
        [json.dumps({"cwd": str(live)})],
    )
    sessions = {
        i.name: i
        for i in of_kind(collect_ai_tools(make_ctx(home)).items, "ai-sessions")
    }
    assert sessions["-gone"].details["cwd"] == "/gone/repo"
    assert sessions["-gone"].details["cwd_exists"] is False
    assert sessions["-live"].details["cwd_exists"] is True


def test_codex_sessions_grouped_by_cwd(tmp_path: Path) -> None:
    home = tmp_path / "h"
    repo = tmp_path / "repo"
    repo.mkdir()
    day = home / ".codex" / "sessions" / "2026" / "09" / "28"
    meta = {"type": "session_meta", "payload": {"cwd": str(repo)}}
    other = {"type": "session_meta", "payload": {"cwd": "/gone"}}
    write_lines(day / "rollout-1.jsonl", [json.dumps(meta), "{}"])
    write_lines(day / "rollout-2.jsonl", [json.dumps(meta)])
    write_lines(day / "rollout-3.jsonl", [json.dumps(other)])
    groups = {
        i.details["cwd"]: i
        for i in of_kind(collect_ai_tools(make_ctx(home)).items, "ai-sessions")
    }
    assert groups[str(repo)].file_count == 2
    assert groups[str(repo)].path is None
    assert groups[str(repo)].details["cwd_exists"] is True
    assert groups["/gone"].file_count == 1
    assert groups["/gone"].details["cwd_exists"] is False
    assert groups[str(repo)].parent_id == item_id("ai-tool-path", str(home / ".codex"))
    assert groups[str(repo)].size_bytes > 0


def test_opencode_missing_worktrees(tmp_path: Path) -> None:
    home = tmp_path / "h"
    live = tmp_path / "live"
    live.mkdir()
    projects = home / ".local" / "share" / "opencode" / "storage" / "project"
    projects.mkdir(parents=True)
    (projects / "a.json").write_text(json.dumps({"worktree": str(live)}))
    (projects / "b.json").write_text(json.dumps({"worktree": "/gone/wt"}))
    (projects / "c.json").write_text("not json")
    tools = of_kind(collect_ai_tools(make_ctx(home)).items, "ai-tool-path")
    [opencode] = [t for t in tools if t.path and t.path.endswith("opencode")]
    assert opencode.details["projects"] == 2
    assert opencode.details["missing_worktrees"] == ["/gone/wt"]


def test_malformed_session_lines_ignored(tmp_path: Path) -> None:
    home = tmp_path / "h"
    write_lines(
        home / ".claude" / "projects" / "-x" / "a.jsonl", ["not json", "{broken"]
    )
    write_lines(home / ".codex" / "sessions" / "rollout-9.jsonl", ["garbage"])
    items = collect_ai_tools(make_ctx(home)).items
    [claude] = [i for i in of_kind(items, "ai-sessions") if i.name == "-x"]
    assert claude.details["cwd"] is None
    assert claude.details["cwd_exists"] is False


def test_no_tools_installed(tmp_path: Path) -> None:
    assert collect_ai_tools(make_ctx(tmp_path / "h")).items == ()
