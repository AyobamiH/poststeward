"""Expanded read-only execution projection for the Post-Once living console.

This module composes existing local evidence into a deeper execution model.  It
never calls a social provider and never writes runtime state.  The projection
keeps three concepts separate:

* evidence movement: a persisted transition happened;
* state: a persisted/read-only subsystem condition is currently true;
* intent: the dry planner says work may happen in the future.
"""
from __future__ import annotations

from collections import Counter
from copy import deepcopy
from datetime import datetime, timezone
from typing import Any, Callable

from ocpf_post import local_store
from ocpf_post.observer import _canonical_digest, build_snapshot as build_base_snapshot
from ocpf_post.state import state_dir

MAX_EXECUTION_EVENTS = 260
PROVIDERS = {"x", "threads", "linkedin"}


def _safe(name: str, fn: Callable[[], Any]) -> Any:
    try:
        return fn()
    except (OSError, ValueError, KeyError, TypeError, RuntimeError) as exc:
        return {"status": "unavailable", "component": name, "error_type": type(exc).__name__}


def _collection() -> dict[str, Any]:
    """Read the persisted bounded collection-cycle report only."""
    raw = local_store.read(state_dir() / "collection-cycle.json")
    if not raw:
        return {"status": "not_recorded", "stages": [], "history_count": 0}
    if not isinstance(raw, dict):
        raise ValueError("Invalid collection-cycle state")
    stages = []
    for row in raw.get("stages", []):
        if not isinstance(row, dict) or not isinstance(row.get("stage"), str):
            continue
        outcome_counts = row.get("outcome_counts") if isinstance(row.get("outcome_counts"), dict) else {}
        stages.append({
            "stage": row["stage"][:120],
            "status": str(row.get("status") or "unknown")[:80],
            "duration_seconds": row.get("duration_seconds") if type(row.get("duration_seconds")) in (int, float) else None,
            "budget_seconds": row.get("budget_seconds") if type(row.get("budget_seconds")) in (int, float) else None,
            "outcome_counts": {str(k)[:80]: int(v) for k, v in outcome_counts.items() if type(v) is int and v >= 0},
        })
    statuses = Counter(row["status"] for row in stages)
    if any(name in statuses for name in ("attention", "timed_out", "unavailable", "unknown")):
        status = "attention"
    elif any(name in statuses for name in ("partial", "insufficient_evidence")):
        status = "partial"
    elif stages:
        status = "observed"
    else:
        status = "idle"
    return {
        "status": status,
        "started_at": raw.get("started_at"),
        "observed_at": raw.get("observed_at"),
        "completed_at": raw.get("completed_at"),
        "stages": stages,
        "history_count": len(raw.get("history", [])) if isinstance(raw.get("history"), list) else 0,
        "network_checked_by_projection": False,
        "boundary": "Persisted collector metadata only; stage output and provider prose remain private.",
    }


def _acceptance() -> dict[str, Any]:
    """Read the persisted compact acceptance view only; never recompute it here."""
    raw = local_store.read(state_dir() / "acceptance-views.json")
    if not raw:
        return {"status": "not_recorded", "sections": {}, "network_checked": False}
    if not isinstance(raw, dict) or raw.get("schema_version") != 1:
        raise ValueError("Invalid acceptance view state")
    return {
        "schema_version": 1,
        "status": raw.get("status") or "unknown",
        "observed_at": raw.get("observed_at"),
        "sections": raw.get("sections") if isinstance(raw.get("sections"), dict) else {},
        "network_checked": False,
        "recomputed": False,
        "boundary": "Persisted acceptance read model only; the console cannot close acceptance or trigger work.",
    }


def _admission() -> dict[str, Any]:
    """Project the active scoped source-admission gate, keeping aggregate pressure diagnostic-only."""
    from ocpf_post import admission
    from ocpf_post.registry import load_registry
    from ocpf_post.scoped_admission import Budget
    from ocpf_post.account_profiles import profile as account_profile, credential_present

    persisted = local_store.read(admission.state_file())
    if persisted and not isinstance(persisted, dict):
        raise ValueError("Invalid admission state")

    # Keep the legacy aggregate pressure controller visible, but do not present it
    # as the production gate. Source/vault/generative admission uses scoped Budget.
    evaluated = admission.decide(persist=False)
    legacy_metrics = evaluated.get("metrics") if isinstance(evaluated.get("metrics"), dict) else {}
    policy = admission.load_policy()
    persisted_mode = str((persisted or {}).get("mode") or "open")
    persisted_at = (persisted or {}).get("observed_at")
    persisted_age_seconds = None
    if persisted_at:
        try:
            parsed = datetime.fromisoformat(str(persisted_at).replace("Z", "+00:00"))
            if parsed.tzinfo is not None:
                persisted_age_seconds = max(
                    0.0,
                    (datetime.now(timezone.utc) - parsed.astimezone(timezone.utc)).total_seconds(),
                )
        except ValueError:
            persisted_age_seconds = None

    now = datetime.now(timezone.utc)
    baseline_budget = Budget(now, False)
    prime = getattr(baseline_budget, "prime_shared_evidence", None)
    if callable(prime):
        baseline_budget = prime()
    routes: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()
    registry = load_registry()
    projects = registry.get("projects") if isinstance(registry.get("projects"), dict) else {}
    for project, project_row in sorted(projects.items()):
        accounts = project_row.get("accounts") if isinstance(project_row, dict) else {}
        if not isinstance(accounts, dict):
            continue
        for alias, account in sorted(accounts.items()):
            if not isinstance(account, dict):
                continue
            provider = str(account.get("provider") or "")
            account_id = str(account.get("account_id") or "")
            identity = (str(project), provider, account_id)
            if provider not in PROVIDERS or not account_id or identity in seen:
                continue
            seen.add(identity)
            try:
                additional = account_profile(provider, account_id)
                if additional is not None and additional.get("enabled") is False:
                    routes.append({
                        "project": project,
                        "alias": alias,
                        "provider": provider,
                        "account_id": account_id,
                        "status": "inactive",
                        "admitted": False,
                        "optional": True,
                        "reasons": ["account_disabled"],
                    })
                    continue
                if additional is not None and not credential_present(additional):
                    routes.append({
                        "project": project,
                        "alias": alias,
                        "provider": provider,
                        "account_id": account_id,
                        "status": "unavailable",
                        "admitted": False,
                        "optional": False,
                        "reasons": ["credential_unavailable"],
                    })
                    continue
                gate = deepcopy(baseline_budget).admit(str(project), provider, account_id)
                admitted = gate.get("admitted") is True
                gate_reasons = list(gate.get("reasons") or [])
                status = (
                    "open"
                    if admitted
                    else "unavailable"
                    if "account_unavailable" in gate_reasons
                    else "paused"
                )
                routes.append({
                    "project": project,
                    "alias": alias,
                    "provider": provider,
                    "account_id": account_id,
                    "status": status,
                    "admitted": admitted,
                    "scope": gate.get("scope"),
                    "reasons": gate_reasons,
                    "protected": bool(gate.get("protected")),
                    "diagnostic_pressure": list(gate.get("pressure_reasons") or []),
                })
            except (OSError, ValueError, KeyError, TypeError, RuntimeError) as exc:
                routes.append({
                    "project": project,
                    "alias": alias,
                    "provider": provider,
                    "account_id": account_id,
                    "status": "unavailable",
                    "admitted": False,
                    "reasons": [type(exc).__name__],
                })

    open_count = sum(row["status"] == "open" for row in routes)
    paused_count = sum(row["status"] == "paused" for row in routes)
    unavailable_count = sum(row["status"] == "unavailable" for row in routes)
    inactive_count = sum(row["status"] == "inactive" for row in routes)
    if unavailable_count and not open_count and not paused_count:
        mode = "unavailable"
    elif open_count and (paused_count or unavailable_count):
        mode = "partial"
    elif open_count:
        mode = "open"
    elif paused_count:
        mode = "paused"
    else:
        mode = "not_configured"

    providers = {}
    for provider in sorted(PROVIDERS):
        current = int((legacy_metrics.get("by_provider") or {}).get(provider, 0) or 0)
        high = int((policy.get("provider_high_water") or {}).get(provider, 0) or 0)
        recovery = int((policy.get("provider_recovery_water") or {}).get(provider, 0) or 0)
        providers[provider] = {"current": current, "high_water": high, "recovery_water": recovery}

    return {
        "status": "partial" if unavailable_count else "observed",
        "mode": mode,
        "observed_at": now.replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "active_gate": "scoped_admission",
        "reasons": sorted({
            reason
            for row in routes
            if row.get("status") not in {"open", "inactive"}
            for reason in row.get("reasons", [])
        }),
        "routes": routes,
        "metrics": {
            "route_count": len(routes),
            "open_route_count": open_count,
            "paused_route_count": paused_count,
            "unavailable_route_count": unavailable_count,
            "inactive_route_count": inactive_count,
            # Retained only as a separately-labelled backlog diagnostic for API
            # compatibility; it is not the value displayed as the admission gate.
            "eligible_unreserved": int(legacy_metrics.get("eligible_unreserved", 0) or 0),
        },
        "backlog_pressure": {
            "diagnostic_only": True,
            "mode": str(evaluated.get("mode") or "unknown"),
            "reasons": [
                str(reason)[:160]
                for reason in (evaluated.get("reasons") or [])
                if isinstance(reason, str) and reason
            ],
            "metrics": {
                "eligible_unreserved": int(legacy_metrics.get("eligible_unreserved", 0) or 0),
                "aged_count": int(legacy_metrics.get("aged_count", 0) or 0),
                "expiry_risk_count": int(legacy_metrics.get("expiry_risk_count", 0) or 0),
                "queue_observation": legacy_metrics.get("queue_observation"),
            },
            "thresholds": {
                "global_high_water": int(policy.get("global_high_water", 0) or 0),
                "global_recovery_water": int(policy.get("global_recovery_water", 0) or 0),
                "providers": providers,
            },
            "persisted_state": {
                "present": bool(persisted),
                "mode": persisted_mode if persisted else None,
                "observed_at": persisted_at,
                "age_seconds": persisted_age_seconds,
                "matches_current_evaluation": (
                    bool(persisted)
                    and persisted_mode == str(evaluated.get("mode") or "unknown")
                ),
            },
        },
        "network_checked": False,
        "boundary": (
            "Read-only projection of the active scoped source-admission gate. Aggregate backlog pressure "
            "is retained separately as diagnostic evidence and is not presented as admission authority."
        ),
    }

def _event(source: str, target: str, kind: str, *, at: Any, label: str, evidence: str,
           motion: str = "travel", status: Any = None, **extra: Any) -> dict[str, Any]:
    value = {
        "source": source,
        "target": target,
        "kind": kind,
        "at": at,
        "label": label,
        "evidence": evidence,
        "motion": motion,
        "status": status,
        **{key: item for key, item in extra.items() if item is not None},
    }
    if value.get("schedule_id"):
        value["journey_key"] = "schedule:" + str(value["schedule_id"])
    elif value.get("engagement_id"):
        value["journey_key"] = "reply:" + str(value["engagement_id"])
    elif value.get("campaign"):
        value["journey_key"] = "campaign:" + str(value["campaign"]) + ":" + str(value.get("provider") or "")
    elif value.get("project"):
        value["journey_key"] = "source:" + str(value["project"])
    else:
        value["journey_key"] = "system:" + kind
    value["id"] = _canonical_digest(value)[:24]
    return value


VERBS = {
    "source_observed": "OBSERVES",
    "evidence_buffered": "BUFFERS",
    "admission_state": "GATES",
    "schedule_persisted": "LOCKS IN",
    "consequence_claimed": "CLAIMS",
    "preflight_deferred": "RETURNS",
    "publication_completed": "RELEASES",
    "publication_receipt": "VERIFIES",
    "ambiguous_effect": "FREEZES",
    "execution_blocked": "BLOCKS",
    "reply_observed": "BRANCHES",
    "reply_send_claimed": "CLAIMS",
    "reply_published": "RESPONDS",
    "reply_readback": "VERIFIES",
    "metrics_stored": "MEASURES",
    "feedback_evaluated": "EVALUATES",
    "feedback_available": "INFLUENCES",
    "collector_stage": "PULSES",
    "editorial_demand": "SIGNALS",
}


def _tone(status: Any, kind: str) -> str:
    text = (str(status or "") + " " + kind).lower()
    if any(word in text for word in ("failed", "blocked", "ambiguous", "attention", "timed_out", "unavailable", "error")):
        return "bad"
    if any(word in text for word in ("deferred", "partial", "pending", "paused", "unverified", "executing", "insufficient")):
        return "warn"
    if any(word in text for word in ("verified", "completed", "available", "observed", "published", "open", "signals")):
        return "good"
    return "normal"


def _execution_projection(base: dict[str, Any], admission: dict[str, Any], collection: dict[str, Any],
                          acceptance: dict[str, Any] | None = None,
                          work: dict[str, Any] | None = None) -> dict[str, Any]:
    portfolio = base.get("portfolio") if isinstance(base.get("portfolio"), dict) else {}
    pstatus = portfolio.get("status") if isinstance(portfolio.get("status"), dict) else {}
    plan = portfolio.get("plan") if isinstance(portfolio.get("plan"), dict) else {}
    plan_rows = plan.get("plan") if isinstance(plan.get("plan"), list) else []
    decisions = plan.get("decisions") if isinstance(plan.get("decisions"), list) else []
    replenishment = base.get("replenishment") if isinstance(base.get("replenishment"), dict) else {}
    observations = replenishment.get("source_observations") if isinstance(replenishment.get("source_observations"), dict) else {}
    activity = base.get("activity") if isinstance(base.get("activity"), dict) else {}
    engagement = base.get("engagement") if isinstance(base.get("engagement"), dict) else {}
    performance = base.get("performance") if isinstance(base.get("performance"), dict) else {}
    feedback = base.get("feedback") if isinstance(base.get("feedback"), dict) else {}
    acceptance = acceptance if isinstance(acceptance, dict) else {}
    work = work if isinstance(work, dict) else {}
    acceptance_sections = acceptance.get("sections") if isinstance(acceptance.get("sections"), dict) else {}
    pc1 = acceptance_sections.get("pc01_editorial_supply") if isinstance(acceptance_sections.get("pc01_editorial_supply"), dict) else {}
    pc2 = acceptance_sections.get("pc02_source_vault") if isinstance(acceptance_sections.get("pc02_source_vault"), dict) else {}
    editorial_requests = pc1.get("open_requests") if isinstance(pc1.get("open_requests"), list) else []
    vault_rows = pc2.get("vaults") if isinstance(pc2.get("vaults"), list) else []
    active_vault_entries = sum(int(row.get("active_entries", 0) or 0) for row in vault_rows if isinstance(row, dict))
    market_cold_count = sum(
        1 for row in editorial_requests
        if isinstance(row, dict) and row.get("market_cold") is True
    )

    pending_source = sum(int(row.get("pending_count", 0) or 0) for row in observations.values() if isinstance(row, dict))
    source_attention = sum(str(row.get("status") or "") not in {"", "observed"} for row in observations.values() if isinstance(row, dict))
    snapshot_at = datetime.now(timezone.utc)
    if base.get("observed_at"):
        try:
            parsed_snapshot_at = datetime.fromisoformat(str(base["observed_at"]).replace("Z", "+00:00"))
            if parsed_snapshot_at.tzinfo is not None:
                snapshot_at = parsed_snapshot_at.astimezone(timezone.utc)
        except ValueError:
            pass
    fresh_sources = 0
    for row in observations.values():
        if not isinstance(row, dict) or row.get("status") != "observed" or not row.get("observed_at"):
            continue
        try:
            observed_at = datetime.fromisoformat(str(row["observed_at"]).replace("Z", "+00:00"))
        except ValueError:
            continue
        if observed_at.tzinfo is not None and 0 <= (snapshot_at - observed_at.astimezone(timezone.utc)).total_seconds() <= 3600:
            fresh_sources += 1
    active_schedules = activity.get("active_schedules") if isinstance(activity.get("active_schedules"), list) else []
    receipts = activity.get("recent_receipts") if isinstance(activity.get("recent_receipts"), list) else []
    verified = Counter(str(row.get("provider")) for row in receipts if row.get("readback_verified") is True)
    engagement_counts = engagement.get("counts") if isinstance(engagement.get("counts"), dict) else {}
    by_age = Counter()
    for row in performance.get("recent", []) if isinstance(performance.get("recent"), list) else []:
        age = row.get("target_age_hours")
        if type(age) in (int, float) and int(age) in {24, 72, 168}:
            by_age[int(age)] += 1

    nodes = [
        {"id": "sources", "label": "Sources", "kind": "input", "value": len(observations) or replenishment.get("observed_repositories", 0), "metric": "configured source states", "status": "attention" if source_attention else "observed" if observations else "idle"},
        {"id": "observer", "label": "Observer", "kind": "collector", "value": fresh_sources, "metric": f"fresh of {len(observations)} watched", "status": "attention" if source_attention or fresh_sources < len(observations) else "observed"},
        {"id": "buffer", "label": "Evidence buffer", "kind": "buffer", "value": pending_source, "metric": "pending revisions", "status": "waiting" if pending_source else "idle"},
        {"id": "editorial", "label": "Editorial demand", "kind": "editorial", "value": len(editorial_requests), "metric": "open supply requests", "status": "attention" if market_cold_count else "open" if editorial_requests else "observed"},
        {"id": "vault", "label": "Drive vaults", "kind": "vault", "value": active_vault_entries, "metric": "active approved entries", "status": "attention" if any(isinstance(row, dict) and row.get("status") != "observed" for row in vault_rows) else "observed" if vault_rows else "idle"},
        {"id": "work", "label": "Open evidence work", "kind": "control", "value": int(work.get("item_count", 0) or 0), "metric": "mixed evidence/action items", "status": work.get("status") or "idle"},
        {"id": "admission", "label": "Source admission", "kind": "gate", "value": int((admission.get("metrics") or {}).get("open_route_count", 0) or 0), "metric": f"open of {int((admission.get('metrics') or {}).get('route_count', 0) or 0)} scoped routes", "status": admission.get("mode") or admission.get("status")},
        {"id": "inventory", "label": "Eligible scheduling pool", "kind": "inventory", "value": int(pstatus.get("eligible_deliveries", 0) or 0), "metric": "eligible unscheduled deliveries", "status": "available" if pstatus.get("eligible_deliveries") else "idle"},
        {"id": "service", "label": "Service selection", "kind": "decision", "value": len(plan_rows), "metric": "next dry-plan slots", "status": "planned" if plan_rows else "idle"},
        {"id": "scheduler", "label": "Scheduler", "kind": "durable", "value": len(active_schedules), "metric": "active schedules", "status": "active" if active_schedules else "idle"},
        {"id": "run_due", "label": "Consequence boundary", "kind": "authority", "value": sum(row.get("status") == "executing" for row in active_schedules), "metric": "executing", "status": "executing" if any(row.get("status") == "executing" for row in active_schedules) else "idle"},
        {"id": "x", "label": "X", "kind": "provider", "value": verified.get("x", 0), "metric": "verified in recent receipt sample", "status": "observed"},
        {"id": "threads", "label": "Threads", "kind": "provider", "value": verified.get("threads", 0), "metric": "verified in recent receipt sample", "status": "observed"},
        {"id": "linkedin", "label": "LinkedIn", "kind": "provider", "value": verified.get("linkedin", 0), "metric": "verified in recent receipt sample", "status": "observed"},
        {"id": "readback", "label": "Readback", "kind": "verification", "value": sum(row.get("readback_verified") is True for row in receipts), "metric": "verified effects", "status": "observed"},
        {"id": "engagement", "label": "Conversation watch", "kind": "monitor", "value": int(engagement_counts.get("pending", 0) or 0) + int(engagement_counts.get("drafted", 0) or 0), "metric": "waiting", "status": "waiting" if any(engagement_counts.get(k) for k in ("pending", "drafted")) else "idle"},
        {"id": "reply_worker", "label": "Reply worker", "kind": "worker", "value": int(engagement_counts.get("sending", 0) or 0), "metric": "sending", "status": "executing" if engagement_counts.get("sending") else "idle"},
        {"id": "measure_24", "label": "24h measure", "kind": "measurement", "value": by_age.get(24, 0), "metric": "stored snapshots", "status": "observed"},
        {"id": "measure_72", "label": "72h measure", "kind": "measurement", "value": by_age.get(72, 0), "metric": "stored snapshots", "status": "observed"},
        {"id": "measure_168", "label": "168h measure", "kind": "measurement", "value": by_age.get(168, 0), "metric": "stored snapshots", "status": "observed"},
        {"id": "learning", "label": "Learning", "kind": "feedback", "value": int(feedback.get("signal_count", 0) or 0), "metric": "active signals", "status": feedback.get("status") or "idle"},
    ]
    edges = [
        {"source": "sources", "target": "observer", "label": "observe source", "mode": "evidence"},
        {"source": "observer", "target": "buffer", "label": "retain unconsumed evidence", "mode": "evidence"},
        {"source": "buffer", "target": "admission", "label": "deterministic source admission", "mode": "state"},
        {"source": "sources", "target": "editorial", "label": "translate product truth for market", "mode": "state"},
        {"source": "editorial", "target": "vault", "label": "reviewed canonical supply", "mode": "state"},
        {"source": "vault", "target": "admission", "label": "approved vault import", "mode": "state"},
        {"source": "work", "target": "editorial", "label": "continuity and evidence demand", "mode": "state"},
        {"source": "admission", "target": "inventory", "label": "accepted inventory", "mode": "state"},
        {"source": "inventory", "target": "service", "label": "fair service pool", "mode": "intent"},
        {"source": "service", "target": "scheduler", "label": "future reservation intent", "mode": "intent"},
        {"source": "inventory", "target": "scheduler", "label": "schedule persisted", "mode": "evidence"},
        {"source": "scheduler", "target": "run_due", "label": "durable consequence claim", "mode": "evidence"},
        {"source": "run_due", "target": "x", "label": "provider consequence", "mode": "evidence"},
        {"source": "run_due", "target": "threads", "label": "provider consequence", "mode": "evidence"},
        {"source": "run_due", "target": "linkedin", "label": "provider consequence", "mode": "evidence"},
        {"source": "x", "target": "readback", "label": "receipt/readback", "mode": "evidence"},
        {"source": "threads", "target": "readback", "label": "receipt/readback", "mode": "evidence"},
        {"source": "linkedin", "target": "readback", "label": "receipt/readback", "mode": "evidence"},
        {"source": "x", "target": "engagement", "label": "reply observed", "mode": "evidence"},
        {"source": "threads", "target": "engagement", "label": "reply observed", "mode": "evidence"},
        {"source": "engagement", "target": "reply_worker", "label": "review/send", "mode": "evidence"},
        {"source": "reply_worker", "target": "x", "label": "reply consequence", "mode": "evidence"},
        {"source": "reply_worker", "target": "threads", "label": "reply consequence", "mode": "evidence"},
        {"source": "readback", "target": "measure_24", "label": "24h observation", "mode": "evidence"},
        {"source": "readback", "target": "measure_72", "label": "72h observation", "mode": "evidence"},
        {"source": "readback", "target": "measure_168", "label": "168h observation", "mode": "evidence"},
        {"source": "measure_24", "target": "learning", "label": "comparable evidence", "mode": "evidence"},
        {"source": "measure_72", "target": "learning", "label": "comparable evidence", "mode": "evidence"},
        {"source": "measure_168", "target": "learning", "label": "comparable evidence", "mode": "evidence"},
        {"source": "learning", "target": "service", "label": "bounded preference", "mode": "evidence"},
    ]

    events: list[dict[str, Any]] = []
    for project, row in observations.items():
        if not isinstance(row, dict) or not row.get("observed_at"):
            continue
        events.append(_event("sources", "observer", "source_observed", at=row.get("observed_at"),
            status=row.get("status"), label="source state observed", evidence="source-observations", project=project))
        pending = int(row.get("pending_count", 0) or 0)
        if pending:
            events.append(_event("observer", "buffer", "evidence_buffered", at=row.get("observed_at"),
                status=row.get("status"), label=f"{pending} source revision{'s' if pending != 1 else ''} buffered",
                evidence="source-observations", project=project, pending_count=pending))

    for request in editorial_requests:
        if not isinstance(request, dict):
            continue
        at_value = request.get("last_observed_at") or request.get("first_requested_at")
        if not at_value:
            continue
        reasons = request.get("demand_reasons") if isinstance(request.get("demand_reasons"), list) else []
        events.append(_event(
            "editorial", "editorial", "editorial_demand", at=at_value,
            status="market_cold" if request.get("market_cold") else "open",
            label="editorial continuity demand observed",
            evidence="editorial-continuity", motion="pulse",
            project=request.get("project"), provider=request.get("provider"),
            request_id=request.get("request_id"), reasons=reasons[:6],
        ))

    base_flows = ((base.get("machine") or {}).get("flows") if isinstance(base.get("machine"), dict) else []) or []
    for flow in base_flows:
        if not isinstance(flow, dict) or not flow.get("at"):
            continue
        kind = str(flow.get("kind") or "")
        if kind in {"metrics_stored", "feedback_evaluated", "feedback_available"}:
            continue
        source, target = str(flow.get("source") or ""), str(flow.get("target") or "")
        if kind == "schedule_persisted":
            source, target = "inventory", "scheduler"
        remap = {"planner": "service", "performance": "measure_24"}
        source, target = remap.get(source, source), remap.get(target, target)
        events.append(_event(source, target, kind, at=flow.get("at"), status=flow.get("status"),
            label=str(flow.get("label") or kind), evidence=str(flow.get("evidence") or "local-evidence"),
            provider=flow.get("provider"), campaign=flow.get("campaign"), schedule_id=flow.get("schedule_id"),
            post_id=flow.get("post_id"), engagement_id=flow.get("engagement_id")))

    for row in performance.get("recent", []) if isinstance(performance.get("recent"), list) else []:
        if not isinstance(row, dict) or not row.get("captured_at"):
            continue
        target_age = row.get("target_age_hours")
        age = int(target_age) if type(target_age) in (int, float) and int(target_age) in {24, 72, 168} else 24
        availability = row.get("availability") if isinstance(row.get("availability"), dict) else {}
        events.append(_event("readback", f"measure_{age}", "metrics_stored", at=row.get("captured_at"),
            status=availability.get("status"), label=f"{age}h performance snapshot stored",
            evidence="performance-snapshot", provider=row.get("provider"), campaign=row.get("campaign"),
            post_id=row.get("post_id"), target_age_hours=age))

    if feedback.get("observed_at"):
        supporting = Counter()
        for signal in feedback.get("signals", []) if isinstance(feedback.get("signals"), list) else []:
            for age in signal.get("supporting_target_ages", []) if isinstance(signal, dict) else []:
                if type(age) in (int, float) and int(age) in {24, 72, 168}:
                    supporting[int(age)] += 1
        age = max(supporting, key=supporting.get) if supporting else 24
        events.append(_event(f"measure_{age}", "learning", "feedback_evaluated", at=feedback.get("observed_at"),
            status=feedback.get("status"), label="performance evidence evaluated", evidence="performance-feedback",
            signal_count=feedback.get("signal_count"), target_age_hours=age))
        if feedback.get("selection_adjustment_available"):
            events.append(_event("learning", "service", "feedback_available", at=feedback.get("observed_at"),
                status="bounded_preference_available", label="bounded preference available to service selection",
                evidence="performance-feedback", signal_count=feedback.get("signal_count")))

    if admission.get("observed_at"):
        events.append(_event("admission", "admission", "admission_state", at=admission.get("observed_at"),
            status=admission.get("mode"), label="admission gate state observed", evidence="admission-state",
            motion="pulse", reasons=list(admission.get("reasons") or [])[:6]))

    collection_at = collection.get("completed_at") or collection.get("observed_at")
    stage_nodes = {
        "source-observation": "observer", "metrics": "measure_24", "performance-feedback": "learning",
        "inbound-replies": "engagement", "operations": "scheduler",
    }
    if collection_at:
        for stage in collection.get("stages", []) if isinstance(collection.get("stages"), list) else []:
            name = str(stage.get("stage") or "")
            node = "vault" if name.startswith("vault:") else stage_nodes.get(name)
            if not node:
                continue
            events.append(_event(node, node, "collector_stage", at=collection_at, status=stage.get("status"),
                label=name.replace("-", " ") + " stage", evidence="collection-cycle", motion="pulse",
                duration_seconds=stage.get("duration_seconds"), budget_seconds=stage.get("budget_seconds")))

    for row in events:
        row["verb"] = VERBS.get(str(row.get("kind") or ""), "MOVES")
        row["tone"] = _tone(row.get("status"), str(row.get("kind") or ""))
    events.sort(key=lambda row: (str(row.get("at") or ""), str(row.get("id") or "")), reverse=True)

    return {
        "schema_version": 2,
        "evidence_only": True,
        "nodes": nodes,
        "edges": edges,
        "events": events[:MAX_EXECUTION_EVENTS],
        "intent": {
            "planned_count": len(plan_rows),
            "decision_count": len(decisions),
            "plan": [{key: row.get(key) for key in ("campaign", "project", "provider", "lane", "run_at", "service_kind", "learning_role") if row.get(key) is not None} for row in plan_rows[:40]],
            "decisions": [{key: row.get(key) for key in ("provider", "run_at", "campaign", "reason") if row.get(key) is not None} for row in decisions[-80:]],
        },
        "motion_language": dict(VERBS),
        "boundary": "Evidence travels only when persisted local evidence supports it. State pulses describe current/persisted subsystem state; dashed intent routes are dry-plan projections and never publication claims.",
    }


def _operator_model(
    base: dict[str, Any],
    admission: dict[str, Any],
    execution: dict[str, Any],
) -> dict[str, Any]:
    """Explain the current automatic publishing path from the same read-only snapshot."""
    replenishment = base.get("replenishment") if isinstance(base.get("replenishment"), dict) else {}
    reserve = replenishment.get("supply_reserve") if isinstance(replenishment.get("supply_reserve"), dict) else {}
    routes = [row for row in reserve.get("routes", []) if isinstance(row, dict)]
    reserve_counts = Counter(
        str(row.get("reserve_status") or row.get("status") or "unknown")
        for row in routes
    )
    available_items = sum(
        int(row.get("reserve_available_items", 0) or 0)
        for row in routes
        if type(row.get("reserve_available_items", 0)) is int
    )
    runway_values = [
        float(row["reserve_runway_days"])
        for row in routes
        if type(row.get("reserve_runway_days")) in (int, float)
    ]
    pressure_projects = sorted({
        str(row.get("project"))
        for row in routes
        if row.get("project")
        and str(row.get("reserve_status") or row.get("status") or "") in {"empty", "emergency", "fallback"}
    })

    if not routes:
        supply_status = "unknown"
    elif reserve_counts.get("empty", 0):
        supply_status = "empty"
    elif reserve_counts.get("emergency", 0):
        supply_status = "emergency"
    elif reserve_counts.get("fallback", 0):
        supply_status = "fallback"
    elif reserve_counts.get("watch", 0):
        supply_status = "watch"
    else:
        supply_status = "healthy"

    node_map = {
        str(row.get("id")): row
        for row in execution.get("nodes", [])
        if isinstance(row, dict) and row.get("id")
    }
    inventory = int((node_map.get("inventory") or {}).get("value", 0) or 0)
    scheduler = int((node_map.get("scheduler") or {}).get("value", 0) or 0)
    executing = int((node_map.get("run_due") or {}).get("value", 0) or 0)
    verified = int((node_map.get("readback") or {}).get("value", 0) or 0)
    planned = int((execution.get("intent") or {}).get("planned_count", 0) or 0)
    activity = base.get("activity") if isinstance(base.get("activity"), dict) else {}
    receipts = activity.get("recent_receipts") if isinstance(activity.get("recent_receipts"), list) else []
    schedules = activity.get("recent_schedules") if isinstance(activity.get("recent_schedules"), list) else []
    provider_effects = len(receipts)
    failed_schedules = [
        row for row in schedules
        if isinstance(row, dict) and row.get("status") == "failed"
    ]
    failure_class_counts = Counter(
        str(row.get("failure_class") or "unclassified_failure")
        for row in failed_schedules
    )
    failure_provider_counts = Counter(
        str(row.get("provider") or "unknown")
        for row in failed_schedules
    )
    failure_class_summary = " · ".join(
        f"{name}={count}"
        for name, count in failure_class_counts.most_common(3)
    )
    admission_mode = str(admission.get("mode") or admission.get("status") or "unknown")
    admission_metrics = admission.get("metrics") if isinstance(admission.get("metrics"), dict) else {}
    backlog = admission.get("backlog_pressure") if isinstance(admission.get("backlog_pressure"), dict) else {}
    admission_detail = (
        f"{int(admission_metrics.get('open_route_count', 0) or 0)}/"
        f"{int(admission_metrics.get('route_count', 0) or 0)} scoped routes open"
    )
    if backlog:
        backlog_metrics = backlog.get("metrics") if isinstance(backlog.get("metrics"), dict) else {}
        admission_detail += (
            f" · backlog pressure={backlog.get('mode') or 'unknown'} "
            f"({int(backlog_metrics.get('eligible_unreserved', 0) or 0)} eligible)"
        )

    schedule_status = (
        "scheduled" if scheduler
        else "planned" if planned
        else "waiting_for_inventory" if inventory == 0
        else "ready"
    )
    stages = [
        {
            "id": "supply",
            "label": "Supply",
            "status": supply_status,
            "value": available_items,
            "detail": (
                f"{len(routes)} routes · {reserve_counts.get('emergency', 0)} emergency · "
                f"{reserve_counts.get('fallback', 0)} fallback"
                if routes else "reserve projection unavailable"
            ),
        },
        {
            "id": "admission",
            "label": "Admission",
            "status": admission_mode,
            "value": int(admission_metrics.get("open_route_count", 0) or 0),
            "detail": admission_detail,
        },
        {
            "id": "automatic_schedule",
            "label": "Automatic schedule",
            "status": schedule_status,
            "value": scheduler,
            "detail": f"{planned} dry-planned · {scheduler} durable active",
        },
        {
            "id": "execute",
            "label": "Execute",
            "status": "executing" if executing else "waiting",
            "value": executing,
            "detail": "run-due owns the external consequence boundary",
        },
        {
            "id": "provider",
            "label": "Provider",
            "status": "attention" if failed_schedules else "observed" if provider_effects else "idle",
            "value": provider_effects,
            "detail": (
                f"{provider_effects} recent receipt effects · {len(failed_schedules)} failed schedules · {failure_class_summary}"
                if failed_schedules
                else "recent X / Threads / LinkedIn receipt effects"
            ),
        },
        {
            "id": "verify",
            "label": "Verify",
            "status": "verified" if verified else "limited" if provider_effects else "idle",
            "value": verified,
            "detail": "receipt/readback evidence; unavailable scope stays unverified",
        },
    ]

    if reserve_counts.get("empty", 0):
        if inventory > 0 or scheduler > 0 or planned > 0:
            diagnosis = {
                "code": "protected_reserve_empty",
                "tone": "warn",
                "title": "Protected reserve needs rebuilding",
                "detail": (
                    f"{reserve_counts['empty']} route(s) have no protected multi-day reserve, "
                    "but current eligible, planned or scheduled work still exists. Reserve recovery "
                    "prepares later days; it is not permission to publish more today."
                ),
            }
        else:
            diagnosis = {
                "code": "protected_reserve_and_inventory_empty",
                "tone": "bad",
                "title": "Protected reserve and current eligible inventory are empty",
                "detail": (
                    f"{reserve_counts['empty']} route(s) have no protected multi-day reserve and the "
                    "current execution projection shows no eligible, planned or scheduled work. "
                    "The hard daily ceiling is only a safety maximum; it does not create supply."
                ),
            }
    elif reserve_counts.get("emergency", 0):
        diagnosis = {
            "code": "supply_emergency",
            "tone": "warn",
            "title": "Supply is the current limiter",
            "detail": (
                f"{reserve_counts['emergency']} route(s) are below the two-day emergency reserve. "
                "Scheduling remains automatic, but only admitted inventory can be reserved. "
                "The 100/day ceiling is only a safety maximum; it does not create supply."
            ),
        }
    elif reserve_counts.get("fallback", 0):
        diagnosis = {
            "code": "supply_rebuilding",
            "tone": "warn",
            "title": "Supply reserve is still rebuilding",
            "detail": (
                f"{reserve_counts['fallback']} route(s) remain below the five-day fallback floor. "
                "Existing eligible inventory can still schedule automatically while reserve recovery continues. "
                "The 100/day ceiling is only a safety maximum; it does not create supply."
            ),
        }
    elif admission_mode == "paused":
        diagnosis = {
            "code": "admission_paused",
            "tone": "warn",
            "title": "Admission is holding new inventory",
            "detail": "Supply may exist, but the admission gate is paused by its persisted pressure policy.",
        }
    elif inventory == 0:
        diagnosis = {
            "code": "no_eligible_inventory",
            "tone": "warn",
            "title": "No eligible inventory has reached automatic scheduling",
            "detail": "The scheduler is automatic; this state means the upstream supply/admission path has nothing eligible to reserve.",
        }
    elif scheduler == 0 and planned == 0:
        diagnosis = {
            "code": "no_schedule_horizon",
            "tone": "warn",
            "title": "Automatic scheduler has no work in the current horizon",
            "detail": "Eligible inventory exists, but the current dry plan has produced no near-term durable reservation.",
        }
    else:
        diagnosis = {
            "code": "no_current_limiter",
            "tone": "good",
            "title": "No current low-volume limiter detected",
            "detail": "Supply, admission and automatic scheduling all expose usable current-state evidence.",
        }

    return {
        "schema_version": 1,
        "stages": stages,
        "diagnosis": diagnosis,
        "reserve": {
            "route_count": len(routes),
            "status_counts": dict(reserve_counts),
            "available_items": available_items,
            "min_runway_days": min(runway_values) if runway_values else None,
            "max_runway_days": max(runway_values) if runway_values else None,
            "pressure_projects": pressure_projects[:20],
            "eligible_now": inventory,
            "planned_now": planned,
            "scheduled_now": scheduler,
            "rate_basis_counts": dict(Counter(
                str(row.get("reserve_rate_basis") or "unknown")
                for row in routes
            )),
        },
        "provider_failures": {
            "count": len(failed_schedules),
            "by_class": dict(sorted(failure_class_counts.items())),
            "by_provider": dict(sorted(failure_provider_counts.items())),
            "automatic_retry_authority": False,
        },
        "boundary": (
            "Read-only current-state explanation from the same console snapshot. "
            "It does not schedule, publish, refill or prove historical causality."
        ),
    }


def build_execution_snapshot(*, base: dict[str, Any] | None = None) -> dict[str, Any]:
    """Return the normal console snapshot plus the expanded execution model."""
    base_snapshot = dict(base or build_base_snapshot())
    admission = _safe("admission", _admission)
    collection = _safe("collection", _collection)
    acceptance = _safe("acceptance", _acceptance)
    work = base_snapshot.get("work")
    if not isinstance(work, dict):
        work = _safe("work", lambda: __import__("ocpf_post.work_status", fromlist=["build"]).build())
    execution = _execution_projection(base_snapshot, admission, collection, acceptance, work)
    operator = _operator_model(base_snapshot, admission, execution)
    base_snapshot["admission"] = admission
    base_snapshot["collection"] = collection
    base_snapshot["acceptance"] = acceptance
    base_snapshot["work"] = work
    base_snapshot["execution"] = execution
    base_snapshot["operator"] = operator
    base_snapshot["revision"] = _canonical_digest({
        "base_revision": base_snapshot.get("revision"),
        "admission": admission,
        "collection": collection,
        "acceptance": acceptance,
        "work": work,
        "execution": execution,
        "operator": operator,
    })
    return base_snapshot
