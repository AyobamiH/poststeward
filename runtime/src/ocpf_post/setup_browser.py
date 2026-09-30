"""Milestone I loopback-only browser renderer for Setup & Recovery.

The browser is a presentation surface over SetupEngine. It owns no durable setup
truth, never binds beyond loopback, and cannot bypass existing review/apply gates.
"""
from __future__ import annotations

from html import escape
from http import HTTPStatus
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import secrets
import threading
from pathlib import Path
from typing import Any, Mapping, NoReturn
from urllib.parse import parse_qs, urlencode, urlsplit
import webbrowser

from ocpf_post.setup_activation import ServiceController
from ocpf_post.setup_engine import SetupEngine, SetupEngineError
from ocpf_post.setup_store import SetupStoreError, resolve_workspace

BIND_HOST = "127.0.0.1"
COOKIE_NAME = "post_once_setup"
MAX_REQUEST_BYTES = 128 * 1024


class SetupBrowserError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def _fail(code: str, message: str) -> NoReturn:
    raise SetupBrowserError(code, message)


def _h(value: Any) -> str:
    return escape(str(value), quote=True)


def _field(fields: Mapping[str, list[str]], name: str, *, required: bool = False) -> str:
    values = fields.get(name, [])
    value = values[-1].strip() if values else ""
    if required and not value:
        _fail("setup.browser.input_required", f"Missing browser field: {name}")
    return value


def _optional_int(fields: Mapping[str, list[str]], name: str) -> int | None:
    raw = _field(fields, name)
    if not raw:
        return None
    try:
        return int(raw)
    except ValueError as exc:
        raise SetupBrowserError("setup.browser.input_invalid", f"{name} must be a whole number") from exc


def _json_input(fields: Mapping[str, list[str]], name: str) -> Any:
    raw = _field(fields, name)
    if not raw:
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        raise SetupBrowserError("setup.browser.input_invalid_json", f"{name} is not valid JSON") from exc


def _hidden(name: str, value: Any) -> str:
    return f'<input type="hidden" name="{_h(name)}" value="{_h(value)}">'


def _input(name: str, label: str, *, required: bool = False, value: str = "", kind: str = "text") -> str:
    req = " required" if required else ""
    return (
        '<label class="field">'
        f'<span>{_h(label)}</span>'
        f'<input type="{_h(kind)}" name="{_h(name)}" value="{_h(value)}"{req}>'
        "</label>"
    )


def _textarea(name: str, label: str, *, required: bool = False, rows: int = 6) -> str:
    req = " required" if required else ""
    return (
        '<label class="field">'
        f'<span>{_h(label)}</span>'
        f'<textarea name="{_h(name)}" rows="{rows}"{req}></textarea>'
        "</label>"
    )


def _select(name: str, label: str, options: list[tuple[str, str]]) -> str:
    rows = "".join(f'<option value="{_h(v)}">{_h(t)}</option>' for v, t in options)
    return f'<label class="field"><span>{_h(label)}</span><select name="{_h(name)}">{rows}</select></label>'


def _form(action: str, csrf: str, body: str, submit: str, *, danger: bool = False) -> str:
    cls = "button danger" if danger else "button"
    return (
        f'<form method="post" action="{_h(action)}">'
        f'{_hidden("csrf_token", csrf)}'
        f"{body}"
        f'<button class="{cls}" type="submit">{_h(submit)}</button>'
        "</form>"
    )


def _page(title: str, body: str) -> str:
    return (
        "<!doctype html><html lang=\"en\"><head><meta charset=\"utf-8\">"
        "<meta name=\"viewport\" content=\"width=device-width,initial-scale=1\">"
        f"<title>{_h(title)}</title>"
        '<link rel="stylesheet" href="/assets/setup.css">'
        "</head><body>"
        f"{body}"
        "</body></html>"
    )


CSS = """
:root{color-scheme:dark;font-family:Inter,ui-sans-serif,system-ui,sans-serif;--bg:#0b0e12;--panel:#151a20;--line:#2b3540;--text:#f4f7fa;--muted:#a8b3bf;--accent:#8ad4ff;--danger:#ffb4ab}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--text)}.shell{width:min(1120px,calc(100% - 32px));margin:auto;padding:40px 0 70px}.narrow{width:min(860px,calc(100% - 32px))}
header{display:flex;justify-content:space-between;gap:20px;align-items:flex-start;margin-bottom:22px}h1{font-size:clamp(2rem,4vw,3.5rem);line-height:1;margin:8px 0 12px}h2{margin-top:0}p,li{color:var(--muted);line-height:1.55}
.card{background:var(--panel);border:1px solid var(--line);border-radius:16px;padding:22px;margin-bottom:18px}.grid{display:grid;gap:18px}.two{grid-template-columns:repeat(2,minmax(0,1fr))}.eyebrow{color:var(--accent);font-size:.75rem;font-weight:800;letter-spacing:.12em;text-transform:uppercase}
.field{display:block;margin:13px 0}.field span{display:block;color:var(--muted);font-size:.85rem;margin-bottom:6px}input,select,textarea{width:100%;background:#0f1419;color:var(--text);border:1px solid var(--line);border-radius:9px;padding:10px;font:inherit}.check{display:flex;gap:8px;color:var(--muted);margin:13px 0}.check input{width:auto}
.button{border:0;border-radius:10px;background:var(--accent);color:#062032;font-weight:800;padding:10px 14px;cursor:pointer}.danger{background:var(--danger);color:#35110d}.warning{border-left:3px solid var(--danger);padding:10px 12px;background:#241719;color:var(--text)}
.kv{display:grid;grid-template-columns:190px 1fr;gap:12px;border-top:1px solid var(--line);padding:9px 0}.kv span{color:var(--muted)}pre{overflow:auto;background:#07090b;border:1px solid var(--line);border-radius:10px;padding:13px;font-size:.82rem}.back{color:var(--accent);text-decoration:none}details{margin-top:14px}summary{cursor:pointer}.pill{border:1px solid var(--line);border-radius:999px;padding:7px 11px;color:#b7f7d1;white-space:nowrap}
@media(max-width:760px){.two{grid-template-columns:1fr}header{display:block}.kv{grid-template-columns:1fr}}
"""


def _status_card(value: dict[str, Any] | None) -> str:
    if value is None:
        return '<section class="card"><h2>No setup session yet</h2><p>Start fresh, explore-only, or healthy-migration source setup below.</p></section>'
    session = value.get("session") or {}
    operation = value.get("operation") or {}
    installation = value.get("installation") or {}
    rows = [
        ("Mode", value.get("mode", "—")),
        ("Stage", session.get("stage", "—")),
        ("Revision", session.get("revision", "—")),
        ("Operation", operation.get("operation_id", "—")),
        ("Installation", installation.get("installation_id", "—")),
        ("Authority generation", operation.get("authority_generation", "—")),
        ("Publishing authority", "YES" if value.get("publishing_authority") else "NO"),
        ("Automation enabled", "YES" if value.get("automation_enabled") else "NO"),
    ]
    html = "".join(f'<div class="kv"><span>{_h(k)}</span><strong>{_h(v)}</strong></div>' for k, v in rows)
    blockers = value.get("blockers") or []
    next_actions = value.get("next_actions") or []
    if blockers:
        html += "<h3>Still needed</h3><ul>" + "".join(f"<li><code>{_h(x)}</code></li>" for x in blockers) + "</ul>"
    if next_actions:
        html += "<h3>Next actions</h3><ul>" + "".join(f"<li><code>{_h(x)}</code></li>" for x in next_actions) + "</ul>"
    html += "<details><summary>Machine-readable state</summary><pre>" + _h(json.dumps(value, indent=2, sort_keys=True)) + "</pre></details>"
    return '<section class="card"><div class="eyebrow">Current setup state</div>' + html + "</section>"


def _home(csrf: str, status: dict[str, Any] | None) -> str:
    start = (
        _select("mode", "Setup mode", [("fresh", "Fresh operation"), ("explore", "Explore only"), ("migrate", "Healthy migration source")])
        + _input("operator_label", "Operator/business label")
        + _input("machine_label", "Machine label")
        + _input("timezone", "Timezone")
        + _select("pace", "Normal pace", [("", "Choose for fresh/explore"), ("occasional", "Occasional — 3/day"), ("regular", "Regular — 5/day"), ("active", "Active — 10/day"), ("high", "High — 20/day"), ("custom", "Custom")])
        + _input("daily_originals", "Custom originals/day", kind="number")
    )
    resume = _input("session_id", "Session ID (optional)") + _input("timezone", "Timezone if still required") + _input("pace", "Pace if still required") + _input("daily_originals", "Custom originals/day", kind="number")
    transfer = (
        _input("session_id", "Session ID (optional)")
        + _input("source_state_dir", "Source state directory", required=True)
        + _input("source_config_dir", "Source config directory", required=True)
        + _input("output", "Bundle output path", required=True)
        + _input("source_version", "Source version", required=True, value="0.28.29")
        + _input("source_revision", "Source Git revision", required=True)
        + _input("state_registry_version", "State registry version", value="1", kind="number")
    )
    inspect = _input("bundle", "Bundle path", required=True) + _input("expected_bundle_sha256", "Expected bundle SHA-256") + '<label class="check"><input type="checkbox" name="recovery" value="1"> Recovery point</label>'
    restore = inspect + _input("machine_label", "New machine label") + _input("source_lost_at", "Source lost at (recovery)") + _input("max_data_loss_minutes", "Max data loss minutes", kind="number")
    verify = _input("session_id", "Session ID (optional)") + _input("provider_readiness", "Provider readiness JSON file path", required=True) + '<label class="check"><input type="checkbox" name="recovery" value="1"> Recovery verification</label>'
    activate = _input("session_id", "Session ID (optional)") + _input("runtime_root", "Runtime checkout", value=".", required=True) + _input("state_dir", "Target production state directory", required=True) + _input("config_dir", "Target production config directory", required=True) + _input("recovery_resolution", "Recovery resolution JSON file path")
    deactivate = _input("session_id", "Session ID (optional)") + _input("runtime_root", "Runtime checkout", value=".", required=True) + _input("state_dir", "Production state directory", required=True) + _textarea("reason", "Deactivation reason", required=True)
    body = (
        '<main class="shell"><header><div><div class="eyebrow">Post-Once Setup &amp; Recovery</div><h1>Local setup control</h1><p>Server-rendered loopback UI over the same SetupEngine used by the terminal.</p></div><div class="pill">127.0.0.1 only</div></header>'
        + _status_card(status)
        + '<section class="grid two"><section class="card"><h2>Start setup</h2>' + _form("/action/start", csrf, start, "Start setup") + '</section><section class="card"><h2>Resume setup</h2>' + _form("/action/resume", csrf, resume, "Resume") + "</section></section>"
        + '<details class="card"><summary><strong>Migration and recovery</strong></summary><div class="grid two"><section><h3>Healthy migration export</h3>' + _form("/action/export-preview", csrf, transfer, "Preview migration export") + '<h3>Recovery point</h3>' + _form("/action/recovery-point-preview", csrf, transfer, "Preview recovery point") + '</section><section><h3>Inspect bundle</h3>' + _form("/action/inspect", csrf, inspect, "Inspect bundle") + '<h3>Restore</h3>' + _form("/action/restore", csrf, restore, "Restore to quarantine") + '<h3>Verify target</h3>' + _form("/action/verify", csrf, verify, "Verify readiness") + "</section></div></details>"
        + '<details class="card"><summary><strong>Activation authority</strong></summary><p class="warning">Preview is mandatory before any authority-changing apply.</p><div class="grid two"><section><h3>Activate</h3>' + _form("/action/activate-preview", csrf, activate, "Preview activation") + '</section><section><h3>Deactivate</h3>' + _form("/action/deactivate-preview", csrf, deactivate, "Preview deactivation") + "</section></div></details></main>"
    )
    return _page("Post-Once Setup & Recovery", body)


def _result_page(title: str, value: dict[str, Any], csrf: str, *, apply_action: str | None = None, preserved: Mapping[str, str] | None = None, digest: str | None = None, apply_label: str = "Apply reviewed action", warning: str = "") -> str:
    apply = ""
    if apply_action and preserved is not None and digest:
        hidden = "".join(_hidden(k, v) for k, v in preserved.items())
        hidden += _hidden("expected_sha256", digest)
        confirm = '<label class="check"><input type="checkbox" name="confirm" value="1" required> I reviewed the exact digest and consequence boundary.</label>'
        apply = _form(apply_action, csrf, hidden + f'<p class="warning">{_h(warning)}</p>' + confirm, apply_label, danger=True)
    body = (
        '<main class="shell narrow"><a class="back" href="/">← Back</a><section class="card">'
        f'<div class="eyebrow">Reviewed result</div><h1>{_h(title)}</h1>'
        + (f'<div class="kv"><span>Review SHA-256</span><strong>{_h(digest)}</strong></div>' if digest else "")
        + (f'<div class="kv"><span>Publishing authority</span><strong>{"YES" if value.get("publishing_authority") else "NO"}</strong></div>' if "publishing_authority" in value else "")
        + (f'<div class="kv"><span>Automation enabled</span><strong>{"YES" if value.get("automation_enabled") else "NO"}</strong></div>' if "automation_enabled" in value else "")
        + apply
        + "<details open><summary>Exact machine-readable result</summary><pre>"
        + _h(json.dumps(value, indent=2, sort_keys=True))
        + "</pre></details></section></main>"
    )
    return _page(title, body)


class SetupBrowserApp:
    def __init__(
        self,
        workspace: str | Path | None = None,
        *,
        service_controller: ServiceController | None = None,
    ) -> None:
        self.workspace = resolve_workspace(workspace)
        self.engine = SetupEngine(self.workspace)
        self.service_controller = service_controller
        self.bootstrap_token: str | None = secrets.token_urlsafe(32)
        self.session_token = secrets.token_urlsafe(32)
        self.csrf_token = secrets.token_urlsafe(32)
        self.origin: str | None = None
        self._bootstrap_lock = threading.Lock()

    def bootstrap_url(self, port: int) -> str:
        token = self.bootstrap_token
        if token is None:
            _fail("setup.browser.bootstrap_consumed", "The one-time browser bootstrap URL has already been used")
        return f"http://{BIND_HOST}:{port}/?{urlencode({'session': token})}"

    def exchange_bootstrap(self, candidate: str) -> bool:
        with self._bootstrap_lock:
            token = self.bootstrap_token
            if token is None or not candidate or not secrets.compare_digest(candidate, token):
                return False
            self.bootstrap_token = None
            return True

    def status(self) -> dict[str, Any] | None:
        try:
            return self.engine.status()
        except (SetupEngineError, SetupStoreError) as exc:
            if getattr(exc, "code", "") in {"setup.store.missing", "setup.session.missing"}:
                return None
            raise

    def home(self) -> str:
        return _home(self.csrf_token, self.status())

    @staticmethod
    def _load_json_file(path: str) -> Any:
        source = Path(path).expanduser()
        if not source.is_file():
            _fail("setup.browser.input_missing", f"JSON file does not exist: {source}")
        try:
            return json.loads(source.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise SetupBrowserError("setup.browser.input_invalid_json", f"Invalid JSON file: {source}") from exc

    def perform(self, path: str, fields: Mapping[str, list[str]]) -> tuple[str, dict[str, Any], dict[str, str] | None, str | None]:
        keep = {k: v[-1] for k, v in fields.items() if v and k not in {"csrf_token", "confirm", "expected_sha256"}}
        if path == "/action/start":
            value = self.engine.start(
                _field(fields, "mode", required=True),
                operator_label=_field(fields, "operator_label") or None,
                timezone=_field(fields, "timezone") or None,
                pace=_field(fields, "pace") or None,
                daily_originals=_optional_int(fields, "daily_originals"),
                machine_label=_field(fields, "machine_label") or None,
            )
            return "Setup started", value, None, None
        if path == "/action/resume":
            value = self.engine.resume(
                session_id=_field(fields, "session_id") or None,
                timezone=_field(fields, "timezone") or None,
                pace=_field(fields, "pace") or None,
                daily_originals=_optional_int(fields, "daily_originals"),
            )
            return "Setup resumed", value, None, None
        if path in {"/action/export-preview", "/action/export-apply"}:
            apply = path.endswith("-apply")
            if apply and _field(fields, "confirm") != "1":
                _fail("setup.browser.confirmation_required", "Migration export confirmation is required")
            value = self.engine.export_migration(
                state_root=_field(fields, "source_state_dir", required=True),
                config_root=_field(fields, "source_config_dir", required=True),
                output=_field(fields, "output", required=True),
                source_post_once_version=_field(fields, "source_version", required=True),
                source_revision=_field(fields, "source_revision", required=True),
                source_state_registry_version=_optional_int(fields, "state_registry_version") or 1,
                session_id=_field(fields, "session_id") or None,
                apply=apply,
                expected_sha256=_field(fields, "expected_sha256") or None,
            )
            digest = None if apply else str((value.get("migration_export") or {}).get("review_sha256") or "")
            return ("Migration bundle sealed" if apply else "Migration export preview"), value, (None if apply else keep), digest
        if path in {"/action/recovery-point-preview", "/action/recovery-point-apply"}:
            apply = path.endswith("-apply")
            if apply and _field(fields, "confirm") != "1":
                _fail("setup.browser.confirmation_required", "Recovery-point confirmation is required")
            value = self.engine.recovery_point(
                state_root=_field(fields, "source_state_dir", required=True),
                config_root=_field(fields, "source_config_dir", required=True),
                output=_field(fields, "output", required=True),
                source_post_once_version=_field(fields, "source_version", required=True),
                source_revision=_field(fields, "source_revision", required=True),
                source_state_registry_version=_optional_int(fields, "state_registry_version") or 1,
                session_id=_field(fields, "session_id") or None,
                apply=apply,
                expected_sha256=_field(fields, "expected_sha256") or None,
            )
            digest = None if apply else str((value.get("recovery_point_export") or {}).get("review_sha256") or "")
            return ("Recovery point sealed" if apply else "Recovery-point preview"), value, (None if apply else keep), digest
        if path == "/action/inspect":
            value = self.engine.inspect_bundle(
                _field(fields, "bundle", required=True),
                expected_bundle_sha256=_field(fields, "expected_bundle_sha256") or None,
                recovery=_field(fields, "recovery") == "1",
            )
            return "Bundle inspection", value, None, None
        if path == "/action/restore":
            if _field(fields, "recovery") == "1":
                gap = _optional_int(fields, "max_data_loss_minutes")
                if gap is None:
                    _fail("setup.browser.input_required", "Recovery restore requires max_data_loss_minutes")
                value = self.engine.restore_recovery(
                    _field(fields, "bundle", required=True),
                    source_lost_at=_field(fields, "source_lost_at", required=True),
                    max_data_loss_minutes=gap,
                    expected_bundle_sha256=_field(fields, "expected_bundle_sha256") or None,
                    machine_label=_field(fields, "machine_label") or None,
                )
            else:
                value = self.engine.restore_migration(
                    _field(fields, "bundle", required=True),
                    expected_bundle_sha256=_field(fields, "expected_bundle_sha256") or None,
                    machine_label=_field(fields, "machine_label") or None,
                )
            return "Restore complete", value, None, None
        if path == "/action/verify":
            readiness = self._load_json_file(_field(fields, "provider_readiness", required=True))
            if _field(fields, "recovery") == "1":
                value = self.engine.verify_recovery(readiness, session_id=_field(fields, "session_id") or None)
            else:
                value = self.engine.verify_migration(readiness, session_id=_field(fields, "session_id") or None)
            return "Target verification", value, None, None
        if path in {"/action/activate-preview", "/action/activate-apply"}:
            apply = path.endswith("-apply")
            if apply and _field(fields, "confirm") != "1":
                _fail("setup.browser.confirmation_required", "Activation confirmation is required")
            resolution_path = _field(fields, "recovery_resolution")
            resolution = self._load_json_file(resolution_path) if resolution_path else None
            runtime_root = _field(fields, "runtime_root", required=True)
            state_root = _field(fields, "state_dir", required=True)
            config_root = _field(fields, "config_dir", required=True)
            session_id = _field(fields, "session_id") or None
            if apply:
                value = self.engine.activate(
                    runtime_root=runtime_root,
                    state_root=state_root,
                    config_root=config_root,
                    expected_sha256=_field(fields, "expected_sha256", required=True),
                    session_id=session_id,
                    recovery_resolution=resolution,
                    service_controller=self.service_controller,
                )
                return "Activation applied", value, None, None
            value = self.engine.activation_preview(
                runtime_root=runtime_root,
                state_root=state_root,
                config_root=config_root,
                session_id=session_id,
                recovery_resolution=resolution,
                service_controller=self.service_controller,
            )
            return "Activation preview", value, keep, str(value.get("review_sha256") or "")
        if path in {"/action/deactivate-preview", "/action/deactivate-apply"}:
            apply = path.endswith("-apply")
            if apply and _field(fields, "confirm") != "1":
                _fail("setup.browser.confirmation_required", "Deactivation confirmation is required")
            runtime_root = _field(fields, "runtime_root", required=True)
            state_root = _field(fields, "state_dir", required=True)
            reason = _field(fields, "reason", required=True)
            session_id = _field(fields, "session_id") or None
            if apply:
                value = self.engine.deactivate(
                    runtime_root=runtime_root,
                    state_root=state_root,
                    reason=reason,
                    expected_sha256=_field(fields, "expected_sha256", required=True),
                    session_id=session_id,
                    service_controller=self.service_controller,
                )
                return "Deactivation applied", value, None, None
            value = self.engine.deactivation_preview(
                runtime_root=runtime_root,
                state_root=state_root,
                reason=reason,
                session_id=session_id,
                service_controller=self.service_controller,
            )
            return "Deactivation preview", value, keep, str(value.get("review_sha256") or "")
        _fail("setup.browser.route_not_found", f"Unknown browser action: {path}")

    def render_result(self, path: str, title: str, value: dict[str, Any], preserved: dict[str, str] | None, digest: str | None) -> str:
        if preserved is None or not digest:
            return _result_page(title, value, self.csrf_token)
        if path == "/action/export-preview":
            return _result_page(title, value, self.csrf_token, apply_action="/action/export-apply", preserved=preserved, digest=digest, apply_label="Seal reviewed migration bundle", warning="Apply retires only the bootstrap source authority model and seals the reviewed bundle. It does not enable target publishing.")
        if path == "/action/recovery-point-preview":
            return _result_page(title, value, self.csrf_token, apply_action="/action/recovery-point-apply", preserved=preserved, digest=digest, apply_label="Seal reviewed recovery point", warning="Apply seals an immutable credential-free recovery point without retiring the source.")
        if path == "/action/activate-preview":
            return _result_page(title, value, self.csrf_token, apply_action="/action/activate-apply", preserved=preserved, digest=digest, apply_label="Enable unattended publishing", warning="This authority change can enable unattended provider consequences only after the accepted H checks pass.")
        if path == "/action/deactivate-preview":
            return _result_page(title, value, self.csrf_token, apply_action="/action/deactivate-apply", preserved=preserved, digest=digest, apply_label="Deactivate unattended publishing", warning="The authority marker closes before timers stop. Durable receipts and schedules are not rolled back.")
        return _result_page(title, value, self.csrf_token)


class _LoopbackServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = False

    def __init__(self, address: tuple[str, int], app: SetupBrowserApp) -> None:
        self.app = app
        super().__init__(address, SetupBrowserHandler)
        self.app.origin = f"http://{BIND_HOST}:{self.server_port}"


class SetupBrowserHandler(BaseHTTPRequestHandler):
    server: _LoopbackServer
    server_version = "PostOnceSetup"
    sys_version = ""

    def log_message(self, format: str, *args: Any) -> None:
        return

    @property
    def app(self) -> SetupBrowserApp:
        return self.server.app

    def _headers(self) -> None:
        self.send_header("Cache-Control", "no-store, max-age=0")
        self.send_header("Pragma", "no-cache")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Cross-Origin-Opener-Policy", "same-origin")
        self.send_header("Cross-Origin-Resource-Policy", "same-origin")
        self.send_header("Content-Security-Policy", "default-src 'none'; style-src 'self'; form-action 'self'; base-uri 'none'; frame-ancestors 'none'; object-src 'none'")
        self.send_header("Permissions-Policy", "camera=(), microphone=(), geolocation=(), payment=(), usb=(), serial=()")

    def _host_ok(self) -> bool:
        return self.headers.get("Host", "") == f"{BIND_HOST}:{self.server.server_port}"

    def _session_ok(self) -> bool:
        cookie = SimpleCookie()
        try:
            cookie.load(self.headers.get("Cookie", ""))
        except Exception:
            return False
        value = cookie.get(COOKIE_NAME)
        return bool(value and secrets.compare_digest(value.value, self.app.session_token))

    def _origin_ok(self) -> bool:
        expected = self.app.origin or ""
        origin = self.headers.get("Origin")
        if origin:
            return secrets.compare_digest(origin, expected)
        referer = self.headers.get("Referer")
        return bool(referer and (referer == expected or referer.startswith(expected + "/")))

    def _send(self, status: HTTPStatus, body: str, *, kind: str = "text/html; charset=utf-8", cookie: str | None = None, location: str | None = None) -> None:
        raw = body.encode("utf-8")
        self.send_response(status.value)
        self._headers()
        self.send_header("Content-Type", kind)
        if cookie:
            self.send_header("Set-Cookie", cookie)
        if location:
            self.send_header("Location", location)
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        if raw:
            self.wfile.write(raw)

    def _error(self, status: HTTPStatus, code: str, message: str) -> None:
        body = _page(f"{status.value} {status.phrase}", f'<main class="shell narrow"><section class="card"><div class="eyebrow">{_h(code)}</div><h1>{_h(status.phrase)}</h1><p>{_h(message)}</p><a class="back" href="/">Back</a></section></main>')
        self._send(status, body)

    def do_GET(self) -> None:
        if not self._host_ok():
            self._error(HTTPStatus.FORBIDDEN, "setup.browser.host_rejected", "Unexpected Host header")
            return
        parsed = urlsplit(self.path)
        if parsed.path == "/" and not self._session_ok():
            token = parse_qs(parsed.query, keep_blank_values=True).get("session", [""])[-1]
            if self.app.exchange_bootstrap(token):
                cookie = f"{COOKIE_NAME}={self.app.session_token}; Path=/; HttpOnly; SameSite=Strict"
                self._send(HTTPStatus.SEE_OTHER, "", cookie=cookie, location="/")
                return
            self._error(HTTPStatus.FORBIDDEN, "setup.browser.session_required", "Use the one-time loopback URL printed by the setup command")
            return
        if not self._session_ok():
            self._error(HTTPStatus.FORBIDDEN, "setup.browser.session_required", "Browser session is not authorised")
            return
        if parsed.path == "/assets/setup.css":
            self._send(HTTPStatus.OK, CSS, kind="text/css; charset=utf-8")
            return
        if parsed.path != "/":
            self._error(HTTPStatus.NOT_FOUND, "setup.browser.route_not_found", "Unknown browser route")
            return
        try:
            self._send(HTTPStatus.OK, self.app.home())
        except (SetupEngineError, SetupStoreError, SetupBrowserError, ValueError) as exc:
            self._error(HTTPStatus.BAD_REQUEST, getattr(exc, "code", "setup.browser.error"), str(exc))

    def do_POST(self) -> None:
        if not self._host_ok():
            self._error(HTTPStatus.FORBIDDEN, "setup.browser.host_rejected", "Unexpected Host header")
            return
        if not self._session_ok():
            self._error(HTTPStatus.FORBIDDEN, "setup.browser.session_required", "Browser session is not authorised")
            return
        if not self._origin_ok():
            self._error(HTTPStatus.FORBIDDEN, "setup.browser.origin_rejected", "Cross-origin browser mutation was rejected")
            return
        parsed = urlsplit(self.path)
        if not parsed.path.startswith("/action/"):
            self._error(HTTPStatus.NOT_FOUND, "setup.browser.route_not_found", "Unknown browser route")
            return
        if self.headers.get("Content-Type", "").split(";", 1)[0].strip().lower() != "application/x-www-form-urlencoded":
            self._error(HTTPStatus.UNSUPPORTED_MEDIA_TYPE, "setup.browser.content_type_rejected", "Only form submissions are accepted")
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            length = -1
        if length < 0 or length > MAX_REQUEST_BYTES:
            self._error(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, "setup.browser.request_too_large", "Request exceeds the bounded local setup limit")
            return
        try:
            fields = parse_qs(self.rfile.read(length).decode("utf-8"), keep_blank_values=True)
        except UnicodeDecodeError:
            self._error(HTTPStatus.BAD_REQUEST, "setup.browser.input_invalid", "Form body is not UTF-8")
            return
        token = _field(fields, "csrf_token")
        if not token or not secrets.compare_digest(token, self.app.csrf_token):
            self._error(HTTPStatus.FORBIDDEN, "setup.browser.csrf_rejected", "Browser form token is missing or invalid")
            return
        try:
            title, value, preserved, digest = self.app.perform(parsed.path, fields)
            self._send(HTTPStatus.OK, self.app.render_result(parsed.path, title, value, preserved, digest))
        except (SetupEngineError, SetupStoreError, SetupBrowserError, ValueError) as exc:
            self._error(HTTPStatus.BAD_REQUEST, getattr(exc, "code", "setup.browser.error"), str(exc))


def build_server(
    workspace: str | Path | None = None,
    *,
    port: int = 0,
    service_controller: ServiceController | None = None,
) -> tuple[_LoopbackServer, SetupBrowserApp]:
    if type(port) is not int or not 0 <= port <= 65535:
        _fail("setup.browser.port_invalid", "Browser port must be between 0 and 65535")
    app = SetupBrowserApp(workspace, service_controller=service_controller)
    try:
        server = _LoopbackServer((BIND_HOST, port), app)
    except OSError as exc:
        raise SetupBrowserError("setup.browser.bind_failed", f"Could not bind loopback browser server: {exc}") from exc
    return server, app


def serve(workspace: str | Path | None = None, *, port: int = 0, launch_browser: bool = True) -> None:
    server, app = build_server(workspace, port=port)
    url = app.bootstrap_url(server.server_port)
    print("Post-Once Setup & Recovery browser")
    print(f"Loopback URL: {url}")
    print("Boundary: 127.0.0.1 only. Closing this process does not change setup or publishing authority.")
    if launch_browser:
        try:
            opened = bool(webbrowser.open(url, new=1))
        except Exception:
            opened = False
        if not opened:
            print("Browser launch unavailable. Open the loopback URL above manually.")
    else:
        print("Automatic browser launch disabled. Open the loopback URL above manually.")
    try:
        server.serve_forever(poll_interval=0.25)
    except KeyboardInterrupt:
        print("\nBrowser renderer stopped. Durable setup progress remains unchanged.")
    finally:
        server.server_close()
