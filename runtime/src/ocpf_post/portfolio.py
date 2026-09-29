from __future__ import annotations

import json
import math
import os
from datetime import date, datetime, time, timedelta, timezone
from importlib.resources import files
from pathlib import Path
from typing import Any, Callable, Iterable
from zoneinfo import ZoneInfo

from ocpf_post import release_pacing
from ocpf_post.campaigns import builtin_manifest, builtin_text, campaign_ids, normalize_campaign_id
from ocpf_post.scheduler import ACTIVE_STATUSES, ScheduleError, cancel_schedule, create_schedule, schedule_records
from ocpf_post.state import config_dir, ensure_private_dir, iter_receipts, state_dir, write_private_json

UTC = timezone.utc
LANES = {"development", "commercial", "evergreen"}
TERMINAL_RECEIPT_STATUSES = {"published_verified", "published_unverified", "ambiguous_effect", "partial_effect"}

DEFAULT_POLICY: dict[str, Any] = {
    "schema_version": 1,
    "timezone": "Europe/London",
    "horizon_minutes": 75,
    "minimum_lead_minutes": 8,
    "planner_interval_minutes": 15,
    "providers": {
        "x": {"daily_target": 20, "flow_mode": "admission", "hard_daily_ceiling": 100,
              "window_start": "07:00", "window_end": "23:00", "minimum_spacing_minutes": 5,
              "development_max": 6, "commercial_min": 8},
        "threads": {"daily_target": 20, "flow_mode": "admission", "hard_daily_ceiling": 100,
                    "window_start": "07:00", "window_end": "23:00", "minimum_spacing_minutes": 5,
                    "development_max": 6, "commercial_min": 8},
        "linkedin": {"daily_target": 6, "flow_mode": "admission", "hard_daily_ceiling": 100,
                     "window_start": "08:00", "window_end": "20:00", "minimum_spacing_minutes": 5,
                     "development_max": 2, "commercial_min": 3},
    },
    "reply_targets": {"x": 10, "threads": 10, "linkedin": 10},
}


class PortfolioError(RuntimeError):
    pass


def _utc_now() -> datetime:
    return datetime.now(tz=UTC)


def _parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError as exc:
        raise PortfolioError(f"Invalid datetime: {value}") from exc
    if parsed.tzinfo is None:
        raise PortfolioError(f"Datetime must include an offset: {value}")
    return parsed.astimezone(UTC)


def _parse_hhmm(value: str) -> time:
    try:
        hour, minute = value.split(":", 1)
        return time(int(hour), int(minute))
    except (ValueError, AttributeError) as exc:
        raise PortfolioError(f"Invalid HH:MM time: {value}") from exc


def policy_file() -> Path:
    override = os.environ.get("OCPF_POST_PORTFOLIO_POLICY")
    return Path(override).expanduser() if override else config_dir() / "portfolio-policy.json"


def validate_policy(policy: dict[str, Any]) -> dict[str, Any]:
    if policy.get("selection", "legacy") not in {"legacy", "fair"}:
        raise PortfolioError("selection must be legacy or fair")
    if policy.get("schema_version") != 1:
        raise PortfolioError("Unsupported portfolio policy schema")
    try:
        ZoneInfo(str(policy["timezone"]))
    except Exception as exc:
        raise PortfolioError("Portfolio policy has an invalid timezone") from exc
    providers = policy.get("providers")
    if not isinstance(providers, dict) or not providers:
        raise PortfolioError("Portfolio policy providers must be a non-empty object")
    for provider, raw in providers.items():
        if provider not in {"x", "threads", "linkedin"} or not isinstance(raw, dict):
            raise PortfolioError(f"Unsupported provider policy: {provider}")
        target = int(raw.get("daily_target", 0))
        if target < 0 or target > 100:
            raise PortfolioError(f"{provider} daily_target must be between 0 and 100")
        flow_mode = str(raw.get("flow_mode", "fixed"))
        if flow_mode not in {"fixed", "admission"}:
            raise PortfolioError(f"{provider} flow_mode must be fixed or admission")
        ceiling = int(raw.get("hard_daily_ceiling", target))
        if ceiling < 0 or ceiling > 100:
            raise PortfolioError(f"{provider} hard_daily_ceiling must be between 0 and 100")
        if flow_mode == "admission" and ceiling < 1:
            raise PortfolioError(f"{provider} admission flow requires a positive hard_daily_ceiling")
        spacing = int(raw.get("minimum_spacing_minutes", 0))
        if spacing < 0 or spacing > 1440:
            raise PortfolioError(f"{provider} minimum_spacing_minutes must be between 0 and 1440")
        _parse_hhmm(str(raw.get("window_start", "")))
        _parse_hhmm(str(raw.get("window_end", "")))
        development_max = int(raw.get("development_max", 0))
        commercial_min = int(raw.get("commercial_min", 0))
        if not 0 <= development_max <= target:
            raise PortfolioError(f"{provider} development_max must fit daily_target")
        if not 0 <= commercial_min <= target:
            raise PortfolioError(f"{provider} commercial_min must fit daily_target")
    try:
        release_pacing.validate(policy)
    except ValueError as exc:
        raise PortfolioError(str(exc)) from exc
    return policy


def load_policy(*, effective: bool = True) -> dict[str, Any]:
    path = policy_file()
    if not path.exists():
        return validate_policy(json.loads(json.dumps(DEFAULT_POLICY)))
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise PortfolioError(f"Cannot read portfolio policy {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise PortfolioError("Portfolio policy must be a JSON object")
    value = validate_policy(value)
    # Owner/default accounts migrate in-memory from quota-as-target to
    # admission-driven flow. The persisted policy remains reversible and
    # additional account profiles keep their own explicit reviewed budgets.
    flow_spacing = {"x": 5, "threads": 5, "linkedin": 5}
    for provider, raw in value.get("providers", {}).items():
        if "flow_mode" not in raw:
            raw["flow_mode"] = "admission"
            raw["hard_daily_ceiling"] = 100
            raw.setdefault("minimum_spacing_minutes", flow_spacing.get(provider, 15))
    value = validate_policy(value)
    if effective and not release_pacing.enabled(value):
        from ocpf_post.capacity_experiment import overlay
        value = overlay(value)
    return value


def write_default_policy(*, force: bool = False) -> Path:
    path = policy_file()
    if path.exists() and not force:
        raise PortfolioError(f"Policy already exists: {path}")
    write_private_json(path, json.loads(json.dumps(DEFAULT_POLICY)))
    return path


def _campaign_ids() -> list[str]:
    return campaign_ids()


def _allocation(manifest: dict[str, Any]) -> dict[str, Any] | None:
    raw = manifest.get("allocation")
    return raw if isinstance(raw, dict) else None


def _copy_ready(manifest: dict[str, Any]) -> bool:
    return str(manifest.get("status") or "").strip().upper() == "COPY-READY"


def _eligible_manifest(manifest: dict[str, Any], *, now: datetime) -> tuple[bool, str]:
    allocation = _allocation(manifest)
    if not allocation or allocation.get("enabled") is not True:
        return False, "not opted into portfolio allocation"
    if not _copy_ready(manifest):
        return False, f"campaign status is {manifest.get('status') or 'unknown'}"
    lane = str(allocation.get("lane") or "").strip().lower()
    if lane not in LANES:
        return False, f"invalid lane {lane or 'missing'}"
    prepared_at = _parse_dt(str(allocation.get("prepared_at") or "") or None)
    if prepared_at and prepared_at > now:
        return False, "prepared_at is in the future"
    expires_at = _parse_dt(str(allocation.get("expires_at") or "") or None)
    if expires_at and expires_at <= now:
        return False, "campaign freshness expired"
    superseded_by = str(allocation.get("superseded_by") or "").strip()
    if superseded_by:
        return False, f"superseded by {superseded_by}"
    from ocpf_post.source_guard import guard as source_guard
    source_reason = source_guard(manifest, now=now)
    if source_reason:
        return False, source_reason
    return True, "eligible"


def delivery_candidates(*, now: datetime | None = None, exclusions: list | None = None) -> list[dict[str, Any]]:
    from ocpf_post.copy_guard import campaign_copy_key, occupied_copies
    now_dt = (now or _utc_now()).astimezone(UTC)
    schedules, receipts = list(schedule_records()), list(iter_receipts())
    active = {(str(r.get("campaign")), str(r.get("provider"))) for r in schedules if r.get("status") in ACTIVE_STATUSES}
    terminal = {(str(r.get("campaign")), str(r.get("provider"))) for r in receipts if r.get("status") in TERMINAL_RECEIPT_STATUSES}
    occupied = occupied_copies(schedules, receipts, ACTIVE_STATUSES)
    out: list[dict[str, Any]] = []
    account_unavailable: dict[tuple[str, str], str | None] = {}
    def exclude(campaign, provider, reason, matching=None):
        if exclusions is not None:
            exclusions.append({"campaign": campaign, "provider": provider, "reason": reason, "matching": matching})
    for campaign in _campaign_ids():
        manifest = builtin_manifest(campaign)
        eligible, reason = _eligible_manifest(manifest, now=now_dt)
        allocation = _allocation(manifest) or {}
        providers = manifest.get("providers")
        if not isinstance(providers, list):
            continue
        for provider_raw in providers:
            provider = str(provider_raw).strip().lower()
            if not eligible:
                exclude(campaign, provider, reason)
                continue
            text = builtin_text(campaign, provider) if provider in {"x", "threads", "linkedin"} else None
            if not text:
                exclude(campaign, provider, "payload unavailable or failed integrity check")
                continue
            from ocpf_post.vault_sync import guard
            vault_reason = guard(manifest, provider, now=now_dt)
            if vault_reason:
                exclude(campaign, provider, vault_reason)
                continue
            key = (campaign, provider)
            if key in active or key in terminal:
                exclude(campaign, provider, "existing active schedule" if key in active else "existing terminal or ambiguous receipt")
                continue
            copy_key = campaign_copy_key(campaign, provider, text)
            account_id = str(copy_key[1]) if copy_key else None
            from ocpf_post.generated_supply_guard import delivery_guard as generated_delivery_guard
            generated_reason = generated_delivery_guard(manifest, provider, account_id, text)
            if generated_reason:
                exclude(campaign, provider, generated_reason)
                continue
            from ocpf_post.account_profiles import unavailable
            if copy_key:
                account_key = (provider, str(copy_key[1]))
                if account_key not in account_unavailable:
                    account_unavailable[account_key] = unavailable(provider, copy_key[1], now=now_dt)
                account_reason = account_unavailable[account_key]
            else:
                account_reason = None
            if account_reason:
                exclude(campaign, provider, account_reason)
                continue
            if copy_key and copy_key in occupied:
                exclude(campaign, provider, "identical copy already reserved or recorded", occupied[copy_key])
                continue
            out.append({"campaign": campaign, "project": str(manifest.get("project") or ""), "title": str(manifest.get("title") or campaign), "provider": provider, "lane": str(allocation["lane"]).strip().lower(), "priority": int(allocation.get("priority", 50)), "prepared_at": allocation.get("prepared_at"), "expires_at": allocation.get("expires_at"), "eligibility": reason, "copy_key": copy_key})
    # One representative per account/payload in a planning snapshot. This also
    # prevents two newly generated IDs from competing for separate slots.
    selected, seen = [], {}
    for item in sorted(out, key=lambda item: (-int(item["priority"]), item["campaign"], item["provider"])):
        key = item.pop("copy_key")
        item["account_id"] = key[1] if key else None
        item["text_sha256"] = key[2] if key else None
        if key and key in seen:
            exclude(item["campaign"], item["provider"], "identical copy represented by another eligible campaign", seen[key])
            continue
        if key:
            seen[key] = {"campaign": item["campaign"], "provider": item["provider"]}
        selected.append(item)
    return selected


def _flow_mode(provider_policy: dict[str, Any]) -> str:
    return str(provider_policy.get("flow_mode", "fixed"))


def _daily_limit(provider_policy: dict[str, Any]) -> int:
    if _flow_mode(provider_policy) == "admission":
        return int(provider_policy.get("hard_daily_ceiling", 100))
    return int(provider_policy.get("daily_target", 0))


def _development_limit(provider_policy: dict[str, Any]) -> int:
    configured = int(provider_policy.get("development_max", 0))
    if _flow_mode(provider_policy) != "admission":
        return configured
    baseline = max(1, int(provider_policy.get("daily_target", 1)))
    ceiling = _daily_limit(provider_policy)
    # Preserve the reviewed development share as throughput grows instead of
    # turning the higher overall ceiling into permission for CI/event spam.
    return min(ceiling, math.ceil(ceiling * configured / baseline))


def daily_capacity(provider: str, *, now: datetime, policy: dict[str, Any]) -> dict[str, Any]:
    zone = ZoneInfo(str(policy["timezone"]))
    day = now.astimezone(zone).date()
    used, _ = _day_counts(provider, day, zone)
    settings = policy["providers"][provider]
    limit = _daily_limit(settings)
    mode = _flow_mode(settings)
    legacy_target = int(settings.get("daily_target", 0))
    return {"local_day": day.isoformat(), "daily_budget_used": used,
            "daily_budget_remaining": max(0, limit - used),
            "daily_target_met": used >= legacy_target,
            "daily_ceiling_met": used >= limit,
            "daily_ceiling": limit, "flow_mode": mode,
            "legacy_mix_target": legacy_target,
            "legacy_target_remaining": max(0, legacy_target - used),
            "budget_scope": (
                "Admission-driven logical-publication safety ceiling; active reservations and terminal effects count"
                if mode == "admission"
                else "Fixed automatic-allocation budget; active reservations and terminal effects count"
            ), **release_pacing.capacity_fields(policy, settings, used)}


def _day_window(day: date, provider_policy: dict[str, Any], zone: ZoneInfo) -> tuple[datetime, datetime]:
    start_local = datetime.combine(day, _parse_hhmm(str(provider_policy["window_start"])), tzinfo=zone)
    end_local = datetime.combine(day, _parse_hhmm(str(provider_policy["window_end"])), tzinfo=zone)
    if end_local <= start_local:
        end_local += timedelta(days=1)
    return start_local, end_local


def _grid(day: date, provider_policy: dict[str, Any], zone: ZoneInfo) -> list[datetime]:
    limit = _daily_limit(provider_policy)
    if limit <= 0:
        return []
    start, end = _day_window(day, provider_policy, zone)
    if limit == 1:
        return [start]
    # Admission flow spreads the hard-ceiling opportunities across the whole
    # provider window. The rolling planner only reserves its near-term horizon,
    # so unused opportunities disappear naturally instead of becoming a quota
    # that later cycles must fill.
    span = (end - start).total_seconds()
    return [start + timedelta(seconds=span * index / (limit - 1)) for index in range(limit)]


def _local_day(value: str | None, zone: ZoneInfo) -> date | None:
    parsed = _parse_dt(value)
    return parsed.astimezone(zone).date() if parsed else None


def _manifest_lane(campaign: str) -> str | None:
    allocation = _allocation(builtin_manifest(campaign))
    if not allocation:
        return None
    lane = str(allocation.get("lane") or "").strip().lower()
    return lane if lane in LANES else None


def _day_counts(provider: str, day: date, zone: ZoneInfo, account_id=None) -> tuple[int, dict[str, int]]:
    from ocpf_post.account_profiles import matches_scope
    total = 0
    lanes = {lane: 0 for lane in LANES}
    seen: set[tuple[str, str]] = set()
    for record in schedule_records():
        if not matches_scope(provider, record.get("account_id"), account_id):
            continue
        if record.get("provider") != provider or record.get("status") not in ACTIVE_STATUSES or _local_day(str(record.get("run_at") or ""), zone) != day:
            continue
        key = (str(record.get("campaign")), provider)
        if key in seen:
            continue
        seen.add(key); total += 1
        lane = _manifest_lane(key[0])
        if lane: lanes[lane] += 1
    for receipt in iter_receipts():
        if not matches_scope(provider, receipt.get("account_id"), account_id):
            continue
        if receipt.get("provider") != provider or receipt.get("status") not in TERMINAL_RECEIPT_STATUSES or _local_day(str(receipt.get("recorded_at") or ""), zone) != day:
            continue
        key = (str(receipt.get("campaign")), provider)
        if key in seen:
            continue
        seen.add(key); total += 1
        lane = _manifest_lane(key[0])
        if lane: lanes[lane] += 1
    return total, lanes


def _choose_candidate(candidates: list[dict[str, Any]], *, provider_policy: dict[str, Any], current_lanes: dict[str, int]) -> dict[str, Any] | None:
    if not candidates:
        return None
    urgent = [c for c in candidates if c["lane"] == "development" and int(c["priority"]) >= 95]
    if urgent and current_lanes["development"] < _development_limit(provider_policy):
        return urgent[0]
    target = max(1, _daily_limit(provider_policy))
    development_target = min(_development_limit(provider_policy), math.ceil(target * 0.30))
    commercial_target = max(int(provider_policy.get("commercial_min", 0)), math.ceil(target * 0.40))
    evergreen_target = max(0, target - development_target - commercial_target)
    desired = {"development": development_target, "commercial": commercial_target, "evergreen": evergreen_target}
    available_lanes = {c["lane"] for c in candidates}
    def deficit(lane: str) -> float:
        goal = desired[lane]
        return -1.0 if goal <= 0 else (goal - current_lanes[lane]) / goal
    ranked_lanes = sorted(available_lanes, key=lambda lane: (deficit(lane), lane == "commercial", lane == "development"), reverse=True)
    for lane in ranked_lanes:
        lane_candidates = [c for c in candidates if c["lane"] == lane]
        if lane_candidates:
            return lane_candidates[0]
    return candidates[0]


def plan_refill(*, now: datetime | None = None, horizon_minutes: int | None = None, policy: dict[str, Any] | None = None) -> dict[str, Any]:
    from ocpf_post.account_profiles import profiles
    if profiles():
        from ocpf_post.portfolio_cross_platform import plan_refill as scoped_plan
        return scoped_plan(now=now, horizon_minutes=horizon_minutes, policy=policy)
    now_dt = (now or _utc_now()).astimezone(UTC)
    effective = validate_policy(policy or load_policy())
    zone = ZoneInfo(str(effective["timezone"]))
    horizon = int(horizon_minutes or effective.get("horizon_minutes", 75))
    if horizon < 1 or horizon > 24 * 60:
        raise PortfolioError("horizon_minutes must be between 1 and 1440")
    lead = int(effective.get("minimum_lead_minutes", 8))
    lower = now_dt + timedelta(minutes=max(1, lead)); upper = now_dt + timedelta(minutes=horizon)
    candidates = delivery_candidates(now=now_dt)
    by_provider: dict[str, list[dict[str, Any]]] = {}
    for candidate in candidates: by_provider.setdefault(str(candidate["provider"]), []).append(candidate)
    active_times: dict[str, list[datetime]] = {}
    for record in schedule_records():
        if record.get("status") not in ACTIVE_STATUSES: continue
        try: run_at = _parse_dt(str(record.get("run_at") or ""))
        except PortfolioError: continue
        if run_at: active_times.setdefault(str(record.get("provider")), []).append(run_at)
    planned: list[dict[str, Any]] = []; capacity: dict[str, dict[str, Any]] = {}
    for provider, provider_policy_raw in effective["providers"].items():
        provider_policy = dict(provider_policy_raw); provider_candidates = list(by_provider.get(provider, [])); slots: list[datetime] = []
        day = lower.astimezone(zone).date()
        while day <= upper.astimezone(zone).date() + timedelta(days=1):
            for local_slot in _grid(day, provider_policy, zone):
                slot = local_slot.astimezone(UTC)
                if not (lower <= slot <= upper): continue
                if any(abs((slot - occupied).total_seconds()) < 60 for occupied in active_times.get(provider, [])): continue
                slots.append(slot)
            day += timedelta(days=1)
        for slot in sorted(set(slots)):
            local_day = slot.astimezone(zone).date(); day_total, lane_counts = _day_counts(provider, local_day, zone)
            same_day_planned = [item for item in planned if item["provider"] == provider and _parse_dt(item["run_at"]).astimezone(zone).date() == local_day]
            day_total += len(same_day_planned)
            for item in same_day_planned: lane_counts[str(item["lane"])] += 1
            if day_total >= _daily_limit(provider_policy): continue
            chosen = _choose_candidate(provider_candidates, provider_policy=provider_policy, current_lanes=lane_counts)
            if chosen is None: break
            provider_candidates.remove(chosen)
            planned.append({**chosen, "run_at": slot.replace(microsecond=0).isoformat().replace("+00:00", "Z"), "display_timezone": str(effective["timezone"])})
        capacity[provider] = {**daily_capacity(provider, now=now_dt, policy=effective), "eligible_unscheduled": len(by_provider.get(provider, [])), "planned_in_horizon": sum(1 for item in planned if item["provider"] == provider), "daily_target": int(provider_policy.get("daily_target", 0)), "daily_ceiling": _daily_limit(provider_policy), "reply_target": int((effective.get("reply_targets") or {}).get(provider, 0))}
    return {"generated_at": now_dt.replace(microsecond=0).isoformat().replace("+00:00", "Z"), "horizon_minutes": horizon, "timezone": str(effective["timezone"]), "plan": sorted(planned, key=lambda item: (item["run_at"], item["provider"], item["campaign"])), "capacity": capacity, "reply_boundary": "Reply targets are tracked separately. post-once does not invent or auto-send conversational replies."}


def allocation_events_file() -> Path:
    return state_dir() / "portfolio-allocation-events.jsonl"


def _append_allocation_event(event: dict[str, Any]) -> None:
    path = allocation_events_file(); ensure_private_dir(path.parent)
    line = json.dumps(event, separators=(",", ":"), ensure_ascii=False) + "\n"
    fd = os.open(path, os.O_APPEND | os.O_CREAT | os.O_WRONLY, 0o600)
    try:
        os.write(fd, line.encode("utf-8")); os.fsync(fd)
    finally: os.close(fd)


def iter_allocation_events() -> Iterable[dict[str, Any]]:
    path = allocation_events_file()
    if not path.exists(): return []
    out: list[dict[str, Any]] = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        if not raw.strip(): continue
        try: value = json.loads(raw)
        except json.JSONDecodeError: continue
        if isinstance(value, dict): out.append(value)
    return out


def allocator_schedule_ids() -> set[str]:
    return {str(event["schedule_id"]) for event in iter_allocation_events() if event.get("event") == "scheduled" and event.get("schedule_id")}


def reconcile_allocator_schedules(*, now: datetime | None = None) -> list[dict[str, Any]]:
    now_dt = (now or _utc_now()).astimezone(UTC); owned = allocator_schedule_ids(); actions: list[dict[str, Any]] = []
    for record in schedule_records():
        schedule_id = str(record.get("schedule_id") or "")
        if schedule_id not in owned or record.get("status") != "scheduled": continue
        campaign = str(record.get("campaign") or ""); eligible, reason = _eligible_manifest(builtin_manifest(campaign), now=now_dt)
        if eligible: continue
        cancelled = cancel_schedule(schedule_id, now=now_dt)
        _append_allocation_event({"event": "freshness_cancelled", "schedule_id": schedule_id, "campaign": campaign, "provider": record.get("provider"), "recorded_at": now_dt.replace(microsecond=0).isoformat().replace("+00:00", "Z"), "reason": reason})
        actions.append({**cancelled, "portfolio_reason": reason})
    return actions


def _apply_refill(*, now: datetime | None = None, horizon_minutes: int | None = None, policy: dict[str, Any] | None = None,
                 schedule_creator: Callable[..., dict[str, Any]] = create_schedule) -> dict[str, Any]:
    from ocpf_post.copy_guard import campaign_copy_key, occupied_copies
    now_dt = (now or _utc_now()).astimezone(UTC); cancelled = reconcile_allocator_schedules(now=now_dt); plan = plan_refill(now=now_dt, horizon_minutes=horizon_minutes, policy=policy)
    scheduled: list[dict[str, Any]] = []; errors: list[dict[str, Any]] = []
    for item in plan["plan"]:
        text = builtin_text(str(item["campaign"]), str(item["provider"]))
        key = campaign_copy_key(str(item["campaign"]), str(item["provider"]), text) if text else None
        occupied = occupied_copies(list(schedule_records()), list(iter_receipts()), ACTIVE_STATUSES)
        if key and item.get("account_id") and key[1] != item["account_id"]:
            errors.append({**item, "error": "Destination changed after planning"})
            continue
        if key and key in occupied:
            errors.append({**item, "error": "Identical copy was reserved or recorded after planning"})
            continue
        try:
            record = schedule_creator(campaign=str(item["campaign"]), provider=str(item["provider"]), at=str(item["run_at"]), timezone_name=str(plan["timezone"]), now=now_dt)
        except ScheduleError as exc:
            errors.append({**item, "error": str(exc)}); continue
        _append_allocation_event({"event": "scheduled", "schedule_id": record["schedule_id"], "account_id": record.get("account_id"), "campaign": item["campaign"], "provider": item["provider"], "lane": item["lane"], "priority": item["priority"], "run_at": record["run_at"], "recorded_at": now_dt.replace(microsecond=0).isoformat().replace("+00:00", "Z")})
        scheduled.append(record)
    return {**plan, "cancelled_for_freshness": cancelled, "scheduled": scheduled, "errors": errors}


def apply_refill(*, now: datetime | None = None, horizon_minutes: int | None = None, policy: dict[str, Any] | None = None,
                 schedule_creator: Callable[..., dict[str, Any]] = create_schedule) -> dict[str, Any]:
    from ocpf_post.queue_watch import safe_observe
    observed_at = (now or _utc_now()).astimezone(UTC)
    # Remember eligible work before it leaves the queue, then record actual
    # ledger outcomes even if reservation creation fails. Monitoring cannot
    # authorise publication or prevent the existing allocator from running.
    safe_observe(now=observed_at, policy=policy, evaluate=False)
    try:
        with release_pacing.policy_lock(policy_file()):
            result = _apply_refill(now=observed_at, horizon_minutes=horizon_minutes, policy=policy,
                                   schedule_creator=schedule_creator)
    finally:
        monitoring = safe_observe(now=observed_at if now else _utc_now(), policy=policy)
    return {**result, "queue_watch": monitoring}


def portfolio_status(*, now: datetime | None = None, policy: dict[str, Any] | None = None) -> dict[str, Any]:
    now_dt = (now or _utc_now()).astimezone(UTC)
    effective = validate_policy(policy or load_policy())
    candidates = delivery_candidates(now=now_dt)
    active = [record for record in schedule_records() if record.get("status") in ACTIVE_STATUSES]
    owned = allocator_schedule_ids()
    providers = tuple(effective["providers"])
    horizon_minutes = int(effective.get("horizon_minutes", 75))
    horizon_end = now_dt + timedelta(minutes=horizon_minutes)

    def run_at(record: dict[str, Any]) -> datetime | None:
        try:
            return _parse_dt(str(record.get("run_at") or ""))
        except PortfolioError:
            return None

    active_by_provider = {
        provider: sum(1 for record in active if record.get("provider") == provider)
        for provider in providers
    }
    allocator_active_by_provider = {
        provider: sum(
            1 for record in active
            if record.get("provider") == provider and str(record.get("schedule_id")) in owned
        )
        for provider in providers
    }
    active_in_refill_horizon_by_provider = {
        provider: sum(
            1
            for record in active
            if record.get("provider") == provider
            and (scheduled_at := run_at(record)) is not None
            and now_dt <= scheduled_at <= horizon_end
        )
        for provider in providers
    }
    next_active_run_at_by_provider = {}
    for provider in providers:
        future = sorted(
            scheduled_at
            for record in active
            if record.get("provider") == provider
            and (scheduled_at := run_at(record)) is not None
            and scheduled_at >= now_dt
        )
        next_active_run_at_by_provider[provider] = (
            future[0].replace(microsecond=0).isoformat().replace("+00:00", "Z") if future else None
        )

    from ocpf_post.account_profiles import profiles
    account_targets = {}
    account_rows = profiles()
    for identity, row in account_rows.items():
        account_targets[identity] = {
            "enabled": row["enabled"],
            **release_pacing.scoped_settings(effective, row),
        }

    return {
        "account_targets": account_targets,
        "release_pacing": release_pacing.summary(effective, account_rows),
        "generated_at": now_dt.replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "policy_file": str(policy_file()),
        "eligible_deliveries": len(candidates),
        "eligible_by_lane": {lane: sum(1 for item in candidates if item["lane"] == lane) for lane in LANES},
        "eligible_by_provider": {
            provider: sum(1 for item in candidates if item["provider"] == provider)
            for provider in providers
        },
        "active_schedules": len(active),
        "active_schedules_by_provider": active_by_provider,
        "active_schedules_in_refill_horizon_by_provider": active_in_refill_horizon_by_provider,
        "next_active_run_at_by_provider": next_active_run_at_by_provider,
        "allocator_owned_active": sum(
            1 for record in active if str(record.get("schedule_id")) in owned
        ),
        "allocator_owned_active_by_provider": allocator_active_by_provider,
        "refill_horizon_minutes": horizon_minutes,
        "targets": {
            provider: {
                "flow_mode": _flow_mode(raw),
                "posts_per_day": int(raw.get("daily_target", 0)),
                "hard_daily_ceiling": (release_pacing.safety_ceiling(raw) if release_pacing.enabled(effective) else _daily_limit(raw)),
                "daily_release_limit": int(raw["daily_target"]) if release_pacing.enabled(effective) else None,
                "replies_per_day": int((effective.get("reply_targets") or {}).get(provider, 0)),
            }
            for provider, raw in effective["providers"].items()
        },
        "reply_boundary": "Replies remain a separate engagement workflow; this allocator only reserves approved campaign posts.",
    }
