"""Registered pull-only HTTPS connectors for receipt-linked business outcomes."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import time
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from ocpf_post import local_store
from ocpf_post.bounded_http import json_request
from ocpf_post.business_outcomes import ingest_authorized
from ocpf_post.engagement import stamp
from ocpf_post.state import config_dir, state_dir

UTC = timezone.utc
MAX_PAGE_EVENTS = 200
MAX_PAGES_PER_CYCLE = 5
BUDGET_SECONDS = 35


def policy_path():
    return config_dir() / "outcome-connectors.json"


def state_path():
    return state_dir() / "outcome-connectors.json"


def credential_path(connector_id):
    return config_dir() / "outcome-connectors" / (connector_id + ".json")


def _digest(value):
    return hashlib.sha256(json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode()).hexdigest()


def _endpoint(value):
    parsed = urlsplit(str(value))
    if (parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password
            or parsed.fragment or parsed.port not in (None, 443)):
        raise ValueError("Outcome connector endpoint must be public HTTPS without credentials/custom port")
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path or "/", parsed.query, ""))


def _policy():
    data = local_store.read(policy_path()) or {"schema_version": 1, "connectors": {}}
    if data.get("schema_version") != 1 or not isinstance(data.get("connectors"), dict):
        raise ValueError("Invalid outcome connector policy")
    for key, row in data["connectors"].items():
        if not isinstance(row, dict) or row.get("id") != key:
            raise ValueError("Invalid outcome connector identity")
        _validate(row)
    return data


def _validate(row):
    required = {"id", "endpoint", "source", "auth", "max_pages", "enabled"}
    if set(row) != required:
        raise ValueError("Invalid outcome connector fields")
    if not re.fullmatch(r"[a-z0-9][a-z0-9_-]{1,63}", str(row["id"])):
        raise ValueError("Invalid connector ID")
    if not re.fullmatch(r"[a-z0-9][a-z0-9_-]{1,79}", str(row["source"])):
        raise ValueError("Invalid connector source")
    _endpoint(row["endpoint"])
    if row["auth"] not in {"none", "bearer"}:
        raise ValueError("Outcome connector auth must be none or bearer")
    if type(row["max_pages"]) is not int or not 1 <= row["max_pages"] <= MAX_PAGES_PER_CYCLE:
        raise ValueError("Invalid outcome connector page budget")
    if type(row["enabled"]) is not bool:
        raise ValueError("Invalid outcome connector enabled state")
    return row


def register(value, *, apply=False, expected_sha256=None):
    if not isinstance(value, dict):
        raise ValueError("Connector import must be an object")
    expected = {"schema_version", "id", "endpoint", "source", "auth", "max_pages"}
    if set(value) != expected or value.get("schema_version") != 1:
        raise ValueError("Invalid connector import")
    row = _validate({
        "id": value["id"], "endpoint": _endpoint(value["endpoint"]),
        "source": value["source"], "auth": value["auth"],
        "max_pages": value["max_pages"], "enabled": False,
    })
    review = _digest(value)
    if apply:
        if expected_sha256 != review:
            raise ValueError("Connector import review changed")
        with local_store.locked(policy_path()):
            data = _policy()
            old = data["connectors"].get(row["id"])
            if old and old != row:
                raise ValueError("Connector already exists with different authority")
            if any(existing["source"] == row["source"] and key != row["id"]
                   for key, existing in data["connectors"].items()):
                raise ValueError("Outcome source is already owned by another connector")
            data["connectors"].setdefault(row["id"], row)
            local_store.write(policy_path(), data)
    return {
        "schema_version": 1, "result": "registered" if apply else "preview",
        "review_sha256": review, "connector": row,
        "boundary": "Registration stores endpoint/source authority only. It does not connect credentials, enable network reads, import outcomes, publish, or alter learning.",
    }


def connect(connector_id, credential_file):
    row = _policy()["connectors"].get(connector_id)
    if not row:
        raise ValueError("Register connector first")
    if row["enabled"]:
        raise ValueError("Disable connector before replacing credentials")
    if row["auth"] == "none":
        raise ValueError("This connector does not use credentials")
    source = Path(credential_file).expanduser()
    if source.stat().st_size > 65536 or source.stat().st_mode & 0o077:
        raise ValueError("Credential file must be private and under 64 KB")
    value = json.loads(source.read_text())
    if set(value) != {"bearer_token"} or not isinstance(value["bearer_token"], str) or not value["bearer_token"].strip():
        raise ValueError("Credential file must contain bearer_token only")
    local_store.write(credential_path(connector_id), {"bearer_token": value["bearer_token"].strip()})
    return {
        "schema_version": 1, "result": "credential_stored", "connector_id": connector_id,
        "boundary": "Credential stored privately. No network read or outcome import occurred.",
    }


def activation(connector_id, *, apply=False, expected_sha256=None, disable=False):
    with local_store.locked(policy_path()):
        data = _policy()
        row = data["connectors"].get(connector_id)
        if not row:
            raise ValueError("Unknown connector")
        if disable:
            if apply:
                row["enabled"] = False
                local_store.write(policy_path(), data)
            return {"result": "disabled" if apply else "disable_preview", "connector_id": connector_id}
        if row["auth"] == "bearer":
            credential = local_store.read(credential_path(connector_id))
            if not isinstance(credential.get("bearer_token"), str) or not credential["bearer_token"]:
                raise ValueError("Connector credential is missing")
        review = _digest({k: row[k] for k in ("id", "endpoint", "source", "auth", "max_pages")})
        if apply:
            if expected_sha256 != review:
                raise ValueError("Connector activation review changed")
            row["enabled"] = True
            local_store.write(policy_path(), data)
        return {
            "schema_version": 1, "result": "enabled" if apply else "preview",
            "review_sha256": review, "connector": row,
            "boundary": "Activation authorises bounded pull-only HTTPS outcome reads. Remote events still must match exact verified publication identities before storage.",
        }


def _headers(row):
    if row["auth"] == "none":
        return {"Accept": "application/json"}
    token = local_store.read(credential_path(row["id"])).get("bearer_token")
    if not isinstance(token, str) or not token:
        raise ValueError("Connector credential unavailable")
    return {"Accept": "application/json", "Authorization": "Bearer " + token}


def _url(row, cursor):
    parsed = urlsplit(row["endpoint"])
    query = dict(parse_qsl(parsed.query, keep_blank_values=True))
    query["limit"] = str(MAX_PAGE_EVENTS)
    if cursor:
        query["cursor"] = cursor
    else:
        query.pop("cursor", None)
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, urlencode(query), ""))


def _page(row, payload):
    if not isinstance(payload, dict):
        raise ValueError("Invalid connector response")
    allowed = {"schema_version", "source", "events", "next_cursor", "has_more"}
    if set(payload) != allowed or payload.get("schema_version") != 1:
        raise ValueError("Invalid connector response schema")
    if payload.get("source") != row["source"]:
        raise ValueError("Connector response source mismatch")
    if not isinstance(payload.get("events"), list) or len(payload["events"]) > MAX_PAGE_EVENTS:
        raise ValueError("Connector response event count exceeds bound")
    if type(payload.get("has_more")) is not bool:
        raise ValueError("Connector response has_more must be boolean")
    cursor = payload.get("next_cursor")
    if cursor is not None and (not isinstance(cursor, str) or not 1 <= len(cursor) <= 500):
        raise ValueError("Invalid connector cursor")
    if payload["has_more"] and not cursor:
        raise ValueError("Connector reports more data without next_cursor")
    return payload


def report():
    policy = _policy()
    state = local_store.read(state_path()) or {"schema_version": 1, "connectors": {}}
    rows = []
    for key, row in sorted(policy["connectors"].items()):
        saved = state.get("connectors", {}).get(key, {})
        rows.append({
            "id": key, "source": row["source"], "enabled": row["enabled"], "auth": row["auth"],
            "credential_present": row["auth"] == "none" or credential_path(key).exists(),
            "last_status": saved.get("status"), "last_success_at": saved.get("last_success_at"),
            "retry_at": saved.get("retry_at"), "imported_events_last_cycle": saved.get("imported_events_last_cycle", 0),
        })
    return {
        "schema_version": 1,
        "status": "observed" if rows else "not_configured",
        "connectors": rows,
        "boundary": "Configured pull-only outcome sources. Missing connectors mean outcomes remain unknown; no causal attribution or publishing authority.",
    }


def sync(*, apply=False, now=None):
    now = now or datetime.now(UTC)
    if not apply:
        return report()
    policy = _policy()
    with local_store.locked(state_path()):
        state = local_store.read(state_path()) or {"schema_version": 1, "connectors": {}}
        if state.get("schema_version") != 1 or not isinstance(state.get("connectors"), dict):
            raise ValueError("Invalid outcome connector state")
    results = []
    deadline = time.monotonic() + BUDGET_SECONDS
    for key, row in sorted(policy["connectors"].items()):
        if not row["enabled"]:
            continue
        saved = state["connectors"].get(key, {})
        retry_at = saved.get("retry_at")
        if retry_at:
            try:
                retry = datetime.fromisoformat(str(retry_at).replace("Z", "+00:00")).astimezone(UTC)
            except ValueError:
                retry = now
            if now < retry:
                results.append({"id": key, "status": "deferred", "retry_at": retry_at})
                continue
        cursor = saved.get("cursor")
        imported = 0
        pages = 0
        try:
            while pages < row["max_pages"] and time.monotonic() < deadline:
                payload = _page(row, json_request(_url(row, cursor), headers=_headers(row), limit=1_000_000))
                document = {"schema_version": 1, "source": row["source"], "events": payload["events"]}
                outcome = ingest_authorized(document, expected_source=row["source"], now=now)
                imported += outcome["event_count"]
                pages += 1
                cursor = payload.get("next_cursor")
                state["connectors"][key] = {
                    "status": "observed", "cursor": cursor,
                    "last_success_at": stamp(now), "last_attempt_at": stamp(now),
                    "imported_events_last_cycle": imported, "pages_last_cycle": pages,
                }
                with local_store.locked(state_path()):
                    local_store.write(state_path(), state)
                if not payload["has_more"]:
                    break
            results.append({"id": key, "status": "observed", "event_count": imported, "pages": pages})
        except Exception as exc:
            retry = stamp(now + timedelta(hours=1))
            # Preserve any cursor already committed after earlier successful
            # pages in this same cycle. Replays are idempotent, but rolling the
            # cursor backwards would cause needless repeated reads forever.
            latest = state["connectors"].get(key, saved)
            state["connectors"][key] = {
                **latest, "status": "unavailable", "last_attempt_at": stamp(now),
                "retry_at": retry, "error_type": type(exc).__name__,
                "imported_events_last_cycle": imported, "pages_last_cycle": pages,
            }
            with local_store.locked(state_path()):
                local_store.write(state_path(), state)
            results.append({"id": key, "status": "unavailable", "retry_at": retry, "error_type": type(exc).__name__})
    statuses = {row["status"] for row in results}
    return {
        "schema_version": 1,
        "status": "attention" if "unavailable" in statuses else "partial" if "deferred" in statuses else "observed" if results else "idle",
        "results": results,
        "boundary": "Bounded pull-only HTTPS connector sync. Cursor advances only after exact receipt-linked events are durably ingested. Provider/social publication state is untouched.",
    }


def add_parser(sub):
    from ocpf_post.onboarding import _run_cli
    group = sub.add_parser("outcome-connectors", help="Register and run receipt-linked HTTPS outcome connectors")
    commands = group.add_subparsers(dest="outcome_connector_command", required=True)

    status = commands.add_parser("status")
    status.set_defaults(func=lambda a: _run_cli(report))

    register_cmd = commands.add_parser("import")
    register_cmd.add_argument("--file", required=True)
    register_cmd.add_argument("--apply", action="store_true")
    register_cmd.add_argument("--expected-sha256")
    def do_register(args):
        path = Path(args.file).expanduser()
        if path.stat().st_size > 65536:
            raise ValueError("Connector import exceeds 64 KB")
        return register(json.loads(path.read_text()), apply=args.apply, expected_sha256=args.expected_sha256)
    register_cmd.set_defaults(func=lambda a: _run_cli(lambda: do_register(a)))

    connect_cmd = commands.add_parser("connect")
    connect_cmd.add_argument("--id", required=True)
    connect_cmd.add_argument("--credential-file", required=True)
    connect_cmd.set_defaults(func=lambda a: _run_cli(lambda: connect(a.id, a.credential_file)))

    for action in ("enable", "disable"):
        command = commands.add_parser(action)
        command.add_argument("--id", required=True)
        command.add_argument("--apply", action="store_true")
        command.add_argument("--expected-sha256")
        command.set_defaults(func=lambda a, action=action: _run_cli(
            lambda: activation(a.id, apply=a.apply, expected_sha256=a.expected_sha256, disable=action == "disable")
        ))

    run = commands.add_parser("sync")
    run.add_argument("--apply", action="store_true")
    run.set_defaults(func=lambda a: _run_cli(lambda: sync(apply=a.apply)))


def main():
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    print(json.dumps(sync(apply=args.apply), indent=2))
