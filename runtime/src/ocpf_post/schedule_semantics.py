"""Authoritative schedule/outcome interpretation.

This module interprets durable state only. It does not call providers, mutate
schedules or grant publication authority. Retryability is tied to consequence
stage: only an explicitly persisted pre-consequence provider-unavailable or
provider-circuit deferral is automatically retryable. Neither state replays a
previous failed provider consequence.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

UTC = timezone.utc
ACTIVE_STATUSES = frozenset({"scheduled", "executing"})
HELD_STATUSES = frozenset({"held"})
PUBLISHED_STATUSES = frozenset({"published_verified", "published_unverified"})
TERMINAL_EFFECT_STATUSES = frozenset({"published_verified", "published_unverified", "ambiguous_effect", "partial_effect"})
TERMINAL_SCHEDULE_STATUSES = frozenset({
    "published_verified", "published_unverified", "ambiguous_effect", "partial_effect", "failed",
    "drift_blocked", "duplicate_blocked", "blocked_drift", "blocked_duplicate", "cancelled",
    "handoff_expired",
})
KNOWN_SCHEDULE_STATUSES = ACTIVE_STATUSES | HELD_STATUSES | TERMINAL_SCHEDULE_STATUSES


def _at(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(UTC)


@dataclass(frozen=True)
class ScheduleMeaning:
    state: str
    consequence_stage: str
    active: bool
    actionable: bool
    automatic_retry_safe: bool
    terminal: bool
    ambiguous: bool
    requires_review: bool
    published: bool
    readback_verified: bool

    def as_dict(self) -> dict[str, Any]:
        return {
            "state": self.state,
            "consequence_stage": self.consequence_stage,
            "active": self.active,
            "actionable": self.actionable,
            "automatic_retry_safe": self.automatic_retry_safe,
            "terminal": self.terminal,
            "ambiguous": self.ambiguous,
            "requires_review": self.requires_review,
            "published": self.published,
            "readback_verified": self.readback_verified,
        }


def classify_schedule(record: dict[str, Any], *, now: datetime | None = None) -> ScheduleMeaning:
    now = (now or datetime.now(UTC)).astimezone(UTC)
    status = str(record.get("status") or "")
    if status == "scheduled":
        retry_at = _at(record.get("retry_at"))
        typed_deferral = record.get("failure_class") in {"provider_unavailable", "provider_circuit_open"} and retry_at is not None
        if typed_deferral and retry_at > now:
            return ScheduleMeaning("deferred", "pre_consequence", True, False, True, False,
                                   False, False, False, False)
        if typed_deferral:
            return ScheduleMeaning("actionable_deferred", "pre_consequence", True, True, True, False,
                                   False, False, False, False)
        return ScheduleMeaning("scheduled", "pre_consequence", True, True, False, False,
                               False, False, False, False)
    if status == "held":
        return ScheduleMeaning("held", "pre_consequence", False, False, False, False,
                               False, False, False, False)
    if status == "executing":
        # Once execution crossed the durable consequence boundary, a process loss
        # or transport loss cannot be converted into a blind retry.
        return ScheduleMeaning("executing", "consequence_started", True, False, False, False,
                               True, True, False, False)
    if status == "ambiguous_effect":
        return ScheduleMeaning("ambiguous_effect", "consequence_unknown", False, False, False, True,
                               True, True, False, False)
    if status == "partial_effect":
        return ScheduleMeaning("partial_effect", "consequence_partial", False, False, False, True,
                               False, True, True, False)
    if status == "published_unverified":
        # Provider readback is durable sidecar evidence keyed to the complete
        # immutable publication identity. Project it here; never rewrite history.
        from ocpf_post.readback_evidence import verified as separately_verified
        if separately_verified(record):
            return ScheduleMeaning("published_verified_by_readback", "consequence_verified", False, False,
                                   False, True, False, False, True, True)
        return ScheduleMeaning("published_unverified", "consequence_confirmed", False, False, False, True,
                               False, True, True, False)
    if status == "published_verified":
        return ScheduleMeaning("published_verified", "consequence_verified", False, False, False, True,
                               False, False, True, bool(record.get("readback_verified") is True))
    if status in {
        "failed", "drift_blocked", "duplicate_blocked", "blocked_drift",
        "blocked_duplicate", "cancelled", "handoff_expired",
    }:
        return ScheduleMeaning(status or "terminal", "pre_consequence", False, False, False, True,
                               False, status != "cancelled", False, False)
    return ScheduleMeaning("unknown", "unknown", False, False, False, False,
                           True, True, False, False)


def classify_receipt(record: dict[str, Any]) -> dict[str, Any]:
    status = str(record.get("status") or "")
    if status == "published_verified":
        return {"state": status, "terminal": True, "published": True,
                "readback_verified": record.get("readback_verified") is True,
                "automatic_retry_safe": False, "requires_review": record.get("readback_verified") is not True}
    if status == "published_unverified":
        return {"state": status, "terminal": True, "published": True,
                "readback_verified": False, "automatic_retry_safe": False, "requires_review": True}
    if status == "ambiguous_effect":
        return {"state": status, "terminal": True, "published": False,
                "readback_verified": False, "automatic_retry_safe": False, "requires_review": True}
    if status == "partial_effect":
        return {"state": status, "terminal": True, "published": True,
                "readback_verified": False, "automatic_retry_safe": False, "requires_review": True}
    return {"state": "unknown", "terminal": False, "published": False,
            "readback_verified": False, "automatic_retry_safe": False, "requires_review": True}


def due_actionable(record: dict[str, Any], *, now: datetime) -> bool:
    meaning = classify_schedule(record, now=now)
    run_at = _at(record.get("run_at"))
    return bool(meaning.actionable and run_at is not None and run_at <= now.astimezone(UTC))


def is_terminal_effect(status: str | None) -> bool:
    return str(status or "") in TERMINAL_EFFECT_STATUSES
