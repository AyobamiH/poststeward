"""Compact, continuously refreshed acceptance read models.

This module projects existing local evidence only. It does not call providers,
change policy, admit inventory, schedule, publish, reply, or rebuild feedback.
"""
from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
import json
import math
import re

from ocpf_post import local_store
from ocpf_post.engagement import at, stamp
from ocpf_post.state import state_dir

UTC = timezone.utc
TARGET_AGES = (24, 72, 168)
INDEX4_PAYLOAD_SHA256 = "87718ffa86c00c105cd8810b381658be07cbbf4f4b279b9d62125ca84be76a0e"
INDEX4_TARGETS = (
    {
        "name": "threads",
        "project": "oneclickpostfactory",
        "campaign": "OCPF-AUTO-04I-671B1C0-THREADS",
        "provider": "threads",
        "account_id": "25914281681582868",
        "text_sha256": INDEX4_PAYLOAD_SHA256,
    },
    {
        "name": "x",
        "project": "oneclickpostfactory",
        "campaign": "OCPF-AUTO-04I-671B1C0-X",
        "provider": "x",
        "account_id": "1480506376447315969",
        "text_sha256": INDEX4_PAYLOAD_SHA256,
    },
)


def path():
    return state_dir() / "acceptance-views.json"


def editorial_supply_view(now):
    from ocpf_post.editorial_continuity import path as continuity_path

    state = local_store.read(continuity_path()) or {}
    routes = state.get("routes") if isinstance(state.get("routes"), dict) else {}
    open_rows = []
    resolved = 0
    for row in routes.values():
        if not isinstance(row, dict):
            continue
        if row.get("status") == "resolved":
            resolved += 1
            continue
        if row.get("status") != "open":
            continue
        open_rows.append({
            key: row.get(key) for key in (
                "request_id", "project", "provider", "account_id", "generation",
                "first_requested_at", "last_observed_at", "runnable", "reserved",
                "stock_floor", "editorial_runway_hours", "surviving_runway_hours",
                "expiring_within_48h", "surviving_after_48h",
                "market_cold_hours", "market_cold", "hours_since_last_effect",
                "last_effect_at", "next_scheduled_at", "scheduled_count", "conditions",
                "suggested_new_items", "demand_reasons",
            )
        })
    open_rows.sort(key=lambda row: (
        str(row.get("project") or ""), str(row.get("provider") or ""),
        str(row.get("account_id") or ""),
    ))
    handoff = local_store.read(state_dir() / "editorial-handoff-status.json") or {}
    observed_at = state.get("observed_at")
    status = "not_recorded" if not observed_at else "open" if open_rows else "observed"
    return {
        "schema_version": 1,
        "acceptance_id": "PC-01",
        "status": status,
        "observed_at": observed_at,
        "open_request_count": len(open_rows),
        "resolved_request_count": resolved,
        "open_requests": open_rows[:60],
        "requests_truncated": len(open_rows) > 60,
        "handoff": {
            "status": handoff.get("status"),
            "observed_at": handoff.get("observed_at"),
            "reason": handoff.get("reason"),
            "write_performed": handoff.get("write_performed"),
        },
        "boundary": (
            "Durable editorial supply demand only. An open request is not permission to replay, "
            "activate, publish, extend expiry or raise quota. Handoff success does not prove semantic "
            "editorial filing or later publication."
        ),
    }


def source_vault_view(now):
    from ocpf_post.source_observations import load as load_sources
    from ocpf_post import vault_sync

    sources = load_sources().get("projects", {})
    cycle = local_store.read(state_dir() / "collection-cycle.json") or {}
    stages = {
        row.get("stage"): row for row in cycle.get("stages", [])
        if isinstance(row, dict) and isinstance(row.get("stage"), str)
    }
    source_rows = []
    for project, row in sorted(sources.items()):
        if not isinstance(row, dict):
            continue
        status = (
            "collection_unavailable" if row.get("collection_error")
            else str(row.get("status") or "not_observed")
        )
        source_rows.append({
            "project": project,
            "status": status,
            "observed_at": row.get("observed_at"),
            "last_attempt_at": row.get("last_attempt_at"),
            "source_ok": row.get("source_ok"),
            "pending_count": len(row.get("pending", [])) if isinstance(row.get("pending"), list) else None,
        })

    vault_rows = []
    observations = vault_sync.observations()
    for vault_id, policy in sorted(vault_sync.policies().items()):
        if not policy.get("enabled"):
            continue
        observed = observations.get(vault_id, {})
        stage = stages.get("vault:" + vault_id, {})
        valid_until = observed.get("valid_until")
        stale = True
        if valid_until:
            try:
                stale = now >= at(valid_until)
            except (TypeError, ValueError):
                stale = True
        stage_status = str(stage.get("status") or "not_observed")
        status = (
            "unavailable" if stage_status in {"attention", "unavailable", "timed_out", "unknown"}
            else "partial" if stage_status == "partial"
            else "stale" if stale
            else "observed"
        )
        vault_rows.append({
            "vault_id": vault_id,
            "project": policy.get("project"),
            "status": status,
            "collector_stage_status": stage_status,
            "observed_at": observed.get("observed_at"),
            "valid_until": valid_until,
            "version": observed.get("version"),
            "active_entries": len(observed.get("active", {})) if isinstance(observed.get("active"), dict) else 0,
            "skipped_entries": len(observed.get("skipped", [])) if isinstance(observed.get("skipped"), list) else 0,
            "deferred_entries": len(observed.get("deferred", [])) if isinstance(observed.get("deferred"), list) else 0,
        })

    bad_source = {"collection_unavailable", "history_gap", "pending_overflow",
                  "profile_changed_review_required", "source_guard_failed", "not_observed"}
    open_sources = [row for row in source_rows if row["status"] in bad_source or row.get("source_ok") is False]
    open_vaults = [row for row in vault_rows if row["status"] != "observed"]
    return {
        "schema_version": 1,
        "acceptance_id": "PC-02",
        "status": "open" if open_sources or open_vaults else "observed",
        "observed_at": cycle.get("completed_at") or cycle.get("observed_at"),
        "sources": source_rows,
        "vaults": vault_rows,
        "open_source_count": len(open_sources),
        "open_vault_count": len(open_vaults),
        "boundary": (
            "Latest source/vault observation state only. Cached content never extends freshness; "
            "a failed or stale latest read remains open. This view performs no network retry, source "
            "activation, vault import, cancellation or publication."
        ),
    }


def publication_readback_view(now):
    from ocpf_post.publication_reconcile import reconcile

    preview = reconcile(apply=False, now=now)
    results = []
    for row in preview.get("results", []):
        if not isinstance(row, dict):
            continue
        results.append({
            key: row.get(key) for key in (
                "schedule_id", "campaign", "provider", "account_id", "post_id",
                "original_status", "status", "observed_at", "http_status",
                "retry_at", "previous_status", "automatic_retry", "next_action", "cached",
                "required_scope", "scope_recorded",
            )
        })
    unresolved = [row for row in results if row.get("status") != "verified"]
    permission_gated = [row for row in unresolved if row.get("status") == "readback_permission_required"]
    manual_review = [
        row for row in unresolved
        if row.get("status") in {"forensic_review_required", "thread_manual_review_required"}
    ]
    automatic_remaining = [
        row for row in unresolved
        if row not in permission_gated and row not in manual_review
    ]
    return {
        "schema_version": 1,
        "acceptance_id": "PC-03",
        "status": "open" if unresolved else "observed",
        "observed_at": stamp(now),
        "result_count": len(results),
        "unresolved_count": len(unresolved),
        "permission_gated_count": len(permission_gated),
        "manual_review_count": len(manual_review),
        "automatic_work_remaining_count": len(automatic_remaining),
        "results": results[:80],
        "results_truncated": len(results) > 80,
        "boundary": (
            "Known provider IDs and exact frozen payloads only. Verified sidecar evidence can close a "
            "readback without rewriting immutable historical receipts. Permission-gated and manual-review "
            "effects stay open but are separated from automatic work; no search, resend, reset or replacement effect."
        ),
    }


def effect_reconciliation_view(now):
    from ocpf_post.publication_reconcile import reconcile as campaign_reconcile
    from ocpf_post.reply_reconcile import reconcile as reply_reconcile
    from ocpf_post.scheduler import schedule_records

    campaign = campaign_reconcile(apply=False, now=now)
    replies = reply_reconcile(apply=False, now=now)
    campaign_rows = [{
        key: row.get(key) for key in (
            "schedule_id", "campaign", "provider", "account_id", "post_id",
            "original_status", "status", "retry_at", "http_status",
            "previous_status", "automatic_retry", "next_action", "cached",
            "required_scope", "scope_recorded",
        )
    } for row in campaign.get("results", []) if isinstance(row, dict)]
    reply_rows = [{
        key: row.get(key) for key in (
            "inbox_id", "provider", "account_id", "post_id", "original_status",
            "status", "retry_at",
        )
    } for row in replies.get("results", []) if isinstance(row, dict)]
    failed = [{
        key: row.get(key) for key in (
            "schedule_id", "campaign", "provider", "account_id", "status",
            "run_at", "updated_at", "failure_class",
        )
    } for row in schedule_records() if row.get("status") == "failed"]
    unresolved_campaign = [row for row in campaign_rows if row.get("status") != "verified"]
    unresolved_reply = [row for row in reply_rows if row.get("status") != "verified"]
    permission_gated = [
        row for row in unresolved_campaign
        if row.get("status") == "readback_permission_required"
    ]
    manual_review = [
        row for row in unresolved_campaign
        if row.get("status") in {"forensic_review_required", "thread_manual_review_required"}
    ]
    automatic_campaign = [
        row for row in unresolved_campaign
        if row not in permission_gated and row not in manual_review
    ]
    return {
        "schema_version": 1,
        "acceptance_id": "PC-05",
        "status": "open" if unresolved_campaign or unresolved_reply or failed else "observed",
        "observed_at": stamp(now),
        "campaign_unresolved_count": len(unresolved_campaign),
        "campaign_permission_gated_count": len(permission_gated),
        "campaign_manual_review_count": len(manual_review),
        "campaign_automatic_work_remaining_count": len(automatic_campaign),
        "reply_unresolved_count": len(unresolved_reply),
        "historical_failed_schedule_count": len(failed),
        "campaign_effects": campaign_rows[:60],
        "reply_effects": reply_rows[:60],
        "failed_schedules": failed[:60],
        "truncated": any(len(rows) > 60 for rows in (campaign_rows, reply_rows, failed)),
        "boundary": (
            "Exact retained external-effect reconciliation. Later successful posts do not erase older "
            "uncertain/failed outcomes. Permission-gated and manual-review campaign effects are counted separately "
            "from automatic work. Unknown and ambiguous effects never gain automatic retry, reset, replacement or send authority."
        ),
    }


def _safe_metrics(row):
    metrics = row.get("metrics") if isinstance(row.get("metrics"), dict) else {}
    return {
        str(key): value for key, value in metrics.items()
        if value is None or (type(value) in (int, float) and math.isfinite(value) and value >= 0)
    }


def _target_age(row, publication_at):
    target = row.get("target_age_hours")
    if type(target) in (int, float) and int(target) in TARGET_AGES:
        return int(target)
    try:
        age = (at(row["captured_at"]) - publication_at).total_seconds() / 3600
    except (KeyError, TypeError, ValueError):
        return None
    matches = [candidate for candidate in TARGET_AGES if abs(age - candidate) <= 2]
    return min(matches, key=lambda candidate: abs(age - candidate)) if matches else None


def _best_snapshot(rows, publication, target_age):
    identity = (
        publication["receipt"]["campaign"],
        publication["receipt"]["provider"],
        publication["receipt"]["account_id"],
        publication["receipt"]["post_id"],
    )
    candidates = []
    for row in rows:
        if tuple(row.get(key) for key in ("campaign", "provider", "account_id", "post_id")) != identity:
            continue
        if _target_age(row, publication["at"]) != target_age:
            continue
        try:
            captured = at(row["captured_at"])
        except (KeyError, TypeError, ValueError):
            continue
        availability = row.get("availability") if isinstance(row.get("availability"), dict) else {}
        status = str(availability.get("status") or "unknown")
        rank = 0 if status == "available" else 1 if status == "attempt_started" else 2
        age = (captured - publication["at"]).total_seconds() / 3600
        candidates.append((rank, abs(age - target_age), -captured.timestamp(), row, age, status))
    if not candidates:
        return None
    _, _, _, row, age, status = min(candidates)
    metrics = _safe_metrics(row)
    exposure_key = "impressions" if identity[1] == "x" else "views"
    exposure = metrics.get(exposure_key)
    editorial = row.get("editorial") if isinstance(row.get("editorial"), dict) else {}
    return {
        "captured_at": row.get("captured_at"),
        "target_age_hours": target_age,
        "actual_age_hours": round(age, 4),
        "availability": status,
        "exposure": exposure,
        "metrics": metrics,
        "editorial_bound": bool(
            editorial
            and editorial.get("text_sha256") == publication["receipt"].get("text_sha256")
        ),
    }


def _feedback_state(feedback, publication, target_age, now):
    try:
        observed_at = at(feedback["observed_at"])
        fresh = 0 <= (now - observed_at).total_seconds() <= 86400
    except (KeyError, TypeError, ValueError):
        observed_at = None
        fresh = False
    identity = (
        publication["receipt"]["campaign"],
        publication["receipt"]["provider"],
        publication["receipt"]["account_id"],
        publication["receipt"]["post_id"],
    )
    match = None
    for row in feedback.get("observations", []) if isinstance(feedback.get("observations"), list) else []:
        if tuple(row.get(key) for key in ("campaign", "provider", "account_id", "post_id")) != identity:
            continue
        if row.get("target_age_hours") != target_age:
            continue
        match = row
        break
    return {
        "feedback_observed_at": observed_at.isoformat() if observed_at else None,
        "feedback_fresh": fresh,
        "feedback_enabled": feedback.get("enabled"),
        "qualifying_observation": bool(match),
        "qualifying_exposure": match.get("exposure") if isinstance(match, dict) else None,
        "qualifying_captured_at": match.get("captured_at") if isinstance(match, dict) else None,
    }


def _measurement_reason(snapshot, feedback_state, publication, target_age, now):
    if feedback_state["qualifying_observation"]:
        return "qualifying_feedback_observation"
    if snapshot is None:
        current_age = (now - publication["at"]).total_seconds() / 3600
        if current_age < target_age - 2:
            return "not_due_yet"
        if current_age <= target_age + 2:
            return "measurement_window_open"
        if not feedback_state["feedback_fresh"]:
            return "persisted_feedback_missing_or_stale"
        return "no_comparable_snapshot"
    if not feedback_state["feedback_fresh"]:
        return "persisted_feedback_missing_or_stale"
    if snapshot["availability"] != "available":
        return "metrics_unavailable"
    if not publication.get("effective_verified"):
        return "publication_not_verified"
    if not snapshot["editorial_bound"]:
        return "missing_or_mismatched_editorial_attribution"
    if type(snapshot.get("exposure")) not in (int, float):
        return "exposure_unavailable"
    if snapshot["exposure"] < 100:
        return "low_exposure"
    return "not_in_persisted_feedback"


def _publication(target, publications):
    exact = []
    related = []
    for key, item in publications.items():
        if key[0:3] != (target["campaign"], target["provider"], target["account_id"]):
            continue
        related.append(item)
        if item["receipt"].get("text_sha256") == target["text_sha256"]:
            exact.append(item)
    if not exact:
        return {
            "status": "payload_mismatch" if related else "not_published",
            "related_effect_count": len(related),
        }, None
    post_ids = {item["receipt"].get("post_id") for item in exact}
    if len(post_ids) != 1:
        return {"status": "ambiguous_multiple_effects", "effect_count": len(exact)}, None
    item = min(exact, key=lambda row: row["at"])
    receipt = item["receipt"]
    return {
        "status": "published_verified" if item.get("effective_verified") else "published_unverified",
        "campaign": receipt.get("campaign"),
        "provider": receipt.get("provider"),
        "account_id": receipt.get("account_id"),
        "post_id": receipt.get("post_id"),
        "text_sha256": receipt.get("text_sha256"),
        "published_at": item["at"].isoformat(),
        "verification_basis": item.get("verification_basis"),
        "readback_verified": receipt.get("readback_verified") is True,
    }, item


def _sampling_gate(target, now, sampling):
    from ocpf_post.campaigns import builtin_manifest

    manifest = builtin_manifest(target["campaign"])
    source = manifest.get("source") if isinstance(manifest.get("source"), dict) else {}
    arm = source.get("comparison_variant")
    source_id = source.get("source_id")
    source_sha = source.get("source_sha")
    if not manifest:
        return {"status": "unknown", "reason": "baseline_manifest_unavailable"}
    if arm not in {"question", "practical"}:
        return {"status": "not_applicable", "reason": "no_assigned_challenger"}
    if not all(isinstance(value, str) and value for value in (source_id, source_sha)):
        return {"status": "unknown", "reason": "baseline_source_identity_unavailable"}
    reason = sampling.reason(
        {
            "predecessor_source_id": source_id,
            "comparison_variant": arm,
            "sha": source_sha,
        },
        target["provider"],
        target["account_id"],
        target["project"],
    )
    result = {
        "status": "eligible" if reason is None else "deferred",
        "acceptance_status": "eligible" if reason is None else "deferred",
        "reason": reason,
        "comparison_variant": arm,
        "predecessor_source_id": source_id,
        "source_sha": source_sha,
        "challenger_admitted": False,
    }
    match = re.search(r"-AUTO-(\d{2})I-", target["campaign"])
    if match:
        code = "Q" if arm == "question" else "P"
        expected = target["campaign"].replace(
            f"-AUTO-{match.group(1)}I-", f"-AUTO-{match.group(1)}{code}-", 1
        )
        result["expected_challenger_campaign"] = expected
        challenger = builtin_manifest(expected)
        challenger_source = challenger.get("source") if isinstance(challenger.get("source"), dict) else {}
        if (
            challenger_source.get("sampling_predecessor") == source_id
            and challenger_source.get("source_sha") == source_sha
        ):
            result["challenger_admitted"] = True
            result["acceptance_status"] = "admitted"
    return result


def index4_view(now):
    from ocpf_post.campaigns import campaign_ids
    from ocpf_post.learning_supply import SamplingBudget
    from ocpf_post.performance import iter_snapshots
    from ocpf_post.performance_feedback import path as feedback_path
    from ocpf_post.performance_review import publications

    publication_map = publications()
    snapshots = list(iter_snapshots())
    feedback = local_store.read(feedback_path())
    sampling = SamplingBudget(now)
    known_campaigns = set(campaign_ids())
    targets = []
    for target in INDEX4_TARGETS:
        publication_state, publication = _publication(target, publication_map)
        item = {
            **target,
            "manifest_present": target["campaign"] in known_campaigns,
            "publication": publication_state,
            "measurements": [],
        }
        if publication is not None:
            for age in TARGET_AGES:
                snapshot = _best_snapshot(snapshots, publication, age)
                persisted = _feedback_state(feedback, publication, age, now)
                item["measurements"].append({
                    "target_age_hours": age,
                    "snapshot": snapshot,
                    "persisted_feedback": persisted,
                    "status": _measurement_reason(snapshot, persisted, publication, age, now),
                })
        else:
            for age in TARGET_AGES:
                item["measurements"].append({
                    "target_age_hours": age,
                    "snapshot": None,
                    "persisted_feedback": {
                        "feedback_observed_at": feedback.get("observed_at"),
                        "feedback_fresh": False,
                        "feedback_enabled": feedback.get("enabled"),
                        "qualifying_observation": False,
                        "qualifying_exposure": None,
                        "qualifying_captured_at": None,
                    },
                    "status": "publication_required",
                })
        try:
            item["challenger_sampling_gate"] = _sampling_gate(target, now, sampling)
        except (OSError, ValueError, KeyError, TypeError, RuntimeError) as exc:
            item["challenger_sampling_gate"] = {
                "status": "unknown",
                "reason": "sampling_gate_unavailable",
                "error_type": type(exc).__name__,
            }
        targets.append(item)
    complete = all(
        row["publication"].get("status") == "published_verified"
        and any(measurement["persisted_feedback"]["qualifying_observation"] for measurement in row["measurements"])
        and row.get("challenger_sampling_gate", {}).get("acceptance_status") in {"eligible", "admitted"}
        for row in targets
    )
    return {
        "schema_version": 1,
        "acceptance_id": "index4-learning",
        "status": "observed" if complete else "open",
        "observed_at": stamp(now),
        "targets": targets,
        "boundary": (
            "Pinned index-4 baseline effects only. Available metrics are distinct from qualifying "
            "persisted feedback. Challenger sampling eligibility is the existing learning-supply gate, "
            "not a reservation, full admission decision, publication instruction, or winner claim."
        ),
    }


def _scope_reason(poll):
    status = poll.get("status")
    if status == "partial":
        if int(poll.get("roots_not_yet_scanned", 0) or 0) > 0:
            return "roots_not_yet_scanned"
        if poll.get("root_cursors"):
            return "pagination_incomplete"
        if poll.get("root_errors"):
            return "root_errors"
        if int(poll.get("skipped_malformed", 0) or 0) > 0:
            return "malformed_rows_skipped"
        return "bounded_scan_partial"
    if status == "stale":
        return "poll_stale"
    if status == "permission_required":
        return str(poll.get("required_scope") or "linkedin_read_permission_required")
    if status == "unavailable":
        return str(poll.get("error_stage") or "provider_read_unavailable")
    if status == "no_recent_local_publications":
        return "no_recent_local_publications"
    if status == "observed":
        return "bounded_scan_complete_for_current_scope"
    return "not_observed"


def inbound_coverage_view(now):
    from ocpf_post.account_profiles import profiles
    from ocpf_post.capacity_experiment import ACCOUNTS
    from ocpf_post.engagement import read, report

    raw = read()
    projected = report(now=now, include_items=False)
    polls = projected.get("polls") if isinstance(projected.get("polls"), dict) else {}
    scopes = {}
    for poll_key, account_id in ACCOUNTS.items():
        scopes.setdefault((poll_key, str(account_id)), {
            "provider": poll_key,
            "account_id": str(account_id),
            "poll_keys": [],
        })["poll_keys"].append(poll_key)
    from ocpf_post.registry import load_registry
    for project in load_registry()["projects"].values():
        for binding in project.get("accounts", {}).values():
            if binding.get("provider") != "linkedin":
                continue
            account_id = str(binding.get("account_id") or "")
            if not account_id:
                continue
            scope = scopes.setdefault(("linkedin", account_id), {
                "provider": "linkedin",
                "account_id": account_id,
                "poll_keys": [],
            })
            poll_key = "linkedin:" + account_id
            if poll_key not in scope["poll_keys"]:
                scope["poll_keys"].append(poll_key)

    for key, row in profiles().items():
        if not row.get("enabled"):
            continue
        provider = str(row.get("provider") or "")
        account_id = str(row.get("account_id") or "")
        if not provider or not account_id:
            continue
        scope = scopes.setdefault((provider, account_id), {
            "provider": provider,
            "account_id": account_id,
            "poll_keys": [],
        })
        scope["poll_keys"].append(key)

    rows = []
    for _scope_key, scope in sorted(scopes.items()):
        provider = scope["provider"]
        account_id = scope["account_id"]
        poll_candidates = [
            (key, polls.get(key, {})) for key in scope["poll_keys"]
            if isinstance(polls.get(key), dict)
        ]
        poll_key, poll = max(
            poll_candidates,
            key=lambda pair: str(pair[1].get("observed_at") or ""),
            default=(scope["poll_keys"][0] if scope["poll_keys"] else provider, {}),
        )
        items = [
            row for row in raw.get("inbox", {}).values()
            if isinstance(row, dict)
            and row.get("provider") == provider
            and row.get("account_id") == account_id
        ]
        counts = Counter(str(row.get("status") or "unknown") for row in items)
        depths = [
            int(row.get("conversation_depth", 0) or 0)
            for row in items if type(row.get("conversation_depth", 0)) is int
        ]
        pending_ages = []
        for row in items:
            if row.get("status") not in {"pending", "drafted"} or not row.get("first_seen_at"):
                continue
            try:
                pending_ages.append(max(0, (now - at(row["first_seen_at"])).total_seconds()))
            except (TypeError, ValueError):
                pass
        status = str(poll.get("status") or "not_observed")
        reason = _scope_reason(poll)
        rows.append({
            "provider": provider,
            "account_id": account_id,
            "poll_key": poll_key,
            "status": status,
            "reason": reason,
            "observed_at": poll.get("observed_at"),
            "retry_at": poll.get("retry_at"),
            "pages_completed": poll.get("pages_completed"),
            "pages_budget": poll.get("pages_budget"),
            "root_count": poll.get("root_count"),
            "roots_not_yet_scanned": int(poll.get("roots_not_yet_scanned", 0) or 0),
            "open_root_cursors": len(poll.get("root_cursors", {})) if isinstance(poll.get("root_cursors"), dict) else 0,
            "root_error_count": len(poll.get("root_errors", {})) if isinstance(poll.get("root_errors"), dict) else 0,
            "oldest_target_scan_age_seconds": poll.get("oldest_target_scan_age_seconds"),
            "targets_older_than_30m": int(poll.get("targets_older_than_30m", 0) or 0),
            "http_status": poll.get("http_status"),
            "error_stage": poll.get("error_stage"),
            "error_type": poll.get("error_type"),
            "required_scope": poll.get("required_scope"),
            "scope_recorded": poll.get("scope_recorded"),
            "automatic_retry": poll.get("automatic_retry"),
            "next_action": poll.get("next_action"),
            "inbox_status_counts": dict(counts),
            "inbox_item_count": len(items),
            "max_conversation_depth": max(depths or [0]),
            "oldest_pending_age_seconds": max(pending_ages) if pending_ages else None,
            "nested_waiting": sum(
                row.get("status") in {"pending", "drafted"}
                and int(row.get("conversation_depth", 0) or 0) > 0
                for row in items
            ),
        })
    supported = [row for row in rows if row["provider"] in {"x", "threads", "linkedin"}]
    incomplete = {"partial", "stale", "unavailable", "not_observed", "permission_required"}
    status = "partial" if any(row["status"] in incomplete for row in supported) else "observed"
    linkedin_rows = [row for row in rows if row["provider"] == "linkedin"]
    return {
        "schema_version": 1,
        "acceptance_id": "PC-06",
        "status": status,
        "observed_at": stamp(now),
        "scopes": rows,
        "supported_scope_count": len(supported),
        "supported_incomplete_count": sum(row["status"] in incomplete for row in supported),
        "linkedin": {
            "status": ("not_configured" if not linkedin_rows else
                       "partial" if any(row["status"] in incomplete for row in linkedin_rows) else "observed"),
            "scope_count": len(linkedin_rows),
            "permission_boundary": "LinkedIn member comment reads require restricted r_member_social_feed authority; organization/page comment reads require approved page role plus r_organization_social_feed. Missing recorded authority is incomplete coverage, never zero comments.",
        },
        "boundary": (
            "Account-level bounded collection coverage only. Zero observed replies is not complete account "
            "coverage unless the current poll scope completed. No inbound text, draft text, provider prose, "
            "DMs or publication authority is exposed."
        ),
    }


def build(*, now=None):
    now = now or datetime.now(UTC)
    result = {
        "schema_version": 1,
        "observed_at": stamp(now),
        "status": "observed",
        "sections": {},
    }
    for name, fn in (
        ("pc01_editorial_supply", lambda: editorial_supply_view(now)),
        ("pc02_source_vault", lambda: source_vault_view(now)),
        ("pc03_publication_readback", lambda: publication_readback_view(now)),
        ("pc04_learning", lambda: index4_view(now)),
        ("pc05_effect_reconciliation", lambda: effect_reconciliation_view(now)),
        ("pc06_inbound_coverage", lambda: inbound_coverage_view(now)),
    ):
        try:
            result["sections"][name] = fn()
        except (OSError, ValueError, KeyError, TypeError, RuntimeError) as exc:
            result["sections"][name] = {
                "schema_version": 1,
                "status": "unavailable",
                "error_type": type(exc).__name__,
            }
            result["status"] = "partial"
    if result["status"] != "partial" and any(
        section.get("status") in {"open", "partial"} for section in result["sections"].values()
    ):
        result["status"] = "open"
    result["boundary"] = (
        "PC-01 through PC-06 read models from existing local evidence. This file does not collect providers, "
        "refresh vaults, rebuild feedback, admit challengers, schedule, publish, reply, change quotas, "
        "repair incidents or close acceptance by itself."
    )
    return result


def persist(result):
    with local_store.locked(path()):
        local_store.write(path(), result)


def main():
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="Persist the compact read model to local state")
    args = parser.parse_args()
    result = build()
    if args.apply:
        persist(result)
    print(json.dumps(result, indent=2, ensure_ascii=False, allow_nan=False))
    # A structured partial/open read model is evidence, not a process crash.
    # The collection stage classifies the JSON status itself.
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
