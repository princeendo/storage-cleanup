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
from tests.helpers import path_item
from tests.web.test_views import scan_of

TOKEN = "t0k3n-for-tests"


@pytest.fixture
def scan_file(tmp_path: Path) -> Path:
    path = tmp_path / "scan.json"
    path.write_text(scan_to_json(scan_of([path_item("/h/Movies", 100)])))
    return path


@pytest.fixture
def server(scan_file: Path) -> Iterator[ThreadingHTTPServer]:
    srv, _ = make_server(scan_file, token=TOKEN)
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
