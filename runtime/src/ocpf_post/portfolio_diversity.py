from __future__ import annotations

import math
import re
from collections import Counter
from datetime import date, datetime, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

import ocpf_post.portfolio as base
from ocpf_post.campaigns import builtin_manifest
from ocpf_post.scheduler import ACTIVE_STATUSES, schedule_records
from ocpf_post.state import iter_receipts

UTC = timezone.utc

DEFAULT_DIVERSITY: dict[str, Any] = {
    "topic_cooldown_hours": 6,
    "avoid_consecutive_project": True,
    "family_repeat_penalty": 12,
    "same_day_project_penalty": 3,
}

PROJECT_FAMILIES: dict[str, str] = {
    "proof-and-state": "proof-state",
    "donestate": "proof-state",
    "opstruth": "proof-state",
    "opstruth-chatgpt-plugin": "proof-state",
    "agentproof": "proof-state",
    "public-decision-intelligence": "public-decision-intelligence",
    "evidence-explorer": "public-decision-intelligence",
    "oneclickpostfactory": "one-click",
    "oneclick-chatgpt-plugin": "one-click",
    "lovable-architecture-auditor": "one-click",
}

_AUTO_TOPIC_RE = re.compile(r"^(?P<prefix>.+-AUTO-\d{2})[IQP]-(?P<sha>[A-F0-9]+)$")


def _topic_key(campaign: str, manifest: dict[str, Any]) -> str:
    match = _AUTO_TOPIC_RE.match(campaign)
    if match:
        return f"{match.group('prefix')}-{match.group('sha')}"
    source = manifest.get("source") if isinstance(manifest.get("source"), dict) else {}
    if source.get("event_group"):
        return str(source["event_group"])
    source_id = str(source.get("source_id") or "").strip()
    if source_id:
        source_id = re.sub(r"-(insight|question|practical)$", "", source_id, flags=re.I)
        return source_id
    title = str(manifest.get("title") or campaign)
    return re.sub(r"\s*\[(insight|question|practical)\]\s*$", "", title, flags=re.I).strip().lower()


def _family(project: str) -> str:
    return PROJECT_FAMILIES.get(project, project)


def _learning_base_priority(candidate: dict[str, Any], manifest: dict[str, Any]) -> int | None:
    """Restore allocator priority after an internal copy-dedupe tie breaker."""
    if manifest.get("payload_frozen") is not True:
        return None
    source = manifest.get("source") if isinstance(manifest.get("source"), dict) else {}
    arm = source.get("comparison_variant")
    source_id = str(source.get("source_id") or "")
    allocation = manifest.get("allocation") if isinstance(manifest.get("allocation"), dict) else {}
    base_priority = allocation.get("learning_base_priority")
    bump = allocation.get("learning_dedupe_priority_bump")
    if (
        source.get("type") != "repository_product_truth"
        or arm not in {"question", "practical"}
        or type(base_priority) is not int
        or type(bump) is not int
        or bump not in {0, 1}
        or int(candidate.get("priority", -1)) != base_priority + bump
    ):
        return None
    if source.get("sampling_predecessor"):
        return base_priority if source_id.endswith("-" + arm) else None
    return base_priority if source_id.endswith("-insight") else None


def _enrich(candidate: dict[str, Any]) -> dict[str, Any]:
    campaign = str(candidate["campaign"])
    manifest = builtin_manifest(campaign)
    project = str(candidate.get("project") or manifest.get("project") or "")
    base_priority = _learning_base_priority(candidate, manifest)
    normalized = {**candidate, **({"priority": base_priority} if base_priority is not None else {})}
    return {
        **normalized,
        "topic_key": _topic_key(campaign, manifest),
        "family": _family(project),
        "service_kind": ("reviewed_brief" if str((manifest.get("source") or {}).get("source_id", "")).startswith("brief:") else "development" if candidate.get("lane") == "development" else "standard"),
    }


def _history(provider: str, account_id=None) -> list[dict[str, Any]]:
    from ocpf_post.account_profiles import matches_scope
    events: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()
    for record in schedule_records():
        if not matches_scope(provider, record.get("account_id"), account_id):
            continue
        if record.get("provider") != provider or record.get("status") not in ACTIVE_STATUSES:
            continue
        at = base._parse_dt(str(record.get("run_at") or ""))
        if not at:
            continue
        campaign = str(record.get("campaign") or "")
        manifest = builtin_manifest(campaign)
        project = str(manifest.get("project") or "")
        key = (campaign, provider, at.isoformat())
        if key in seen:
            continue
        seen.add(key)
        events.append({"at": at, "campaign": campaign, "project": project, "family": _family(project), "topic_key": _topic_key(campaign, manifest)})
    for receipt in iter_receipts():
        if not matches_scope(provider, receipt.get("account_id"), account_id):
            continue
        if receipt.get("provider") != provider or receipt.get("status") not in base.TERMINAL_RECEIPT_STATUSES:
            continue
        at = base._parse_dt(str(receipt.get("recorded_at") or ""))
        if not at:
            continue
        campaign = str(receipt.get("campaign") or "")
        manifest = builtin_manifest(campaign)
        project = str(manifest.get("project") or "")
        key = (campaign, provider, at.isoformat())
        if key in seen:
            continue
        seen.add(key)
        events.append({"at": at, "campaign": campaign, "project": project, "family": _family(project), "topic_key": _topic_key(campaign, manifest)})
    return sorted(events, key=lambda item: item["at"])


def _lane_order(candidates: list[dict[str, Any]], provider_policy: dict[str, Any], current_lanes: dict[str, int]) -> list[str]:
    target = max(1, base._daily_limit(provider_policy))
    development_target = min(base._development_limit(provider_policy), math.ceil(target * 0.30))
    commercial_target = max(int(provider_policy.get("commercial_min", 0)), math.ceil(target * 0.40))
    evergreen_target = max(0, target - development_target - commercial_target)
    desired = {"development": development_target, "commercial": commercial_target, "evergreen": evergreen_target}
    available = {str(c["lane"]) for c in candidates}

    urgent = any(c["lane"] == "development" and int(c["priority"]) >= 95 for c in candidates)
    if urgent and current_lanes["development"] < base._development_limit(provider_policy):
        return ["development", "commercial", "evergreen"]

    def deficit(lane: str) -> float:
        goal = desired[lane]
        return -1.0 if goal <= 0 else (goal - current_lanes[lane]) / goal

    ranked = sorted(available, key=lambda lane: (deficit(lane), lane == "commercial", lane == "development"), reverse=True)
    return ranked


def _recent_before(events: list[dict[str, Any]], slot: datetime) -> dict[str, Any] | None:
    before = [event for event in events if event["at"] < slot]
    return before[-1] if before else None


def _topic_blocked(candidate: dict[str, Any], events: list[dict[str, Any]], slot: datetime, hours: int) -> bool:
    if hours <= 0:
        return False
    window = timedelta(hours=hours)
    return any(event["topic_key"] == candidate["topic_key"] and abs(event["at"] - slot) < window for event in events)


def _choose_diverse(
    candidates: list[dict[str, Any]], *, provider_policy: dict[str, Any], current_lanes: dict[str, int],
    slot: datetime, events: list[dict[str, Any]], diversity: dict[str, Any], zone: ZoneInfo,
) -> dict[str, Any] | None:
    if not candidates:
        return None
    lane_order = _lane_order(candidates, provider_policy, current_lanes)
    urgent_pool = [c for c in candidates if c["lane"] == "development" and int(c["priority"]) >= 95]
    if urgent_pool and current_lanes["development"] < base._development_limit(provider_policy):
        return sorted(urgent_pool, key=lambda c: (-int(c["priority"]), c["campaign"]))[0]

    topic_hours = int(diversity.get("topic_cooldown_hours", 6))
    avoid_same_project = bool(diversity.get("avoid_consecutive_project", True))
    family_penalty = int(diversity.get("family_repeat_penalty", 12))
    day_project_penalty = int(diversity.get("same_day_project_penalty", 3))
    last = _recent_before(events, slot)
    local_day = slot.astimezone(zone).date()
    same_day_projects = Counter(
        event["project"] for event in events if event["at"].astimezone(zone).date() == local_day
    )

    for lane in lane_order:
        pool = [c for c in candidates if c["lane"] == lane]
        if not pool:
            continue
        no_topic = [c for c in pool if not _topic_blocked(c, events, slot, topic_hours)]
        if no_topic:
            pool = no_topic
        if avoid_same_project and last:
            different_project = [c for c in pool if c["project"] != last["project"]]
            if different_project:
                pool = different_project

        def score(candidate: dict[str, Any]) -> tuple[int, int, str]:
            value = int(candidate["priority"])
            if last and candidate["family"] == last["family"]:
                value -= family_penalty
            value -= same_day_projects[candidate["project"]] * day_project_penalty
            return value, int(candidate["priority"]), str(candidate["campaign"])

        return max(pool, key=score)
    return max(candidates, key=lambda c: (int(c["priority"]), str(c["campaign"])))


def _diversity_policy(policy: dict[str, Any]) -> dict[str, Any]:
    raw = policy.get("diversity") if isinstance(policy.get("diversity"), dict) else {}
    result = {**DEFAULT_DIVERSITY, **raw}
    topic_hours = int(result["topic_cooldown_hours"])
    if topic_hours < 0 or topic_hours > 72:
        raise base.PortfolioError("diversity.topic_cooldown_hours must be between 0 and 72")
    for key in ("family_repeat_penalty", "same_day_project_penalty"):
        value = int(result[key])
        if value < 0 or value > 100:
            raise base.PortfolioError(f"diversity.{key} must be between 0 and 100")
    return result


def plan_refill(*, now: datetime | None = None, horizon_minutes: int | None = None, policy: dict[str, Any] | None = None, inputs: dict[str, Any] | None = None) -> dict[str, Any]:
    now_dt = (now or base._utc_now()).astimezone(UTC)
    effective = base.validate_policy(policy or base.load_policy())
    diversity = _diversity_policy(effective)
    zone = ZoneInfo(str(effective["timezone"]))
    horizon = int(horizon_minutes or effective.get("horizon_minutes", 75))
    if horizon < 1 or horizon > 24 * 60:
        raise base.PortfolioError("horizon_minutes must be between 1 and 1440")
    lead = int(effective.get("minimum_lead_minutes", 8))
    lower = now_dt + timedelta(minutes=max(1, lead))
    upper = now_dt + timedelta(minutes=horizon)
    candidates = inputs["candidates"] if inputs is not None else [_enrich(c) for c in base.delivery_candidates(now=now_dt)]
    by_provider: dict[str, list[dict[str, Any]]] = {}
    for candidate in candidates:
        by_provider.setdefault(str(candidate["provider"]), []).append(candidate)

    active_times: dict[str, list[datetime]] = {}
    histories: dict[str, list[dict[str, Any]]] = {}
    for provider in effective["providers"]:
        histories[provider] = ([{**e, "at": base._parse_dt(e["at"])} for e in inputs["histories"][provider]]
                               if inputs is not None else _history(provider))
        active_times[provider] = [event["at"] for event in histories[provider] if event["at"] >= now_dt]

    planned: list[dict[str, Any]] = []
    capacity: dict[str, dict[str, Any]] = {}
    for provider, provider_policy_raw in effective["providers"].items():
        provider_policy = dict(provider_policy_raw)
        provider_candidates = list(by_provider.get(provider, []))
        slots: list[datetime] = []
        day = lower.astimezone(zone).date()
        while day <= upper.astimezone(zone).date() + timedelta(days=1):
            for local_slot in base._grid(day, provider_policy, zone):
                slot = local_slot.astimezone(UTC)
                if not (lower <= slot <= upper):
                    continue
                if any(abs((slot - occupied).total_seconds()) < 60 for occupied in active_times.get(provider, [])):
                    continue
                slots.append(slot)
            day += timedelta(days=1)

        provider_events = list(histories.get(provider, []))
        for slot in sorted(set(slots)):
            local_day = slot.astimezone(zone).date()
            if inputs is not None:
                counts = inputs["day_counts"][provider][local_day.isoformat()]
                day_total, lane_counts = counts["total"], dict(counts["lanes"])
            else:
                day_total, lane_counts = base._day_counts(provider, local_day, zone)
            same_day_planned = [
                item for item in planned
                if item["provider"] == provider and base._parse_dt(item["run_at"]).astimezone(zone).date() == local_day
            ]
            day_total += len(same_day_planned)
            for item in same_day_planned:
                lane_counts[str(item["lane"])] += 1
            if day_total >= base._daily_limit(provider_policy):
                continue
            chosen = _choose_diverse(
                provider_candidates,
                provider_policy=provider_policy,
                current_lanes=lane_counts,
                slot=slot,
                events=provider_events,
                diversity=diversity,
                zone=zone,
            )
            if chosen is None:
                break
            provider_candidates.remove(chosen)
            item = {
                **chosen,
                "run_at": slot.replace(microsecond=0).isoformat().replace("+00:00", "Z"),
                "display_timezone": str(effective["timezone"]),
            }
            planned.append(item)
            provider_events.append({
                "at": slot,
                "campaign": chosen["campaign"],
                "project": chosen["project"],
                "family": chosen["family"],
                "topic_key": chosen["topic_key"],
            })
            provider_events.sort(key=lambda event: event["at"])

        capacity[provider] = {
            **(inputs["capacity"][provider] if inputs is not None else base.daily_capacity(provider, now=now_dt, policy=effective)),
            "eligible_unscheduled": len(by_provider.get(provider, [])),
            "planned_in_horizon": sum(1 for item in planned if item["provider"] == provider),
            "daily_target": int(provider_policy.get("daily_target", 0)),
            "daily_ceiling": base._daily_limit(provider_policy),
            "flow_mode": base._flow_mode(provider_policy),
            "reply_target": int((effective.get("reply_targets") or {}).get(provider, 0)),
        }
        from ocpf_post.release_pacing import capacity_fields
        capacity[provider].update(capacity_fields(effective, provider_policy, int(capacity[provider].get("daily_budget_used", 0))))

    return {
        "generated_at": now_dt.replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "horizon_minutes": horizon,
        "timezone": str(effective["timezone"]),
        "plan": sorted(planned, key=lambda item: (item["run_at"], item["provider"], item["campaign"])),
        "capacity": capacity,
        "diversity": diversity,
        "reply_boundary": "Reply targets are tracked separately. post-once does not invent or auto-send conversational replies.",
    }
