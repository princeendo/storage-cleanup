"""A read-only, token-guarded web server for one scan, bound to this Mac only."""

import json
import secrets
import sys
import threading
import time
import webbrowser
from dataclasses import asdict
from http import HTTPStatus
from http.cookies import CookieError, SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib.resources import files
from pathlib import Path
from typing import Any, Final, cast
from urllib.parse import parse_qs, unquote, urlsplit

from storage_cleanup.actions.model import ActionPlan, plan_id, plan_to_json
from storage_cleanup.actions.plan import build_plan
from storage_cleanup.actions.run import run_plan
from storage_cleanup.actions.trash import empty_trash
from storage_cleanup.findings import (
    Findings,
    findings_path_for,
    parse_findings,
    validate_findings,
)
from storage_cleanup.scan.context import Runner, subprocess_runner
from storage_cleanup.scan.model import JsonValue, Scan, scan_from_json
from storage_cleanup.web.views import build_view

TOKEN_COOKIE: Final = "sc_token"
HOST: Final = "127.0.0.1"
FORBIDDEN_TEXT: Final = 'Open the link printed by "storage-cleanup serve".'
SECURITY_HEADERS: Final = {
    "Content-Security-Policy": (
        "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
        "img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; "
        "base-uri 'none'"
    ),
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
}
CONTENT_TYPES: Final = {
    ".html": "text/html; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".json": "application/json",
    ".svg": "image/svg+xml",
}


def static_dir() -> Path:
    return Path(str(files("storage_cleanup.web") / "static"))


def _read_scan(path: Path) -> Scan:
    try:
        return scan_from_json(path.read_text(encoding="utf-8"))
    except Exception as exc:  # any malformed file must become a readable message
        reason = exc.strerror if isinstance(exc, OSError) and exc.strerror else exc
        raise ValueError(f"Cannot read scan {path}: {reason}") from exc


MAX_NOTED_PROBLEMS: Final = 3
MAX_BODY_BYTES: Final = 1024 * 1024
PERMANENT_PHRASE: Final = "delete permanently"
EMPTY_TRASH_PHRASE: Final = "empty trash"
POST_ROUTES: Final = {
    "/api/plan": "_post_plan",
    "/api/run": "_post_run",
    "/api/empty-trash": "_post_empty_trash",
}


def _load_findings(
    scan: Scan, scan_path: Path, findings_path: Path | None
) -> tuple[Findings | None, str | None]:
    """Valid findings for this scan, or None with a sentence saying why not."""
    path = findings_path or findings_path_for(scan_path)
    if findings_path is None and not path.exists():
        older = (
            sorted(p.name for p in path.parent.glob("*.json"))
            if path.parent.is_dir()
            else []
        )
        if older:
            when = "an older" if older[-1] < scan_path.name else "another"
            return None, (
                f"Agent findings are for {when} scan ({older[-1]}). Ask your agent "
                "to run AGENT_PLAYBOOK.md again for this scan."
            )
        return None, None
    try:
        findings = parse_findings(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        reason = exc.strerror if isinstance(exc, OSError) and exc.strerror else exc
        return None, f"Agent findings were not shown: {reason}"
    try:
        problems = validate_findings(findings, scan, scan_path.name)
    except Exception as exc:  # broken findings must only ever add a note
        return None, f"Agent findings were not shown: {exc!r}"
    if not problems:
        return findings, None
    shown = " ".join(problems[:MAX_NOTED_PROBLEMS])
    extra = len(problems) - MAX_NOTED_PROBLEMS
    more = f" … and {extra} more." if extra > 0 else ""
    return None, f"Agent findings were not shown: {shown}{more}"


def load_view(
    path: Path, now: float, findings_path: Path | None = None
) -> dict[str, JsonValue]:
    """Read a scan (and its findings) and build the page data.

    A broken scan raises a readable ValueError; broken findings only add a note.
    """
    scan = _read_scan(path)
    findings, note = _load_findings(scan, path, findings_path)
    try:
        return build_view(scan, now, findings, note)
    except Exception as exc:
        raise ValueError(
            f"Cannot read scan {path}: the page cannot draw it ({exc!r})"
        ) from exc


def load_scan(path: Path) -> Scan:
    """Read a scan the page can draw, or raise ValueError with a readable message."""
    load_view(path, time.time())
    return _read_scan(path)


class ScanServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(
        self,
        port: int,
        scan_path: Path,
        token: str,
        findings_path: Path | None = None,
        runner: Runner = subprocess_runner,
        brew_prefix: Path | None = None,
    ) -> None:
        self.scan_path = scan_path
        self.findings_path = findings_path
        self.token = token
        self.runner = runner
        self.brew_prefix = brew_prefix
        self.run_lock = threading.Lock()
        super().__init__((HOST, port), _Handler)

    @property
    def port(self) -> int:
        return int(self.server_address[1])

    @property
    def cookie_name(self) -> str:
        # Browsers share cookies across ports, so two servers need two names.
        return f"{TOKEN_COOKIE}_{self.port}"

    def handle_error(self, request: Any, client_address: Any) -> None:
        # A browser that closes the connection early is normal; stay quiet.
        if isinstance(sys.exception(), ConnectionError):
            return
        super().handle_error(request, client_address)


class _Handler(BaseHTTPRequestHandler):
    server_version = "storage-cleanup"
    sys_version = ""

    @property
    def app(self) -> ScanServer:
        return cast(ScanServer, self.server)

    def log_message(self, format: str, *args: Any) -> None:
        pass

    def _send(
        self,
        status: HTTPStatus,
        body: bytes = b"",
        content_type: str = "text/plain; charset=utf-8",
        headers: dict[str, str] | None = None,
    ) -> None:
        self.send_response(status)
        for name, value in {**SECURITY_HEADERS, **(headers or {})}.items():
            self.send_header(name, value)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _forbidden(self) -> None:
        self._send(HTTPStatus.FORBIDDEN, FORBIDDEN_TEXT.encode())

    def _host_ok(self) -> bool:
        port = self.app.port
        return self.headers.get("Host", "") in {f"{HOST}:{port}", f"localhost:{port}"}

    def _cookie_ok(self) -> bool:
        try:
            cookie = SimpleCookie(self.headers.get("Cookie", ""))
        except CookieError:
            return False
        morsel = cookie.get(self.app.cookie_name)
        return morsel is not None and secrets.compare_digest(
            morsel.value.encode(), self.app.token.encode()
        )

    def _not_allowed(self) -> None:
        self._send(HTTPStatus.METHOD_NOT_ALLOWED, b"This page is read-only.")

    do_PUT = do_DELETE = do_PATCH = do_HEAD = do_OPTIONS = _not_allowed

    def _json(self, status: HTTPStatus, data: object) -> None:
        body = json.dumps(data, ensure_ascii=True, default=str).encode()
        self._send(status, body, "application/json", {"Cache-Control": "no-store"})

    def do_POST(self) -> None:
        if not self._host_ok() or not self._cookie_ok():
            self._forbidden()
            return
        route = POST_ROUTES.get(urlsplit(self.path).path)
        if route is None:
            self._not_allowed()
            return
        port = self.app.port
        origin = self.headers.get("Origin")
        if origin not in {f"http://{HOST}:{port}", f"http://localhost:{port}"}:
            self._forbidden()
            return
        if not self.headers.get("Content-Type", "").startswith("application/json"):
            self._json(HTTPStatus.UNSUPPORTED_MEDIA_TYPE, "Send JSON.")
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            length = -1
        if length < 0 or length > MAX_BODY_BYTES:
            self._json(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, "Request too large.")
            return
        try:
            body = json.loads(self.rfile.read(length) or b"null")
        except (ValueError, RecursionError):
            self._json(HTTPStatus.BAD_REQUEST, "Not valid JSON.")
            return
        if not isinstance(body, dict):
            self._json(HTTPStatus.BAD_REQUEST, "Send a JSON object.")
            return
        getattr(self, route)(body)

    def _plan_from(self, body: dict[str, Any]) -> ActionPlan | None:
        ids = body.get("item_ids")
        permanent = body.get("permanent")
        if (
            not isinstance(ids, list)
            or not all(isinstance(i, str) for i in ids)
            or not isinstance(permanent, bool)
        ):
            self._json(
                HTTPStatus.BAD_REQUEST,
                "Send item_ids (a list) and permanent (true/false).",
            )
            return None
        try:
            scan = _read_scan(self.app.scan_path)
        except ValueError as exc:
            self._json(HTTPStatus.INTERNAL_SERVER_ERROR, str(exc))
            return None
        return build_plan(scan, self.app.scan_path.name, ids, permanent=permanent)

    def _post_plan(self, body: dict[str, Any]) -> None:
        plan = self._plan_from(body)
        if plan is not None:
            self._json(HTTPStatus.OK, plan_to_json(plan))

    def _post_run(self, body: dict[str, Any]) -> None:
        plan = self._plan_from(body)
        if plan is None:
            return
        if body.get("plan_id") != plan_id(plan):
            self._json(HTTPStatus.CONFLICT, "The plan changed; review it again.")
            return
        if plan.permanent and body.get("confirm") != PERMANENT_PHRASE:
            self._json(
                HTTPStatus.BAD_REQUEST,
                f'Type "{PERMANENT_PHRASE}" to confirm permanent deletion.',
            )
            return
        if not self.app.run_lock.acquire(blocking=False):
            self._json(HTTPStatus.CONFLICT, "Another run is in progress.")
            return
        try:
            scan = _read_scan(self.app.scan_path)
            result = run_plan(
                plan,
                scan,
                runner=self.app.runner,
                brew_prefix=self.app.brew_prefix,
                log_dir=self.app.scan_path.parent.parent / "actions",
            )
        finally:
            self.app.run_lock.release()
        self._json(HTTPStatus.OK, asdict(result))

    def _post_empty_trash(self, body: dict[str, Any]) -> None:
        if body.get("confirm") != EMPTY_TRASH_PHRASE:
            self._json(
                HTTPStatus.BAD_REQUEST, f'Type "{EMPTY_TRASH_PHRASE}" to confirm.'
            )
            return
        if not self.app.run_lock.acquire(blocking=False):
            self._json(HTTPStatus.CONFLICT, "Another run is in progress.")
            return
        try:
            done, message = empty_trash(self.app.runner)
        finally:
            self.app.run_lock.release()
        self._json(HTTPStatus.OK, {"ok": done, "message": message})

    def do_GET(self) -> None:
        if not self._host_ok():
            self._forbidden()
            return
        url = urlsplit(self.path)
        if url.path == "/" and "token" in parse_qs(url.query):
            given = parse_qs(url.query)["token"][0]
            if secrets.compare_digest(given.encode(), self.app.token.encode()):
                cookie = (
                    f"{self.app.cookie_name}={self.app.token}; "
                    "HttpOnly; SameSite=Strict; Path=/"
                )
                self._send(
                    HTTPStatus.FOUND, headers={"Location": "/", "Set-Cookie": cookie}
                )
            else:
                self._forbidden()
            return
        if not self._cookie_ok():
            self._forbidden()
            return
        if url.path == "/":
            self._static("index.html")
        elif url.path.startswith("/static/"):
            self._static(unquote(url.path.removeprefix("/static/")))
        elif url.path == "/api/view":
            self._view()
        else:
            self._send(HTTPStatus.NOT_FOUND, b"Not found.")

    def _static(self, name: str) -> None:
        root = static_dir().resolve()
        target = (root / name).resolve()
        if not target.is_relative_to(root) or not target.is_file():
            self._send(HTTPStatus.NOT_FOUND, b"Not found.")
            return
        content_type = CONTENT_TYPES.get(target.suffix, "text/plain; charset=utf-8")
        self._send(HTTPStatus.OK, target.read_bytes(), content_type)

    def _view(self) -> None:
        try:
            view = load_view(self.app.scan_path, time.time(), self.app.findings_path)
        except ValueError as exc:
            self._send(HTTPStatus.INTERNAL_SERVER_ERROR, str(exc).encode())
            return
        body = json.dumps(view, ensure_ascii=True).encode()
        self._send(
            HTTPStatus.OK,
            body,
            "application/json",
            headers={"Cache-Control": "no-store"},
        )


def detect_brew_prefix() -> Path | None:
    for candidate in (Path("/opt/homebrew"), Path("/usr/local")):
        if (candidate / "bin" / "brew").exists():
            return candidate
    return None


def make_server(
    scan_path: Path,
    *,
    port: int = 0,
    token: str | None = None,
    findings_path: Path | None = None,
    runner: Runner = subprocess_runner,
    brew_prefix: Path | str | None = "detect",
) -> tuple[ScanServer, str]:
    token = token or secrets.token_urlsafe(32)
    prefix = detect_brew_prefix() if brew_prefix == "detect" else brew_prefix
    assert prefix is None or isinstance(prefix, Path)
    server = ScanServer(port, scan_path, token, findings_path, runner, prefix)
    return server, token


def page_url(server: ThreadingHTTPServer, token: str) -> str:
    return f"http://{HOST}:{server.server_address[1]}/?token={token}"


def serve(
    scan_path: Path,
    *,
    port: int = 0,
    open_browser: bool = True,
    findings_path: Path | None = None,
) -> None:
    """Check the scan, print the private link, and serve until Ctrl-C."""
    load_scan(scan_path)
    server, token = make_server(scan_path, port=port, findings_path=findings_path)
    url = page_url(server, token)
    print(f"Storage Explorer is showing {scan_path}")
    print(f"Open this link (it only works on this Mac):\n  {url}")
    print("Press Ctrl-C to stop.", flush=True)
    if open_browser:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
