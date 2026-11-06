import hashlib
import http.client
import json
import re
import threading
from collections.abc import Iterator
from http.server import ThreadingHTTPServer
from pathlib import Path
from typing import Any

import pytest

from storage_cleanup.findings import findings_path_for
from storage_cleanup.scan.model import scan_to_json
from storage_cleanup.web import ECHARTS_SHA256
from storage_cleanup.web.server import (
    TOKEN_COOKIE,
    load_scan,
    load_view,
    make_server,
    page_url,
    static_dir,
)
from tests.helpers import FakeRunner, make_file, path_item
from tests.web.test_views import scan_of

TOKEN = "t0k3n-for-tests"


@pytest.fixture
def scan_file(tmp_path: Path) -> Path:
    path = tmp_path / "scan.json"
    path.write_text(scan_to_json(scan_of([path_item("/h/Movies", 100)])))
    return path


@pytest.fixture
def server(scan_file: Path) -> Iterator[ThreadingHTTPServer]:
    srv, _ = make_server(scan_file, token=TOKEN, runner=FakeRunner(), brew_prefix=None)
    thread = threading.Thread(
        target=srv.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True
    )
    thread.start()
    yield srv
    srv.shutdown()
    srv.server_close()


def request(
    srv: ThreadingHTTPServer,
    path: str,
    *,
    method: str = "GET",
    cookie: str | None = TOKEN,
    host: str | None = None,
) -> http.client.HTTPResponse:
    port = srv.server_address[1]
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    headers = {"Host": host or f"127.0.0.1:{port}"}
    if cookie is not None:
        headers["Cookie"] = f"{TOKEN_COOKIE}_{port}={cookie}"
    conn.request(method, path, headers=headers)
    return conn.getresponse()


def test_page_url_has_token(server: ThreadingHTTPServer) -> None:
    port = server.server_address[1]
    assert page_url(server, TOKEN) == f"http://127.0.0.1:{port}/?token={TOKEN}"


def test_token_link_sets_cookie_and_redirects(server: ThreadingHTTPServer) -> None:
    resp = request(server, f"/?token={TOKEN}", cookie=None)
    assert resp.status == 302
    assert resp.getheader("Location") == "/"
    cookie = resp.getheader("Set-Cookie") or ""
    assert f"{TOKEN_COOKIE}_{server.server_address[1]}={TOKEN}" in cookie
    assert "HttpOnly" in cookie
    assert "SameSite=Strict" in cookie


@pytest.mark.parametrize("path", ["/", "/api/view", "/static/index.html"])
@pytest.mark.parametrize("cookie", [None, "wrong"])
def test_missing_or_wrong_token_is_forbidden(
    server: ThreadingHTTPServer, path: str, cookie: str | None
) -> None:
    resp = request(server, path, cookie=cookie)
    assert resp.status == 403
    assert b"storage-cleanup serve" in resp.read()


def test_wrong_token_link_is_forbidden(server: ThreadingHTTPServer) -> None:
    assert request(server, "/?token=nope", cookie=None).status == 403


@pytest.mark.parametrize("host", ["evil.example", "evil.example:{port}", "127.0.0.1:1"])
def test_foreign_host_is_forbidden_even_with_cookie(
    server: ThreadingHTTPServer, host: str
) -> None:
    port = server.server_address[1]
    assert request(server, "/api/view", host=host.format(port=port)).status == 403


def test_localhost_host_is_allowed(server: ThreadingHTTPServer) -> None:
    port = server.server_address[1]
    assert request(server, "/", host=f"localhost:{port}").status == 200


def test_api_view_returns_view_json(server: ThreadingHTTPServer) -> None:
    resp = request(server, "/api/view")
    assert resp.status == 200
    assert resp.getheader("Content-Type", "").startswith("application/json")
    assert resp.getheader("Cache-Control") == "no-store"
    data = json.loads(resp.read())
    assert data["schema"] == 1
    assert data["overview"]["item_count"] == 1


def test_index_and_static_are_served(server: ThreadingHTTPServer) -> None:
    resp = request(server, "/")
    assert resp.status == 200
    assert resp.getheader("Content-Type", "").startswith("text/html")
    assert b"Storage Explorer" in resp.read()


@pytest.mark.parametrize(
    "path",
    [
        "/static/../server.py",
        "/static/%2e%2e/server.py",
        "/static/..%2fserver.py",
        "/static/nope.js",
        "/elsewhere",
    ],
)
def test_static_path_traversal_and_unknown_paths_are_404(
    server: ThreadingHTTPServer, path: str
) -> None:
    assert request(server, path).status == 404


@pytest.mark.parametrize("method", ["POST", "PUT", "DELETE", "PATCH"])
def test_non_get_is_405(server: ThreadingHTTPServer, method: str) -> None:
    assert request(server, "/api/view", method=method).status == 405


def test_security_headers_present(server: ThreadingHTTPServer) -> None:
    for resp in (request(server, "/"), request(server, "/", cookie=None)):
        csp = resp.getheader("Content-Security-Policy", "")
        assert "script-src 'self'" in csp
        assert "frame-ancestors 'none'" in csp
        assert resp.getheader("X-Content-Type-Options") == "nosniff"
        assert resp.getheader("Referrer-Policy") == "no-referrer"


def test_binds_loopback_only(server: ThreadingHTTPServer) -> None:
    assert server.server_address[0] == "127.0.0.1"


def test_api_rereads_scan_file(server: ThreadingHTTPServer, scan_file: Path) -> None:
    scan_file.write_text(
        scan_to_json(scan_of([path_item("/h/A", 1), path_item("/h/B", 1)]))
    )
    data = json.loads(request(server, "/api/view").read())
    assert data["overview"]["item_count"] == 2


@pytest.mark.parametrize(
    "content",
    [
        "not json",
        "[]",
        "null",
        '{"schema_version": 2}',
        '{"schema_version": 1}',
        "[" * 100_000 + "]" * 100_000,
    ],
)
def test_load_scan_rejects_bad_files(tmp_path: Path, content: str) -> None:
    path = tmp_path / "bad.json"
    path.write_text(content)
    with pytest.raises(ValueError, match=re.escape(str(path))):
        load_scan(path)


def test_load_scan_rejects_missing_file(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match=re.escape("missing.json")):
        load_scan(tmp_path / "missing.json")


def test_vendored_echarts_is_pinned() -> None:
    vendor = static_dir() / "vendor"
    script = (vendor / "echarts.min.js").read_bytes()
    assert hashlib.sha256(script).hexdigest() == ECHARTS_SHA256
    assert b"Licensed to the Apache Software Foundation" in script[:2000]
    assert "Apache License" in (vendor / "ECHARTS_LICENSE").read_text()
    assert (vendor / "ECHARTS_NOTICE").exists()


def test_vendored_echarts_is_served(server: ThreadingHTTPServer) -> None:
    resp = request(server, "/static/vendor/echarts.min.js")
    assert resp.status == 200
    assert resp.getheader("Content-Type", "").startswith("text/javascript")


def test_dropped_connections_are_quiet(
    server: ThreadingHTTPServer, capsys: pytest.CaptureFixture[str]
) -> None:
    for _ in range(3):
        resp = request(server, "/static/vendor/echarts.min.js")
        resp.close()
    request(server, "/").read()
    assert "Traceback" not in capsys.readouterr().err


def test_cookie_name_is_per_port(server: ThreadingHTTPServer) -> None:
    port = server.server_address[1]
    resp = request(server, f"/?token={TOKEN}", cookie=None)
    assert (resp.getheader("Set-Cookie") or "").startswith(f"{TOKEN_COOKIE}_{port}=")


def broken_scan_text() -> str:
    data = json.loads(scan_to_json(scan_of([path_item("/h/Movies", 100)])))
    data["items"][0]["size_bytes"] = "5"
    return json.dumps(data)


def test_load_scan_rejects_scans_the_page_cannot_draw(tmp_path: Path) -> None:
    path = tmp_path / "typed-wrong.json"
    path.write_text(broken_scan_text())
    with pytest.raises(ValueError, match=re.escape(str(path))):
        load_scan(path)


def test_api_view_answers_500_with_message(
    server: ThreadingHTTPServer, scan_file: Path
) -> None:
    scan_file.write_text(broken_scan_text())
    resp = request(server, "/api/view")
    assert resp.status == 500
    assert b"Cannot read scan" in resp.read()


MOVIES = path_item("/h/Movies", 100)


def write_pair(tmp_path: Path, **overrides: Any) -> tuple[Path, Path]:
    scan_path = tmp_path / "reports" / "scans" / "S.json"
    scan_path.parent.mkdir(parents=True)
    scan_path.write_text(scan_to_json(scan_of([MOVIES])))
    findings = {
        "schema_version": 1,
        "scan": "S.json",
        "created_at": "2026-11-05T23:00:00Z",
        "agent": "Test agent",
        "headlines": [{"title": "Movies", "body": "100 B.", "item_ids": [MOVIES.id]}],
        "categories": [],
        "recommendations": [
            {
                "item_id": MOVIES.id,
                "action": "archive",
                "confidence": 0.7,
                "reason": "Old.",
            }
        ],
    }
    findings.update(overrides)
    findings_path = findings_path_for(scan_path)
    findings_path.parent.mkdir(parents=True)
    findings_path.write_text(json.dumps(findings))
    return scan_path, findings_path


def test_view_includes_matching_findings(tmp_path: Path) -> None:
    scan_path, _ = write_pair(tmp_path)
    view: Any = load_view(scan_path, 0.0)
    assert view["findings"]["agent"] == "Test agent"
    assert view["findings_note"] is None
    assert [r["source"] for r in view["recommendations"]] == ["agent"]


def test_view_without_findings_file(tmp_path: Path) -> None:
    scan_path, findings_path = write_pair(tmp_path)
    findings_path.unlink()
    view: Any = load_view(scan_path, 0.0)
    assert view["findings"] is None
    assert view["findings_note"] is None


def test_stale_findings_are_noted_not_shown(tmp_path: Path) -> None:
    scan_path, _ = write_pair(tmp_path, scan="other.json")
    view: Any = load_view(scan_path, 0.0)
    assert view["findings"] is None
    assert view["findings_note"] == (
        "Agent findings were not shown: These findings are for scan other.json, "
        "not S.json."
    )


def test_invalid_findings_are_noted_not_shown(tmp_path: Path) -> None:
    bad = [
        {"item_id": f"x:{i}", "action": "delete", "confidence": 1, "reason": "r"}
        for i in range(5)
    ]
    scan_path, _ = write_pair(tmp_path, recommendations=bad)
    view: Any = load_view(scan_path, 0.0)
    assert view["findings"] is None
    note = view["findings_note"]
    assert note.startswith("Agent findings were not shown: Unknown item ID: x:0")
    assert note.endswith("and 2 more.")


def test_unreadable_findings_are_noted(tmp_path: Path) -> None:
    scan_path, findings_path = write_pair(tmp_path)
    findings_path.write_text("{oops")
    view: Any = load_view(scan_path, 0.0)
    assert view["findings"] is None
    assert view["findings_note"].startswith(
        "Agent findings were not shown: Not valid JSON"
    )


def test_findings_reread_per_request(tmp_path: Path) -> None:
    scan_path, findings_path = write_pair(tmp_path)
    srv, _ = make_server(scan_path, token=TOKEN, findings_path=findings_path)
    thread = threading.Thread(
        target=srv.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True
    )
    thread.start()
    try:
        first = json.loads(request(srv, "/api/view").read())
        findings_path.write_text(
            findings_path.read_text().replace("Test agent", "Other")
        )
        second = json.loads(request(srv, "/api/view").read())
    finally:
        srv.shutdown()
        srv.server_close()
    assert first["findings"]["agent"] == "Test agent"
    assert second["findings"]["agent"] == "Other"


def test_validation_errors_never_blank_the_page(tmp_path: Path) -> None:
    huge = [
        {"item_id": MOVIES.id, "action": "delete", "confidence": 10**400, "reason": "r"}
    ]
    scan_path, _ = write_pair(tmp_path, recommendations=huge)
    view: Any = load_view(scan_path, 0.0)
    assert view["findings"] is None
    assert view["findings_note"].startswith("Agent findings were not shown:")


def test_findings_for_an_older_scan_are_noted_after_a_rescan(tmp_path: Path) -> None:
    old_scan, _ = write_pair(tmp_path)
    new_scan = old_scan.parent / "T.json"
    new_scan.write_text(old_scan.read_text())
    view: Any = load_view(new_scan, 0.0)
    assert view["findings"] is None
    assert view["findings_note"] == (
        "Agent findings are for an older scan (S.json). Ask your agent to run "
        "AGENT_PLAYBOOK.md again for this scan."
    )


# --- Removal endpoints -----------------------------------------------------


def post(
    srv: ThreadingHTTPServer,
    path: str,
    body: object,
    *,
    origin: str | None = "self",
    content_type: str = "application/json",
) -> tuple[int, Any]:
    port = srv.server_address[1]
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    headers = {
        "Host": f"127.0.0.1:{port}",
        "Cookie": f"{TOKEN_COOKIE}_{port}={TOKEN}",
        "Content-Type": content_type,
    }
    if origin is not None:
        headers["Origin"] = f"http://127.0.0.1:{port}" if origin == "self" else origin
    raw = body if isinstance(body, bytes) else json.dumps(body).encode()
    conn.request("POST", path, body=raw, headers=headers)
    resp = conn.getresponse()
    text = resp.read()
    try:
        return resp.status, json.loads(text)
    except ValueError:
        return resp.status, text.decode()


@pytest.fixture
def sandbox(
    tmp_path: Path,
) -> Iterator[tuple[ThreadingHTTPServer, Any, list[Path], Path]]:
    from storage_cleanup.scan.model import scan_to_json as to_json
    from tests.actions.test_run import MovingTrash
    from tests.actions.test_verify import scan_paths

    home = tmp_path / "h"
    files = [
        make_file(home / "Downloads" / f"f{i}.dmg", 1000 * (i + 1)) for i in range(3)
    ]
    scan_path = tmp_path / "reports" / "scans" / "S.json"
    scan_path.parent.mkdir(parents=True)
    scan_path.write_text(to_json(scan_paths(home, files)))
    runner = MovingTrash()
    srv, _ = make_server(scan_path, token=TOKEN, runner=runner, brew_prefix=None)
    thread = threading.Thread(
        target=srv.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True
    )
    thread.start()
    yield srv, runner, files, tmp_path
    srv.shutdown()
    srv.server_close()


def ids_of(srv: ThreadingHTTPServer, files: list[Path]) -> list[str]:
    from storage_cleanup.scan.model import scan_from_json

    scan = scan_from_json(srv.scan_path.read_text())  # type: ignore[attr-defined]
    by_path = {i.path: i.id for i in scan.items}
    return [by_path[str(f)] for f in files]


def test_plan_endpoint_has_no_side_effects(sandbox: Any) -> None:
    srv, runner, files, _ = sandbox
    status, plan = post(
        srv, "/api/plan", {"item_ids": ids_of(srv, files), "permanent": False}
    )
    assert status == 200
    assert len(plan["entries"]) == 3
    assert plan["plan_id"]
    assert runner.calls == []
    assert all(f.exists() for f in files)


@pytest.mark.parametrize("origin", [None, "http://evil.example", "null"])
def test_post_requires_origin(sandbox: Any, origin: str | None) -> None:
    srv, _runner, _files, _ = sandbox
    status, _ = post(
        srv, "/api/plan", {"item_ids": [], "permanent": False}, origin=origin
    )
    assert status == 403


def test_post_requires_json_and_size_limit(sandbox: Any) -> None:
    srv, _, _, _ = sandbox
    status, _ = post(srv, "/api/plan", {"item_ids": []}, content_type="text/plain")
    assert status == 415
    port = srv.server_address[1]
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    conn.putrequest("POST", "/api/plan", skip_host=True)
    for name, value in {
        "Host": f"127.0.0.1:{port}",
        "Cookie": f"{TOKEN_COOKIE}_{port}={TOKEN}",
        "Origin": f"http://127.0.0.1:{port}",
        "Content-Type": "application/json",
        "Content-Length": "2000000",
    }.items():
        conn.putheader(name, value)
    conn.endheaders(b"{}")
    assert conn.getresponse().status == 413
    status, _ = post(srv, "/api/plan", b"{not json")
    assert status == 400
    status, _ = post(srv, "/api/plan", {"item_ids": "nope", "permanent": False})
    assert status == 400


def test_run_requires_matching_plan_id(sandbox: Any) -> None:
    srv, runner, files, _ = sandbox
    body = {
        "item_ids": ids_of(srv, files),
        "permanent": False,
        "plan_id": "stale",
        "confirm": None,
    }
    status, message = post(srv, "/api/run", body)
    assert status == 409
    assert message == "The plan changed; review it again."
    assert runner.calls == []


def test_permanent_requires_phrase(sandbox: Any) -> None:
    srv, _runner, files, _ = sandbox
    ids = ids_of(srv, files)
    _, plan = post(srv, "/api/plan", {"item_ids": ids, "permanent": True})
    body = {
        "item_ids": ids,
        "permanent": True,
        "plan_id": plan["plan_id"],
        "confirm": "yes",
    }
    status, message = post(srv, "/api/run", body)
    assert status == 400
    assert 'Type "delete permanently"' in message
    assert all(f.exists() for f in files)


def test_run_trashes_via_runner_and_logs(sandbox: Any) -> None:
    srv, _runner, files, tmp_path = sandbox
    ids = ids_of(srv, files[:2])
    _, plan = post(srv, "/api/plan", {"item_ids": ids, "permanent": False})
    body = {
        "item_ids": ids,
        "permanent": False,
        "plan_id": plan["plan_id"],
        "confirm": None,
    }
    status, result = post(srv, "/api/run", body)
    assert status == 200
    assert [r["status"] for r in result["results"]] == ["done", "done"]
    assert not files[0].exists() and not files[1].exists() and files[2].exists()
    log = Path(result["log_path"])
    assert log.parent == tmp_path / "reports" / "actions"
    assert log.exists()


def test_concurrent_run_gets_409(sandbox: Any) -> None:
    srv, runner, files, _ = sandbox
    ids = ids_of(srv, files)
    _, plan = post(srv, "/api/plan", {"item_ids": ids, "permanent": False})
    body = {
        "item_ids": ids,
        "permanent": False,
        "plan_id": plan["plan_id"],
        "confirm": None,
    }
    srv.run_lock.acquire()
    try:
        status, message = post(srv, "/api/run", body)
    finally:
        srv.run_lock.release()
    assert (status, message) == (409, "Another run is in progress.")
    assert runner.calls == []


def test_empty_trash_requires_phrase_and_uses_osascript(sandbox: Any) -> None:
    srv, runner, _, _ = sandbox
    status, _ = post(srv, "/api/empty-trash", {"confirm": "sure"})
    assert status == 400
    assert runner.calls == []
    status, result = post(srv, "/api/empty-trash", {"confirm": "empty trash"})
    assert status == 200
    assert result == {"ok": True, "message": "The Trash is empty."}
    assert runner.calls[0][0] == "osascript"


def test_post_to_unknown_path_is_405(sandbox: Any) -> None:
    srv, _, _, _ = sandbox
    status, _ = post(srv, "/api/view", {})
    assert status == 405


def test_run_errors_come_back_as_json(
    sandbox: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    from storage_cleanup.web import server as server_mod

    srv, _, files, _ = sandbox
    ids = ids_of(srv, files[:1])
    _, plan = post(srv, "/api/plan", {"item_ids": ids, "permanent": False})

    def boom(*args: object, **kwargs: object) -> object:
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(server_mod, "run_plan", boom)
    body = {
        "item_ids": ids,
        "permanent": False,
        "plan_id": plan["plan_id"],
        "confirm": None,
    }
    status, message = post(srv, "/api/run", body)
    assert status == 500
    assert message.startswith("The run stopped unexpectedly")
    assert not srv.run_lock.locked()


def test_logs_stay_beside_a_scan_outside_reports(tmp_path: Path) -> None:
    from storage_cleanup.web.server import action_log_dir

    assert action_log_dir(tmp_path / "reports" / "scans" / "S.json") == (
        tmp_path / "reports" / "actions"
    )
    assert action_log_dir(tmp_path / "x.json") == tmp_path / "actions"


def test_server_uses_the_no_terminal_runner_by_default(scan_file: Path) -> None:
    from storage_cleanup.actions.run import action_runner

    srv, _ = make_server(scan_file, token=TOKEN)
    try:
        assert srv.runner is action_runner
    finally:
        srv.server_close()


def test_view_growth_with_two_scans_and_none_with_one(tmp_path: Path) -> None:
    folder = tmp_path / "scans"
    folder.mkdir()
    old = folder / "20260101T000000Z.json"
    new = folder / "20260102T000000Z.json"
    old.write_text(scan_to_json(scan_of([path_item("/h/Movies", 100)], used=1000)))
    new.write_text(scan_to_json(scan_of([path_item("/h/Movies", 100)], used=1500)))
    one: Any = load_view(old, 0.0)
    assert one["growth"] is None
    assert len(one["history"]) == 2
    two: Any = load_view(new, 0.0)
    assert two["growth"]["used_change"] == 500
    assert [h["used_bytes"] for h in two["history"]] == [1000, 1500]


def test_unreadable_previous_scan_is_skipped(tmp_path: Path) -> None:
    folder = tmp_path / "scans"
    folder.mkdir()
    (folder / "20260101T000000Z.json").write_text("{broken")
    new = folder / "20260102T000000Z.json"
    new.write_text(scan_to_json(scan_of([path_item("/h/Movies", 100)])))
    view: Any = load_view(new, 0.0)
    assert view["growth"] is None
    assert len(view["history"]) == 1


# --- Archive endpoints ------------------------------------------------------


def archive_config() -> Any:
    from storage_cleanup.actions.archive_config import ArchiveConfig

    return ArchiveConfig(
        hosts=("lab.example",),
        user="u",
        key=Path("/k"),
        expected_hostname="lab",
        mount_point="/srv/archive",
        fs_uuid="uuid",
        allowed_root="/srv/archive",
        location="/srv/archive/mac",
        owner="o",
        group="g",
    )


def archive_server(scan_path: Path, config: Any) -> Any:
    srv, _ = make_server(
        scan_path,
        token=TOKEN,
        runner=FakeRunner(),
        brew_prefix=None,
        archive_config=config,
    )
    thread = threading.Thread(
        target=srv.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True
    )
    thread.start()
    return srv


@pytest.fixture
def archive_sandbox(sandbox: Any) -> Iterator[tuple[Any, list[Path]]]:
    old, _, files, _ = sandbox
    srv = archive_server(old.scan_path, archive_config())
    yield srv, files
    srv.shutdown()
    srv.server_close()


def archive_body(srv: Any, files: list[Path], **extra: Any) -> dict[str, Any]:
    return {
        "item_ids": ids_of(srv, files),
        "permanent": False,
        "action": "archive",
        **extra,
    }


def test_archive_plan_uses_default_location(archive_sandbox: Any) -> None:
    srv, files = archive_sandbox
    status, plan = post(srv, "/api/plan", archive_body(srv, files))
    assert status == 200
    assert plan["location"] == "/srv/archive/mac"
    assert {e["method"] for e in plan["entries"]} == {"archive"}
    assert plan["trash_bytes"] == sum(e["size"] for e in plan["entries"])
    assert all(f.exists() for f in files)


def test_archive_plan_accepts_given_location(archive_sandbox: Any) -> None:
    srv, files = archive_sandbox
    _, plan = post(
        srv, "/api/plan", archive_body(srv, files, location="/srv/archive/x")
    )
    assert plan["location"] == "/srv/archive/x"


def test_archive_without_config_is_400(sandbox: Any) -> None:
    srv, _, files, _ = sandbox
    status, message = post(srv, "/api/plan", archive_body(srv, files))
    assert status == 400
    assert message == (
        "Archive is not set up: copy archive.example.toml to local/archive.toml."
    )


@pytest.mark.parametrize(
    "location", ["/srv/archive/../x", "/etc/x", "/srv/archive/it's"]
)
def test_archive_bad_location_is_400(archive_sandbox: Any, location: str) -> None:
    from storage_cleanup.actions.archive_config import check_location

    srv, files = archive_sandbox
    status, message = post(
        srv, "/api/plan", archive_body(srv, files, location=location)
    )
    assert status == 400
    assert message == check_location(archive_config(), location)


def test_archive_permanent_is_400(archive_sandbox: Any) -> None:
    srv, files = archive_sandbox
    status, _ = post(srv, "/api/plan", archive_body(srv, files, permanent=True))
    assert status == 400


def test_bad_action_is_400(sandbox: Any) -> None:
    srv, _, files, _ = sandbox
    body = {"item_ids": ids_of(srv, files), "permanent": False, "action": "zip"}
    assert post(srv, "/api/plan", body)[0] == 400


def test_archive_run_is_501_after_plan_check(archive_sandbox: Any) -> None:
    srv, files = archive_sandbox
    body = archive_body(srv, files)
    status, _ = post(srv, "/api/run", {**body, "plan_id": "stale"})
    assert status == 409
    _, plan = post(srv, "/api/plan", body)
    status, message = post(srv, "/api/run", {**body, "plan_id": plan["plan_id"]})
    assert status == 501
    assert message == "Archive runs arrive in the next task."
    assert all(f.exists() for f in files)


def test_view_reports_archive_state(archive_sandbox: Any, server: Any) -> None:
    srv, _ = archive_sandbox
    on = json.loads(request(srv, "/api/view").read())["archive"]
    assert on == {"configured": True, "location": "/srv/archive/mac"}
    off = json.loads(request(server, "/api/view").read())["archive"]
    assert off == {"configured": False, "location": None}
