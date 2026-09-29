"""Durable editorial demand reconciliation and receipt-linked response evidence.

This layer never publishes, reserves, changes provider/account authority or rewrites
approved copy. It turns the existing read-only editorial workpack into stable
per-route request identities and projects already persisted audience evidence back
to the editor.
"""
from __future__ import annotations

from collections import Counter
from datetime import datetime, timedelta, timezone
import hashlib
import json
import math
from typing import Any

from ocpf_post import local_store
from ocpf_post.state import state_dir

UTC = timezone.utc
CADENCE_HOURS = 4
EXPIRY_HORIZON_HOURS = 48
MIN_STOCK_FLOOR = 2
MAX_STOCK_FLOOR = 8
RESERVE_TARGET_DAYS = 7
RESERVE_FALLBACK_TRIGGER_DAYS = 5
RESERVE_EMERGENCY_DAYS = 2
PUBLICATION_RATE_LOOKBACK_DAYS = 7
MAX_RESERVE_REQUEST_ITEMS = 24
MAX_REQUESTS = 500
MAX_AUDIENCE_RECORDS = 160
DEFAULT_MARKET_COLD_HOURS = 48
MIN_MARKET_COLD_HOURS = 12
MAX_MARKET_COLD_HOURS = 168


def _stamp(value: datetime) -> str:
    return value.astimezone(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _at(value: Any) -> datetime:
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("Editorial evidence timestamp requires an offset")
    return parsed.astimezone(UTC)


def _digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False,
    ).encode()).hexdigest()


def path():
    return state_dir() / "editorial-continuity.json"


def assessment_path():
    return state_dir() / "editorial-assessments.json"


def _window_hours(policy: dict[str, Any]) -> float:
    def minute(value: Any) -> int:
        hour, minute_value = str(value).split(":", 1)
        result = int(hour) * 60 + int(minute_value)
        if not 0 <= result < 1440:
            raise ValueError("Invalid provider publishing window")
        return result
    start, end = minute(policy.get("window_start")), minute(policy.get("window_end"))
    if end <= start:
        end += 1440
    return max(1.0, (end - start) / 60)


def route_policy(provider: str, account_id: str) -> dict[str, Any]:
    from ocpf_post.portfolio import load_policy
    raw = dict(load_policy()["providers"][provider])
    try:
        from ocpf_post.account_profiles import profiles
        for profile in profiles().values():
            if (profile.get("provider"), str(profile.get("account_id"))) != (provider, str(account_id)):
                continue
            if profile.get("enabled") is not True or not isinstance(profile.get("policy"), dict):
                continue
            raw.update(profile["policy"])
            break
    except (OSError, ValueError, KeyError, TypeError):
        pass
    return raw


def stock_floor(provider: str, account_id: str, policy: dict[str, Any] | None = None) -> int:
    """Enough unreserved stock to span one four-hour editorial cadence.

    This is a replenishment signal only. It neither raises provider targets nor
    creates a minimum posting requirement.
    """
    raw = policy or route_policy(provider, account_id)
    target = int(raw.get("daily_target", 0))
    if target <= 0:
        return MIN_STOCK_FLOOR
    floor = math.ceil(target * CADENCE_HOURS / _window_hours(raw))
    return max(MIN_STOCK_FLOOR, min(MAX_STOCK_FLOOR, floor))


def market_cold_hours(provider: str, account_id: str, policy: dict[str, Any] | None = None) -> int:
    """Route-specific continuity horizon; a visibility signal, never a posting quota."""
    raw = policy or route_policy(provider, account_id)
    value = raw.get("market_cold_hours", DEFAULT_MARKET_COLD_HOURS)
    try:
        hours = int(value)
    except (TypeError, ValueError):
        hours = DEFAULT_MARKET_COLD_HOURS
    return max(MIN_MARKET_COLD_HOURS, min(MAX_MARKET_COLD_HOURS, hours))


def route_publication_rates(*, now: datetime | None = None,
                            lookback_days: int = PUBLICATION_RATE_LOOKBACK_DAYS) -> dict[tuple[str, str, str], float]:
    """Observed logical publications/day by exact project/provider/account route.

    This is descriptive consumption evidence, not a target. The reserve controller
    never increases publication authority from this rate.
    """
    from collections import Counter
    from ocpf_post.campaigns import builtin_manifest
    from ocpf_post.performance_review import publications

    now = now or datetime.now(UTC)
    if type(lookback_days) is not int or not 1 <= lookback_days <= 30:
        raise ValueError("lookback_days must be between 1 and 30")
    cutoff = now - timedelta(days=lookback_days)
    counts: Counter[tuple[str, str, str]] = Counter()
    for publication in publications().values():
        if not isinstance(publication, dict):
            continue
        at_value = publication.get("at")
        receipt = publication.get("receipt") if isinstance(publication.get("receipt"), dict) else {}
        if not isinstance(at_value, datetime) or not cutoff <= at_value <= now:
            continue
        campaign = str(receipt.get("campaign") or "")
        provider = str(receipt.get("provider") or "")
        account_id = str(receipt.get("account_id") or "")
        try:
            project = str(builtin_manifest(campaign).get("project") or "")
        except (OSError, ValueError, KeyError, TypeError):
            project = ""
        if all((project, provider, account_id)):
            counts[(project, provider, account_id)] += 1
    return {key: round(count / lookback_days, 3) for key, count in counts.items()}


def _configured_account_route_counts() -> Counter[tuple[str, str]]:
    """Configured project routes per physical destination, including LinkedIn Pages."""
    from ocpf_post.portfolio_source_loader import merged_source_profiles
    from ocpf_post.source_routes import routes as source_routes

    counts: Counter[tuple[str, str]] = Counter()
    try:
        profiles = merged_source_profiles().get("projects") or {}
    except (OSError, ValueError, KeyError, TypeError):
        return counts
    if not isinstance(profiles, dict):
        return counts
    for project, raw in profiles.items():
        if not isinstance(raw, dict):
            continue
        try:
            configured = source_routes(str(project), raw)
        except (OSError, ValueError, KeyError, TypeError):
            continue
        for route in configured:
            account_id = str(route.get("account_id") or "")
            provider = str(route.get("provider") or "")
            if provider and account_id:
                counts[(provider, account_id)] += 1
    return counts


def reserve_rate_plan(
    route_keys: list[tuple[str, str, str]],
    observed_rates: dict[tuple[str, str, str], float],
) -> dict[tuple[str, str, str], dict[str, Any]]:
    """Choose the inventory-protection rate without turning a burst into future demand.

    Unmarked policies preserve the historical observed-consumption controller.
    Under the owner-reviewed steady-originals policy, each account's saved daily
    release amount is divided across its configured project routes. Observed rate
    remains evidence, but no longer enlarges reserve demand.
    """
    from ocpf_post import release_pacing
    from ocpf_post.portfolio import load_policy

    unique = sorted(set(route_keys))
    try:
        policy = load_policy()
    except (OSError, ValueError, KeyError, TypeError):
        policy = {}

    steady = isinstance(policy, dict) and release_pacing.enabled(policy)
    grouped: dict[tuple[str, str], list[tuple[str, str, str]]] = {}
    for key in unique:
        grouped.setdefault((key[1], key[2]), []).append(key)
    configured_counts = _configured_account_route_counts() if steady else Counter()

    result: dict[tuple[str, str, str], dict[str, Any]] = {}
    for (provider, account_id), keys in grouped.items():
        if steady:
            settings = route_policy(provider, account_id)
            daily_limit = max(0, int(settings.get("daily_target", 0) or 0))
            route_count = max(1, len(keys), int(configured_counts.get((provider, account_id), 0) or 0))
            protection_rate = daily_limit / route_count
            basis = "steady_saved_daily_share"
        else:
            daily_limit = None
            route_count = len(keys)
            protection_rate = 0.0
            basis = "observed_history"

        for key in keys:
            observed = max(0.0, float(observed_rates.get(key, 0.0) or 0.0))
            rate = protection_rate if steady else observed
            result[key] = {
                "observed_daily_rate": round(observed, 3),
                "reserve_daily_rate": round(rate, 6),
                "reserve_rate_basis": basis,
                "steady_account_daily_limit": daily_limit,
                "steady_account_route_count": route_count if steady else None,
            }
    return result


def reserve_stock_floor(
    provider: str,
    account_id: str,
    rate: dict[str, Any],
    fallback_floor: int,
) -> int:
    """Immediate route floor aligned with steady account pacing when activated."""
    if rate.get("reserve_rate_basis") != "steady_saved_daily_share":
        return fallback_floor
    settings = route_policy(provider, account_id)
    route_rate = max(0.0, float(rate.get("reserve_daily_rate", 0.0) or 0.0))
    if route_rate <= 0:
        return 0
    floor = math.ceil(route_rate * CADENCE_HOURS / _window_hours(settings))
    return max(1, min(MAX_STOCK_FLOOR, floor))


def reserve_thresholds(daily_rate: float, immediate_floor: int) -> dict[str, Any]:
    rate = max(0.0, float(daily_rate or 0.0))
    immediate = max(0, int(immediate_floor))
    if rate <= 0:
        return {
            "observed_daily_rate": 0.0,
            "target_items": immediate,
            "fallback_trigger_items": immediate,
            "emergency_items": 0,
            "rate_observed": False,
        }
    return {
        "observed_daily_rate": round(rate, 3),
        "target_items": max(immediate, math.ceil(rate * RESERVE_TARGET_DAYS)),
        "fallback_trigger_items": max(immediate, math.ceil(rate * RESERVE_FALLBACK_TRIGGER_DAYS)),
        "emergency_items": max(1, math.ceil(rate * RESERVE_EMERGENCY_DAYS)),
        "rate_observed": True,
    }


def reserve_state(*, runnable: int, reserved: int, expiries: list[datetime],
                  daily_rate: float, immediate_floor: int, now: datetime,
                  reserve_daily_rate: float | None = None,
                  reserve_rate_basis: str = "observed_history",
                  steady_account_daily_limit: int | None = None,
                  steady_account_route_count: int | None = None) -> dict[str, Any]:
    observed_rate = max(0.0, float(daily_rate or 0.0))
    protection_rate = observed_rate if reserve_daily_rate is None else max(0.0, float(reserve_daily_rate))
    thresholds = reserve_thresholds(protection_rate, immediate_floor)
    protection_available = protection_rate > 0
    fallback_horizon = now + timedelta(days=RESERVE_FALLBACK_TRIGGER_DAYS)
    expiring_before_fallback = sum(now <= value < fallback_horizon for value in expiries)
    surviving = max(0, runnable - expiring_before_fallback)
    available = surviving + reserved
    runway = round(available / protection_rate, 3) if protection_rate > 0 else None
    immediate_deficit = max(0, immediate_floor - (runnable + reserved))
    editorial_refill_required = bool(
        protection_available and available < thresholds["target_items"]
    )
    fallback_required = bool(
        immediate_deficit
        or (
            protection_available
            and available < thresholds["fallback_trigger_items"]
        )
    )
    if available == 0 and not protection_available and immediate_floor <= 0:
        status = "inactive"
    elif available == 0:
        status = "empty"
    elif protection_available and available < thresholds["emergency_items"]:
        status = "emergency"
    elif fallback_required:
        status = "fallback"
    elif editorial_refill_required:
        status = "watch"
    else:
        status = "healthy"
    target_deficit = max(0, int(thresholds["target_items"]) - available)
    return {
        **thresholds,
        # Preserve descriptive history separately from the rate that sizes stock.
        "observed_daily_rate": round(observed_rate, 3),
        "reserve_daily_rate": round(protection_rate, 6),
        "reserve_rate_basis": reserve_rate_basis,
        "rate_observed": observed_rate > 0,
        "reserve_rate_available": protection_available,
        "steady_account_daily_limit": steady_account_daily_limit,
        "steady_account_route_count": steady_account_route_count,
        "status": status,
        "reserve_status": status,
        "runnable": runnable,
        "reserved": reserved,
        "expiring_before_fallback_horizon": expiring_before_fallback,
        "surviving_reserve_items": surviving,
        "reserve_available_items": available,
        "reserve_runway_days": runway,
        "target_days": RESERVE_TARGET_DAYS,
        "fallback_trigger_days": RESERVE_FALLBACK_TRIGGER_DAYS,
        "emergency_days": RESERVE_EMERGENCY_DAYS,
        "editorial_refill_required": editorial_refill_required,
        "fallback_required": fallback_required,
        "target_deficit": target_deficit,
        "immediate_deficit": immediate_deficit,
        "boundary": (
            "Supply reserve only. Under steady originals, saved daily release pace sizes "
            "inventory protection while observed consumption remains descriptive evidence; "
            "neither rate raises publication authority, quotas or provider authority."
        ),
    }

def portfolio_reserve(profiles: dict[str, dict[str, Any]], *, now: datetime | None = None) -> list[dict[str, Any]]:
    """Project reserve for configured profiles with one scan of shared local evidence.

    Delivery candidates, publication rates and schedule history are portfolio-wide
    inputs. Read them once, then project each project/provider/account route. This
    keeps console/status inspection proportional to portfolio evidence rather than
    multiplying full-ledger scans by the number of projects.
    """
    from ocpf_post.campaigns import builtin_manifest
    from ocpf_post.portfolio import delivery_candidates
    from ocpf_post.scheduler import schedule_records

    now = now or datetime.now(UTC)
    candidates = delivery_candidates(now=now)
    rates = route_publication_rates(now=now)
    schedules = schedule_records()

    candidate_counts: Counter[tuple[str, str, str]] = Counter()
    expiries: dict[tuple[str, str, str], list[datetime]] = {}
    for row in candidates:
        key = (
            str(row.get("project") or ""),
            str(row.get("provider") or ""),
            str(row.get("account_id") or ""),
        )
        candidate_counts[key] += 1
        raw_expiry = row.get("expires_at")
        if raw_expiry:
            try:
                expiries.setdefault(key, []).append(_at(raw_expiry))
            except (ValueError, TypeError):
                pass

    scheduled: Counter[tuple[str, str, str]] = Counter()
    for row in schedules:
        if row.get("status") != "scheduled":
            continue
        try:
            run_at = _at(row.get("run_at"))
        except (ValueError, TypeError):
            continue
        if run_at < now:
            continue
        campaign = str(row.get("campaign") or "")
        try:
            scheduled_project = str(builtin_manifest(campaign).get("project") or "")
        except (OSError, ValueError, KeyError, TypeError):
            scheduled_project = ""
        key = (
            scheduled_project,
            str(row.get("provider") or ""),
            str(row.get("account_id") or ""),
        )
        scheduled[key] += 1

    from ocpf_post.account_profiles import credential_present, profile as account_profile
    from ocpf_post.source_routes import routes as source_routes

    resolved_routes: list[tuple[str, str, str, dict[str, Any], int, str, bool, bool]] = []
    for project, raw in sorted(profiles.items()):
        if not isinstance(raw, dict):
            continue
        try:
            configured = source_routes(project, raw)
        except (OSError, ValueError, KeyError, TypeError):
            resolved_routes.append((project, "", "", raw, 0, "", False, False))
            continue
        for route in configured:
            provider = str(route["provider"])
            account_id = str(route["account_id"])
            try:
                account_row = account_profile(provider, account_id)
                available = (
                    True
                    if account_row is None
                    else bool(account_row.get("enabled")) and credential_present(account_row)
                )
            except (OSError, ValueError, RuntimeError, KeyError, TypeError):
                available = False
            resolved_routes.append((
                project,
                provider,
                account_id,
                raw,
                stock_floor(provider, account_id),
                str(route["alias"]),
                bool(route.get("default")),
                available,
            ))

    rate_plan = reserve_rate_plan(
        [(project, provider, account_id)
         for project, provider, account_id, _raw, _floor, _alias, _default, available in resolved_routes
         if account_id and available],
        rates,
    )

    rows: list[dict[str, Any]] = []
    failed_projects: set[str] = set()
    for project, provider, account_id, raw, floor, alias, default_route, available in resolved_routes:
        if project in failed_projects:
            continue
        if not account_id:
            rows.append({"project": project, "status": "unavailable"})
            failed_projects.add(project)
            continue
        key = (project, provider, account_id)
        rate = rate_plan.get(key, {
            "observed_daily_rate": rates.get(key, 0.0),
            "reserve_daily_rate": rates.get(key, 0.0),
            "reserve_rate_basis": "observed_history",
            "steady_account_daily_limit": None,
            "steady_account_route_count": None,
        })
        floor = reserve_stock_floor(provider, account_id, rate, floor)
        reserve = reserve_state(
            runnable=candidate_counts[key],
            reserved=scheduled[key],
            expiries=expiries.get(key, []),
            daily_rate=rate["observed_daily_rate"],
            reserve_daily_rate=rate["reserve_daily_rate"],
            reserve_rate_basis=str(rate["reserve_rate_basis"]),
            steady_account_daily_limit=rate["steady_account_daily_limit"],
            steady_account_route_count=rate["steady_account_route_count"],
            immediate_floor=floor,
            now=now,
        )
        rows.append({
            "project": project,
            "provider": provider,
            "account_id": account_id,
            "destination_alias": alias,
            "default_route": default_route,
            "destination_available": available,
            "stock_floor": floor,
            **reserve,
            **({"status": "unavailable", "reserve_status": "unavailable"} if not available else {}),
        })
    return rows


def profile_reserve(profile: dict[str, Any], *, now: datetime | None = None) -> list[dict[str, Any]]:
    """Compatibility wrapper for projecting one configured source profile."""
    project = str(profile.get("project") or "")
    if not project:
        return []
    return portfolio_reserve({project: profile}, now=now)


def route_activity(*, now: datetime | None = None) -> dict[tuple[str, str, str], dict[str, Any]]:
    """Project recent publication and future reservation continuity from local evidence."""
    from ocpf_post.performance_review import publications
    from ocpf_post.source_receipts import publication_inputs
    from ocpf_post.scheduler import schedule_records

    now = now or datetime.now(UTC)
    manifests, _, _ = publication_inputs()
    grouped: dict[tuple[str, str, str], dict[str, Any]] = {}

    for publication in publications().values():
        receipt = publication.get("receipt") if isinstance(publication, dict) else {}
        campaign = str(receipt.get("campaign") or "")
        provider = str(receipt.get("provider") or "")
        account = str(receipt.get("account_id") or "")
        project = str((manifests.get(campaign) or {}).get("project") or "")
        at_value = publication.get("at") if isinstance(publication, dict) else None
        if not all((project, provider, account)) or not isinstance(at_value, datetime):
            continue
        key = (project, provider, account)
        row = grouped.setdefault(key, {
            "last_effect_at": None,
            "last_verified_publication_at": None,
            "next_scheduled_at": None,
            "scheduled_count": 0,
        })
        stamp = _stamp(at_value)
        if row["last_effect_at"] is None or _at(stamp) > _at(row["last_effect_at"]):
            row["last_effect_at"] = stamp
        if publication.get("effective_verified") is True:
            if row["last_verified_publication_at"] is None or _at(stamp) > _at(row["last_verified_publication_at"]):
                row["last_verified_publication_at"] = stamp

    for scheduled in schedule_records():
        if not isinstance(scheduled, dict) or scheduled.get("status") != "scheduled":
            continue
        campaign = str(scheduled.get("campaign") or "")
        provider = str(scheduled.get("provider") or "")
        account = str(scheduled.get("account_id") or "")
        project = str((manifests.get(campaign) or {}).get("project") or "")
        raw_run_at = scheduled.get("run_at")
        if not all((project, provider, account)) or not raw_run_at:
            continue
        try:
            run_at = _at(raw_run_at)
        except (TypeError, ValueError):
            continue
        if run_at < now:
            continue
        key = (project, provider, account)
        row = grouped.setdefault(key, {
            "last_effect_at": None,
            "last_verified_publication_at": None,
            "next_scheduled_at": None,
            "scheduled_count": 0,
        })
        row["scheduled_count"] += 1
        if row["next_scheduled_at"] is None or run_at < _at(row["next_scheduled_at"]):
            row["next_scheduled_at"] = _stamp(run_at)
    return grouped


def _condition(kind: str, status: str, reason: str, message: str, observed_at: str) -> dict[str, Any]:
    return {
        "type": kind,
        "status": status,
        "reason": reason,
        "message": message,
        "observed_at": observed_at,
    }


def candidate_expiries(*, now: datetime | None = None) -> dict[tuple[str, str, str], list[datetime]]:
    """Read currently eligible candidate expiries; no source/provider collection."""
    from ocpf_post.portfolio import delivery_candidates
    now = now or datetime.now(UTC)
    grouped: dict[tuple[str, str, str], list[datetime]] = {}
    for candidate in delivery_candidates(now=now):
        key = (str(candidate.get("project") or ""), str(candidate.get("provider") or ""),
               str(candidate.get("account_id") or ""))
        if not all(key):
            continue
        raw = candidate.get("expires_at")
        if not raw:
            continue
        try:
            expiry = _at(raw)
        except (ValueError, TypeError):
            continue
        grouped.setdefault(key, []).append(expiry)
    return grouped


def demand(workpack: dict[str, Any], *, now: datetime | None = None,
           expiries: dict[tuple[str, str, str], list[datetime]] | None = None,
           activity: dict[tuple[str, str, str], dict[str, Any]] | None = None,
           rates: dict[tuple[str, str, str], float] | None = None,
           policy_fn=stock_floor) -> dict[str, Any]:
    now = now or datetime.now(UTC)
    if workpack.get("status") != "observed" or type(workpack.get("schema_version")) is not int or workpack["schema_version"] != 1:
        raise ValueError("Complete editorial workpack required")
    if not 0 <= (now - _at(workpack["coverage_observed_at"])).total_seconds() <= 3600:
        raise ValueError("Fresh editorial workpack required")
    routes = workpack.get("route_supply")
    if not isinstance(routes, list):
        routes = [{**row, "publishing_intent": True, "runnable": 0, "reserved": 0}
                  for row in workpack.get("supply_reviews", [])]
    expiries = candidate_expiries(now=now) if expiries is None else expiries
    activity = route_activity(now=now) if activity is None else activity
    rates = route_publication_rates(now=now) if rates is None else rates
    observed_at = _stamp(now)
    route_keys = [
        (str(route.get("project") or ""), str(route.get("provider") or ""), str(route.get("account_id") or ""))
        for route in routes if isinstance(route, dict)
    ]
    rate_plan = reserve_rate_plan(
        [key for key in route_keys if all(key)],
        rates,
    )
    requests, route_states = [], []
    seen = set()
    for route in routes:
        if not isinstance(route, dict):
            raise ValueError("Invalid editorial route")
        project, provider, account = (str(route.get("project") or ""), str(route.get("provider") or ""),
                                      str(route.get("account_id") or ""))
        key = (project, provider, account)
        if not all(key) or key in seen or provider not in {"x", "threads", "linkedin"}:
            raise ValueError("Invalid or duplicate editorial route identity")
        seen.add(key)
        intent = bool(route.get("publishing_intent"))
        runnable = route.get("runnable", 0)
        reserved = route.get("reserved", 0)
        if type(runnable) is not int or type(reserved) is not int or runnable < 0 or reserved < 0:
            raise ValueError("Invalid editorial stock count")
        route_expiries = sorted(expiries.get(key, []))
        expiring = sum(now <= value <= now + timedelta(hours=EXPIRY_HORIZON_HOURS) for value in route_expiries)
        surviving = max(0, runnable - expiring)
        rate = rate_plan.get(key, {
            "observed_daily_rate": rates.get(key, 0.0),
            "reserve_daily_rate": rates.get(key, 0.0),
            "reserve_rate_basis": "observed_history",
            "steady_account_daily_limit": None,
            "steady_account_route_count": None,
        })
        floor = reserve_stock_floor(provider, account, rate, policy_fn(provider, account))
        reserve = reserve_state(
            runnable=runnable,
            reserved=reserved,
            expiries=route_expiries,
            daily_rate=rate["observed_daily_rate"],
            reserve_daily_rate=rate["reserve_daily_rate"],
            reserve_rate_basis=str(rate["reserve_rate_basis"]),
            steady_account_daily_limit=rate["steady_account_daily_limit"],
            steady_account_route_count=rate["steady_account_route_count"],
            immediate_floor=floor,
            now=now,
        )
        continuity = activity.get(key, {}) if isinstance(activity.get(key, {}), dict) else {}
        horizon_hours = market_cold_hours(provider, account)
        last_effect_at = continuity.get("last_effect_at")
        next_scheduled_at = continuity.get("next_scheduled_at")
        try:
            hours_since_last_effect = (
                max(0.0, (now - _at(last_effect_at)).total_seconds() / 3600)
                if last_effect_at else None
            )
        except (TypeError, ValueError):
            hours_since_last_effect = None
        try:
            next_schedule = _at(next_scheduled_at) if next_scheduled_at else None
        except (TypeError, ValueError):
            next_schedule = None
        schedule_within_horizon = bool(
            next_schedule and now <= next_schedule <= now + timedelta(hours=horizon_hours)
        )
        market_cold = bool(
            intent
            and not schedule_within_horizon
            and (hours_since_last_effect is None or hours_since_last_effect >= horizon_hours)
        )
        runway_hours = round((runnable + reserved) * CADENCE_HOURS / max(1, floor), 3)
        surviving_runway_hours = round((surviving + reserved) * CADENCE_HOURS / max(1, floor), 3)
        reasons = []
        if intent and runnable == 0:
            reasons.append("no_unreserved_inventory")
        if intent and runnable < floor:
            reasons.append("below_editorial_cadence_floor")
        if intent and expiring and surviving < floor:
            reasons.append("expiry_horizon_would_break_floor")
        if market_cold and runnable + reserved == 0:
            reasons.append("market_presence_cold")
        if intent and reserve["editorial_refill_required"]:
            reasons.append("reserve_runway_below_target")
        suggested = max(0, floor - surviving)
        if intent and reserve["editorial_refill_required"]:
            suggested = max(suggested, int(reserve["target_deficit"]))
        supply_ready = intent and surviving + reserved >= floor
        expiry_safe = not intent or not expiring or surviving + reserved >= floor
        continuity_ready = not intent or not market_cold
        conditions = [
            _condition(
                "SupplyReady", "True" if supply_ready else "False",
                "CadenceFloorMet" if supply_ready else "BelowCadenceFloor",
                "Usable accepted/reserved stock spans the editorial cadence floor."
                if supply_ready else "Usable accepted/reserved stock is below the editorial cadence floor.",
                observed_at,
            ),
            _condition(
                "ExpirySafe", "True" if expiry_safe else "False",
                "SurvivesExpiryHorizon" if expiry_safe else "ExpiryBreaksCadenceFloor",
                "Near-term expiry does not break the current cadence floor."
                if expiry_safe else "Near-term expiry would leave the route below its cadence floor.",
                observed_at,
            ),
            _condition(
                "MarketContinuity", "True" if continuity_ready else "False",
                "RecentOrScheduledPresence" if continuity_ready else "MarketCold",
                "A recent provider effect or near-term schedule maintains market continuity."
                if continuity_ready else "No recent provider effect or near-term schedule is visible inside the continuity horizon.",
                observed_at,
            ),
            _condition(
                "SupplyReserve",
                "Unknown" if not reserve["reserve_rate_available"] else "True" if not reserve["editorial_refill_required"] else "False",
                "ReserveRateUnavailable" if not reserve["reserve_rate_available"]
                else "ReserveTargetMet" if not reserve["editorial_refill_required"] else "ReserveBelowTarget",
                "No reserve sizing rate is available; the immediate cadence floor remains authoritative."
                if not reserve["reserve_rate_available"]
                else "Accepted/scheduled inventory meets the configured multi-day reserve target."
                if not reserve["editorial_refill_required"]
                else "The multi-day supply reserve is below its configured protection target.",
                observed_at,
            ),
        ]
        state = {
            "project": project, "provider": provider, "account_id": account,
            "aliases": list(route.get("aliases") or []), "publishing_intent": intent,
            "runnable": runnable, "reserved": reserved, "stock_floor": floor,
            "editorial_runway_hours": runway_hours,
            "surviving_runway_hours": surviving_runway_hours,
            "expiring_within_48h": expiring,
            "earliest_runnable_expiry": _stamp(route_expiries[0]) if route_expiries else None,
            "surviving_after_48h": surviving,
            "reserve_status": reserve["status"],
            "observed_daily_publication_rate": reserve["observed_daily_rate"],
            "reserve_daily_rate": reserve["reserve_daily_rate"],
            "reserve_rate_basis": reserve["reserve_rate_basis"],
            "steady_account_daily_limit": reserve["steady_account_daily_limit"],
            "steady_account_route_count": reserve["steady_account_route_count"],
            "reserve_target_days": reserve["target_days"],
            "reserve_fallback_trigger_days": reserve["fallback_trigger_days"],
            "reserve_emergency_days": reserve["emergency_days"],
            "reserve_target_items": reserve["target_items"],
            "reserve_fallback_trigger_items": reserve["fallback_trigger_items"],
            "reserve_available_items": reserve["reserve_available_items"],
            "reserve_runway_days": reserve["reserve_runway_days"],
            "reserve_expiring_before_fallback_horizon": reserve["expiring_before_fallback_horizon"],
            "reserve_fallback_required": reserve["fallback_required"],
            "market_cold_hours": horizon_hours,
            "market_cold": market_cold,
            "hours_since_last_effect": round(hours_since_last_effect, 3) if hours_since_last_effect is not None else None,
            "last_effect_at": last_effect_at,
            "last_verified_publication_at": continuity.get("last_verified_publication_at"),
            "next_scheduled_at": next_scheduled_at,
            "scheduled_count": int(continuity.get("scheduled_count", 0) or 0),
            "conditions": conditions,
            "demand_reasons": reasons,
            "suggested_new_items": min(MAX_RESERVE_REQUEST_ITEMS, suggested),
            "source": route.get("source") if isinstance(route.get("source"), dict) else {},
            "vaults": route.get("vaults") if isinstance(route.get("vaults"), list) else [],
            "exclusion_counts": route.get("exclusion_counts") if isinstance(route.get("exclusion_counts"), dict) else {},
        }
        route_states.append(state)
        if reasons:
            requests.append({**state,
                "request": "reconcile_existing_batch_then_author_or_resolve_eligibility",
                "permission_to_replay_or_activate": False,
                "stock_floor_basis": (
                    "Immediate floor spans one existing four-hour editorial cadence and is not a posting quota; "
                    "the separate multi-day reserve follows the active reserve-rate basis and is not a posting quota."
                )})
    if len(requests) > MAX_REQUESTS:
        raise ValueError("Editorial demand exceeds bounded route count")
    return {"schema_version": 1, "status": "observed", "observed_at": _stamp(now),
            "requests": requests, "routes": route_states,
            "boundary": "Proactive editorial demand only. Low stock and expiry risk do not authorise publication, quota increases, expiry extension, replay or account substitution."}


def reconcile(workpack: dict[str, Any], *, now: datetime | None = None, apply: bool = False,
              expiries: dict[tuple[str, str, str], list[datetime]] | None = None,
              activity: dict[tuple[str, str, str], dict[str, Any]] | None = None,
              rates: dict[tuple[str, str, str], float] | None = None,
              policy_fn=stock_floor) -> dict[str, Any]:
    """Give each unresolved route one durable request identity until evidence changes."""
    now = now or datetime.now(UTC)
    projection = demand(
        workpack, now=now, expiries=expiries, activity=activity, rates=rates, policy_fn=policy_fn,
    )
    current = local_store.read(path()) or {"schema_version": 1, "routes": {}}
    if current.get("schema_version") != 1 or not isinstance(current.get("routes"), dict):
        raise ValueError("Invalid editorial continuity state")
    previous_routes = current["routes"]
    next_routes = dict(previous_routes)
    active_keys = set()
    resolved = []
    open_rows = []
    request_by_key = {(r["project"], r["provider"], r["account_id"]): r for r in projection["requests"]}
    all_route_keys = {(r["project"], r["provider"], r["account_id"]) for r in projection["routes"]}
    for key, request in request_by_key.items():
        route_key = _digest(list(key))[:24]
        active_keys.add(route_key)
        old = previous_routes.get(route_key, {})
        if old.get("status") == "open":
            generation = int(old.get("generation", 1))
            request_id = old.get("request_id") or f"edr-{route_key}-{generation}"
            first = old.get("first_requested_at") or _stamp(now)
            previous_ids = list(old.get("previous_request_ids") or [])[-8:]
        else:
            generation = int(old.get("generation", 0)) + 1
            request_id = f"edr-{route_key}-{generation}"
            first = _stamp(now)
            previous_ids = list(old.get("previous_request_ids") or [])
            if old.get("request_id"):
                previous_ids = (previous_ids + [old["request_id"]])[-8:]
        row = {**request, "route_key": route_key, "request_id": request_id,
               "generation": generation, "status": "open", "first_requested_at": first,
               "last_observed_at": _stamp(now), "previous_request_ids": previous_ids}
        next_routes[route_key] = row
        open_rows.append(row)
    for route_key, old in list(previous_routes.items()):
        if old.get("status") != "open" or route_key in active_keys:
            continue
        identity = (old.get("project"), old.get("provider"), old.get("account_id"))
        if identity not in all_route_keys:
            reason = "resolved_by_no_current_intent_or_route"
        else:
            reason = "resolved_by_observed_supply"
        row = {**old, "status": "resolved", "resolved_at": _stamp(now), "resolution": reason}
        next_routes[route_key] = row
        resolved.append({"request_id": old.get("request_id"), "route_key": route_key, "resolution": reason})
    state = {"schema_version": 1, "observed_at": _stamp(now), "routes": next_routes}
    if apply:
        with local_store.locked(path()):
            latest = local_store.read(path()) or {"schema_version": 1, "routes": {}}
            if latest != current:
                return reconcile(
                    workpack, now=now, apply=apply, expiries=expiries, activity=activity,
                    rates=rates, policy_fn=policy_fn,
                )
            local_store.write(path(), state)
    return {"schema_version": 1, "status": "observed", "observed_at": state["observed_at"],
            "open_request_count": len(open_rows), "open_requests": open_rows,
            "resolved_this_cycle": resolved, "apply": apply,
            "boundary": "Durable editorial request reconciliation only. One open request identity survives unchanged polls until route evidence resolves it; no campaign, schedule, approval or provider state is changed."}


def record_assessments(packet: dict[str, Any], reviewed: dict[str, Any], *, apply: bool = False,
                       expected_sha256: str | None = None, now: datetime | None = None) -> dict[str, Any]:
    """Persist exact-copy review identities without persisting the copy itself."""
    now = now or datetime.now(UTC)
    review_sha = _digest(packet)
    if apply and expected_sha256 != review_sha:
        raise ValueError("Assessment apply requires exact reviewed input hash")
    entries = {row["campaign"]: row for row in reviewed.get("entries", [])}
    if reviewed.get("status") != "review_records_valid" or not entries:
        raise ValueError("Validated review records required")
    projected = []
    for batch in packet.get("batches", []):
        project, account = str(batch.get("project") or ""), str(batch.get("account_id") or "")
        reviews = {row["campaign"]: row for row in batch.get("editorial_reviews", [])}
        for entry in batch.get("entries", []):
            campaign, provider = entry.get("campaign"), entry.get("provider")
            validated = entries.get(campaign)
            review = reviews.get(campaign)
            if not validated or not review or not project or not account:
                raise ValueError("Assessment identity mismatch")
            if review.get("audience_response") != "not_observed":
                raise ValueError("Editorial approval cannot pre-claim audience response")
            projected.append({"project": project, "campaign": campaign, "provider": provider,
                              "account_id": account, "payload_sha256": validated["text_sha256"],
                              "approval_sha256": entry.get("approval_sha256"),
                              "decision": review.get("decision"),
                              "assessment_sha256": _digest(review),
                              "audience_response_at_review": "not_observed",
                              "reviewed_at": packet.get("reviewed_at") or _stamp(now)})
    if apply:
        with local_store.locked(assessment_path()):
            state = local_store.read(assessment_path()) or {"schema_version": 1, "records": {}}
            if state.get("schema_version") != 1 or not isinstance(state.get("records"), dict):
                raise ValueError("Invalid editorial assessment state")
            for row in projected:
                key = _digest([row[k] for k in ("project", "campaign", "provider", "account_id")])[:32]
                old = state["records"].get(key)
                if old is not None and old != row:
                    raise ValueError("Existing exact-copy assessment cannot be reassigned")
                state["records"][key] = row
            local_store.write(assessment_path(), state)
    return {"schema_version": 1, "result": "recorded" if apply else "preview",
            "input_sha256": review_sha, "record_count": len(projected), "records": projected,
            "boundary": "Copy-bound editorial judgement only. The copy is not persisted here and audience response remains unobserved until independent receipt-linked evidence exists."}


def audience_evidence(*, now: datetime | None = None) -> dict[str, Any]:
    """Project response evidence by exact publication; never infer sentiment or causality."""
    from ocpf_post.performance_review import publications
    from ocpf_post.performance import iter_snapshots
    from ocpf_post.engagement import read as read_engagement
    from ocpf_post.business_outcomes import report as outcome_report
    from ocpf_post.source_receipts import publication_inputs

    now = now or datetime.now(UTC)
    manifests, _, _ = publication_inputs()
    assessments = local_store.read(assessment_path()) or {"records": {}}
    assessment_rows = list(assessments.get("records", {}).values()) if isinstance(assessments.get("records", {}), dict) else []
    assessment_index = {(r.get("campaign"), r.get("provider"), str(r.get("account_id")), r.get("payload_sha256")): r
                        for r in assessment_rows if isinstance(r, dict)}
    snapshots = list(iter_snapshots())
    engagement = read_engagement()
    outcomes = outcome_report().get("events", [])
    records = []
    for key, publication in publications().items():
        campaign, provider, account, post_id = map(str, key)
        if not (now - timedelta(days=7) <= publication["at"] <= now):
            continue
        receipt = publication["receipt"]
        project = (manifests.get(campaign) or {}).get("project")
        if not isinstance(project, str) or not project:
            continue
        exact_snapshots = [s for s in snapshots if tuple(str(s.get(k) or "") for k in ("campaign", "provider", "account_id", "post_id")) == key]
        safe_snapshots = []
        for row in sorted(exact_snapshots, key=lambda r: str(r.get("captured_at") or ""), reverse=True)[:12]:
            metrics = row.get("metrics") if isinstance(row.get("metrics"), dict) else {}
            safe_snapshots.append({"captured_at": row.get("captured_at"),
                                   "target_age_hours": row.get("target_age_hours") or row.get("comparable_target"),
                                   "availability": (row.get("availability") or {}).get("status") if isinstance(row.get("availability"), dict) else None,
                                   "metrics": {str(k): v for k, v in metrics.items() if v is None or type(v) in (int, float)}})
        inbox = [r for r in engagement.get("inbox", {}).values() if isinstance(r, dict)
                 and (str(r.get("campaign") or ""), str(r.get("provider") or ""), str(r.get("account_id") or ""), str(r.get("conversation_root_id") or "")) == (campaign, provider, account, post_id)]
        inbound_counts = dict(Counter(str(r.get("status") or "unknown") for r in inbox))
        max_depth = max((int(r.get("conversation_depth", 0) or 0) for r in inbox), default=0)
        exact_outcomes = [r for r in outcomes if isinstance(r, dict)
                          and tuple(str(r.get(k) or "") for k in ("campaign", "provider", "account_id", "post_id")) == key]
        outcome_counts = dict(Counter(str(r.get("event_type") or "unknown") for r in exact_outcomes))
        revenue = Counter()
        for row in exact_outcomes:
            if row.get("event_type") == "sale" and type(row.get("revenue_minor")) is int and isinstance(row.get("currency"), str):
                revenue[row["currency"]] += row["revenue_minor"]
        payload = receipt.get("text_sha256")
        assessment = assessment_index.get((campaign, provider, account, payload))
        any_response = bool(inbox or exact_outcomes or any(r.get("availability") == "available" for r in safe_snapshots))
        status = ("published_unverified" if not publication.get("effective_verified") else
                  "response_evidence_observed" if any_response else "published_verified_no_response_evidence_yet")
        records.append({"project": project, "campaign": campaign, "provider": provider,
                        "account_id": account, "post_id": post_id, "text_sha256": payload,
                        "published_at": _stamp(publication["at"]), "status": status,
                        "editorial_assessment_recorded": assessment is not None,
                        "assessment_sha256": assessment.get("assessment_sha256") if assessment else None,
                        "performance": safe_snapshots,
                        "inbound": {"observed_count": len(inbox), "status_counts": inbound_counts,
                                    "max_conversation_depth": max_depth},
                        "business_outcomes": {"event_counts": outcome_counts,
                                              "revenue_minor_by_currency": dict(revenue)}})
    records.sort(key=lambda r: r["published_at"], reverse=True)
    omitted = max(0, len(records) - MAX_AUDIENCE_RECORDS)
    return {"schema_version": 1, "status": "partial" if omitted else "observed",
            "observed_at": _stamp(now), "records": records[:MAX_AUDIENCE_RECORDS],
            "omitted_count": omitted,
            "boundary": "Receipt-linked descriptive evidence only. Inbound activity is not sentiment, numeric metrics are not recognition, owner-supplied outcomes are not independently verified attribution, and none of this automatically rewrites copy or allocation."}
