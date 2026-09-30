"""Deduplicated outbound delivery for local operating incidents.

This module is notification-only. It cannot publish, retry social consequences,
change policy, repair incidents, or acknowledge them as resolved.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import re
from urllib.parse import urlsplit, urlunsplit

from ocpf_post import local_store
from ocpf_post.bounded_http import post_json
from ocpf_post.engagement import stamp
from ocpf_post.state import config_dir, state_dir

UTC = timezone.utc
MAX_ALERTS_PER_DELIVERY = 100
SAFE_FIELDS = {
    "code", "schedule_id", "provider", "account_id", "campaign", "project",
    "stage", "inbox_id", "expires_at", "first_seen_at", "last_seen_at",
}


def policy_path():
    return config_dir() / "alert-delivery.json"


def credential_path():
    return config_dir() / "alert-delivery-credential.json"


def state_path():
    return state_dir() / "alert-delivery.json"


def _digest(value):
    return hashlib.sha256(json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode()).hexdigest()


def _endpoint(value):
    parsed = urlsplit(str(value))
    if (parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password
            or parsed.fragment or parsed.port not in (None, 443)):
        raise ValueError("Alert endpoint must be HTTPS without embedded credentials/custom port")
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path or "/", parsed.query, ""))


def _load_policy():
    data = local_store.read(policy_path())
    if not data:
        return {}
    required = {
        "schema_version", "enabled", "endpoint", "auth",
        "include_resolved", "minimum_interval_seconds",
    }
    if set(data) != required or data.get("schema_version") != 1:
        raise ValueError("Invalid alert delivery policy")
    if type(data["enabled"]) is not bool or type(data["include_resolved"]) is not bool:
        raise ValueError("Invalid alert booleans")
    if data["auth"] not in {"none", "bearer"}:
        raise ValueError("Alert auth must be none or bearer")
    if type(data["minimum_interval_seconds"]) is not int or not 0 <= data["minimum_interval_seconds"] <= 3600:
        raise ValueError("Invalid alert minimum interval")
    data["endpoint"] = _endpoint(data["endpoint"])
    return data


def configure(value, *, apply=False, expected_sha256=None):
    if not isinstance(value, dict):
        raise ValueError("Alert configuration must be an object")
    expected = {"schema_version", "endpoint", "auth", "include_resolved", "minimum_interval_seconds"}
    if set(value) != expected or value.get("schema_version") != 1:
        raise ValueError("Invalid alert configuration")
    row = {
        "schema_version": 1, "enabled": False,
        "endpoint": _endpoint(value["endpoint"]),
        "auth": value["auth"],
        "include_resolved": value["include_resolved"],
        "minimum_interval_seconds": value["minimum_interval_seconds"],
    }
    # Validate strict policy shape before storing.
    if row["auth"] not in {"none", "bearer"}:
        raise ValueError("Alert auth must be none or bearer")
    if type(row["include_resolved"]) is not bool:
        raise ValueError("include_resolved must be boolean")
    if type(row["minimum_interval_seconds"]) is not int or not 0 <= row["minimum_interval_seconds"] <= 3600:
        raise ValueError("Invalid alert minimum interval")
    review = _digest(value)
    if apply:
        if expected_sha256 != review:
            raise ValueError("Alert configuration review changed")
        with local_store.locked(policy_path()):
            old = _load_policy()
            if old and {k: old[k] for k in row if k != "enabled"} != {k: row[k] for k in row if k != "enabled"}:
                raise ValueError("Disable/remove existing alert configuration before replacing authority")
            local_store.write(policy_path(), old or row)
    return {
        "schema_version": 1, "result": "configured" if apply else "preview",
        "review_sha256": review, "policy": row,
        "boundary": "Configuration stores notification destination only. It does not send, acknowledge, repair or mutate any incident.",
    }


def connect(credential_file):
    policy = _load_policy()
    if not policy:
        raise ValueError("Configure alert delivery first")
    if policy["enabled"]:
        raise ValueError("Disable alert delivery before replacing credentials")
    if policy["auth"] == "none":
        raise ValueError("Configured alert endpoint does not use credentials")
    source = Path(credential_file).expanduser()
    if source.stat().st_size > 65536 or source.stat().st_mode & 0o077:
        raise ValueError("Alert credential file must be private and under 64 KB")
    value = json.loads(source.read_text())
    if set(value) != {"bearer_token"} or not isinstance(value["bearer_token"], str) or not value["bearer_token"].strip():
        raise ValueError("Alert credential file must contain bearer_token only")
    local_store.write(credential_path(), {"bearer_token": value["bearer_token"].strip()})
    return {"schema_version": 1, "result": "credential_stored",
            "boundary": "Credential stored privately; no alert was sent."}


def activation(*, apply=False, expected_sha256=None, disable=False):
    with local_store.locked(policy_path()):
        policy = _load_policy()
        if not policy:
            raise ValueError("Configure alert delivery first")
        if disable:
            if apply:
                policy["enabled"] = False
                local_store.write(policy_path(), policy)
            return {"result": "disabled" if apply else "disable_preview"}
        if policy["auth"] == "bearer":
            token = local_store.read(credential_path()).get("bearer_token")
            if not isinstance(token, str) or not token:
                raise ValueError("Alert bearer credential is missing")
        review = _digest({k: policy[k] for k in (
            "endpoint", "auth", "include_resolved", "minimum_interval_seconds"
        )})
        if apply:
            if expected_sha256 != review:
                raise ValueError("Alert activation review changed")
            policy["enabled"] = True
            local_store.write(policy_path(), policy)
        return {"schema_version": 1, "result": "enabled" if apply else "preview",
                "review_sha256": review, "policy": policy,
                "boundary": "Enabling authorises notification delivery only. It grants no repair, retry, publication or acknowledgement authority."}


def _headers(policy):
    if policy["auth"] == "none":
        return {"Accept": "application/json"}
    token = local_store.read(credential_path()).get("bearer_token")
    if not isinstance(token, str) or not token:
        raise ValueError("Alert credential unavailable")
    return {"Accept": "application/json", "Authorization": "Bearer " + token}


def _safe_incident(identity, row):
    projected = {"incident_id": identity}
    for key in SAFE_FIELDS:
        value = row.get(key)
        if value is None:
            continue
        if isinstance(value, (str, int, float, bool)):
            projected[key] = value
    return projected


def report():
    policy = _load_policy()
    state = local_store.read(state_path()) or {}
    if not policy:
        return {"schema_version": 1, "status": "not_configured", "enabled": False}
    return {
        "schema_version": 1,
        "status": state.get("status") or ("enabled" if policy["enabled"] else "disabled"),
        "enabled": policy["enabled"],
        "credential_present": policy["auth"] == "none" or credential_path().exists(),
        "last_delivery_at": state.get("last_delivery_at"),
        "retry_at": state.get("retry_at"),
        "active_incident_count": len(state.get("active_incident_ids", [])),
        "last_delivery_count": state.get("last_delivery_count", 0),
        "boundary": "Notification delivery state only. Delivery does not repair or acknowledge incidents.",
    }


def deliver(*, apply=False, now=None):
    now = now or datetime.now(UTC)
    if not apply:
        return report()
    policy = _load_policy()
    if not policy:
        return {"schema_version": 1, "status": "idle", "reason": "not_configured"}
    if not policy["enabled"]:
        return {"schema_version": 1, "status": "idle", "reason": "disabled"}
    incidents = local_store.read(state_dir() / "operating-incidents.json")
    current = incidents.get("incidents", {}) if isinstance(incidents.get("incidents"), dict) else {}
    with local_store.locked(state_path()):
        state = local_store.read(state_path()) or {
            "schema_version": 1, "active_incident_ids": [],
        }
        if state.get("schema_version") != 1 or not isinstance(state.get("active_incident_ids"), list):
            raise ValueError("Invalid alert delivery state")
        retry_at = state.get("retry_at")
        if retry_at:
            retry = datetime.fromisoformat(str(retry_at).replace("Z", "+00:00")).astimezone(UTC)
            if now < retry:
                return {"schema_version": 1, "status": "deferred", "retry_at": retry_at}
        last = state.get("last_delivery_at")
        if last and policy["minimum_interval_seconds"]:
            last_at = datetime.fromisoformat(str(last).replace("Z", "+00:00")).astimezone(UTC)
            if now - last_at < timedelta(seconds=policy["minimum_interval_seconds"]):
                return {"schema_version": 1, "status": "deferred",
                        "retry_at": stamp(last_at + timedelta(seconds=policy["minimum_interval_seconds"]))}
        previous = set(str(v) for v in state["active_incident_ids"])
        current_ids = set(str(v) for v in current)
        new_ids = sorted(current_ids - previous)
        resolved_ids = sorted(previous - current_ids) if policy["include_resolved"] else []
        if not new_ids and not resolved_ids:
            # Even when resolved notifications are disabled, reconciliation must
            # forget resolved incident IDs. Otherwise the same incident could
            # reappear later and be incorrectly suppressed as already delivered.
            state.update(
                status="observed", last_checked_at=stamp(now),
                active_incident_ids=sorted(current_ids),
            )
            local_store.write(state_path(), state)
            return {"schema_version": 1, "status": "idle", "new_count": 0, "resolved_count": 0}
        if len(new_ids) + len(resolved_ids) > MAX_ALERTS_PER_DELIVERY:
            return {"schema_version": 1, "status": "attention", "reason": "alert_batch_exceeds_bound",
                    "new_count": len(new_ids), "resolved_count": len(resolved_ids)}
        payload = {
            "schema_version": 1,
            "kind": "post-once-operating-incidents",
            "observed_at": incidents.get("observed_at") or stamp(now),
            "delivery_id": _digest([incidents.get("observed_at"), new_ids, resolved_ids])[:32],
            "new_incidents": [_safe_incident(identity, current[identity]) for identity in new_ids],
            "resolved_incident_ids": resolved_ids,
            "boundary": "Notification only; no automated repair or social consequence.",
        }
        try:
            post_json(policy["endpoint"], payload, headers=_headers(policy))
        except Exception as exc:
            retry = stamp(now + timedelta(hours=1))
            state.update(status="unavailable", retry_at=retry,
                         last_error_type=type(exc).__name__, last_attempt_at=stamp(now))
            local_store.write(state_path(), state)
            return {"schema_version": 1, "status": "attention", "retry_at": retry,
                    "error_type": type(exc).__name__}
        state.update(
            status="observed", active_incident_ids=sorted(current_ids),
            last_delivery_at=stamp(now), last_checked_at=stamp(now),
            last_delivery_count=len(new_ids) + len(resolved_ids),
        )
        state.pop("retry_at", None)
        state.pop("last_error_type", None)
        local_store.write(state_path(), state)
    return {
        "schema_version": 1, "status": "observed",
        "new_count": len(new_ids), "resolved_count": len(resolved_ids),
        "delivery_id": payload["delivery_id"],
        "boundary": "At-least-once notification with deterministic delivery ID. No incident acknowledgement, repair, retry or publishing authority.",
    }


def add_parser(sub):
    from ocpf_post.onboarding import _run_cli
    group = sub.add_parser("alerts", help="Configure and deliver deduplicated operating incident alerts")
    commands = group.add_subparsers(dest="alert_command", required=True)

    status = commands.add_parser("status")
    status.set_defaults(func=lambda a: _run_cli(report))

    configure_cmd = commands.add_parser("configure")
    configure_cmd.add_argument("--file", required=True)
    configure_cmd.add_argument("--apply", action="store_true")
    configure_cmd.add_argument("--expected-sha256")
    def do_configure(args):
        path = Path(args.file).expanduser()
        if path.stat().st_size > 65536:
            raise ValueError("Alert config exceeds 64 KB")
        return configure(json.loads(path.read_text()), apply=args.apply, expected_sha256=args.expected_sha256)
    configure_cmd.set_defaults(func=lambda a: _run_cli(lambda: do_configure(a)))

    connect_cmd = commands.add_parser("connect")
    connect_cmd.add_argument("--credential-file", required=True)
    connect_cmd.set_defaults(func=lambda a: _run_cli(lambda: connect(a.credential_file)))

    for action in ("enable", "disable"):
        command = commands.add_parser(action)
        command.add_argument("--apply", action="store_true")
        command.add_argument("--expected-sha256")
        command.set_defaults(func=lambda a, action=action: _run_cli(
            lambda: activation(apply=a.apply, expected_sha256=a.expected_sha256, disable=action == "disable")
        ))

    send = commands.add_parser("send")
    send.add_argument("--apply", action="store_true")
    send.set_defaults(func=lambda a: _run_cli(lambda: deliver(apply=a.apply)))


def main():
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    print(json.dumps(deliver(apply=args.apply), indent=2))
