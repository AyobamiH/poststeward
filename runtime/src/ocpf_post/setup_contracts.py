from __future__ import annotations

import math
import re
import uuid
from copy import deepcopy
from datetime import datetime, timezone
from enum import Enum
from typing import Any

from ocpf_post.schedule_semantics import classify_schedule

UTC = timezone.utc
SCHEMA_VERSION = 1


class SetupMode(str, Enum):
    FRESH = "fresh"
    MIGRATE = "migrate"
    DIFFERENT_OPERATOR = "different_operator"
    RECOVER = "recover"
    EXPLORE = "explore"


class SetupStage(str, Enum):
    CREATED = "created"
    PREFLIGHT_READY = "preflight_ready"
    OPERATION_READY = "operation_ready"
    CONFIGURATION_READY = "configuration_ready"
    SOURCE_DRAINED = "source_drained"
    BUNDLE_VERIFIED = "bundle_verified"
    RESTORED_QUARANTINED = "restored_quarantined"
    PROVIDER_AUTHORITY_READY = "provider_authority_ready"
    SCHEDULE_RECONCILED = "schedule_reconciled"
    RECOVERY_REVIEW_READY = "recovery_review_ready"
    VERIFICATION_READY = "verification_ready"
    DRY_ACCEPTED = "dry_accepted"
    ACTIVATION_READY = "activation_ready"
    ACTIVE = "active"
    EXPLORE_READY = "explore_ready"
    ABORTED = "aborted"


OPERATION_STATUSES = frozenset({"prepared", "active", "recovery_review", "retired"})
INSTALLATION_STATUSES = frozenset({"candidate", "active", "retired", "lost", "recovery_unknown"})
READINESS_STATUSES = frozenset({"ready", "blocked", "attention", "unknown", "skipped", "not_applicable"})
READINESS_SEVERITIES = frozenset({"blocker", "attention", "info"})
READINESS_OVERALL = frozenset(
    {"BLOCKED", "RECOVERY_REVIEW_REQUIRED", "READY_WITH_OPTIONAL_GAPS", "READY", "EXPLORE_ONLY"}
)
MIGRATION_CONTEXTS = frozenset({"healthy_migration", "recovery"})
SCHEDULE_DISPOSITIONS = frozenset(
    {
        "historical_only",
        "terminal_review",
        "block_sealing",
        "recovery_quarantine",
        "held_future",
        "handoff_expired",
        "hold_recompute",
        "blocked_unknown",
    }
)

_REASON_CODE_RE = re.compile(r"^[a-z][a-z0-9_]*(?:\.[a-z][a-z0-9_]*)+$")
_RECOVERY_PREFIXES = (
    "authority.",
    "bundle.authenticity.",
    "schedule.executing.",
    "schedule.ambiguous_effect.",
    "schedule.partial_effect.",
    "provider.stale_authority.",
)

_MODE_PATHS = {
    SetupMode.FRESH.value: (
        SetupStage.CREATED.value,
        SetupStage.PREFLIGHT_READY.value,
        SetupStage.OPERATION_READY.value,
        SetupStage.CONFIGURATION_READY.value,
        SetupStage.VERIFICATION_READY.value,
        SetupStage.DRY_ACCEPTED.value,
        SetupStage.ACTIVATION_READY.value,
        SetupStage.ACTIVE.value,
    ),
    SetupMode.DIFFERENT_OPERATOR.value: (
        SetupStage.CREATED.value,
        SetupStage.PREFLIGHT_READY.value,
        SetupStage.OPERATION_READY.value,
        SetupStage.CONFIGURATION_READY.value,
        SetupStage.VERIFICATION_READY.value,
        SetupStage.DRY_ACCEPTED.value,
        SetupStage.ACTIVATION_READY.value,
        SetupStage.ACTIVE.value,
    ),
    SetupMode.MIGRATE.value: (
        SetupStage.CREATED.value,
        SetupStage.PREFLIGHT_READY.value,
        SetupStage.SOURCE_DRAINED.value,
        SetupStage.BUNDLE_VERIFIED.value,
        SetupStage.RESTORED_QUARANTINED.value,
        SetupStage.PROVIDER_AUTHORITY_READY.value,
        SetupStage.SCHEDULE_RECONCILED.value,
        SetupStage.VERIFICATION_READY.value,
        SetupStage.DRY_ACCEPTED.value,
        SetupStage.ACTIVATION_READY.value,
        SetupStage.ACTIVE.value,
    ),
    SetupMode.RECOVER.value: (
        SetupStage.CREATED.value,
        SetupStage.PREFLIGHT_READY.value,
        SetupStage.BUNDLE_VERIFIED.value,
        SetupStage.RESTORED_QUARANTINED.value,
        SetupStage.PROVIDER_AUTHORITY_READY.value,
        SetupStage.SCHEDULE_RECONCILED.value,
        SetupStage.RECOVERY_REVIEW_READY.value,
        SetupStage.VERIFICATION_READY.value,
        SetupStage.DRY_ACCEPTED.value,
        SetupStage.ACTIVATION_READY.value,
        SetupStage.ACTIVE.value,
    ),
    SetupMode.EXPLORE.value: (
        SetupStage.CREATED.value,
        SetupStage.PREFLIGHT_READY.value,
        SetupStage.OPERATION_READY.value,
        SetupStage.CONFIGURATION_READY.value,
        SetupStage.VERIFICATION_READY.value,
        SetupStage.EXPLORE_READY.value,
    ),
}

_TERMINAL_SETUP_STAGES = frozenset(
    {SetupStage.ACTIVE.value, SetupStage.EXPLORE_READY.value, SetupStage.ABORTED.value}
)


def _now_iso(value: datetime | None = None) -> str:
    dt = (value or datetime.now(UTC)).astimezone(UTC).replace(microsecond=0)
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def _uuid(value: str | None = None) -> str:
    if value is None:
        return str(uuid.uuid4())
    parsed = uuid.UUID(str(value))
    if parsed.version != 4:
        raise ValueError("Identity must be a UUID4")
    return str(parsed)


def _parse_time(value: Any) -> datetime:
    if not isinstance(value, str) or not value:
        raise ValueError("Timestamp is required")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("Invalid timestamp") from exc
    if parsed.tzinfo is None:
        raise ValueError("Timestamp must include timezone")
    return parsed.astimezone(UTC)


def _require_schema(value: dict[str, Any]) -> None:
    if not isinstance(value, dict) or value.get("schema_version") != SCHEMA_VERSION:
        raise ValueError("Unsupported setup/recovery schema")


def new_operation(operator_label: str, *, operation_id: str | None = None) -> dict[str, Any]:
    label = str(operator_label or "").strip()
    if not label or len(label) > 200:
        raise ValueError("operator_label must be between 1 and 200 characters")
    return {
        "schema_version": SCHEMA_VERSION,
        "operation_id": _uuid(operation_id),
        "operator_label": label,
        "status": "prepared",
        "authority_generation": 1,
        "active_installation_id": None,
    }


def validate_operation(value: dict[str, Any]) -> dict[str, Any]:
    _require_schema(value)
    _uuid(str(value.get("operation_id") or ""))
    label = value.get("operator_label")
    if not isinstance(label, str) or not label.strip() or len(label) > 200:
        raise ValueError("Invalid operator_label")
    status = value.get("status")
    if status not in OPERATION_STATUSES:
        raise ValueError("Invalid operation status")
    generation = value.get("authority_generation")
    if type(generation) is not int or generation < 1:
        raise ValueError("Invalid authority_generation")
    active = value.get("active_installation_id")
    if status == "active":
        if active is None:
            raise ValueError("Active operation requires active installation")
        _uuid(str(active))
    elif active is not None:
        raise ValueError("Only active operations may name an active installation")
    return value


def new_installation(
    operation_id: str,
    *,
    installation_id: str | None = None,
    machine_label: str | None = None,
) -> dict[str, Any]:
    operation = _uuid(operation_id)
    label = str(machine_label).strip() if machine_label is not None else None
    if label is not None and (not label or len(label) > 200):
        raise ValueError("machine_label must be between 1 and 200 characters")
    return {
        "schema_version": SCHEMA_VERSION,
        "installation_id": _uuid(installation_id),
        "operation_id": operation,
        "status": "candidate",
        "authority_generation": None,
        **({"machine_label": label} if label is not None else {}),
    }


def validate_installation(value: dict[str, Any]) -> dict[str, Any]:
    _require_schema(value)
    _uuid(str(value.get("installation_id") or ""))
    _uuid(str(value.get("operation_id") or ""))
    status = value.get("status")
    if status not in INSTALLATION_STATUSES:
        raise ValueError("Invalid installation status")
    generation = value.get("authority_generation")
    if status == "candidate":
        if generation is not None:
            raise ValueError("Candidate installation cannot hold authority_generation")
    elif generation is not None and (type(generation) is not int or generation < 1):
        raise ValueError("Invalid installation authority_generation")
    if status == "active" and generation is None:
        raise ValueError("Active installation requires authority_generation")
    label = value.get("machine_label")
    if label is not None and (not isinstance(label, str) or not label.strip() or len(label) > 200):
        raise ValueError("Invalid machine_label")
    return value


def new_setup_session(
    mode: str,
    *,
    operation_id: str | None = None,
    session_id: str | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    mode_value = SetupMode(mode).value
    timestamp = _now_iso(now)
    return {
        "schema_version": SCHEMA_VERSION,
        "session_id": _uuid(session_id),
        "mode": mode_value,
        "operation_id": _uuid(operation_id) if operation_id else None,
        "stage": SetupStage.CREATED.value,
        "session_status": "open",
        "revision": 1,
        "last_transition_id": None,
        "created_at": timestamp,
        "updated_at": timestamp,
    }


def validate_setup_session(value: dict[str, Any]) -> dict[str, Any]:
    _require_schema(value)
    _uuid(str(value.get("session_id") or ""))
    mode = SetupMode(str(value.get("mode") or "")).value
    operation_id = value.get("operation_id")
    if operation_id is not None:
        _uuid(str(operation_id))
    stage = str(value.get("stage") or "")
    if stage not in set(_MODE_PATHS[mode]) | {SetupStage.ABORTED.value}:
        raise ValueError("Stage is invalid for setup mode")
    revision = value.get("revision")
    if type(revision) is not int or revision < 1:
        raise ValueError("Invalid setup revision")
    status = value.get("session_status")
    expected = (
        "completed"
        if stage in {SetupStage.ACTIVE.value, SetupStage.EXPLORE_READY.value}
        else "aborted"
        if stage == SetupStage.ABORTED.value
        else "open"
    )
    if status != expected:
        raise ValueError("setup session_status does not match stage")
    transition_id = value.get("last_transition_id")
    if transition_id is not None:
        _uuid(str(transition_id))
    for key in ("created_at", "updated_at"):
        _parse_time(value.get(key))
    return value


def allowed_next_stages(value: dict[str, Any]) -> tuple[str, ...]:
    validate_setup_session(value)
    stage = value["stage"]
    if stage in _TERMINAL_SETUP_STAGES:
        return ()
    path = _MODE_PATHS[value["mode"]]
    index = path.index(stage)
    result = []
    if index + 1 < len(path):
        result.append(path[index + 1])
    result.append(SetupStage.ABORTED.value)
    return tuple(result)


def transition_setup_session(
    value: dict[str, Any],
    next_stage: str,
    *,
    transition_id: str | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    validate_setup_session(value)
    target = str(next_stage)
    if target not in allowed_next_stages(value):
        raise ValueError(f"Illegal setup transition: {value['stage']} -> {target}")
    updated = deepcopy(value)
    updated["previous_stage"] = value["stage"]
    updated["stage"] = target
    updated["revision"] = int(value["revision"]) + 1
    updated["last_transition_id"] = _uuid(transition_id)
    updated["updated_at"] = _now_iso(now)
    if target == SetupStage.ABORTED.value:
        updated["session_status"] = "aborted"
    elif target in {SetupStage.ACTIVE.value, SetupStage.EXPLORE_READY.value}:
        updated["session_status"] = "completed"
    else:
        updated["session_status"] = "open"
    validate_setup_session(updated)
    return updated


def readiness_check(
    *,
    code: str,
    status: str,
    severity: str,
    required: bool,
    scope: str,
    evidence_method: str,
    summary: str,
    consequence_boundary: str,
    remediation_action_id: str | None = None,
    evidence: dict[str, Any] | None = None,
    observed_at: datetime | None = None,
) -> dict[str, Any]:
    value = {
        "schema_version": SCHEMA_VERSION,
        "code": code,
        "status": status,
        "severity": severity,
        "required": required,
        "scope": scope,
        "observed_at": _now_iso(observed_at),
        "evidence_method": evidence_method,
        "evidence": evidence or {},
        "summary": summary,
        "consequence_boundary": consequence_boundary,
        "remediation_action_id": remediation_action_id,
    }
    return validate_readiness_check(value)


def validate_readiness_check(value: dict[str, Any]) -> dict[str, Any]:
    _require_schema(value)
    code = value.get("code")
    if not isinstance(code, str) or not _REASON_CODE_RE.fullmatch(code):
        raise ValueError("Invalid readiness reason code")
    if value.get("status") not in READINESS_STATUSES:
        raise ValueError("Invalid readiness status")
    if value.get("severity") not in READINESS_SEVERITIES:
        raise ValueError("Invalid readiness severity")
    if type(value.get("required")) is not bool:
        raise ValueError("Readiness required must be boolean")
    for key in ("scope", "evidence_method", "summary", "consequence_boundary"):
        raw = value.get(key)
        if not isinstance(raw, str) or not raw.strip() or len(raw) > 1000:
            raise ValueError(f"Invalid readiness {key}")
    remediation = value.get("remediation_action_id")
    if remediation is not None and (
        not isinstance(remediation, str) or not remediation.strip() or len(remediation) > 200
    ):
        raise ValueError("Invalid remediation_action_id")
    evidence = value.get("evidence")
    if not isinstance(evidence, dict) or len(evidence) > 32:
        raise ValueError("Readiness evidence must be a bounded object")
    for key, item in evidence.items():
        if not isinstance(key, str) or not key or len(key) > 100:
            raise ValueError("Invalid readiness evidence key")
        if item is not None and type(item) not in (str, int, float, bool):
            raise ValueError("Readiness evidence values must be JSON scalars")
        if type(item) is float and not math.isfinite(item):
            raise ValueError("Readiness evidence number must be finite")
        if isinstance(item, str) and len(item) > 1000:
            raise ValueError("Readiness evidence string is too long")
    _parse_time(value.get("observed_at"))
    return value


def _recovery_sensitive(code: str) -> bool:
    return code.startswith(_RECOVERY_PREFIXES)


def aggregate_readiness(checks: list[dict[str, Any]], *, mode: str) -> dict[str, Any]:
    mode_value = SetupMode(mode).value
    rows = [validate_readiness_check(deepcopy(row)) for row in checks]
    codes = [row["code"] for row in rows]
    if len(codes) != len(set(codes)):
        raise ValueError("Duplicate readiness reason code")
    hard = [row for row in rows if row["required"] and row["status"] in {"blocked", "unknown"}]
    routine_hard = [row for row in hard if not _recovery_sensitive(row["code"])]
    recovery_hard = [row for row in hard if _recovery_sensitive(row["code"])]

    if routine_hard:
        overall = "BLOCKED"
    elif recovery_hard:
        overall = "RECOVERY_REVIEW_REQUIRED" if mode_value == SetupMode.RECOVER.value else "BLOCKED"
    elif mode_value == SetupMode.EXPLORE.value:
        overall = "EXPLORE_ONLY"
    elif any(row["status"] not in {"ready", "skipped", "not_applicable"} for row in rows):
        overall = "READY_WITH_OPTIONAL_GAPS"
    else:
        overall = "READY"

    return {
        "schema_version": SCHEMA_VERSION,
        "mode": mode_value,
        "status": overall,
        "check_count": len(rows),
        "required_blocker_codes": [row["code"] for row in hard],
        "attention_codes": [
            row["code"]
            for row in rows
            if row["status"] not in {"ready", "skipped", "not_applicable"}
        ],
    }


def schedule_disposition(
    record: dict[str, Any],
    *,
    context: str,
    now: datetime | None = None,
) -> dict[str, Any]:
    if context not in MIGRATION_CONTEXTS:
        raise ValueError("Unknown migration context")
    current = (now or datetime.now(UTC)).astimezone(UTC)
    meaning = classify_schedule(record, now=current)
    status = str(record.get("status") or "")

    if status == "held":
        disposition = "hold_recompute"
        review = False
        rearm = True
    elif status == "handoff_expired":
        disposition = "historical_only"
        review = False
        rearm = False
    elif status in {"ambiguous_effect", "partial_effect"}:
        disposition = "terminal_review"
        review = True
        rearm = False
    elif meaning.published or status in {
        "failed",
        "drift_blocked",
        "duplicate_blocked",
        "blocked_drift",
        "blocked_duplicate",
        "cancelled",
    }:
        disposition = "historical_only"
        review = False
        rearm = False
    elif status == "executing":
        disposition = "block_sealing" if context == "healthy_migration" else "recovery_quarantine"
        review = True
        rearm = False
    elif status == "scheduled" and meaning.state in {"deferred", "actionable_deferred"}:
        disposition = "hold_recompute"
        review = False
        rearm = True
    elif status == "scheduled":
        try:
            run_at = _parse_time(record.get("run_at"))
        except ValueError:
            disposition = "blocked_unknown"
            review = True
            rearm = False
        else:
            if run_at > current:
                disposition = "held_future"
                review = False
                rearm = True
            else:
                disposition = "handoff_expired"
                review = False
                rearm = False
    else:
        disposition = "blocked_unknown"
        review = True
        rearm = False

    return {
        "schema_version": SCHEMA_VERSION,
        "schedule_id": str(record.get("schedule_id") or ""),
        "source_status": status or "unknown",
        "semantic_state": meaning.state,
        "context": context,
        "disposition": disposition,
        "actionable_after_restore": False,
        "eligible_for_rearm": rearm,
        "automatic_retry_safe": False,
        "requires_review": review,
    }
