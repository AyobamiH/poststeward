#!/usr/bin/env python3
from __future__ import annotations

from html import unescape
from http.client import HTTPConnection
import json
from pathlib import Path
import re
import tempfile
import threading
from urllib.parse import urlencode

from ocpf_post import automation_authority
from ocpf_post.setup_browser import BIND_HOST, build_server
from ocpf_post.setup_engine import SetupEngine

ROOT = Path(__file__).resolve().parents[1]
REVISION = "a" * 40
QUIESCENT = {
    "schema_version": 1,
    "status": "quiescent",
    "units": [],
    "active_writer_units": [],
    "mutation_attempted": False,
    "boundary": "Milestone I clean-room fixture",
}


def write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(json.dumps(row, separators=(",", ":")) + "\n" for row in rows),
        encoding="utf-8",
    )


def source_tree(root: Path) -> tuple[Path, Path]:
    state = root / "source-state"
    config = root / "source-config"
    state.mkdir()
    config.mkdir()
    write_jsonl(
        state / "schedule-events.jsonl",
        [{
            "schedule_id": "sch_browser_future",
            "event": "scheduled",
            "status": "scheduled",
            "recorded_at": "2026-09-27T08:00:00Z",
            "run_at": "2030-09-30T12:00:00Z",
            "provider": "x",
            "account_id": "123456",
            "campaign": "BROWSER-1",
        }],
    )
    write_jsonl(
        state / "publish-receipts.jsonl",
        [{
            "campaign": "OLD-BROWSER-1",
            "provider": "x",
            "status": "published_verified",
            "recorded_at": "2026-09-26T12:00:00Z",
        }],
    )
    (config / "portfolio-policy.json").write_text(
        '{"schema_version":1,"daily_target":5}\n',
        encoding="utf-8",
    )
    (config / "account-profiles.json").write_text(
        '{"schema_version":1,"accounts":{"x-main":{"provider":"x","account_id":"123456"}}}\n',
        encoding="utf-8",
    )
    (config / "x-token.json").write_text(
        '{"access_token":"SOURCE_SECRET_MUST_NOT_MOVE"}\n',
        encoding="utf-8",
    )
    return state, config


def readiness() -> list[dict]:
    return [{
        "provider": "x",
        "expected_identity": "123456",
        "observed_identity": "123456",
        "identity_match": "match",
        "ready_for_write_configuration": True,
        "blocking_reasons": [],
    }]


class FakeServices:
    def __init__(self, state_root: Path) -> None:
        self.state_root = state_root
        self.enabled = False
        self.active = False
        self.staged = False

    def inspect(self) -> dict:
        return {
            "schema_version": 1,
            "timers": [{"unit": "fixture.timer", "enabled": self.enabled, "active": self.active}],
            "all_enabled": self.enabled,
            "all_active": self.active,
        }

    def stage(self, **_kwargs) -> dict:
        self.staged = True
        return {"status": "staged", "provider_consequence": False}

    def preflight(self, **_kwargs) -> dict:
        return {"status": "passed", "checks": [], "provider_consequence": False}

    def arm(self) -> dict:
        self.enabled = True
        self.active = True
        return self.inspect()

    def disarm(self) -> dict:
        self.enabled = False
        self.active = False
        return self.inspect()


class Client:
    def __init__(self, port: int, app) -> None:
        self.port = port
        self.origin = f"http://{BIND_HOST}:{port}"
        self.app = app
        self.cookie = ""

    def request(self, method: str, path: str, *, values: dict[str, str] | None = None, origin: str | None = None) -> tuple[int, dict[str, str], str]:
        conn = HTTPConnection(BIND_HOST, self.port, timeout=10)
        headers: dict[str, str] = {}
        body = None
        if self.cookie:
            headers["Cookie"] = self.cookie
        if values is not None:
            body = urlencode(values).encode("utf-8")
            headers["Content-Type"] = "application/x-www-form-urlencoded"
            headers["Origin"] = origin or self.origin
        conn.request(method, path, body=body, headers=headers)
        response = conn.getresponse()
        text = response.read().decode("utf-8")
        response_headers = {key: value for key, value in response.getheaders()}
        status = response.status
        conn.close()
        return status, response_headers, text

    def bootstrap(self) -> dict[str, str]:
        token = self.app.bootstrap_token
        if token is None:
            raise RuntimeError("bootstrap token was already consumed")
        status, headers, _ = self.request("GET", f"/?session={token}")
        if status != 303:
            raise RuntimeError(f"bootstrap failed: {status}")
        if self.app.bootstrap_token is not None:
            raise RuntimeError("bootstrap token was not consumed")
        self.cookie = headers["Set-Cookie"].split(";", 1)[0]
        return headers

    def post(self, path: str, values: dict[str, str], *, origin: str | None = None) -> tuple[int, dict[str, str], str]:
        return self.request("POST", path, values={"csrf_token": self.app.csrf_token, **values}, origin=origin)


def reviewed_digest(page: str) -> str:
    match = re.search(r'name="expected_sha256" value="([0-9a-f]{64})"', page)
    if match is None:
        raise RuntimeError("browser preview did not render an exact review digest")
    return unescape(match.group(1))


def main() -> None:
    with tempfile.TemporaryDirectory(prefix="post-once-i-browser-") as temp:
        root = Path(temp)

        # First prove the browser can create fresh local setup without authority.
        fresh_server, fresh_app = build_server(root / "fresh-bootstrap", port=0)
        fresh_thread = threading.Thread(target=fresh_server.serve_forever, daemon=True)
        fresh_thread.start()
        fresh_client = Client(fresh_server.server_port, fresh_app)
        fresh_token = fresh_app.bootstrap_token
        if fresh_token is None:
            raise RuntimeError("fresh bootstrap token missing")
        fresh_bootstrap = fresh_client.bootstrap()
        replay_client = Client(fresh_server.server_port, fresh_app)
        bootstrap_replay_status, _, _ = replay_client.request(
            "GET",
            f"/?session={fresh_token}",
        )
        fresh_status, fresh_headers, fresh_page = fresh_client.request("GET", "/")
        if fresh_status != 200:
            raise RuntimeError("fresh browser home did not render")
        cross_status, _, _ = fresh_client.post(
            "/action/start",
            {
                "mode": "fresh",
                "operator_label": "Browser Example",
                "timezone": "UTC",
                "pace": "occasional",
            },
            origin="https://attacker.example",
        )
        valid_status, _, _ = fresh_client.post(
            "/action/start",
            {
                "mode": "fresh",
                "operator_label": "Browser Example",
                "machine_label": "browser-host",
                "timezone": "UTC",
                "pace": "occasional",
            },
        )
        fresh_state = fresh_app.status()
        fresh_server.shutdown()
        fresh_server.server_close()
        fresh_thread.join(timeout=3)

        # Build a verified migration target, then exercise H preview/apply through I.
        source_state, source_config = source_tree(root)
        source = SetupEngine(root / "source-bootstrap")
        source.start("migrate", operator_label="Browser Example", machine_label="old-host")
        export_preview = source.export_migration(
            state_root=source_state,
            config_root=source_config,
            output=root / "transfer.tar.gz",
            source_post_once_version="0.28.29",
            source_revision=REVISION,
            unit_observation=QUIESCENT,
        )
        sealed = source.export_migration(
            state_root=source_state,
            config_root=source_config,
            output=root / "transfer.tar.gz",
            source_post_once_version="0.28.29",
            source_revision=REVISION,
            apply=True,
            expected_sha256=export_preview["migration_export"]["review_sha256"],
            unit_observation=QUIESCENT,
        )
        target_workspace = root / "target-bootstrap"
        target = SetupEngine(target_workspace)
        target.restore_migration(
            root / "transfer.tar.gz",
            expected_bundle_sha256=sealed["migration_export"]["bundle_sha256"],
            machine_label="new-host",
        )
        target.verify_migration(readiness())

        target_state = root / "target-state"
        target_config = root / "target-config"
        target_config.mkdir()
        (target_config / "machine-token.json").write_text(
            '{"access_token":"TARGET_ONLY"}\n',
            encoding="utf-8",
        )
        services = FakeServices(target_state)
        server, app = build_server(
            target_workspace,
            port=0,
            service_controller=services,
        )
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        client = Client(server.server_port, app)
        client.bootstrap()

        activate_fields = {
            "runtime_root": str(ROOT),
            "state_dir": str(target_state),
            "config_dir": str(target_config),
        }
        preview_status, preview_headers, preview_page = client.post(
            "/action/activate-preview",
            activate_fields,
        )
        digest = reviewed_digest(preview_page)
        marker_before = automation_authority.read(target_state)["status"]
        apply_status, _, _ = client.post(
            "/action/activate-apply",
            {
                **activate_fields,
                "expected_sha256": digest,
                "confirm": "1",
            },
        )
        active = app.status()
        marker_after = automation_authority.read(target_state)["status"]
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)

        result = {
            "schema_version": 1,
            "status": "pass",
            "bind_host": fresh_server.server_address[0],
            "ephemeral_port_selected": fresh_server.server_port > 0,
            "bootstrap_redirect_stripped_token": fresh_bootstrap.get("Location") == "/",
            "bootstrap_token_consumed": fresh_app.bootstrap_token is None,
            "bootstrap_reuse_rejected": bootstrap_replay_status == 403,
            "strict_cookie": "HttpOnly" in fresh_bootstrap.get("Set-Cookie", "") and "SameSite=Strict" in fresh_bootstrap.get("Set-Cookie", ""),
            "security_headers_present": (
                fresh_headers.get("Cache-Control") == "no-store, max-age=0"
                and "default-src 'none'" in fresh_headers.get("Content-Security-Policy", "")
                and fresh_headers.get("X-Frame-Options") == "DENY"
            ),
            "server_rendered_no_javascript": "<script" not in fresh_page.lower(),
            "cross_origin_rejected": cross_status == 403,
            "fresh_setup_succeeded": valid_status == 200 and fresh_state is not None,
            "fresh_stage": fresh_state["session"]["stage"] if fresh_state else None,
            "fresh_publishing_authority": fresh_state["publishing_authority"] if fresh_state else None,
            "fresh_automation_enabled": fresh_state["automation_enabled"] if fresh_state else None,
            "activation_preview_rendered": preview_status == 200 and bool(digest),
            "activation_preview_no_store": preview_headers.get("Cache-Control") == "no-store, max-age=0",
            "marker_inactive_before_apply": marker_before == "inactive",
            "activation_apply_succeeded": apply_status == 200,
            "activation_stage": active["session"]["stage"] if active else None,
            "activation_publishing_authority": active["publishing_authority"] if active else None,
            "activation_automation_enabled": active["automation_enabled"] if active else None,
            "marker_active_after_apply": marker_after == "active",
            "provider_consequence_attempted": False,
        }
        print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()
