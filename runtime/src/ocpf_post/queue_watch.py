"""Automatic local queue supervision. Observations never confer publish authority."""
from __future__ import annotations

from collections import Counter
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import tempfile
from zoneinfo import ZoneInfo

from ocpf_post import portfolio as base
from ocpf_post.campaigns import builtin_manifest, builtin_text, destination_binding
from ocpf_post.scheduler import schedule_records
from ocpf_post.state import state_dir, ensure_private_dir, iter_receipts

UTC = timezone.utc
DEFAULTS = {"wait_warning_hours": 24, "deadline_warning_hours": 24,
            "stale_after_minutes": 45, "retention_days": 30}
CLOSED = {"published_verified", "published_unverified", "ambiguous_effect", "partial_effect", "expired_unpublished",
          "removed", "revision_changed", "allocation_disabled"}


def settings(policy):
    raw = policy.get("queue_watch", {})
    if not isinstance(raw, dict) or set(raw) - set(DEFAULTS):
        raise base.PortfolioError("Invalid queue_watch policy fields")
    result = {**DEFAULTS, **raw}
    limits = {"wait_warning_hours": (1, 720), "deadline_warning_hours": (1, 168),
              "stale_after_minutes": (15, 1440), "retention_days": (1, 365)}
    for name, value in result.items():
        lo, hi = limits[name]
        if type(value) is not int or not lo <= value <= hi:
            raise base.PortfolioError(f"queue_watch.{name} must be an integer between {lo} and {hi}")
    return result


def path():
    return state_dir() / "queue-watch.json"


def identity(item):
    values = [item.get(k) for k in ("campaign", "provider", "account_id", "text_sha256")]
    return hashlib.sha256(json.dumps(values, separators=(",", ":")).encode()).hexdigest()


def _at(value):
    return base._parse_dt(value)


def _stamp(value):
    return value.isoformat().replace("+00:00", "Z")


def load():
    source = path()
    if not source.exists():
        return {"schema_version": 1, "records": {}}
    if source.stat().st_size > 50_000_000:
        raise ValueError("Queue watch exceeds size bound")
    value = json.loads(source.read_text(encoding="utf-8"))
    if (not isinstance(value, dict) or value.get("schema_version") != 1
            or not isinstance(value.get("records"), dict)):
        raise ValueError("Invalid queue watch state")
    for key, record in value["records"].items():
        if (not isinstance(record, dict) or key != identity(record)
                or not _at(record.get("first_eligible_at")) or not _at(record.get("last_seen_at"))):
            raise ValueError("Invalid queue watch record")
    return value


def hints():
    """Optional scheduling hints; damaged monitoring must not stop publication."""
    try:
        value = load()
        return {k: r["first_eligible_at"] for k, r in value["records"].items()
                if r.get("state") == "waiting"}, "observed" if value.get("observed_at") else "not_observed"
    except (OSError, ValueError, KeyError, TypeError):
        return {}, "unavailable"


@contextmanager
def _lock():
    try:
        import fcntl
    except ImportError as exc:
        raise ValueError("Queue observation requires POSIX file locks") from exc
    ensure_private_dir(state_dir())
    fd = os.open(state_dir() / "queue-watch.lock", os.O_CREAT | os.O_RDWR, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield
    finally:
        os.close(fd)


def _write(value):
    # A unique private temp file plus atomic replacement permits read-only health
    # checks while another process commits an observation; no partial JSON.
    fd, filename = tempfile.mkstemp(prefix="queue-watch-", suffix=".tmp", dir=state_dir())
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(value, handle, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
            handle.flush(); os.fsync(handle.fileno())
        os.replace(filename, path())
    finally:
        if os.path.exists(filename):
            os.unlink(filename)


def _matching(row, record):
    return all(row.get(k) == record.get(k) for k in ("campaign", "provider", "account_id", "text_sha256"))


def _outcome(record, candidates, excluded, schedules, receipts, now):
    if not record.get("account_id") or not record.get("text_sha256"):
        return (("blocked", "destination_identity_unavailable", {}) if identity(record) in candidates
                else ("removed", "unbound_candidate_no_longer_eligible", {}))
    effects = [r for r in receipts if _matching(r, record) and r.get("status") in base.TERMINAL_RECEIPT_STATUSES]
    if effects:
        latest = effects[-1]
        status = latest["status"]
        if ((status == "published_verified" and latest.get("readback_verified") is not True)
                or (status in {"published_verified", "published_unverified"} and not latest.get("post_id"))):
            status = "receipt_inconsistent"
        return status, "matching_local_receipt", {k: latest.get(k) for k in ("post_id", "schedule_id", "readback_verified")}
    reservations = [r for r in schedules if _matching(r, record)]
    active = [r for r in reservations if r.get("status") in base.ACTIVE_STATUSES]
    if active:
        return active[-1]["status"], "active_reservation", {"schedule_id": active[-1].get("schedule_id")}
    if reservations and reservations[-1].get("status") in {"failed", "drift_blocked", "duplicate_blocked", "published_verified", "published_unverified", "ambiguous_effect", "partial_effect"}:
        last = reservations[-1]
        return "schedule_requires_review", str(last["status"]), {"schedule_id": last.get("schedule_id")}
    if identity(record) in candidates:
        return "waiting", "eligible_unreserved", {}
    manifest = builtin_manifest(record["campaign"])
    if not manifest:
        return "removed", "campaign_no_longer_available", {}
    allocation = manifest.get("allocation") or {}
    if not allocation.get("enabled") or str(manifest.get("status", "")).upper() != "COPY-READY":
        return "allocation_disabled", "allocation_or_copy_authority_withdrawn", {}
    if allocation.get("superseded_by"):
        return "revision_changed", "source_superseded", {}
    binding = destination_binding(record["campaign"], record["provider"]) or {}
    text = builtin_text(record["campaign"], record["provider"])
    current_hash = hashlib.sha256(text.strip().encode()).hexdigest() if text else None
    if binding.get("account_id") != record["account_id"] or current_hash != record["text_sha256"]:
        return "revision_changed", "destination_or_payload_changed", {}
    expires = _at(record.get("expires_at"))
    if expires and now >= expires:
        return "expired_unpublished", "deadline_passed_without_matching_receipt", {}
    return "blocked", excluded.get((record["campaign"], record["provider"]), "not_currently_eligible"), {}


def observe(*, now=None, policy=None, evaluate=True):
    from ocpf_post.portfolio_queue import capture_inputs, _rescue_state
    from ocpf_post.portfolio_cross_platform import plan_refill
    now = (now or base._utc_now()).astimezone(UTC)
    effective = base.validate_policy(policy or base.load_policy())
    controls = settings(effective)
    with _lock():
        previous = load()
        if previous.get("observed_at") and _at(previous["observed_at"]) > now:
            raise ValueError("Clock moved behind previous queue observation")
        inputs = capture_inputs(now, 1440, effective)
        projection = plan_refill(now=now, horizon_minutes=1440, policy=effective, inputs=inputs) if evaluate else None
        selected = {identity(c): c for c in (projection or {}).get("plan", [])}
        all_candidates = inputs["candidates"] + [c for partition in inputs.get("account_partitions", {}).values() for c in partition["inputs"]["candidates"]]
        candidates = {identity(c): c for c in all_candidates}
        candidate_scopes = {}
        rescue_states = {}
        def capture_rescue_states(scope_inputs, scope_policy, scope=None):
            zone = ZoneInfo(scope_policy["timezone"])
            for candidate in scope_inputs.get("candidates", []):
                key = identity(candidate)
                if scope is not None:
                    candidate_scopes[key] = scope
                provider = candidate["provider"]
                rescue_states[key] = _rescue_state(
                    candidate,
                    scope_inputs.get("candidates", []),
                    now,
                    provider,
                    scope_policy["providers"][provider],
                    scope_inputs.get("day_counts", {}),
                    zone,
                    controls,
                )
        capture_rescue_states(inputs, effective)
        for scope, partition in inputs.get("account_partitions", {}).items():
            capture_rescue_states(partition["inputs"], partition["policy"], scope)
        decisions = list((projection or {}).get("decisions", []))
        # Keep exceptions after they leave the eligible set; do not backfill
        # invented first-eligible dates for historical expired imports.
        cutoff = now - timedelta(days=controls["retention_days"])
        records = {k: dict(r) for k, r in previous["records"].items()
                   if not (r.get("closed_at") and _at(r["closed_at"]) < cutoff)}
        for key, candidate in candidates.items():
            record = records.setdefault(key, {
                **{k: candidate.get(k) for k in ("campaign", "provider", "account_id", "text_sha256", "project", "lane", "expires_at")},
                "first_eligible_at": _stamp(now), "waiting_evaluations": 0, "unselected_evaluations": 0,
            })
            record["last_eligible_at"] = _stamp(now)
            record["expires_at"] = candidate.get("expires_at")
        exclusions = {(r["campaign"], r["provider"]): r["reason"] for r in inputs["exclusions"]}
        schedule_index, receipt_index = {}, {}
        for row in schedule_records():
            schedule_index.setdefault(identity(row), []).append(row)
        for row in iter_receipts():
            receipt_index.setdefault(identity(row), []).append(row)
        # One evaluation per configured refill interval, including manual reruns.
        interval = max(1, int(effective.get("planner_interval_minutes", 15)))
        bucket = int(now.timestamp()) // (interval * 60)
        for key, record in records.items():
            try:
                state, reason, detail = _outcome(record, candidates, exclusions,
                                                schedule_index.get(key, []), receipt_index.get(key, []), now)
            except Exception as exc:
                state, reason, detail = "observation_unavailable", type(exc).__name__, {}
            if state == "waiting" and record["provider"] not in effective["providers"]:
                state, reason = "blocked", "provider_not_configured_for_allocation"
            record["last_seen_at"] = _stamp(now)
            if (state, reason) != (record.get("state"), record.get("reason")):
                record["changed_at"] = _stamp(now)
            record.update(state=state, reason=reason, outcome=detail)
            rescue_info = rescue_states.get(key) if state == "waiting" else None
            record["rescue_active"] = bool(rescue_info and rescue_info.get("rescue"))
            record["rescue_trigger"] = rescue_info.get("trigger") if rescue_info else None
            record["due_before_expiry"] = int(rescue_info.get("due_count", 0)) if rescue_info else 0
            record["authorised_slots_before_expiry"] = int(rescue_info.get("authorised_slots_before_expiry", 0)) if rescue_info else 0
            record["capacity_shortfall"] = max(
                0, record["due_before_expiry"] - record["authorised_slots_before_expiry"]
            ) if rescue_info else 0
            if state == "expired_unpublished":
                record.setdefault("expiry_observed_at", _stamp(now))
            if state in CLOSED:
                record.setdefault("closed_at", _stamp(now))
            else:
                record.pop("closed_at", None)
            if evaluate:
                old_projection = _at(record.get("projected_run_at"))
                if state == "waiting" and old_projection and old_projection <= now:
                    record.setdefault("missed_projection_at", _stamp(old_projection))
                if state not in {"waiting", "expired_unpublished"}:
                    record.pop("missed_projection_at", None)
                projected = selected.get(key) if state == "waiting" else None
                record["projected_run_at"] = projected.get("run_at") if projected else None
                new_evaluation = record.get("last_evaluation_bucket") != bucket
                if state == "waiting" and new_evaluation:
                    record["waiting_evaluations"] += 1
                    if not record["projected_run_at"]:
                        record["unselected_evaluations"] += 1
                    expires = _at(record.get("expires_at"))
                    remaining = (expires - now).total_seconds() / 3600 if expires else None
                    rescue_window = bool(record.get("rescue_active"))
                    if rescue_window:
                        record["rescue_window_evaluations"] = int(record.get("rescue_window_evaluations", 0) or 0) + 1
                        record["last_rescue_window_at"] = _stamp(now)
                        if projected:
                            record["rescue_projected_evaluations"] = int(record.get("rescue_projected_evaluations", 0) or 0) + 1
                        else:
                            record["rescue_unselected_evaluations"] = int(record.get("rescue_unselected_evaluations", 0) or 0) + 1
                        scope = candidate_scopes.get(key)
                        scoped = [
                            decision for decision in decisions
                            if decision.get("provider") == record["provider"]
                            and ((scope is None and not decision.get("account")) or decision.get("account") == scope)
                            and _at(decision.get("run_at"))
                            and _at(decision.get("run_at")) < expires
                        ]
                        if any(str(decision.get("reason", "")).startswith("bounded_rescue_") for decision in scoped):
                            record["rescue_turn_evaluations"] = int(record.get("rescue_turn_evaluations", 0) or 0) + 1
                        if any(decision.get("campaign") for decision in scoped):
                            record["service_opportunity_evaluations"] = int(record.get("service_opportunity_evaluations", 0) or 0) + 1
                record["last_evaluation_bucket"] = bucket
            record["age_hours"] = round(max(0, (now - _at(record["first_eligible_at"])).total_seconds()) / 3600, 3)
        value = {"schema_version": 1, "observed_at": _stamp(now), "settings": controls, "records": records,
                 "last_evaluated_at": _stamp(now) if evaluate else previous.get("last_evaluated_at"),
                 "selection": effective.get("selection", "legacy")}
        if evaluate:
            value["capacity"] = projection["capacity"]
            value["account_capacity"] = projection.get("account_capacity", {})
        else:
            value["capacity"] = previous.get("capacity", {})
            value["account_capacity"] = previous.get("account_capacity", {})
        _write(value)
    return report(now=now, value=value)


def _survival_class(record, *, now, controls):
    state = record.get("state")
    first = _at(record.get("first_eligible_at"))
    age_hours = max(0, (now - first).total_seconds()) / 3600 if first else 0
    expires = _at(record.get("expires_at"))
    remaining = (expires - now).total_seconds() / 3600 if expires else None
    if state == "expired_unpublished":
        if record.get("missed_projection_at"):
            return "expired_after_projection_elapsed"
        if int(record.get("rescue_unselected_evaluations", 0) or 0) > 0:
            return "expired_after_rescue_window_unselected"
        if int(record.get("rescue_window_evaluations", 0) or 0) > 0:
            return "expired_after_rescue_window"
        return "expired_without_recorded_rescue_window"
    if state == "waiting" and record.get("rescue_active"):
        return "rescue_capacity_pressure" if record.get("rescue_trigger") == "capacity_feasibility" else "rescue_window"
    if state == "waiting" and age_hours >= controls["wait_warning_hours"]:
        return "aged_waiting"
    if state == "waiting":
        return "healthy_waiting"
    return state or "unknown"


def _survival_summary(records, *, now, controls):
    rows = list(records.values())
    classes = Counter(_survival_class(record, now=now, controls=controls) for record in rows)
    waiting = [record for record in rows if record.get("state") == "waiting"]
    expired = [record for record in rows if record.get("state") == "expired_unpublished"]
    pressure_groups = {}
    for record in waiting:
        if record.get("rescue_trigger") != "capacity_feasibility":
            continue
        key = (record.get("provider"), record.get("account_id"), record.get("expires_at"))
        pressure_groups[key] = max(
            pressure_groups.get(key, 0),
            int(record.get("capacity_shortfall", 0) or 0),
        )
    by_lane = {}
    for record in rows:
        lane = str(record.get("lane") or "unknown")
        bucket = by_lane.setdefault(lane, {"waiting": 0, "rescue_window": 0, "expired_unpublished": 0})
        cls = _survival_class(record, now=now, controls=controls)
        if record.get("state") == "waiting":
            bucket["waiting"] += 1
        if cls in {"rescue_window", "rescue_capacity_pressure"}:
            bucket["rescue_window"] += 1
        if record.get("state") == "expired_unpublished":
            bucket["expired_unpublished"] += 1
    return {
        "classes": dict(classes),
        "waiting": len(waiting),
        "rescue_window": classes.get("rescue_window", 0) + classes.get("rescue_capacity_pressure", 0),
        "capacity_pressure": classes.get("rescue_capacity_pressure", 0),
        "capacity_shortfall": sum(pressure_groups.values()),
        "capacity_pressure_groups": len(pressure_groups),
        "expired_unpublished": len(expired),
        "expired_after_rescue_window": sum(int(record.get("rescue_window_evaluations", 0) or 0) > 0 for record in expired),
        "expired_after_unselected_rescue_evaluations": sum(int(record.get("rescue_unselected_evaluations", 0) or 0) > 0 for record in expired),
        "expired_after_projection_elapsed": sum(bool(record.get("missed_projection_at")) for record in expired),
        "by_lane": by_lane,
        "boundary": (
            "Survival accounting is observational. Rescue uses only existing authorised allocation capacity; "
            "expiry is never extended and development content is never converted into evergreen content."
        ),
    }


def report(*, now=None, value=None, include_records=False):
    now = (now or base._utc_now()).astimezone(UTC)
    value = load() if value is None else value
    controls = value.get("settings", DEFAULTS)
    evaluated = _at(value.get("last_evaluated_at"))
    fresh = bool(evaluated and timedelta(0) <= now - evaluated <= timedelta(minutes=controls["stale_after_minutes"]))
    issues = []
    groups = {}
    for record in value["records"].values():
        state = record.get("state")
        group = groups.setdefault((record["provider"], record.get("project", "")),
                                  {"waiting": 0, "oldest_wait_hours": 0, "expiry_risks": 0})
        age = max(0, (now - _at(record["first_eligible_at"])).total_seconds()) / 3600
        expires = _at(record.get("expires_at"))
        remaining = (expires - now).total_seconds() / 3600 if expires else None
        projected = _at(record.get("projected_run_at"))
        codes = []
        if state == "waiting":
            if record.get("missed_projection_at") or (projected and projected <= now):
                codes.append("projection_elapsed_unreserved")
            group["waiting"] += 1
            group["oldest_wait_hours"] = round(max(group["oldest_wait_hours"], age), 3)
            if age >= controls["wait_warning_hours"]:
                codes.append("waiting_too_long")
            if record.get("rescue_active"):
                group["expiry_risks"] += 1
                # A projection is not a durable reservation. Capacity-feasibility
                # pressure can become true days before the fixed warning window.
                if record.get("rescue_trigger") == "capacity_feasibility":
                    codes.append("expiry_capacity_pressure")
                elif remaining is not None:
                    codes.append("expiry_approaching" if remaining > 0 else "expired_since_observation")
        elif state in {"expired_unpublished", "ambiguous_effect", "partial_effect", "schedule_requires_review", "receipt_inconsistent", "blocked", "observation_unavailable"}:
            codes.append(state)
        survival_class = _survival_class(record, now=now, controls=controls)
        for code in codes:
            issues.append({"code": code, "campaign": record["campaign"], "provider": record["provider"],
                           "account_id": record.get("account_id"), "missed_projection_at": record.get("missed_projection_at"),
                           "project": record.get("project"), "lane": record.get("lane"), "state": state, "reason": record.get("reason"),
                           "survival_class": survival_class, "age_hours": round(age, 3),
                           "hours_to_expiry": round(remaining, 3) if remaining is not None else None,
                           "expires_at": record.get("expires_at"),
                           "projected_run_at": _stamp(projected) if projected else None,
                           "waiting_evaluations": record.get("waiting_evaluations", 0),
                           "unselected_evaluations": record.get("unselected_evaluations", 0),
                           "rescue_window_evaluations": record.get("rescue_window_evaluations", 0),
                           "rescue_projected_evaluations": record.get("rescue_projected_evaluations", 0),
                           "rescue_unselected_evaluations": record.get("rescue_unselected_evaluations", 0),
                           "rescue_turn_evaluations": record.get("rescue_turn_evaluations", 0),
                           "service_opportunity_evaluations": record.get("service_opportunity_evaluations", 0),
                           "rescue_trigger": record.get("rescue_trigger"),
                           "due_before_expiry": record.get("due_before_expiry", 0),
                           "authorised_slots_before_expiry": record.get("authorised_slots_before_expiry", 0),
                           "capacity_shortfall": record.get("capacity_shortfall", 0)})
    result = {"schema_version": 1, "status": "not_observed" if not evaluated else "stale" if not fresh else "attention" if issues else "ok",
              "observed_at": value.get("observed_at"), "last_evaluated_at": value.get("last_evaluated_at"),
              "settings": controls, "selection": value.get("selection"), "record_count": len(value["records"]),
              "states": dict(Counter(r.get("state") for r in value["records"].values())),
              "issue_counts": dict(Counter(r["code"] for r in issues)), "issues": issues,
              "by_project": [{"provider": p, "project": project, **counts} for (p, project), counts in sorted(groups.items())],
              "capacity": value.get("capacity", {}), "account_capacity": value.get("account_capacity", {}),
              "survival": _survival_summary(value["records"], now=now, controls=controls),
              "boundary": "Local monitoring only. Age starts at the first eligible observation, not copy preparation; includes gaps between observations. Projections are not reservations. Closed records retained for the configured period; publication receipts are untouched. Alerts are local health/journal output, not delivered email or push notifications."}
    if include_records:
        result["records"] = list(value["records"].values())
    return result


def safe_observe(**kwargs):
    try:
        return observe(**kwargs)
    except Exception as exc:
        # Never leak a provider payload/path/secret in a monitoring exception.
        return {"status": "unavailable", "error_type": type(exc).__name__,
                "boundary": "Queue supervision failed; publishing authority unchanged. Inspect health locally."}
