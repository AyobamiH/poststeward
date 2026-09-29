"""Read-only local observability projection for the post-once console.

The projection deliberately reads existing local state. It never calls a social
provider, writes state, creates/cancels schedules, changes policy or grants
publication authority.
"""
from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
import subprocess
import time
from typing import Any, Callable

from ocpf_post import __version__

UTC = timezone.utc
MAX_SCHEDULES = 80
MAX_RECEIPTS = 80
MAX_ACTIVITY = 120
MAX_PERFORMANCE = 60
MAX_ENGAGEMENT_ITEMS = 80
MAX_MACHINE_FLOWS = 180


def _stamp(value: datetime | None = None) -> str:
    return (value or datetime.now(UTC)).astimezone(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _safe(name: str, fn: Callable[[], Any]) -> Any:
    try:
        return fn()
    except (OSError, ValueError, KeyError, TypeError, RuntimeError, subprocess.SubprocessError) as exc:
        return {
            "status": "unavailable",
            "component": name,
            "error_type": type(exc).__name__,
        }


def _canonical_digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False,
    ).encode("utf-8")).hexdigest()


def _revision_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """Remove observer-clock-only fields while retaining meaningful state changes."""
    value = json.loads(json.dumps(payload))
    work = value.get("work")
    if isinstance(work, dict):
        work.pop("observed_at", None)
        for condition in work.get("conditions", []) if isinstance(work.get("conditions"), list) else []:
            if isinstance(condition, dict):
                condition.pop("observed_at", None)
        for item in work.get("items", []) if isinstance(work.get("items"), list) else []:
            if not isinstance(item, dict):
                continue
            item.pop("observed_at", None)
            for condition in item.get("conditions", []) if isinstance(item.get("conditions"), list) else []:
                if isinstance(condition, dict):
                    condition.pop("observed_at", None)
    return value


def _schedule_projection(row: dict[str, Any]) -> dict[str, Any]:
    from ocpf_post.scheduler import provider_failure_metadata

    allowed = (
        "schedule_id", "status", "campaign", "provider", "account_id", "account_label",
        "run_at", "recorded_at", "updated_at", "last_event", "post_id", "url",
        "receipt_status", "readback_verified", "retry_at", "failure_class", "failure_stage",
        "provider_http_status", "automatic_retry", "provider_problem_type", "provider_reason",
        "provider_error_code", "circuit_failure_class", "circuit_retry_at", "preflight_attempts",
    )
    projected = {key: row.get(key) for key in allowed if row.get(key) is not None}
    if row.get("status") == "failed":
        for key, value in provider_failure_metadata(row).items():
            if key != "classification_basis" and value is not None:
                projected.setdefault(key, value)
    return projected


def _receipt_projection(row: dict[str, Any]) -> dict[str, Any]:
    allowed = (
        "campaign", "provider", "account_id", "schedule_id", "username", "status",
        "text_sha256", "recorded_at", "post_id", "url", "readback_verified",
    )
    return {key: row.get(key) for key in allowed if row.get(key) is not None}


def _campaign_origin(campaign: Any) -> dict[str, Any]:
    """Project bounded campaign provenance for owner observability only."""
    from ocpf_post.campaigns import builtin_manifest

    if not campaign:
        return {}
    try:
        manifest = builtin_manifest(str(campaign))
    except (OSError, ValueError, KeyError, TypeError):
        return {}
    if not isinstance(manifest, dict):
        return {}
    source = manifest.get("source") if isinstance(manifest.get("source"), dict) else {}
    vault = manifest.get("vault") if isinstance(manifest.get("vault"), dict) else {}
    allocation = manifest.get("allocation") if isinstance(manifest.get("allocation"), dict) else {}
    value = {
        "project": manifest.get("project"),
        "title": manifest.get("title"),
        "lane": allocation.get("lane"),
        "source_type": source.get("type"),
        "source_repository": source.get("repository"),
        "source_path": source.get("path"),
        "vault_id": vault.get("id"),
        "base_campaign": vault.get("base_campaign"),
    }
    return {key: item for key, item in value.items() if item not in (None, "", [])}


def _publication_effects_today(*, now: datetime | None = None) -> dict[str, Any]:
    """One row per effective publication, using the first local creation time."""
    from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
    from ocpf_post.performance_review import publications
    from ocpf_post.portfolio import load_policy

    observed = (now or datetime.now(UTC)).astimezone(UTC)
    policy = load_policy(effective=False)
    timezone_name = str(policy.get("timezone") or "Europe/London")
    try:
        zone = ZoneInfo(timezone_name)
    except ZoneInfoNotFoundError:
        zone = UTC
        timezone_name = "UTC"

    day = observed.astimezone(zone).date()
    rows: list[dict[str, Any]] = []
    for publication in publications().values():
        if not isinstance(publication, dict):
            continue
        receipt = publication.get("receipt")
        at = publication.get("at")
        if not isinstance(receipt, dict) or not isinstance(at, datetime):
            continue
        at = at.astimezone(UTC)
        if at.astimezone(zone).date() != day:
            continue
        row = _receipt_projection(receipt)
        row["published_at"] = _stamp(at)
        row["effective_verified"] = bool(publication.get("effective_verified"))
        if publication.get("verification_basis"):
            row["verification_basis"] = publication.get("verification_basis")
        row.update(_campaign_origin(row.get("campaign")))
        rows.append(row)

    rows.sort(key=lambda row: str(row.get("published_at") or ""), reverse=True)
    return {
        "date": day.isoformat(),
        "timezone": timezone_name,
        "effects": rows[:100],
        "provider_counts": dict(Counter(str(row.get("provider") or "unknown") for row in rows)),
        "boundary": (
            "Effective publication rows are deduplicated by the existing performance read model and dated by "
            "their first local creation receipt. No provider call or publication is performed."
        ),
    }


def _event_projection(row: dict[str, Any]) -> dict[str, Any]:
    from ocpf_post.scheduler import provider_failure_metadata

    allowed = (
        "event", "status", "schedule_id", "campaign", "provider", "account_id", "recorded_at",
        "post_id", "url", "receipt_status", "readback_verified", "retry_at", "failure_class",
        "failure_stage", "provider_http_status", "automatic_retry", "provider_problem_type",
        "provider_reason", "provider_error_code", "circuit_failure_class", "circuit_retry_at",
    )
    projected = {key: row.get(key) for key in allowed if row.get(key) is not None}
    if row.get("status") == "failed":
        for key, value in provider_failure_metadata(row).items():
            if key != "classification_basis" and value is not None:
                projected.setdefault(key, value)
    return projected


def _engagement_item_projection(row: dict[str, Any]) -> dict[str, Any]:
    """Expose reply lifecycle evidence without untrusted inbound or drafted text."""
    allowed = (
        "id", "provider", "account_id", "campaign", "status", "first_seen_at", "last_seen_at",
        "conversation_depth", "attempted_at", "published_at", "post_id", "url",
        "readback_verified", "error_type",
    )
    return {key: row.get(key) for key in allowed if row.get(key) is not None}


def _local_git() -> dict[str, Any]:
    from ocpf_post.runtime_attestation import runtime_root

    root = runtime_root()

    def run(*args: str) -> str | None:
        result = subprocess.run(
            ["git", "-C", str(root), *args], capture_output=True, text=True, timeout=3, check=False,
        )
        return result.stdout.strip() if result.returncode == 0 else None

    head = run("rev-parse", "HEAD")
    branch = run("branch", "--show-current")
    status = run("status", "--porcelain=v1", "--untracked-files=all")
    return {
        "cli_version": __version__,
        "git_commit_sha": head,
        "branch": branch,
        "checkout_clean": None if status is None else not bool(status),
        "network_checked": False,
        "publishing_authority": False,
    }


def _timers() -> dict[str, Any]:
    result = subprocess.run(
        ["systemctl", "--user", "list-timers", "--all", "ocpf-post-*", "--no-pager", "--no-legend"],
        capture_output=True, text=True, timeout=3, check=False,
    )
    if result.returncode != 0:
        return {"status": "unavailable", "error_type": "SystemctlUnavailable", "timers": []}
    rows = [line.strip() for line in result.stdout.splitlines() if line.strip()]
    return {"status": "observed", "timers": rows[:20]}


def _replenishment() -> dict[str, Any]:
    from ocpf_post.campaigns import runtime_campaign_root
    from ocpf_post.replenisher import replenisher_status, source_state_file
    from ocpf_post.source_observations import load as load_observations
    from ocpf_post.state import read_json

    campaigns: list[dict[str, Any]] = []
    root = runtime_campaign_root()
    if root.exists():
        for child in sorted(root.iterdir()):
            if not child.is_dir():
                continue
            manifest_path = child / "manifest.json"
            try:
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if not isinstance(manifest, dict):
                continue
            allocation = manifest.get("allocation") if isinstance(manifest.get("allocation"), dict) else {}
            source = manifest.get("source") if isinstance(manifest.get("source"), dict) else {}
            vault = manifest.get("vault") if isinstance(manifest.get("vault"), dict) else {}
            campaigns.append({
                "campaign": manifest.get("campaign"),
                "project": manifest.get("project"),
                "status": manifest.get("status"),
                "lane": allocation.get("lane"),
                "expires_at": allocation.get("expires_at"),
                "providers": list(manifest.get("providers") or []),
                "source_type": source.get("type"),
                "source_id": source.get("source_id"),
                "source_repository": source.get("repository"),
                "source_path": source.get("path"),
                "vault_id": vault.get("id"),
                "base_campaign": vault.get("base_campaign"),
            })
    legacy = read_json(source_state_file())
    observations = load_observations()
    reserve_status = replenisher_status()
    projects = {}
    for project, row in observations.get("projects", {}).items():
        projects[project] = {
            key: row.get(key) for key in (
                "status", "observed_at", "last_attempt_at", "head_sha", "buffered_head_sha",
                "last_processed_sha", "expired_delivery_count",
            ) if row.get(key) is not None
        }
        projects[project]["pending_count"] = len(row.get("pending", [])) if isinstance(row.get("pending"), list) else None
    return {
        "runtime_campaign_count": len(campaigns),
        "runtime_by_lane": dict(Counter(row.get("lane") or "unknown" for row in campaigns)),
        "runtime_by_project": dict(Counter(row.get("project") or "unknown" for row in campaigns)),
        "observed_repositories": len(legacy.get("repositories", {})) if isinstance(legacy.get("repositories"), dict) else 0,
        "source_observations": projects,
        "campaigns": campaigns[-80:],
        "supply_reserve": (
            reserve_status.get("supply_reserve")
            if isinstance(reserve_status, dict) and isinstance(reserve_status.get("supply_reserve"), dict)
            else {"routes": [], "status_counts": {}}
        ),
        "network_checked": False,
    }


def _performance() -> dict[str, Any]:
    """Read stored metric snapshots only; never invoke performance capture."""
    from ocpf_post.performance import iter_snapshots

    rows: list[dict[str, Any]] = []
    for raw in iter_snapshots():
        if not isinstance(raw, dict):
            continue
        availability = raw.get("availability") if isinstance(raw.get("availability"), dict) else {}
        metrics = raw.get("metrics") if isinstance(raw.get("metrics"), dict) else {}
        safe_metrics = {
            str(key): value for key, value in metrics.items()
            if value is None or type(value) in (int, float)
        }
        rows.append({
            "campaign": raw.get("campaign"),
            "project": raw.get("project"),
            "provider": raw.get("provider"),
            "account_id": raw.get("account_id"),
            "post_id": raw.get("post_id"),
            "url": raw.get("url"),
            "captured_at": raw.get("captured_at"),
            "target_age_hours": raw.get("target_age_hours"),
            "metrics": safe_metrics,
            "availability": {
                "status": availability.get("status"),
                "unavailable_metrics": list(availability.get("unavailable_metrics") or []),
            },
        })
    rows.sort(key=lambda row: str(row.get("captured_at") or ""), reverse=True)
    return {
        "snapshot_count": len(rows),
        "recent": rows[:MAX_PERFORMANCE],
        "by_provider": dict(Counter(row.get("provider") or "unknown" for row in rows)),
        "network_checked": False,
    }


def _engagement() -> dict[str, Any]:
    """Read reply state while projecting only lifecycle metadata for the browser."""
    from ocpf_post.engagement import report

    raw = report(include_items=True)
    if not isinstance(raw, dict):
        raise ValueError("Invalid engagement report")
    items = raw.pop("items", [])
    projected = [_engagement_item_projection(row) for row in items if isinstance(row, dict)]
    projected.sort(key=lambda row: str(
        row.get("published_at") or row.get("attempted_at") or row.get("last_seen_at") or row.get("first_seen_at") or ""
    ), reverse=True)
    raw["recent_items"] = projected[:MAX_ENGAGEMENT_ITEMS]
    try:
        from ocpf_post.reply_worker import report as worker_report
        worker = worker_report()
    except (OSError, ValueError, KeyError, TypeError, RuntimeError):
        worker = {}
    accounts = worker.get("accounts") if isinstance(worker.get("accounts"), dict) else {}
    mode_counts = Counter()
    provider_mode_counts = Counter()
    for account, setting in accounts.items():
        if not isinstance(setting, dict):
            continue
        mode = str(setting.get("mode") or "unknown")
        provider = str(account).split(":", 1)[0]
        mode_counts[mode] += 1
        provider_mode_counts[f"{provider}:{mode}"] += 1
    raw["worker"] = {
        "enabled": worker.get("enabled") is True,
        "model_ready": worker.get("model_credential_present") is True,
        "configured_accounts": len(accounts),
        "mode_counts": dict(mode_counts),
        "provider_mode_counts": dict(provider_mode_counts),
        "opt_out_count": int(worker.get("opt_out_count", 0) or 0),
    }
    raw["copy_exposed"] = False
    return raw


def _feedback() -> dict[str, Any]:
    """Read persisted performance-feedback state; never recompute or apply it."""
    from ocpf_post import local_store
    from ocpf_post.performance_feedback import path

    raw = local_store.read(path())
    if not raw:
        return {
            "status": "not_recorded",
            "observed_at": None,
            "enabled": None,
            "selection_adjustment_available": False,
            "signal_count": 0,
            "cohort_count": 0,
            "signals": [],
            "network_checked": False,
            "recomputed": False,
        }
    if not isinstance(raw, dict):
        raise ValueError("Invalid performance feedback state")
    signals: list[dict[str, Any]] = []
    for scope, row in (raw.get("signals") or {}).items():
        if not isinstance(row, dict):
            continue
        signals.append({
            "signal_id": str(scope)[:16],
            "provider": row.get("provider"),
            "account_id": row.get("account_id"),
            "lane": row.get("lane"),
            "variant": row.get("variant"),
            "target_age_hours": row.get("target_age_hours"),
            "supporting_target_ages": list(row.get("supporting_target_ages") or []),
            "boost": row.get("boost"),
            "expires_at": row.get("expires_at"),
            "evidence_count": len(row.get("evidence_post_ids") or []),
        })
    return {
        "status": raw.get("status") or "unknown",
        "observed_at": raw.get("observed_at"),
        "enabled": raw.get("enabled"),
        "selection_adjustment_available": bool(raw.get("selection_adjustment_available")),
        "signal_count": len(signals),
        "cohort_count": len(raw.get("cohorts") or []),
        "observation_count": len(raw.get("observations") or []),
        "age_conflict_count": len(raw.get("age_conflicts") or []),
        "signals": signals,
        "network_checked": False,
        "recomputed": False,
    }


def _portfolio() -> dict[str, Any]:
    from ocpf_post.portfolio import portfolio_status
    from ocpf_post.portfolio_cross_platform import plan_refill

    return {
        "status": portfolio_status(),
        "plan": plan_refill(horizon_minutes=75),
    }


def _activity(*, now: datetime | None = None) -> dict[str, Any]:
    from ocpf_post.scheduler import ACTIVE_STATUSES, iter_schedule_events, schedule_records
    from ocpf_post.state import iter_receipts

    schedules = [_schedule_projection(row) for row in schedule_records()]
    schedules.sort(key=lambda row: str(row.get("updated_at") or row.get("recorded_at") or row.get("run_at") or ""), reverse=True)
    active = [row for row in schedules if row.get("status") in ACTIVE_STATUSES]

    receipts = [_receipt_projection(row) for row in iter_receipts()]
    receipts.sort(key=lambda row: str(row.get("recorded_at") or ""), reverse=True)
    receipt_summary = {
        "integrity": "observed",
        "published_effects": sum(
            row.get("status") in {"published_verified", "published_unverified"}
            for row in receipts
        ),
        "verified_effects": sum(
            row.get("status") == "published_verified" and row.get("readback_verified") is True
            for row in receipts
        ),
    }
    today = _publication_effects_today(now=now)
    for row in active:
        row.update(_campaign_origin(row.get("campaign")))

    activity: list[dict[str, Any]] = []
    for row in iter_schedule_events():
        projected = _event_projection(row)
        projected["kind"] = "schedule"
        projected["at"] = projected.get("recorded_at")
        activity.append(projected)
    for row in receipts:
        projected = dict(row)
        projected["kind"] = "receipt"
        projected["at"] = projected.get("recorded_at")
        activity.append(projected)
    activity.sort(key=lambda row: str(row.get("at") or ""), reverse=True)

    return {
        "active_schedules": active[:MAX_SCHEDULES],
        "recent_schedules": schedules[:MAX_SCHEDULES],
        "recent_receipts": receipts[:MAX_RECEIPTS],
        "activity": activity[:MAX_ACTIVITY],
        "receipt_summary": receipt_summary,
        "today": today,
    }


def _machine_flow(source: str, target: str, kind: str, *, at: Any, status: Any = None,
                  label: str, evidence: str, **extra: Any) -> dict[str, Any]:
    value = {
        "source": source,
        "target": target,
        "kind": kind,
        "at": at,
        "status": status,
        "label": label,
        "evidence": evidence,
        **{key: item for key, item in extra.items() if item is not None},
    }
    value["id"] = _canonical_digest(value)[:24]
    return value


def _machine_projection(payload: dict[str, Any]) -> dict[str, Any]:
    """Normalize existing evidence into a topology and replayable flow stream.

    Edges describe the architecture. A flow is emitted only when a persisted
    schedule/reply/receipt/metric/feedback record justifies that movement.
    """
    activity = payload.get("activity") if isinstance(payload.get("activity"), dict) else {}
    engagement = payload.get("engagement") if isinstance(payload.get("engagement"), dict) else {}
    performance = payload.get("performance") if isinstance(payload.get("performance"), dict) else {}
    feedback = payload.get("feedback") if isinstance(payload.get("feedback"), dict) else {}
    replenishment = payload.get("replenishment") if isinstance(payload.get("replenishment"), dict) else {}
    portfolio = payload.get("portfolio") if isinstance(payload.get("portfolio"), dict) else {}

    recent_receipts = activity.get("recent_receipts") if isinstance(activity.get("recent_receipts"), list) else []
    active_schedules = activity.get("active_schedules") if isinstance(activity.get("active_schedules"), list) else []
    engagement_counts = engagement.get("counts") if isinstance(engagement.get("counts"), dict) else {}
    plan = portfolio.get("plan") if isinstance(portfolio.get("plan"), dict) else {}
    plan_rows = plan.get("plan") if isinstance(plan.get("plan"), list) else []

    provider_verified = Counter(
        str(row.get("provider")) for row in recent_receipts
        if row.get("provider") and row.get("readback_verified") is True
    )
    nodes = [
        {"id": "sources", "label": "Sources", "kind": "input", "value": replenishment.get("observed_repositories", 0), "metric": "observed repos"},
        {"id": "campaigns", "label": "Campaigns", "kind": "work", "value": replenishment.get("runtime_campaign_count", 0), "metric": "runtime campaigns"},
        {"id": "planner", "label": "Planner", "kind": "decision", "value": len(plan_rows), "metric": "dry planned"},
        {"id": "scheduler", "label": "Scheduler", "kind": "durable", "value": len(active_schedules), "metric": "active"},
        {"id": "run_due", "label": "run-due", "kind": "authority", "value": sum(row.get("status") == "executing" for row in active_schedules), "metric": "executing"},
        {"id": "x", "label": "X", "kind": "provider", "value": provider_verified.get("x", 0), "metric": "verified receipts"},
        {"id": "threads", "label": "Threads", "kind": "provider", "value": provider_verified.get("threads", 0), "metric": "verified receipts"},
        {"id": "linkedin", "label": "LinkedIn", "kind": "provider", "value": provider_verified.get("linkedin", 0), "metric": "verified receipts"},
        {"id": "readback", "label": "Readback", "kind": "verification", "value": sum(row.get("readback_verified") is True for row in recent_receipts), "metric": "verified"},
        {"id": "engagement", "label": "Reply watch", "kind": "monitor", "value": int(engagement_counts.get("pending", 0) or 0) + int(engagement_counts.get("drafted", 0) or 0), "metric": "waiting"},
        {"id": "reply_worker", "label": "Reply worker", "kind": "worker", "value": int(engagement_counts.get("sending", 0) or 0), "metric": "sending"},
        {"id": "performance", "label": "Performance", "kind": "measurement", "value": performance.get("snapshot_count", 0), "metric": "stored snapshots"},
        {"id": "learning", "label": "Learning", "kind": "feedback", "value": feedback.get("signal_count", 0), "metric": "active signals"},
    ]
    edges = [
        {"source": "sources", "target": "campaigns", "label": "eligible source material", "mode": "architecture"},
        {"source": "campaigns", "target": "planner", "label": "available work", "mode": "architecture"},
        {"source": "campaigns", "target": "scheduler", "label": "persist schedule", "mode": "evidence"},
        {"source": "planner", "target": "scheduler", "label": "selected work", "mode": "architecture"},
        {"source": "scheduler", "target": "run_due", "label": "durable claim", "mode": "evidence"},
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
        {"source": "readback", "target": "performance", "label": "stored measurement", "mode": "evidence"},
        {"source": "performance", "target": "learning", "label": "evaluate evidence", "mode": "evidence"},
        {"source": "learning", "target": "planner", "label": "bounded preference", "mode": "evidence"},
    ]

    flows: list[dict[str, Any]] = []
    for row in activity.get("activity", []) if isinstance(activity.get("activity"), list) else []:
        if not isinstance(row, dict) or not row.get("at"):
            continue
        provider = str(row.get("provider") or "")
        if provider not in {"x", "threads", "linkedin"}:
            provider = ""
        common = {
            "at": row.get("at"), "status": row.get("status"), "provider": provider or None,
            "campaign": row.get("campaign"), "schedule_id": row.get("schedule_id"), "post_id": row.get("post_id"),
        }
        if row.get("kind") == "receipt":
            if provider and row.get("post_id"):
                flows.append(_machine_flow(provider, "readback", "publication_receipt", label="publication receipt recorded",
                    evidence="receipt", **common))
            continue
        event = str(row.get("event") or "")
        if event == "scheduled":
            flows.append(_machine_flow("campaigns", "scheduler", "schedule_persisted", label="schedule persisted",
                evidence="schedule-ledger", **common))
        elif event == "claimed":
            flows.append(_machine_flow("scheduler", "run_due", "consequence_claimed", label="run-due claimed work",
                evidence="schedule-ledger", **common))
        elif event == "preflight_deferred":
            flows.append(_machine_flow("run_due", "scheduler", "preflight_deferred", label="preflight deferred safely",
                evidence="schedule-ledger", **common))
        elif event == "completed" and provider:
            flows.append(_machine_flow("run_due", provider, "publication_completed", label="provider consequence completed",
                evidence="schedule-ledger", **common))
        elif event == "ambiguous_effect" and provider:
            flows.append(_machine_flow("run_due", provider, "ambiguous_effect", label="provider effect ambiguous",
                evidence="schedule-ledger", **common))
        elif event in {"failed", "drift_blocked", "duplicate_blocked"}:
            flows.append(_machine_flow("scheduler", "run_due", "execution_blocked", label=event.replace("_", " "),
                evidence="schedule-ledger", **common))

    for row in engagement.get("recent_items", []) if isinstance(engagement.get("recent_items"), list) else []:
        if not isinstance(row, dict):
            continue
        provider = str(row.get("provider") or "")
        if provider not in {"x", "threads"}:
            continue
        common = {
            "provider": provider, "campaign": row.get("campaign"), "post_id": row.get("post_id"),
            "engagement_id": row.get("id"), "status": row.get("status"),
        }
        if row.get("first_seen_at"):
            flows.append(_machine_flow(provider, "engagement", "reply_observed", at=row.get("first_seen_at"),
                label="reply observed", evidence="engagement-state", **common))
        if row.get("attempted_at"):
            flows.append(_machine_flow("engagement", "reply_worker", "reply_send_claimed", at=row.get("attempted_at"),
                label="reply send claimed", evidence="engagement-state", **common))
        if row.get("published_at"):
            flows.append(_machine_flow("reply_worker", provider, "reply_published", at=row.get("published_at"),
                label="reply published", evidence="engagement-state", **common))
            if row.get("readback_verified") is True:
                flows.append(_machine_flow(provider, "readback", "reply_readback", at=row.get("published_at"),
                    label="reply readback verified", evidence="engagement-state", **common))

    for row in performance.get("recent", []) if isinstance(performance.get("recent"), list) else []:
        if not isinstance(row, dict) or not row.get("captured_at"):
            continue
        flows.append(_machine_flow("readback", "performance", "metrics_stored", at=row.get("captured_at"),
            status=(row.get("availability") or {}).get("status") if isinstance(row.get("availability"), dict) else None,
            label="performance snapshot stored", evidence="performance-snapshot", provider=row.get("provider"),
            campaign=row.get("campaign"), post_id=row.get("post_id")))

    if feedback.get("observed_at"):
        flows.append(_machine_flow("performance", "learning", "feedback_evaluated", at=feedback.get("observed_at"),
            status=feedback.get("status"), label="performance evidence evaluated", evidence="performance-feedback",
            signal_count=feedback.get("signal_count")))
        if feedback.get("selection_adjustment_available"):
            flows.append(_machine_flow("learning", "planner", "feedback_available", at=feedback.get("observed_at"),
                status="bounded_preference_available", label="bounded preference available to planner",
                evidence="performance-feedback", signal_count=feedback.get("signal_count")))

    flows.sort(key=lambda row: (str(row.get("at") or ""), str(row.get("id") or "")), reverse=True)
    return {
        "schema_version": 1,
        "evidence_only": True,
        "animation_authority": "browser-only",
        "runtime_paused_by_browser": False,
        "nodes": nodes,
        "edges": edges,
        "flows": flows[:MAX_MACHINE_FLOWS],
        "boundary": (
            "Topology edges explain architecture; animated flows are emitted only from persisted local evidence. "
            "Pausing or changing playback in the browser never pauses timers, workers, run-due or provider activity."
        ),
    }


def build_snapshot(*, now: datetime | None = None) -> dict[str, Any]:
    """Build one bounded local-only console snapshot.

    Live observability validates authority-bearing ledgers but deliberately does
    not perform the full forensic state/config fingerprint walk. The complete
    registry remains available through `ocpf-post state verify`.
    """
    observed = (now or datetime.now(UTC)).astimezone(UTC)
    total_started = time.monotonic()
    timings: dict[str, float] = {}

    from ocpf_post import alert_delivery as alert_delivery_module
    from ocpf_post import capabilities as capabilities_module
    from ocpf_post import outcome_connectors as outcome_connectors_module
    from ocpf_post import state_registry as state_registry_module
    from ocpf_post.capacity_experiment import report as experiment_report
    from ocpf_post.queue_watch import report as queue_report

    def capture(name: str, fn: Callable[[], Any]) -> Any:
        started = time.monotonic()
        value = _safe(name, fn)
        timings[name] = round((time.monotonic() - started) * 1000, 1)
        return value

    runtime = capture("runtime", _local_git)
    portfolio = capture("portfolio", _portfolio)
    experiment = capture("experiment", experiment_report)
    activity = capture("activity", lambda: _activity(now=observed))
    queue = capture("queue", queue_report)
    engagement = capture("engagement", _engagement)
    replenishment = capture("replenishment", _replenishment)
    performance = capture("performance", _performance)
    feedback = capture("feedback", _feedback)
    timers = capture("timers", _timers)
    state_integrity = capture("state_integrity", state_registry_module.verify_critical)
    outcome_connectors = capture("outcome_connectors", outcome_connectors_module.report)
    alert_delivery = capture("alert_delivery", alert_delivery_module.report)

    receipt_summary = activity.get("receipt_summary") if isinstance(activity, dict) else None
    if not isinstance(receipt_summary, dict):
        receipt_summary = {"integrity": "unavailable", "published_effects": 0, "verified_effects": 0}
    engagement_summary = engagement if isinstance(engagement, dict) else {"status": "unavailable"}

    capabilities = capture("capabilities", lambda: capabilities_module.report(
        state_override=state_integrity if isinstance(state_integrity, dict) else {},
        connector_state_override=outcome_connectors if isinstance(outcome_connectors, dict) else {},
        alert_state_override=alert_delivery if isinstance(alert_delivery, dict) else {},
        engagement_state_override=engagement_summary,
        receipt_summary_override=receipt_summary,
    ))
    work = capture("work", lambda: __import__("ocpf_post.work_status", fromlist=["build"]).build(now=observed))

    payload = {
        "schema_version": 1,
        "consequence": "READ_ONLY",
        "local_only": True,
        "streaming": "server-sent-events",
        "runtime": runtime,
        "portfolio": portfolio,
        "experiment": experiment,
        "activity": activity,
        "queue": queue,
        "engagement": engagement,
        "replenishment": replenishment,
        "performance": performance,
        "feedback": feedback,
        "timers": timers,
        "capabilities": capabilities,
        "state_integrity": state_integrity,
        "outcome_connectors": outcome_connectors,
        "alert_delivery": alert_delivery,
        "work": work,
        "consistency": {
            "atomic": False,
            "model": "sequential_local_read_model",
            "meaning": (
                "Components are read sequentially and may have different evidence timestamps. "
                "The snapshot revision identifies the exact composite rendered; it is not a transactional state generation."
            ),
        },
        "boundary": (
            "Local observability only. No social-provider call, policy/state mutation, schedule mutation, "
            "publication, retry, credential refresh or metric capture is performed by this snapshot. Copy, "
            "secrets and raw provider error prose are intentionally excluded. Full forensic state inventory "
            "remains an explicit state-verify operation rather than live-console work."
        ),
    }

    machine_started = time.monotonic()
    payload["machine"] = _machine_projection(payload)
    timings["machine"] = round((time.monotonic() - machine_started) * 1000, 1)

    revision_started = time.monotonic()
    payload["revision"] = _canonical_digest(_revision_payload(payload))
    timings["revision"] = round((time.monotonic() - revision_started) * 1000, 1)
    payload["observed_at"] = _stamp(observed)
    timings["total"] = round((time.monotonic() - total_started) * 1000, 1)
    payload["projection_timing_ms"] = timings
    return payload
