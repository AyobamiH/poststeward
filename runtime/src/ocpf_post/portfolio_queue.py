"""Frozen allocator inputs, offline comparison and opt-in fair selection.

A replay is a projection, never authority to reserve or publish its contents.
"""
from __future__ import annotations

import copy
import hashlib
import json
import os
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from ocpf_post import __version__, release_pacing
from ocpf_post import portfolio as base
from ocpf_post import portfolio_diversity as diversity

UTC = timezone.utc
ENGINE = "portfolio-queue-v12"
CANDIDATE_FIELDS = (
    "campaign", "project", "title", "provider", "lane", "priority", "prepared_at",
    "expires_at", "eligibility", "copy_key", "topic_key", "family", "account_id", "text_sha256", "service_kind",
)


def digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                                     ensure_ascii=False, allow_nan=False).encode()).hexdigest()


def _stamp(value: datetime) -> str:
    return value.isoformat().replace("+00:00", "Z")


def _learning_role(candidate: dict, manifest: dict) -> str | None:
    """Classify only explicitly frozen experiment inventory; legacy copy stays ordinary."""
    from ocpf_post.performance_feedback import metadata
    try:
        meta = metadata(manifest, {
            "provider": candidate["provider"],
            "text_sha256": candidate.get("text_sha256"),
        })
    except (KeyError, TypeError, ValueError):
        return None
    if not meta or meta.get("comparison_variant") not in {"question", "practical"}:
        return None
    source = manifest.get("source") if isinstance(manifest.get("source"), dict) else {}
    predecessor = source.get("sampling_predecessor")
    if predecessor:
        return "challenger" if meta.get("variant") == meta.get("comparison_variant") else None
    return "baseline" if meta.get("variant") == "insight" else None


def _scoped_account_policy(policy: dict, row: dict) -> dict:
    """Keep account-local scheduling controls while inheriting provider flow authority."""
    return release_pacing.scoped_settings(policy, row)


def capture_inputs(now: datetime, horizon: int, policy: dict, account_id=None, _candidates=None) -> dict:
    from ocpf_post.queue_watch import hints, identity, settings
    if not 1 <= horizon <= 1440:
        raise base.PortfolioError("horizon_minutes must be between 1 and 1440")
    zone = ZoneInfo(policy["timezone"])
    waiting, watch_status = hints()
    candidates, exclusions = [], []
    from ocpf_post.performance_feedback import snapshot, candidate_preference
    feedback = snapshot(now)
    from ocpf_post.account_profiles import matches_scope, profiles, credential_present
    all_candidates = base.delivery_candidates(now=now, exclusions=exclusions) if _candidates is None else _candidates
    for candidate in all_candidates:
        if candidate["provider"] not in policy["providers"] or not matches_scope(candidate["provider"], candidate.get("account_id"), account_id):
            continue
        enriched = diversity._enrich(candidate)
        row = {k: enriched[k] for k in CANDIDATE_FIELDS if k in enriched}
        if identity(row) in waiting:
            row["queue_first_eligible_at"] = waiting[identity(row)]
        manifest = diversity.builtin_manifest(row["campaign"])
        role = _learning_role(row, manifest)
        if role:
            row["learning_role"] = role
        row.update(candidate_preference(row, manifest, feedback, now))
        candidates.append(row)
    histories, counts, capacity = {}, {}, {}
    for provider in policy["providers"]:
        histories[provider] = [{**e, "at": _stamp(e["at"])} for e in (diversity._history(provider, account_id) if account_id else diversity._history(provider))]
        counts[provider] = {}
        day = now.astimezone(zone).date()
        horizon_last = (now + timedelta(minutes=horizon)).astimezone(zone).date() + timedelta(days=1)
        expiry_days = [
            base._parse_dt(c.get("expires_at")).astimezone(zone).date()
            for c in candidates
            if c.get("provider") == provider and base._parse_dt(c.get("expires_at"))
        ]
        # Capture enough future day-count state to make deadline-feasibility
        # replay deterministic. Bound it so a malformed far-future TTL cannot
        # inflate snapshots without limit.
        expiry_last = max(expiry_days) if expiry_days else horizon_last
        last = min(max(horizon_last, expiry_last), day + timedelta(days=31))
        while day <= last:
            total, lanes = base._day_counts(provider, day, zone, account_id) if account_id else base._day_counts(provider, day, zone)
            counts[provider][day.isoformat()] = {"total": total, "lanes": lanes}
            day += timedelta(days=1)
        used = counts[provider][now.astimezone(zone).date().isoformat()]["total"]
        settings_row = policy["providers"][provider]
        limit = base._daily_limit(settings_row)
        legacy_target = int(settings_row.get("daily_target", 0))
        capacity[provider] = {
            "local_day": now.astimezone(zone).date().isoformat(), "daily_budget_used": used,
            "daily_budget_remaining": max(0, limit - used),
            "daily_target_met": used >= legacy_target,
            "daily_ceiling_met": used >= limit,
            "daily_ceiling": limit, "flow_mode": base._flow_mode(settings_row),
            "legacy_mix_target": legacy_target,
            "legacy_target_remaining": max(0, legacy_target - used),
            "budget_scope": (
                "Admission-driven logical-publication safety ceiling; active reservations and terminal effects count"
                if base._flow_mode(settings_row) == "admission"
                else "Fixed automatic-allocation budget; active reservations and terminal effects count"
            ),
            **release_pacing.capacity_fields(policy, settings_row, used),
        }
    partitions = {}
    if account_id is None:
        for identity, row in profiles().items():
            if not row["enabled"] or not credential_present(row):
                continue
            # Account-local windows, spacing and lane-mix inputs remain isolated,
            # but flow authority is provider-wide: every enabled publishing
            # identity inherits the current admission/fixed mode and hard ceiling.
            scoped_account_policy = _scoped_account_policy(policy, row)
            scoped_policy = {**copy.deepcopy(policy), "providers": {row["provider"]: scoped_account_policy}, "selection": "fair", "reply_targets": {}}
            partitions[identity] = {"policy": scoped_policy, "inputs": capture_inputs(now, horizon, scoped_policy, row["account_id"], all_candidates)}
    return {"now": _stamp(now), "horizon_minutes": horizon, "candidates": candidates, "account_partitions": partitions,
            "histories": histories, "day_counts": counts, "capacity": capacity, "exclusions": exclusions,
            "queue_watch_status": watch_status, "queue_watch_settings": settings(policy)}


def _events(inputs: dict) -> dict:
    return {p: [{**e, "at": base._parse_dt(e["at"])} for e in rows]
            for p, rows in inputs["histories"].items()}


def _fresh(candidate: dict, slot: datetime) -> bool:
    expires = base._parse_dt(candidate.get("expires_at"))
    prepared = base._parse_dt(candidate.get("prepared_at"))
    return (expires is None or slot < expires) and (prepared is None or prepared <= slot)


def _wait_state(candidate: dict, slot: datetime, watch_controls: dict) -> dict[str, Any]:
    observed = base._parse_dt(candidate.get("queue_first_eligible_at"))
    prepared = observed or base._parse_dt(candidate.get("prepared_at"))
    age_seconds = max(0, (slot - prepared).total_seconds()) if prepared else 0
    expiry = base._parse_dt(candidate.get("expires_at"))
    aged = bool(observed and age_seconds >= int(watch_controls["wait_warning_hours"]) * 3600)
    near_deadline = bool(
        expiry and slot < expiry <= slot + timedelta(hours=int(watch_controls["deadline_warning_hours"]))
    )
    return {
        "observed": observed,
        "age_seconds": age_seconds,
        "age_band": int(age_seconds // 21600),
        "aged": aged,
        "expiry": expiry,
        "near_deadline": near_deadline,
    }


def _remaining_authorised_slots(provider: str, slot: datetime, expiry: datetime, settings: dict,
                                counts: dict, zone: ZoneInfo) -> int:
    """Estimate remaining configured provider slots before expiry from captured state.

    This is intentionally a planner-side estimate, not provider quota discovery.
    It uses only configured grid slots and already-captured day counts, so replay
    remains offline and deterministic.
    """
    if expiry <= slot:
        return 0
    target = base._daily_limit(settings)
    total = 0
    day = slot.astimezone(zone).date()
    last = expiry.astimezone(zone).date()
    while day <= last:
        used = int(counts.get(provider, {}).get(day.isoformat(), {}).get("total", 0) or 0)
        budget = max(0, target - used)
        grid = sum(
            1 for local_slot in base._grid(day, settings, zone)
            if slot <= local_slot.astimezone(UTC) < expiry
        )
        total += min(budget, grid)
        day += timedelta(days=1)
    return total


def _rescue_state(candidate: dict, pool: list[dict], slot: datetime, provider: str, settings: dict,
                  counts: dict, zone: ZoneInfo, watch_controls: dict) -> dict[str, Any]:
    state = _wait_state(candidate, slot, watch_controls)
    expiry = state["expiry"]
    eligible_lane = (
        candidate.get("service_kind") != "reviewed_brief"
        and candidate.get("lane") in {"commercial", "evergreen"}
    )
    if not eligible_lane or not expiry or not slot < expiry:
        return {**state, "capacity_pressure": False, "rescue": False,
                "due_count": 0, "authorised_slots_before_expiry": 0, "trigger": None}
    due = [
        c for c in pool
        if c.get("provider") == provider
        and c.get("service_kind") != "reviewed_brief"
        and c.get("lane") in {"commercial", "evergreen"}
        and (candidate_expiry := base._parse_dt(c.get("expires_at"))) is not None
        and candidate_expiry <= expiry
        and _fresh(c, slot)
    ]
    slots_before_expiry = _remaining_authorised_slots(provider, slot, expiry, settings, counts, zone)
    capacity_pressure = len(due) > slots_before_expiry
    trigger = "deadline_window" if state["near_deadline"] else "capacity_feasibility" if capacity_pressure else None
    return {
        **state,
        "capacity_pressure": capacity_pressure,
        "rescue": bool(state["near_deadline"] or capacity_pressure),
        "due_count": len(due),
        "authorised_slots_before_expiry": slots_before_expiry,
        "trigger": trigger,
    }


def _fair_choice(pool: list[dict], events: list[dict], slot: datetime, controls: dict, watch_controls: dict | None = None, *, expiry_ties=True) -> dict:
    """Equal project opportunity, then observed wait/deadline and priority.

    Repeated provisional/final receipts for one campaign count once. Priority
    breaks age ties; adding more inventory does not earn a project more turns.
    """
    no_topic = [c for c in pool if not diversity._topic_blocked(
        c, events, slot, int(controls["topic_cooldown_hours"]))]
    if no_topic:
        pool = no_topic
    last = diversity._recent_before(events, slot)
    if controls["avoid_consecutive_project"] and last:
        different = [c for c in pool if c["project"] != last["project"]]
        if different:
            pool = different
    served: dict[str, set[str]] = {}
    for event in events:
        if slot - timedelta(hours=24) <= event["at"] <= slot:
            served.setdefault(event["project"], set()).add(event["campaign"])

    from ocpf_post.queue_watch import DEFAULTS
    watch_controls = watch_controls or DEFAULTS
    def score(c: dict) -> tuple:
        state = _wait_state(c, slot, watch_controls)
        expiry = state["expiry"]
        priority = int(c['priority'])
        # Alternate each project's served turns: evidence preference, then exploration.
        boost = c.get('performance_boost', 0)
        feedback_expiry = base._parse_dt(c.get('performance_expires_at'))
        if (feedback_expiry and slot < feedback_expiry
                and len(served.get(c['project'], set())) % 2 == 0
                and type(boost) is int and boost in {0, 3, 7, 11}):
            priority += boost
        if last and c["family"] == last["family"]:
            priority -= int(controls["family_repeat_penalty"])
        return (
            -len(served.get(c["project"], set())),
            state["aged"],
            state["near_deadline"],
            -expiry.timestamp() if state["near_deadline"] and expiry else 0,
            state["age_band"],
            # Already-aged work in the same age band: drain the earlier expiry
            # before static priority. Project service still precedes this tie.
            (-expiry.timestamp() if state["aged"] and expiry else float('-inf')) if expiry_ties else 0,
            priority,
            int(c["priority"]),
            c["campaign"],
        )

    return max(pool, key=score)


def merge_partitions(result, inputs, service=True, expiry_ties=True):
    result = copy.deepcopy(result)
    result['account_capacity'] = {}
    for identity, partition in inputs.get('account_partitions', {}).items():
        extra = fair_plan(partition['inputs'], partition['policy'], service=service, expiry_ties=expiry_ties)
        result['plan'].extend(extra['plan'])
        provider = next(iter(partition['policy']['providers']))
        result['account_capacity'][identity] = extra['capacity'][provider]
        result.setdefault('decisions', []).extend({**d, 'account': identity} for d in extra.get('decisions', []))
    result['plan'].sort(key=lambda item: (item['run_at'], item['provider'], item['campaign']))
    return result


def fair_plan(inputs: dict, policy: dict, *, service=True, expiry_ties=True) -> dict:
    if inputs.get('account_partitions'):
        return merge_partitions(fair_plan({**inputs, 'account_partitions': {}}, policy, service=service, expiry_ties=expiry_ties), inputs, service, expiry_ties)
    # One chronological pass: a later cross-provider replacement cannot undo
    # fairness, freshness, lane limits or the same-provider history decisions.
    from ocpf_post.portfolio_cross_platform import _cross_policy, _cross_topic_conflict, _PROVIDER_ORDER
    from ocpf_post.queue_watch import DEFAULTS
    base.validate_policy(policy)
    controls = diversity._diversity_policy(policy)
    cross = _cross_policy(policy)
    watch_controls = inputs.get("queue_watch_settings") or DEFAULTS
    now = base._parse_dt(inputs["now"])
    horizon = int(inputs["horizon_minutes"])
    if not 1 <= horizon <= 1440:
        raise base.PortfolioError("horizon_minutes must be between 1 and 1440")
    lower = now + timedelta(minutes=max(1, int(policy.get("minimum_lead_minutes", 8))))
    upper = now + timedelta(minutes=horizon)
    zone = ZoneInfo(policy["timezone"])
    histories = _events(inputs)
    slots = []
    for provider, settings in policy["providers"].items():
        day = lower.astimezone(zone).date()
        while day <= upper.astimezone(zone).date() + timedelta(days=1):
            for local_slot in base._grid(day, settings, zone):
                slot = local_slot.astimezone(UTC)
                if lower <= slot <= upper and not any(
                    abs((slot - e["at"]).total_seconds()) < max(60, int(settings.get("minimum_spacing_minutes", 0)) * 60)
                    for e in histories[provider]
                ):
                    slots.append((slot, provider))
            day += timedelta(days=1)
    remaining = list(inputs["candidates"])
    counts = copy.deepcopy(inputs["day_counts"])
    planned, decisions = [], []
    for slot, provider in sorted(set(slots), key=lambda s: (s[0], _PROVIDER_ORDER.get(s[1], 99))):
        settings = policy["providers"][provider]
        if any(abs((slot - e["at"]).total_seconds()) < max(60, int(settings.get("minimum_spacing_minutes", 0)) * 60)
               for e in histories[provider]):
            decisions.append({"provider": provider, "run_at": _stamp(slot), "reason": "minimum_spacing"})
            continue
        day = slot.astimezone(zone).date().isoformat()
        used = counts[provider][day]
        if used["total"] >= base._daily_limit(settings):
            decisions.append({"provider": provider, "run_at": _stamp(slot), "reason": "daily_safety_ceiling_used" if base._flow_mode(settings) == "admission" else "daily_budget_used"})
            continue
        available = [c for c in remaining if c["provider"] == provider and _fresh(c, slot)
                     and (c["lane"] != "development" or used["lanes"]["development"] < base._development_limit(settings))]
        if not available:
            decisions.append({"provider": provider, "run_at": _stamp(slot), "reason": "no_fresh_candidate_within_lane_limits"})
            continue
        turn_index = slot.astimezone(zone).date().toordinal() * max(1, base._daily_limit(settings)) + used["total"]
        service_class = ("timely", "aged", "rescue", "standard")[turn_index % 4]
        learning_turn = bool(service and service_class == "standard" and turn_index % 8 == 3)
        timely = [
            c for c in available
            if c.get("service_kind") == "reviewed_brief" or c["lane"] == "development"
        ]
        rescue_states = {
            c["campaign"]: _rescue_state(
                c, available, slot, provider, settings, counts, zone, watch_controls
            )
            for c in available
        }
        rescue = [c for c in available if rescue_states[c["campaign"]]["rescue"]]
        aged = [
            c for c in available
            if c not in timely and _wait_state(c, slot, watch_controls)["aged"]
        ]
        learning = [c for c in available if c.get("learning_role") in {"baseline", "challenger"}]
        dedicated_class = None
        dedicated = []
        if service:
            if service_class == "timely" and timely:
                dedicated_class, dedicated = "timely", timely
            elif service_class == "aged" and aged:
                dedicated_class, dedicated = "aged", aged
            elif service_class == "rescue" and rescue:
                dedicated_class, dedicated = "rescue", rescue
            elif service_class in {"timely", "aged"} and rescue:
                # Rescue may borrow an otherwise-empty protected share. It never
                # displaces actual timely or aged work from that share.
                dedicated_class, dedicated = "rescue", rescue
            elif learning_turn and learning:
                dedicated_class, dedicated = "learning", learning
        if service and service_class in {"rescue", "standard"} and not dedicated:
            # An empty rescue turn must retain the old standard-turn semantics:
            # warming rescue is additive only when a rescue candidate exists.
            # It must not accidentally reopen the standard share to timely
            # development work.
            ordinary = [c for c in available if c not in timely]
            if ordinary:
                available = ordinary
        urgent = [c for c in available if c["lane"] == "development" and int(c["priority"]) >= 95]
        if dedicated:
            separated = [c for c in dedicated if not _cross_topic_conflict(c, provider=provider, slot=slot, selected=planned, cooldown_minutes=int(cross["topic_cooldown_minutes"]))]
            pool = separated or dedicated
            if dedicated_class == "timely":
                overdue_briefs = [c for c in pool if c.get("service_kind") == "reviewed_brief" and _wait_state(c, slot, watch_controls)["age_seconds"] >= 86400 and not diversity._topic_blocked(c, histories[provider], slot, int(controls["topic_cooldown_hours"]))]
                if overdue_briefs:
                    pool = overdue_briefs
            # A bounded aged turn serves the oldest age band before project balance.
            # Aged and rescue are overlapping protections: becoming capacity-at-risk
            # must never remove an already-aged delivery from its aged-service turn.
            # A rescue turn is narrower: among still-valid commercial/evergreen work
            # already inside the configured expiry-warning window, drain the earliest
            # expiry first. This never extends expiry or changes capacity.
            # Learning turns remain project-fair and never alter admission or capacity.
            if dedicated_class == "aged":
                oldest = max(_wait_state(c, slot, watch_controls)["age_band"] for c in pool)
                pool = [c for c in pool if _wait_state(c, slot, watch_controls)["age_band"] == oldest]
            elif dedicated_class == "rescue":
                # Capacity-feasibility pressure is stronger than the fixed warning
                # window because the queue can become mathematically impossible to
                # drain days before the final 24 hours.
                pressured = [c for c in pool if rescue_states[c["campaign"]]["capacity_pressure"]]
                if pressured:
                    pool = pressured
                expiries = [(rescue_states[c["campaign"]]["expiry"], c) for c in pool]
                valid_expiries = [expiry for expiry, _ in expiries if expiry is not None]
                if valid_expiries:
                    earliest = min(valid_expiries)
                    pool = [c for expiry, c in expiries if expiry == earliest]
            chosen = _fair_choice(pool, histories[provider], slot, controls, watch_controls, expiry_ties=expiry_ties)
            reason = (
                "bounded_rescue_capacity_feasibility"
                if dedicated_class == "rescue" and rescue_states[chosen["campaign"]]["capacity_pressure"]
                else "bounded_" + dedicated_class + "_service"
            )
        elif urgent and not service:
            chosen = sorted(urgent, key=lambda c: (-int(c["priority"]), c["campaign"]))[0]
            reason = "urgent_development_within_limit"
        else:
            lanes = diversity._lane_order(available, settings, used["lanes"])
            lane_pool = [c for c in available if c["lane"] == lanes[0]]
            aged_cross_lane = [
                c for c in available
                if c not in lane_pool and _wait_state(c, slot, watch_controls)["aged"]
            ]
            candidate_pool = [*lane_pool, *aged_cross_lane]
            separated = [c for c in candidate_pool if not _cross_topic_conflict(
                c, provider=provider, slot=slot, selected=planned,
                cooldown_minutes=int(cross["topic_cooldown_minutes"]))]
            chosen = _fair_choice(separated or candidate_pool, histories[provider], slot, controls,
                                  watch_controls, expiry_ties=expiry_ties)
            if chosen in aged_cross_lane:
                reason = "observed_waiting_threshold_overrides_soft_lane_balance"
            else:
                reason = "lane_then_project_service_then_observed_age_and_deadline"
        remaining.remove(chosen)
        used["total"] += 1
        used["lanes"][chosen["lane"]] += 1
        planned.append({**chosen, "run_at": _stamp(slot.replace(microsecond=0)), "display_timezone": policy["timezone"]})
        histories[provider].append({"at": slot, **{k: chosen[k] for k in ("campaign", "project", "family", "topic_key")}})
        histories[provider].sort(key=lambda e: e["at"])
        decisions.append({"provider": provider, "run_at": _stamp(slot), "campaign": chosen["campaign"], "reason": reason})
    return {
        "generated_at": _stamp(now.replace(microsecond=0)), "horizon_minutes": horizon,
        "timezone": policy["timezone"], "selection": "fair", "plan": planned,
        "capacity": {p: {**inputs["capacity"][p], "daily_target": int(s["daily_target"]),
                         "daily_ceiling": base._daily_limit(s), "flow_mode": base._flow_mode(s),
                         "reply_target": int(policy.get("reply_targets", {}).get(p, 0)),
                         "eligible_unscheduled": sum(c["provider"] == p for c in inputs["candidates"]),
                         "planned_in_horizon": sum(c["provider"] == p for c in planned),
                         **release_pacing.capacity_fields(policy, s, int(inputs["capacity"][p].get("daily_budget_used", 0)))}
                     for p, s in policy["providers"].items()},
        "diversity": controls, "cross_platform": cross, "decisions": decisions,
        "queue_watch_status": inputs.get("queue_watch_status", "not_observed"),
        **({"service_policy": {"cycle": ["timely", "aged", "rescue", "standard"],
                               "rescue": {"lanes": ["commercial", "evergreen"],
                                          "trigger": "deadline_warning_or_capacity_feasibility",
                                          "order": "capacity_pressure_then_earliest_expiry",
                                          "extends_expiry": False,
                                          "quota_changed": False},
                               "learning_standard_turn": {"modulus": 8, "remainder": 3,
                                                          "roles": ["baseline", "challenger"],
                                                          "source": "existing_standard_share"},
                               "idle_share": "borrowed; rescue may consume empty timely/aged shares",
                               "capacity_changed": any(base._flow_mode(row) == "admission" for row in policy["providers"].values())}} if service else {}),
        "reply_boundary": "Reply targets are tracked separately. post-once does not invent or auto-send conversational replies.",
    }


def _legacy(inputs: dict, policy: dict) -> dict:
    from ocpf_post.portfolio_cross_platform import plan_refill
    legacy_inputs = {**inputs, "candidates": [{k: v for k, v in c.items() if k != "queue_first_eligible_at"}
                                            for c in inputs["candidates"]]}
    return plan_refill(now=base._parse_dt(inputs["now"]), horizon_minutes=inputs["horizon_minutes"],
                       policy={**policy, "selection": "legacy"}, inputs=legacy_inputs)


def snapshot(*, horizon_minutes: int = 1440, now: datetime | None = None) -> dict:
    now = (now or base._utc_now()).astimezone(UTC)
    policy = copy.deepcopy(base.load_policy())
    inputs = capture_inputs(now, horizon_minutes, policy)
    # Detect ordinary timer/import races instead of silently storing two states.
    # This is a stability check, not a transactional lock over every host writer.
    if digest(inputs) != digest(capture_inputs(now, horizon_minutes, policy)) or policy != base.load_policy():
        raise base.PortfolioError("Queue changed during capture; rerun portfolio snapshot")
    body = {"schema_version": 1, "engine": ENGINE, "cli_version": __version__, "policy": policy,
            "inputs": inputs, "baseline": _legacy(inputs, policy)}
    return {**body, "snapshot_sha256": digest(body),
            "boundary": "Two matching local observations; not an atomic host transaction. Includes eligible candidates and planner history, no copy text or credentials. Historical metadata reflects current manifests. Replay never reserves or publishes."}


def explanations(inputs: dict, plan: dict) -> list[dict]:
    now = base._parse_dt(inputs["now"])
    upper = now + timedelta(minutes=inputs["horizon_minutes"])
    selected = {(c["campaign"], c["provider"]): c for c in plan["plan"]}
    rows = []
    for candidate in inputs["candidates"]:
        chosen = selected.get((candidate["campaign"], candidate["provider"]))
        prepared = base._parse_dt(candidate.get("queue_first_eligible_at") or candidate.get("prepared_at"))
        expires = base._parse_dt(candidate.get("expires_at"))
        rows.append({
            "campaign": candidate["campaign"], "provider": candidate["provider"], "account_id": candidate.get("account_id"), "project": candidate["project"],
            "lane": candidate["lane"], "priority": candidate["priority"], "learning_role": candidate.get("learning_role"),
            "status": "selected_in_projection" if chosen else "waiting_not_selected_in_horizon",
            "projected_run_at": chosen["run_at"] if chosen else None,
            "waiting_age_hours": round(max(0, (now - prepared).total_seconds()) / 3600, 2) if prepared else None,
            "waiting_age_source": "first_eligible_observation" if candidate.get("queue_first_eligible_at") else "prepared_at_proxy",
            "expires_at": candidate.get("expires_at"),
            "expiry_risk": ("selected_at_or_after_expiry" if chosen and expires and base._parse_dt(chosen["run_at"]) >= expires
                            else "unselected_expires_within_horizon" if not chosen and expires and expires <= upper
                            else "not_observed_within_horizon"),
            "context": {"provider_slots_selected": sum(c["provider"] == candidate["provider"] and c.get("account_id") == candidate.get("account_id") for c in plan["plan"]),
                        "same_lane_candidates": sum(c["provider"] == candidate["provider"] and c["lane"] == candidate["lane"] for c in inputs["candidates"]),
                        "today_budget_used": inputs["capacity"].get(candidate["provider"], {}).get("daily_budget_used"),
                        "provider_configured": candidate["provider"] in inputs["capacity"]},
        })
    for partition in inputs.get('account_partitions', {}).values():
        rows.extend(explanations(partition['inputs'], plan))
    return rows


def replay(value: dict) -> dict:
    if value.get("schema_version") != 1 or value.get("engine") != ENGINE:
        raise base.PortfolioError("Unsupported queue snapshot engine/schema")
    body = {k: value[k] for k in ("schema_version", "engine", "cli_version", "policy", "inputs", "baseline")}
    if digest(body) != value.get("snapshot_sha256"):
        raise base.PortfolioError("Queue snapshot hash mismatch")
    inputs, policy = value["inputs"], value["policy"]
    baseline = _legacy(inputs, policy)
    if baseline != value["baseline"]:
        raise base.PortfolioError("Baseline is not reproducible with this planner; capture a new snapshot")
    previous_fair = fair_plan(inputs, {**policy, "selection": "fair"}, service=False, expiry_ties=False)
    previous_expiry = fair_plan(inputs, {**policy, "selection": "fair"}, expiry_ties=False)
    fair = fair_plan(inputs, {**policy, "selection": "fair"})
    def summary(plan: dict) -> dict:
        return {p: {"selected": sum(c["provider"] == p for c in plan["plan"]),
                    "by_project": dict(sorted(Counter(c["project"] for c in plan["plan"] if c["provider"] == p).items()))}
                for p in policy["providers"]}
    return {"schema_version": 1, "snapshot_sha256": value["snapshot_sha256"], "baseline_reproduced": True,
            "observed_at": inputs["now"], "horizon_minutes": inputs["horizon_minutes"],
            "baseline_summary": summary(baseline), "fair_summary": summary(fair),
            "baseline": baseline, "previous_fair": previous_fair, "fair": fair,
            "service_comparison": {"previous_fair": explanations(inputs, previous_fair), "current_fair": explanations(inputs, fair)},
            "expiry_comparison": {"previous_v7": explanations(inputs, previous_expiry), "current_v8": explanations(inputs, fair)},
            "excluded_before_selection": inputs.get("exclusions", []),
            "waiting": {"legacy": explanations(inputs, baseline), "fair": explanations(inputs, fair)},
            "boundary": "Frozen-input projection only. Waiting age identifies its source: first eligible observation when available, otherwise prepared_at proxy. No arrival forecast or promised publication time beyond the horizon. Budget, windows and authority unchanged; no schedules or posts created."}


def read_snapshot(path: Path) -> dict:
    if path.stat().st_size > 20_000_000:
        raise base.PortfolioError("Queue snapshot exceeds 20 MB")
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise base.PortfolioError("Queue snapshot must be an object")
    return value


def write_artifact(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        json.dump(value, handle, indent=2, ensure_ascii=False, allow_nan=False)
        handle.write("\n")


def set_selection(selection: str) -> Path:
    policy = copy.deepcopy(base.load_policy(effective=False))
    policy["selection"] = selection
    base.validate_policy(policy)
    path = base.policy_file()
    base.write_private_json(path, policy)
    return path
