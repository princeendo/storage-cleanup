"""A read-only, token-guarded web server for one scan, bound to this Mac only."""

import json
import secrets
import time
import webbrowser
from http import HTTPStatus
from http.cookies import CookieError, SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib.resources import files
from pathlib import Path
from typing import Any, Final, cast
from urllib.parse import parse_qs, unquote, urlsplit

from storage_cleanup.scan.model import Scan, scan_from_json
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


def load_scan(path: Path) -> Scan:
    """Read a scan file, or raise ValueError with a message a person can act on."""
    try:
        return scan_from_json(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, KeyError, TypeError) as exc:
        reason = exc.strerror if isinstance(exc, OSError) and exc.strerror else exc
        raise ValueError(f"Cannot read scan {path}: {reason}") from exc


class ScanServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, port: int, scan_path: Path, token: str) -> None:
        self.scan_path = scan_path
        self.token = token
        super().__init__((HOST, port), _Handler)

    @property
    def port(self) -> int:
        return int(self.server_address[1])


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
        morsel = cookie.get(TOKEN_COOKIE)
        return morsel is not None and secrets.compare_digest(
            morsel.value.encode(), self.app.token.encode()
        )

    def _not_allowed(self) -> None:
        self._send(HTTPStatus.METHOD_NOT_ALLOWED, b"This page is read-only.")

    do_POST = do_PUT = do_DELETE = do_PATCH = do_HEAD = do_OPTIONS = _not_allowed

    def do_GET(self) -> None:
        if not self._host_ok():
            self._forbidden()
            return
        url = urlsplit(self.path)
        if url.path == "/" and "token" in parse_qs(url.query):
            given = parse_qs(url.query)["token"][0]
            if secrets.compare_digest(given.encode(), self.app.token.encode()):
                cookie = (
                    f"{TOKEN_COOKIE}={self.app.token}; "
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
            scan = load_scan(self.app.scan_path)
        except ValueError as exc:
            self._send(HTTPStatus.INTERNAL_SERVER_ERROR, str(exc).encode())
            return
        body = json.dumps(build_view(scan, time.time()), ensure_ascii=True).encode()
        self._send(
            HTTPStatus.OK,
            body,
            "application/json",
            headers={"Cache-Control": "no-store"},
        )


def make_server(
    scan_path: Path, *, port: int = 0, token: str | None = None
) -> tuple[ScanServer, str]:
    token = token or secrets.token_urlsafe(32)
    return ScanServer(port, scan_path, token), token


def page_url(server: ThreadingHTTPServer, token: str) -> str:
    return f"http://{HOST}:{server.server_address[1]}/?token={token}"


def serve(scan_path: Path, *, port: int = 0, open_browser: bool = True) -> None:
    """Check the scan, print the private link, and serve until Ctrl-C."""
    load_scan(scan_path)
    server, token = make_server(scan_path, port=port)
    url = page_url(server, token)
    print(f"Storage Explorer is showing {scan_path}")
    print(f"Open this link (it only works on this Mac):\n  {url}")
    print("Press Ctrl-C to stop.")
    if open_browser:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
