"""Read-only historical schedule-versus-publication volume audit.

The audit answers a deliberately narrow question from existing local ledgers:
what was scheduled to run on a local calendar day, what publication effects are
receipt-backed, and which scheduled outcomes did not become a completed
publication.  It never calls a provider, creates a schedule, refills supply or
rewrites evidence.
"""
from __future__ import annotations

from collections import Counter
from datetime import date as Date, datetime, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

UTC = timezone.utc
PUBLISHED_STATUSES = {"published_verified", "published_unverified"}


def _parse_day(value: str) -> Date:
    try:
        return Date.fromisoformat(str(value))
    except ValueError as exc:
        raise ValueError("Audit date must be YYYY-MM-DD") from exc


def _zone(name: str) -> ZoneInfo:
    try:
        return ZoneInfo(str(name))
    except ZoneInfoNotFoundError as exc:
        raise ValueError(f"Unknown timezone: {name}") from exc


def _timestamp(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value.astimezone(UTC) if value.tzinfo else None
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed.astimezone(UTC) if parsed.tzinfo else None


def _local_day(value: Any, zone: ZoneInfo) -> Date | None:
    parsed = _timestamp(value)
    return parsed.astimezone(zone).date() if parsed else None


def _receipt(publication: dict[str, Any]) -> dict[str, Any]:
    value = publication.get("receipt")
    return value if isinstance(value, dict) else {}


def _safe_schedule(row: dict[str, Any]) -> dict[str, Any]:
    from ocpf_post.scheduler import provider_failure_metadata

    keys = (
        "schedule_id", "campaign", "provider", "account_id", "run_at", "status",
        "last_event", "updated_at", "post_id", "receipt_status",
        "readback_verified", "retry_at", "failure_class", "failure_stage",
        "provider_http_status", "automatic_retry", "provider_problem_type", "provider_reason",
        "provider_error_code", "circuit_failure_class", "circuit_retry_at",
    )
    value = {key: row.get(key) for key in keys if row.get(key) is not None}
    if row.get("status") == "failed":
        for key, item in provider_failure_metadata(row).items():
            if key != "classification_basis" and item is not None:
                value.setdefault(key, item)
    return value


def _safe_effect(publication: dict[str, Any]) -> dict[str, Any]:
    receipt = _receipt(publication)
    keys = ("campaign", "provider", "account_id", "schedule_id", "post_id", "status", "url")
    row = {key: receipt.get(key) for key in keys if receipt.get(key) is not None}
    at = publication.get("at")
    if isinstance(at, datetime):
        row["at"] = at.astimezone(UTC).isoformat().replace("+00:00", "Z")
    row["effective_verified"] = bool(publication.get("effective_verified"))
    if publication.get("verification_basis"):
        row["verification_basis"] = publication.get("verification_basis")
    return row


def _day_summary(
    day: Date,
    *,
    schedules: list[dict[str, Any]],
    publications: list[dict[str, Any]],
    zone: ZoneInfo,
) -> dict[str, Any]:
    target_schedules = [
        row for row in schedules
        if isinstance(row, dict) and _local_day(row.get("run_at"), zone) == day
    ]
    target_ids = {
        str(row.get("schedule_id"))
        for row in target_schedules
        if row.get("schedule_id")
    }
    status_counts = Counter(str(row.get("status") or "unknown") for row in target_schedules)
    provider_counts = Counter(str(row.get("provider") or "unknown") for row in target_schedules)
    provider_status_counts = Counter(
        f"{row.get('provider') or 'unknown'}:{row.get('status') or 'unknown'}"
        for row in target_schedules
    )
    nonpublished = [
        _safe_schedule(row)
        for row in target_schedules
        if str(row.get("status") or "") not in PUBLISHED_STATUSES
    ]
    failure_class_counts = Counter(
        str(row.get("failure_class") or "unclassified_failure")
        for row in nonpublished
        if row.get("status") == "failed"
    )
    failure_stage_counts = Counter(
        str(row.get("failure_stage") or "unknown")
        for row in nonpublished
        if row.get("status") == "failed"
    )
    provider_http_status_counts = Counter(
        str(row.get("provider_http_status"))
        for row in nonpublished
        if row.get("status") == "failed" and row.get("provider_http_status") is not None
    )

    effects_on_day = [
        publication for publication in publications
        if isinstance(publication, dict) and _local_day(publication.get("at"), zone) == day
    ]
    effects_for_target_schedules = []
    linked_ids: set[str] = set()
    for publication in publications:
        if not isinstance(publication, dict):
            continue
        receipt = _receipt(publication)
        if not receipt:
            continue
        schedule_id = str(receipt.get("schedule_id") or "")
        if schedule_id and schedule_id in target_ids:
            effects_for_target_schedules.append(publication)
            linked_ids.add(schedule_id)

    effect_provider_counts = Counter(
        str(_receipt(publication).get("provider") or "unknown")
        for publication in effects_on_day
        if _receipt(publication)
    )
    effect_status_counts = Counter(
        str(_receipt(publication).get("status") or "unknown")
        for publication in effects_on_day
        if _receipt(publication)
    )
    direct_or_other_effects = []
    for publication in effects_on_day:
        receipt = _receipt(publication)
        schedule_id = str(receipt.get("schedule_id") or "")
        if not schedule_id or schedule_id not in target_ids:
            direct_or_other_effects.append(_safe_effect(publication))

    return {
        "date": day.isoformat(),
        "scheduled_total": len(target_schedules),
        "scheduled_published_terminal": sum(status_counts[name] for name in PUBLISHED_STATUSES),
        "scheduled_nonpublished": len(nonpublished),
        "schedule_status_counts": dict(sorted(status_counts.items())),
        "schedule_provider_counts": dict(sorted(provider_counts.items())),
        "schedule_provider_status_counts": dict(sorted(provider_status_counts.items())),
        "failure_class_counts": dict(sorted(failure_class_counts.items())),
        "failure_stage_counts": dict(sorted(failure_stage_counts.items())),
        "provider_http_status_counts": dict(sorted(provider_http_status_counts.items())),
        "publication_effects_on_day": len(effects_on_day),
        "publication_effects_for_scheduled_work": len(effects_for_target_schedules),
        "scheduled_without_receipt_effect": max(0, len(target_ids - linked_ids)),
        "effect_provider_counts": dict(sorted(effect_provider_counts.items())),
        "effect_status_counts": dict(sorted(effect_status_counts.items())),
        "nonpublished_schedules": nonpublished,
        "direct_or_other_schedule_effects_on_day": direct_or_other_effects,
    }


def audit(*, day: str, timezone_name: str | None = None) -> dict[str, Any]:
    """Return target-day and adjacent-day schedule/effect evidence from local state."""
    from ocpf_post.performance_review import publications
    from ocpf_post.portfolio import load_policy
    from ocpf_post.scheduler import schedule_records

    target = _parse_day(day)
    if timezone_name is None:
        policy = load_policy(effective=False)
        timezone_name = str(policy.get("timezone") or "Europe/London")
    zone = _zone(timezone_name)

    schedules = [row for row in schedule_records() if isinstance(row, dict)]
    publication_rows = [row for row in publications().values() if isinstance(row, dict)]
    days = [target - timedelta(days=1), target, target + timedelta(days=1)]
    summaries = [
        _day_summary(value, schedules=schedules, publications=publication_rows, zone=zone)
        for value in days
    ]
    target_summary = summaries[1]
    before, after = summaries[0], summaries[2]

    target_total = int(target_summary["scheduled_total"])
    adjacent = [int(before["scheduled_total"]), int(after["scheduled_total"])]
    if target_total < min(adjacent):
        relative = "lower_than_both_adjacent_days"
    elif target_total < max(adjacent):
        relative = "lower_than_one_adjacent_day"
    elif target_total > max(adjacent):
        relative = "higher_than_both_adjacent_days"
    else:
        relative = "not_lower_than_adjacent_days"

    execution_attention = int(target_summary["scheduled_nonpublished"])
    linked_effects = int(target_summary["publication_effects_for_scheduled_work"])
    if target_total == 0:
        evidence_code = "no_scheduled_work"
        evidence_detail = (
            "No schedule run times fell on the target local day. Historical ledgers therefore show "
            "no provider execution opportunity to explain for that day."
        )
    elif execution_attention:
        evidence_code = "nonpublished_schedule_outcomes_present"
        evidence_detail = (
            f"{execution_attention} target-day schedule(s) ended outside published_verified/"
            "published_unverified; inspect the listed statuses before attributing low volume only to supply."
        )
    elif linked_effects == target_total:
        evidence_code = "scheduled_work_completed"
        evidence_detail = (
            "Every target-day schedule has a receipt-backed publication effect. If volume was lower than "
            "adjacent days, the evidence points to fewer scheduled opportunities rather than execution loss."
        )
    else:
        evidence_code = "schedule_effect_linkage_incomplete"
        evidence_detail = (
            f"{target_total - linked_effects} target-day schedule(s) lack a matching receipt-backed publication "
            "effect in the current local evidence."
        )

    return {
        "schema_version": 1,
        "status": "observed",
        "date": target.isoformat(),
        "timezone": timezone_name,
        "days": summaries,
        "target": target_summary,
        "comparison": {
            "previous_scheduled_total": adjacent[0],
            "target_scheduled_total": target_total,
            "next_scheduled_total": adjacent[1],
            "relative_schedule_volume": relative,
        },
        "evidence_observation": {
            "code": evidence_code,
            "detail": evidence_detail,
        },
        "boundary": (
            "Read-only local ledger audit. Counts schedules by configured local run date and receipt-backed "
            "publication effects by first local creation receipt. No provider call, refill, reservation, cancellation "
            "or replay is performed, and no historical causality is claimed beyond the recorded evidence."
        ),
    }
