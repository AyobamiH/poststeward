"""Loopback-only, read-only HTTP/SSE server for local observability."""
from __future__ import annotations

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib.resources import files
import json
import threading
import time
from typing import Any
from urllib.parse import urlsplit

from ocpf_post.execution_observer import build_execution_snapshot
from ocpf_post.observer import build_snapshot
from ocpf_post import operator_guide

HOST = "127.0.0.1"
DEFAULT_PORT = 8767
SNAPSHOT_INTERVAL_SECONDS = 2.0
SNAPSHOT_CACHE_SECONDS = 5.0
HEARTBEAT_SECONDS = 15.0
MAX_STREAM_SECONDS = 60 * 60
ALLOWED_HOSTS = {"127.0.0.1", "localhost"}


class ConsoleServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, *args: Any, **kwargs: Any):
        super().__init__(*args, **kwargs)
        self._snapshot_lock = threading.Lock()
        self._snapshot_cache: dict[str, Any] | None = None
        self._snapshot_cached_at = 0.0

    def snapshot(self) -> dict[str, Any]:
        """Share one expensive read model across HTTP and SSE readers."""
        now = time.monotonic()
        cached = self._snapshot_cache
        if cached is not None and now - self._snapshot_cached_at < SNAPSHOT_CACHE_SECONDS:
            return cached
        with self._snapshot_lock:
            now = time.monotonic()
            cached = self._snapshot_cache
            if cached is not None and now - self._snapshot_cached_at < SNAPSHOT_CACHE_SECONDS:
                return cached
            value = build_execution_snapshot(base=build_snapshot())
            value["guide"] = operator_guide.build(value)
            self._snapshot_cache = value
            self._snapshot_cached_at = time.monotonic()
            return value


class ConsoleHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "post-once-console"
    sys_version = ""

    def log_message(self, _format: str, *_args: Any) -> None:
        # Browser polling/SSE reconnects are routine; avoid noisy access logs that
        # could obscure the systemd/runtime journal the console is observing.
        return

    def _headers(self, content_type: str, length: int | None = None) -> None:
        self.send_header("Content-Type", content_type)
        if length is not None:
            self.send_header("Content-Length", str(length))
        self.send_header("Cache-Control", "no-store, max-age=0")
        self.send_header("Pragma", "no-cache")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Cross-Origin-Resource-Policy", "same-origin")
        self.send_header("Permissions-Policy", "camera=(), microphone=(), geolocation=(), payment=(), usb=()")
        self.send_header(
            "Content-Security-Policy",
            "default-src 'self'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'; "
            "img-src 'self' data:; style-src 'self' 'unsafe-inline'; script-src 'self' 'unsafe-inline'; "
            "connect-src 'self'; object-src 'none'",
        )

    def _send_bytes(self, status: int, body: bytes, content_type: str) -> None:
        self.send_response(status)
        self._headers(content_type, len(body))
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _json(self, value: Any, status: int = 200) -> None:
        body = json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode("utf-8")
        self._send_bytes(status, body, "application/json; charset=utf-8")

    def _method_not_allowed(self) -> None:
        self.send_response(405)
        self.send_header("Allow", "GET, HEAD")
        self._headers("application/json; charset=utf-8", 0)
        self.end_headers()

    def _host_allowed(self) -> bool:
        raw = str(self.headers.get("Host") or "").strip()
        if not raw:
            return False
        try:
            hostname = urlsplit("//" + raw).hostname
        except ValueError:
            return False
        return hostname in ALLOWED_HOSTS

    def do_HEAD(self) -> None:  # noqa: N802
        self._route()

    def do_GET(self) -> None:  # noqa: N802
        self._route()

    def do_POST(self) -> None:  # noqa: N802
        self._method_not_allowed()

    def do_PUT(self) -> None:  # noqa: N802
        self._method_not_allowed()

    def do_PATCH(self) -> None:  # noqa: N802
        self._method_not_allowed()

    def do_DELETE(self) -> None:  # noqa: N802
        self._method_not_allowed()

    def do_OPTIONS(self) -> None:  # noqa: N802
        self._method_not_allowed()

    def _snapshot(self) -> dict[str, Any]:
        # HTTP snapshot reads and SSE clients share one bounded cache so a browser
        # cannot multiply full-ledger projection work merely by opening both paths.
        return self.server.snapshot()  # type: ignore[attr-defined]

    def _route(self) -> None:
        if not self._host_allowed():
            self._json({"error": "loopback_host_required"}, 421)
            return
        path = urlsplit(self.path).path
        if path in {"/", "/execution", "/execution.html"}:
            body = (files("ocpf_post") / "ui" / "execution.html").read_bytes()
            body = operator_guide.decorate_page(body)
            self._send_bytes(200, body, "text/html; charset=utf-8")
            return
        if path in {"/classic", "/index.html"}:
            body = (files("ocpf_post") / "ui" / "index.html").read_bytes()
            self._send_bytes(200, body, "text/html; charset=utf-8")
            return
        if path in {"/guide.css", "/guide.js"}:
            name = path[1:]
            mime = "text/css; charset=utf-8" if name.endswith(".css") else "text/javascript; charset=utf-8"
            self._send_bytes(200, (files("ocpf_post") / "ui" / name).read_bytes(), mime)
            return
        if path == "/api/guide/commands":
            self._json(operator_guide.command_library())
            return
        if path == "/api/snapshot":
            self._json(self._snapshot())
            return
        if path == "/api/stream":
            if self.command == "HEAD":
                self._send_bytes(200, b"", "text/event-stream; charset=utf-8")
            else:
                self._stream()
            return
        if path == "/healthz":
            self._json({"status": "ok", "read_only": True, "local_only": True})
            return
        self._json({"error": "not_found"}, 404)

    def _event(self, name: str, value: Any) -> None:
        body = json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
        self.wfile.write(f"event: {name}\ndata: {body}\n\n".encode("utf-8"))
        self.wfile.flush()

    def _stream(self) -> None:
        self.send_response(200)
        self._headers("text/event-stream; charset=utf-8")
        self.send_header("Connection", "keep-alive")
        self.send_header("X-Accel-Buffering", "no")
        self.end_headers()

        started = time.monotonic()
        heartbeat_at = started
        previous_revision: str | None = None
        try:
            while time.monotonic() - started < MAX_STREAM_SECONDS:
                snapshot = self._snapshot()
                revision = str(snapshot.get("revision") or "")
                if revision != previous_revision:
                    self._event("snapshot", snapshot)
                    previous_revision = revision
                now = time.monotonic()
                if now - heartbeat_at >= HEARTBEAT_SECONDS:
                    self._event("heartbeat", {"at": time.time(), "read_only": True,
                                              "observed_at": snapshot.get("observed_at"),
                                              "revision": revision})
                    heartbeat_at = now
                time.sleep(SNAPSHOT_INTERVAL_SECONDS)
        except (BrokenPipeError, ConnectionResetError, TimeoutError):
            return


def serve(*, port: int = DEFAULT_PORT) -> None:
    if type(port) is not int or not 1024 <= port <= 65535:
        raise ValueError("Console port must be an integer between 1024 and 65535")
    server = ConsoleServer((HOST, port), ConsoleHandler)
    print(f"post-once read-only console: http://{HOST}:{port}")
    print("Boundary: loopback-only GET/HEAD + SSE; no mutation or provider routes are exposed.")
    try:
        server.serve_forever(poll_interval=0.5)
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
