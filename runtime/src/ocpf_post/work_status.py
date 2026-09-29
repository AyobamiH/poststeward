"""Generated current-work projection from existing local evidence.

This module is intentionally read-only. It consolidates already persisted runtime,
acceptance and queue evidence into one bounded operator/agent backlog. It never
collects a provider, refreshes credentials, changes approval, schedules, publishes,
retries or renews anything.
"""
from __future__ import annotations

from collections import Counter
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

from ocpf_post import local_store
from ocpf_post.state import state_dir

UTC = timezone.utc
MAX_ITEMS = 240


def _stamp(value: datetime) -> str:
    return value.astimezone(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _at(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed.astimezone(UTC) if parsed.tzinfo is not None else None


def _safe(name: str, fn: Callable[[], Any]) -> Any:
    try:
        return fn()
    except (OSError, ValueError, KeyError, TypeError, RuntimeError) as exc:
        return {"status": "unavailable", "component": name, "error_type": type(exc).__name__}


def _condition(kind: str, status: str, reason: str, message: str, observed_at: str) -> dict[str, Any]:
    return {
        "type": kind,
        "status": status,
        "reason": reason,
        "message": message,
        "observed_at": observed_at,
    }


def _item(
    item_id: str,
    title: str,
    *,
    kind: str,
    state: str,
    dependency: str,
    observed_at: str,
    blocker: str | None = None,
    deadline: str | None = None,
    safe_next_action: str,
    automatic_action: bool = False,
    do_not_replay: bool = False,
    do_not_renew: bool = False,
    progress: dict[str, bool | None] | None = None,
    conditions: list[dict[str, Any]] | None = None,
    evidence: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "id": item_id,
        "title": title,
        "kind": kind,
        "state": state,
        "dependency": dependency,
        "blocker": blocker,
        "deadline": deadline,
        "safe_next_action": safe_next_action,
        "automatic_action": automatic_action,
        "do_not_replay": do_not_replay,
        "do_not_renew": do_not_renew,
        "progress": progress or {
            "implemented": True,
            "configured": True,
            "observed": True,
            "accepted": False,
        },
        "conditions": conditions or [],
        "evidence": evidence or {},
        "observed_at": observed_at,
    }


def _editorial_items(observed_at: str) -> list[dict[str, Any]]:
    from ocpf_post.editorial_continuity import path

    state = local_store.read(path()) or {}
    raw_routes = state.get("routes")
    routes: dict[str, Any] = raw_routes if isinstance(raw_routes, dict) else {}
    rows = []
    for route in routes.values():
        if not isinstance(route, dict) or route.get("status") != "open":
            continue
        request_id = str(route.get("request_id") or route.get("route_key") or "unknown")
        project = str(route.get("project") or "unknown")
        provider = str(route.get("provider") or "unknown")
        reasons = [str(value) for value in route.get("demand_reasons", []) if isinstance(value, str)]
        market_cold = route.get("market_cold") is True
        conditions = route.get("conditions") if isinstance(route.get("conditions"), list) else []
        rows.append(_item(
            "EDITORIAL:" + request_id,
            f"{project}/{provider} editorial continuity",
            kind="editorial_supply",
            state="actionable",
            dependency="editorial_work",
            observed_at=observed_at,
            blocker=",".join(reasons) or "editorial_supply_open",
            deadline=route.get("earliest_runnable_expiry"),
            safe_next_action=str(route.get("request") or "reconcile_existing_batch_then_author_or_resolve_eligibility"),
            automatic_action=False,
            do_not_replay=True,
            do_not_renew=True,
            conditions=conditions,
            evidence={
                key: route.get(key) for key in (
                    "project", "provider", "account_id", "runnable", "reserved",
                    "stock_floor", "editorial_runway_hours", "surviving_runway_hours",
                    "market_cold", "market_cold_hours", "hours_since_last_effect",
                    "last_effect_at", "next_scheduled_at", "suggested_new_items",
                )
            } | {"market_cold": market_cold},
        ))
    return rows


def _queue_items(now: datetime, observed_at: str) -> list[dict[str, Any]]:
    from ocpf_post.queue_watch import report

    value = report(now=now)
    rows = []
    for issue in value.get("issues", []) if isinstance(value, dict) else []:
        if not isinstance(issue, dict):
            continue
        code = str(issue.get("code") or "queue_attention")
        if code not in {
            "expiry_approaching", "expiry_capacity_pressure", "expired_unpublished",
            "projection_elapsed_unreserved", "ambiguous_effect", "partial_effect",
            "schedule_requires_review", "receipt_inconsistent", "blocked",
        }:
            continue
        campaign = str(issue.get("campaign") or "unknown")
        provider = str(issue.get("provider") or "unknown")
        effect_uncertain = code in {"ambiguous_effect", "partial_effect", "receipt_inconsistent"}
        expired = code == "expired_unpublished"
        if effect_uncertain:
            state = "manual_review"
            dependency = "manual_review"
            action = "inspect_existing_effect_without_replay"
        elif expired:
            state = "terminal_attention"
            dependency = "evidence"
            action = "record_expiry_without_automatic_renewal"
        else:
            state = "deadline"
            dependency = "local_operator"
            action = "allow_normal_allocator_and_reconcile_without_forcing_or_renewing"
        rows.append(_item(
            f"QUEUE:{campaign}:{provider}:{code}",
            f"{campaign}/{provider} {code.replace('_', ' ')}",
            kind="queue",
            state=state,
            dependency=dependency,
            observed_at=observed_at,
            blocker=code,
            deadline=issue.get("expires_at"),
            safe_next_action=action,
            automatic_action=False,
            do_not_replay=effect_uncertain,
            do_not_renew=True,
            conditions=[_condition(
                "QueueSafe",
                "False",
                "".join(word.title() for word in code.split("_")),
                f"Queue supervision reported {code}.",
                observed_at,
            )],
            evidence={
                key: issue.get(key) for key in (
                    "campaign", "provider", "account_id", "project", "lane", "state",
                    "age_hours", "hours_to_expiry", "expires_at", "projected_run_at",
                    "capacity_shortfall", "survival_class",
                )
            },
        ))
    return rows


def _vault_deadline_items(now: datetime, observed_at: str) -> list[dict[str, Any]]:
    """Surface near-term imported-vault expiry even before Queue Watch enters rescue.

    This is a read-only deadline projection. It does not reserve, renew, replay,
    schedule or publish a campaign. Completed publication receipts remove a provider
    from the deadline set. No-replay consequence states such as ambiguous/partial
    effects remain visible until their evidence/recovery boundary is reconciled.
    """
    from ocpf_post.scheduler import ACTIVE_STATUSES
    from ocpf_post.source_receipts import publication_inputs

    manifests, schedules, receipts = publication_inputs()
    active = {
        (str(row.get("campaign") or ""), str(row.get("provider") or ""))
        for row in schedules
        if row.get("status") in ACTIVE_STATUSES
    }
    consequence_receipts: dict[tuple[str, str], dict[str, Any]] = {}
    for row in receipts:
        status = str(row.get("status") or "")
        if status not in {"published_verified", "published_unverified", "ambiguous_effect", "partial_effect"}:
            continue
        key = (str(row.get("campaign") or ""), str(row.get("provider") or ""))
        consequence_receipts[key] = row
    horizon = now + timedelta(hours=24)
    rows: list[dict[str, Any]] = []
    for campaign, manifest in manifests.items():
        if not isinstance(manifest, dict) or manifest.get("runtime_imported") is not True:
            continue
        raw_vault = manifest.get("vault")
        if not isinstance(raw_vault, dict):
            continue
        vault: dict[str, Any] = raw_vault
        raw_allocation = manifest.get("allocation")
        allocation: dict[str, Any] = raw_allocation if isinstance(raw_allocation, dict) else {}
        if allocation.get("enabled") is not True:
            continue
        expires = _at(allocation.get("expires_at"))
        if expires is None or not (now < expires <= horizon):
            continue
        raw_providers = manifest.get("providers")
        providers: list[Any] = raw_providers if isinstance(raw_providers, list) else []
        for raw_provider in providers:
            provider = str(raw_provider or "")
            if not provider:
                continue
            identity = (str(campaign), provider)
            receipt = consequence_receipts.get(identity)
            receipt_status = str(receipt.get("status") or "") if isinstance(receipt, dict) else ""
            if receipt_status in {"published_verified", "published_unverified"}:
                continue
            scheduled = identity in active
            hours_to_expiry = round((expires - now).total_seconds() / 3600, 3)

            if receipt_status == "ambiguous_effect":
                state = "manual_review"
                dependency = "manual_review"
                blocker = "ambiguous_effect"
                safe_next_action = "forensically_reconcile_existing_effect_without_replay_or_renewal"
                condition_reason = "AmbiguousEffectBeforeExpiry"
                condition_message = (
                    "An imported-vault effect is ambiguous inside the approval window; "
                    "preserve no-replay and reconcile existing evidence."
                )
            elif receipt_status == "partial_effect":
                state = "manual_review"
                dependency = "manual_review"
                blocker = "partial_effect"
                safe_next_action = "reconcile_partial_effect_without_replay_or_renewal"
                condition_reason = "PartialEffectBeforeExpiry"
                condition_message = (
                    "An imported-vault publication has a partial effect inside the approval window; "
                    "preserve no-replay and reconcile the existing provider effect."
                )
            elif scheduled:
                state = "waiting"
                dependency = "evidence"
                blocker = "approved_vault_expiry_within_24h"
                safe_next_action = (
                    "allow_existing_schedule_to_run_then_reconcile_exact_effect_without_replay_or_renewal"
                )
                condition_reason = "ExpiryWithin24Hours"
                condition_message = (
                    "An approved imported-vault campaign remains unresolved inside the next 24 hours."
                )
            else:
                state = "deadline"
                dependency = "local_operator"
                blocker = "approved_vault_expiry_within_24h"
                safe_next_action = (
                    "allow_normal_allocator_to_consider_while_valid_and_reconcile_exact_effect_before_expiry_without_replay_or_renewal"
                )
                condition_reason = "ExpiryWithin24Hours"
                condition_message = (
                    "An approved imported-vault campaign remains unresolved inside the next 24 hours."
                )

            rows.append(_item(
                f"VAULT-DEADLINE:{campaign}:{provider}",
                f"{campaign}/{provider} approved vault expiry",
                kind="vault_deadline",
                state=state,
                dependency=dependency,
                observed_at=observed_at,
                blocker=blocker,
                deadline=_stamp(expires),
                safe_next_action=safe_next_action,
                automatic_action=False,
                do_not_replay=True,
                do_not_renew=True,
                conditions=[_condition(
                    "VaultExpirySafe", "False", condition_reason, condition_message, observed_at,
                )],
                evidence={
                    "campaign": campaign,
                    "provider": provider,
                    "project": manifest.get("project"),
                    "expires_at": _stamp(expires),
                    "hours_to_expiry": hours_to_expiry,
                    "active_schedule": scheduled,
                    "receipt_status": receipt_status or None,
                    "schedule_id": receipt.get("schedule_id") if isinstance(receipt, dict) else None,
                    "post_id": receipt.get("post_id") if isinstance(receipt, dict) else None,
                    "vault_id": vault.get("id"),
                    "vault_base_campaign": vault.get("base_campaign"),
                },
            ))
    return rows


def _acceptance_items(now: datetime, observed_at: str) -> list[dict[str, Any]]:
    from ocpf_post.acceptance_views import path as acceptance_path

    # Use the persisted acceptance projection. Recomputing PC views here can
    # acquire component locks or perform fresh bounded collection preparation,
    # which would violate the read-only work/console contract.
    raw_value = local_store.read(acceptance_path()) or {}
    value: dict[str, Any] = raw_value if isinstance(raw_value, dict) else {}
    raw_sections = value.get("sections")
    sections: dict[str, Any] = raw_sections if isinstance(raw_sections, dict) else {}
    rows: list[dict[str, Any]] = []

    pc2 = sections.get("pc02_source_vault", {})
    if isinstance(pc2, dict) and pc2.get("status") != "observed":
        rows.append(_item(
            "PC-02",
            "Source and vault observation completeness",
            kind="acceptance",
            state="waiting",
            dependency="evidence",
            observed_at=observed_at,
            blocker=str(pc2.get("status") or "not_observed"),
            safe_next_action="continue_bounded_collection_and_fix_only_the_specific_unavailable_source_or_vault",
            evidence={
                "open_source_count": pc2.get("open_source_count"),
                "open_vault_count": pc2.get("open_vault_count"),
            },
        ))

    pc3 = sections.get("pc03_publication_readback", {})
    if isinstance(pc3, dict):
        for effect in pc3.get("results", []) if isinstance(pc3.get("results"), list) else []:
            if not isinstance(effect, dict) or effect.get("status") == "verified":
                continue
            status = str(effect.get("status") or "readback_open")
            schedule_id = str(effect.get("schedule_id") or "unknown")
            permission = status == "readback_permission_required"
            manual = status in {"forensic_review_required", "thread_manual_review_required"}
            rows.append(_item(
                "READBACK:" + schedule_id,
                f"{effect.get('campaign') or 'campaign'} readback",
                kind="publication_readback",
                state="external_gate" if permission else "manual_review" if manual else "waiting",
                dependency="external_provider" if permission else "manual_review" if manual else "evidence",
                observed_at=observed_at,
                blocker=status,
                safe_next_action=str(effect.get("next_action") or "bounded_reconciliation_without_resend"),
                automatic_action=effect.get("automatic_retry") is True,
                do_not_replay=effect.get("automatic_retry") is not True,
                do_not_renew=True,
                conditions=[_condition(
                    "ReadbackVerified", "False",
                    "".join(word.title() for word in status.split("_")),
                    "Existing provider effect is not independently verified in the current evidence.",
                    observed_at,
                )],
                evidence={
                    key: effect.get(key) for key in (
                        "schedule_id", "campaign", "provider", "account_id", "post_id",
                        "required_scope", "scope_recorded", "previous_status",
                    )
                },
            ))

    pc4 = sections.get("pc04_learning", {})
    if isinstance(pc4, dict) and pc4.get("status") != "observed":
        rows.append(_item(
            "PC-04",
            "Comparable learning evidence",
            kind="learning",
            state="waiting",
            dependency="evidence",
            observed_at=observed_at,
            blocker=str(pc4.get("status") or "open"),
            safe_next_action="collect_comparable_age_evidence_and_allow_bounded_learning_to_remain_descriptive_until_qualified",
            evidence={
                "target_count": len(pc4.get("targets", [])) if isinstance(pc4.get("targets"), list) else None,
            },
        ))

    pc5 = sections.get("pc05_effect_reconciliation", {})
    if isinstance(pc5, dict):
        for effect in pc5.get("reply_effects", []) if isinstance(pc5.get("reply_effects"), list) else []:
            if not isinstance(effect, dict) or effect.get("status") == "verified":
                continue
            inbox_id = str(effect.get("inbox_id") or "unknown")
            rows.append(_item(
                "REPLY:" + inbox_id,
                "Reply effect reconciliation",
                kind="reply_readback",
                state="manual_review" if effect.get("status") in {"forensic_review_required"} else "waiting",
                dependency="manual_review" if effect.get("status") in {"forensic_review_required"} else "evidence",
                observed_at=observed_at,
                blocker=str(effect.get("status") or "reply_readback_open"),
                safe_next_action="reconcile_existing_reply_without_resend",
                do_not_replay=True,
                do_not_renew=True,
                evidence={key: effect.get(key) for key in (
                    "inbox_id", "provider", "account_id", "post_id", "original_status", "retry_at",
                )},
            ))
        for failed in pc5.get("failed_schedules", []) if isinstance(pc5.get("failed_schedules"), list) else []:
            if not isinstance(failed, dict):
                continue
            schedule_id = str(failed.get("schedule_id") or "unknown")
            rows.append(_item(
                "FAILED:" + schedule_id,
                f"{failed.get('campaign') or 'campaign'} historical failed schedule",
                kind="historical_effect",
                state="manual_review",
                dependency="manual_review",
                observed_at=observed_at,
                blocker=str(failed.get("failure_class") or "failed"),
                safe_next_action="review_historical_failure_without_automatic_replay",
                do_not_replay=True,
                do_not_renew=True,
                evidence={key: failed.get(key) for key in (
                    "schedule_id", "campaign", "provider", "account_id", "run_at", "updated_at",
                )},
            ))

    pc6 = sections.get("pc06_inbound_coverage", {})
    if isinstance(pc6, dict) and pc6.get("status") != "observed":
        rows.append(_item(
            "PC-06",
            "Inbound coverage completeness",
            kind="acceptance",
            state="external_gate" if pc6.get("linkedin", {}).get("status") == "permission_required" else "waiting",
            dependency="external_provider" if pc6.get("linkedin", {}).get("status") == "permission_required" else "evidence",
            observed_at=observed_at,
            blocker=str(pc6.get("status") or "partial"),
            safe_next_action="continue_bounded_collection; treat unavailable provider scope as unknown rather_than_zero",
            do_not_replay=True,
            evidence={
                "scope_count": len(pc6.get("scopes", [])) if isinstance(pc6.get("scopes"), list) else None,
                "linkedin": pc6.get("linkedin"),
            },
        ))
    return rows


def _external_items(now: datetime, observed_at: str) -> list[dict[str, Any]]:
    from ocpf_post import google_connection
    from ocpf_post.alert_delivery import report as alert_report
    from ocpf_post.outcome_connectors import report as connector_report
    from ocpf_post.capacity_experiment import report as experiment_report
    from ocpf_post.open_work import sustained

    rows: list[dict[str, Any]] = []

    google = _safe("google_connection", google_connection.report)
    if isinstance(google, dict):
        longevity = google.get("refresh_beyond_seven_days_observed") is True
        consent = google.get("oauth_publishing_status") not in {None, "not_observed", "unknown"}
        if not (longevity and consent):
            rows.append(_item(
                "GOOGLE-OAUTH-LONGEVITY",
                "Google vault OAuth longevity and consent",
                kind="external_acceptance",
                state="time_gate" if not longevity else "owner_configuration",
                dependency="time" if not longevity else "owner_configuration",
                observed_at=observed_at,
                blocker="same_credential_span_below_seven_days" if not longevity else "consent_publishing_state_not_observed",
                safe_next_action="continue_normal_vault_collection_with_same_credential_and_record_cloud_console_consent_state",
                progress={
                    "implemented": True,
                    "configured": google.get("status") == "observed",
                    "observed": google.get("status") == "observed",
                    "accepted": False,
                },
                evidence={
                    "observed_success_span_days": google.get("observed_success_span_days"),
                    "refresh_beyond_seven_days_observed": longevity,
                    "oauth_publishing_status": google.get("oauth_publishing_status"),
                },
            ))

    connectors = _safe("outcome_connectors", connector_report)
    connector_rows = connectors.get("connectors", []) if isinstance(connectors, dict) else []
    enabled = any(isinstance(row, dict) and row.get("enabled") for row in connector_rows)
    successful = any(isinstance(row, dict) and row.get("last_success_at") for row in connector_rows)
    if not (enabled and successful):
        rows.append(_item(
            "OUTCOME-CONNECTOR",
            "Receipt-linked business outcomes",
            kind="optional_integration",
            state="owner_configuration" if not enabled else "waiting",
            dependency="owner_configuration" if not enabled else "evidence",
            observed_at=observed_at,
            blocker="no_enabled_connector" if not enabled else "no_successful_sync_observed",
            safe_next_action="register_and_enable_one_authorised_real_outcome_source_when_available",
            progress={
                "implemented": True,
                "configured": bool(connector_rows),
                "observed": successful,
                "accepted": successful,
            },
            evidence={"connector_count": len(connector_rows), "enabled": enabled, "successful_sync": successful},
        ))

    alerts = _safe("alerts", alert_report)
    if isinstance(alerts, dict) and not alerts.get("last_delivery_at"):
        rows.append(_item(
            "ALERT-DELIVERY",
            "Operating incident delivery",
            kind="optional_integration",
            state="owner_configuration" if alerts.get("status") == "not_configured" else "waiting",
            dependency="owner_configuration" if alerts.get("status") == "not_configured" else "evidence",
            observed_at=observed_at,
            blocker="no_alert_destination" if alerts.get("status") == "not_configured" else "no_successful_delivery_observed",
            safe_next_action="configure_one_reviewed_alert_receiver_then_wait_for_or_send_only_notification_evidence",
            progress={
                "implemented": True,
                "configured": alerts.get("status") != "not_configured",
                "observed": bool(alerts.get("last_delivery_at")),
                "accepted": bool(alerts.get("last_delivery_at")),
            },
            evidence={
                key: alerts.get(key) for key in ("status", "enabled", "last_delivery_at", "retry_at")
            },
        ))

    experiment = _safe("capacity_experiment", lambda: experiment_report(now=now))
    if isinstance(experiment, dict) and experiment.get("ends_at"):
        end = _at(experiment.get("ends_at"))
        saved = local_store.read(state_dir() / "capacity-experiment-report.json")
        if end and (now < end or not saved):
            rows.append(_item(
                "CAPACITY-TRIAL",
                "Historical capacity trial close-out",
                kind="time_based_evidence",
                state="time_gate" if now < end else "actionable",
                dependency="time" if now < end else "local_operator",
                observed_at=observed_at,
                blocker="trial_end_not_reached" if now < end else "final_report_not_saved",
                deadline=_stamp(end),
                safe_next_action="wait_for_stored_end_then_reconcile_and_save_descriptive_report"
                if now < end else "reconcile_completed_trial_and_save_descriptive_report",
                do_not_renew=True,
                evidence={
                    "status": experiment.get("status"),
                    "ends_at": experiment.get("ends_at"),
                    "runtime_overlay_superseded": experiment.get("status") == "superseded",
                },
            ))

    collection = local_store.read(state_dir() / "collection-cycle.json") or {}
    sustained_state = sustained(collection, now)
    if sustained_state.get("status") != "observed":
        rows.append(_item(
            "SUSTAINED-OPERATION",
            "Sustained unattended operation",
            kind="operational_evidence",
            state="waiting",
            dependency="evidence",
            observed_at=observed_at,
            blocker=str(sustained_state.get("status") or "insufficient_evidence"),
            safe_next_action="continue_normal_timers_and_retain_bounded_cycle_history_until_24h_gap_and_error_criteria_pass",
            evidence={key: sustained_state.get(key) for key in (
                "retained_cycles", "observed_span_hours", "latest_age_seconds",
                "max_gap_seconds", "attention_cycles_last_24h",
            )},
        ))
    return rows


def _collapse_duplicate_roots(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Prefer the most specific effect-reconciliation item over queue symptoms."""
    readback_keys = {
        (
            str((row.get("evidence") or {}).get("campaign") or ""),
            str((row.get("evidence") or {}).get("provider") or ""),
        )
        for row in items
        if row.get("kind") == "publication_readback"
        and isinstance(row.get("evidence"), dict)
    }
    specific_vault_keys = {
        (
            str((row.get("evidence") or {}).get("campaign") or ""),
            str((row.get("evidence") or {}).get("provider") or ""),
        )
        for row in items
        if row.get("kind") == "queue"
        and row.get("blocker") in {
            "expiry_approaching", "expiry_capacity_pressure",
            "ambiguous_effect", "partial_effect", "receipt_inconsistent",
        }
        and isinstance(row.get("evidence"), dict)
    }
    collapsed = []
    for row in items:
        raw_evidence = row.get("evidence")
        evidence: dict[str, Any] = raw_evidence if isinstance(raw_evidence, dict) else {}
        key = (str(evidence.get("campaign") or ""), str(evidence.get("provider") or ""))
        if (
            row.get("kind") == "queue"
            and row.get("blocker") in {"ambiguous_effect", "partial_effect", "receipt_inconsistent"}
            and key in readback_keys
        ):
            continue
        if row.get("kind") == "vault_deadline" and key in specific_vault_keys:
            continue
        collapsed.append(row)
    return collapsed


def build(*, now: datetime | None = None) -> dict[str, Any]:
    now = (now or datetime.now(UTC)).astimezone(UTC)
    observed_at = _stamp(now)
    items: list[dict[str, Any]] = []
    for producer in (
        lambda: _editorial_items(observed_at),
        lambda: _queue_items(now, observed_at),
        lambda: _vault_deadline_items(now, observed_at),
        lambda: _acceptance_items(now, observed_at),
        lambda: _external_items(now, observed_at),
    ):
        value = _safe("work_projection", producer)
        if isinstance(value, list):
            items.extend(value)
        elif isinstance(value, dict) and value.get("status") == "unavailable":
            items.append(_item(
                "WORK-PROJECTION:" + str(value.get("component") or "unknown"),
                "Work projection unavailable",
                kind="projection",
                state="unknown",
                dependency="evidence",
                observed_at=observed_at,
                blocker=str(value.get("error_type") or "unavailable"),
                safe_next_action="inspect_the_specific_local_projection_failure_without_social_side_effects",
                progress={"implemented": True, "configured": True, "observed": False, "accepted": False},
            ))

    items = _collapse_duplicate_roots(items)
    deduped: dict[str, dict[str, Any]] = {}
    for item in items:
        deduped.setdefault(str(item["id"]), item)
    rows = list(deduped.values())
    rows.sort(key=lambda row: (
        row.get("deadline") is None,
        str(row.get("deadline") or ""),
        str(row.get("state") or ""),
        str(row.get("id") or ""),
    ))
    truncated = len(rows) > MAX_ITEMS
    rows = rows[:MAX_ITEMS]
    state_counts = Counter(str(row.get("state") or "unknown") for row in rows)
    dependency_counts = Counter(str(row.get("dependency") or "unknown") for row in rows)
    deadline_24h = sum(
        1 for row in rows
        if (deadline := _at(row.get("deadline"))) is not None and now <= deadline <= now + timedelta(hours=24)
    )
    status = (
        "attention" if any(row.get("state") in {"actionable", "deadline", "manual_review", "terminal_attention"} for row in rows)
        else "open" if rows
        else "observed"
    )
    return {
        "schema_version": 1,
        "status": status,
        "observed_at": observed_at,
        "item_count": len(rows),
        "truncated": truncated,
        "summary": {
            "by_state": dict(state_counts),
            "by_dependency": dict(dependency_counts),
            "deadlines_within_24h": deadline_24h,
            "no_replay_count": sum(row.get("do_not_replay") is True for row in rows),
            "no_renew_count": sum(row.get("do_not_renew") is True for row in rows),
        },
        "items": rows,
        "conditions": [
            _condition(
                "WorkProjectionReady", "True", "LocalEvidenceProjected",
                "Current work was generated from existing local evidence.", observed_at,
            ),
            _condition(
                "ImmediateDeadline", "True" if deadline_24h else "False",
                "DeadlineWithin24Hours" if deadline_24h else "NoDeadlineWithin24Hours",
                f"{deadline_24h} work item(s) have a deadline inside 24 hours.",
                observed_at,
            ),
        ],
        "boundary": (
            "Read-only generated backlog. It consolidates local evidence and safe next actions; "
            "it never publishes, retries, renews, changes account authority, refreshes credentials "
            "or converts unknown evidence into failure."
        ),
    }


def render_text(value: dict[str, Any]) -> str:
    raw_summary = value.get("summary")
    summary: dict[str, Any] = raw_summary if isinstance(raw_summary, dict) else {}
    raw_by_state = summary.get("by_state")
    by_state: dict[str, Any] = raw_by_state if isinstance(raw_by_state, dict) else {}
    lines = [
        f"Work status: {value.get('status')} · items={value.get('item_count', 0)}",
        "States: " + ", ".join(
            f"{key}={count}" for key, count in sorted(by_state.items())
        ),
        f"Deadlines within 24h: {summary.get('deadlines_within_24h', 0)}",
    ]
    for row in value.get("items", [])[:40]:
        deadline = f" · deadline={row['deadline']}" if row.get("deadline") else ""
        flags = []
        if row.get("do_not_replay"):
            flags.append("NO-REPLAY")
        if row.get("do_not_renew"):
            flags.append("NO-RENEW")
        suffix = (" · " + ",".join(flags)) if flags else ""
        lines.append(
            f"{row.get('id')} · {row.get('state')} · {row.get('dependency')}{deadline}{suffix}\n"
            f"  {row.get('safe_next_action')}"
        )
    if value.get("truncated"):
        lines.append("Output truncated; use --json for the complete bounded projection.")
    return "\n".join(lines)
