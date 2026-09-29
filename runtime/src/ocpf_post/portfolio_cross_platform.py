from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

import ocpf_post.portfolio as base
import ocpf_post.portfolio_diversity as diversity

UTC = timezone.utc

DEFAULT_CROSS_PLATFORM: dict[str, Any] = {
    "topic_cooldown_minutes": 90,
}

_PROVIDER_ORDER = {"x": 0, "threads": 1, "linkedin": 2}


def _cross_policy(policy: dict[str, Any]) -> dict[str, Any]:
    raw = policy.get("cross_platform") if isinstance(policy.get("cross_platform"), dict) else {}
    result = {**DEFAULT_CROSS_PLATFORM, **raw}
    minutes = int(result["topic_cooldown_minutes"])
    if minutes < 0 or minutes > 720:
        raise base.PortfolioError("cross_platform.topic_cooldown_minutes must be between 0 and 720")
    result["topic_cooldown_minutes"] = minutes
    return result


def _at(item: dict[str, Any]) -> datetime:
    value = base._parse_dt(str(item.get("run_at") or ""))
    if value is None:
        raise base.PortfolioError("Planned item is missing run_at")
    return value


def _urgent(item: dict[str, Any]) -> bool:
    return str(item.get("lane")) == "development" and int(item.get("priority", 0)) >= 95


def _cross_topic_conflict(
    candidate: dict[str, Any], *, provider: str, slot: datetime,
    selected: list[dict[str, Any]], cooldown_minutes: int,
) -> bool:
    if cooldown_minutes <= 0:
        return False
    window = timedelta(minutes=cooldown_minutes)
    topic = str(candidate.get("topic_key") or "")
    if not topic:
        return False
    for item in selected:
        if str(item.get("provider")) == provider:
            continue
        if str(item.get("topic_key") or "") != topic:
            continue
        if abs(_at(item) - slot) < window:
            return True
    return False


def _replacement(
    original: dict[str, Any], *, slot: datetime, selected: list[dict[str, Any]],
    candidates: list[dict[str, Any]], reserved_originals: set[tuple[str, str]],
    used: set[tuple[str, str]], cooldown_minutes: int,
) -> dict[str, Any] | None:
    provider = str(original["provider"])
    lane = str(original["lane"])
    prior_same_provider = [item for item in selected if str(item.get("provider")) == provider and _at(item) < slot]
    last_project = str(prior_same_provider[-1].get("project") or "") if prior_same_provider else ""
    last_family = str(prior_same_provider[-1].get("family") or "") if prior_same_provider else ""

    pool = []
    for candidate in candidates:
        key = (str(candidate["campaign"]), str(candidate["provider"]))
        if str(candidate["provider"]) != provider or str(candidate["lane"]) != lane:
            continue
        if key in used or key in reserved_originals:
            continue
        if _cross_topic_conflict(
            candidate,
            provider=provider,
            slot=slot,
            selected=selected,
            cooldown_minutes=cooldown_minutes,
        ):
            continue
        pool.append(candidate)
    if not pool:
        return None

    def score(candidate: dict[str, Any]) -> tuple[int, int, int, str]:
        project_bonus = 1 if str(candidate.get("project") or "") != last_project else 0
        family_bonus = 1 if str(candidate.get("family") or "") != last_family else 0
        return (
            int(candidate.get("priority", 0)),
            project_bonus,
            family_bonus,
            str(candidate.get("campaign") or ""),
        )

    return max(pool, key=score)


def plan_refill(*, now: datetime | None = None, horizon_minutes: int | None = None, policy: dict[str, Any] | None = None, inputs: dict[str, Any] | None = None) -> dict[str, Any]:
    now_dt = (now or base._utc_now()).astimezone(UTC)
    effective = base.validate_policy(policy or base.load_policy())
    if effective.get("selection", "legacy") == "fair":
        from ocpf_post.portfolio_queue import capture_inputs, fair_plan
        horizon = int(horizon_minutes or effective.get("horizon_minutes", 75))
        frozen = inputs if inputs is not None else capture_inputs(now_dt, horizon, effective)
        return fair_plan(frozen, effective)
    from ocpf_post.account_profiles import profiles
    if inputs is None and profiles():
        from ocpf_post.portfolio_queue import capture_inputs
        inputs = capture_inputs(now_dt, int(horizon_minutes or effective.get("horizon_minutes", 75)), effective)
    if inputs and inputs.get("account_partitions"):
        from ocpf_post.portfolio_queue import merge_partitions
        return merge_partitions(plan_refill(now=now_dt, horizon_minutes=horizon_minutes, policy=effective,
                               inputs={**inputs, "account_partitions": {}}), inputs)
    cross = _cross_policy(effective)
    kwargs = {"inputs": inputs} if inputs is not None else {}
    result = diversity.plan_refill(now=now_dt, horizon_minutes=horizon_minutes, policy=effective, **kwargs)
    cooldown = int(cross["topic_cooldown_minutes"])
    if cooldown <= 0 or not result.get("plan"):
        return {**result, "cross_platform": cross}

    candidates = inputs["candidates"] if inputs is not None else [diversity._enrich(item) for item in base.delivery_candidates(now=now_dt)]
    originals = [dict(item) for item in result["plan"]] if inputs is not None else [diversity._enrich(dict(item)) for item in result["plan"]]
    reserved_originals = {(str(item["campaign"]), str(item["provider"])) for item in originals}
    used: set[tuple[str, str]] = set()
    selected: list[dict[str, Any]] = []
    replacements: list[dict[str, Any]] = []

    ordered = sorted(
        originals,
        key=lambda item: (_at(item), _PROVIDER_ORDER.get(str(item.get("provider")), 99), str(item.get("campaign"))),
    )
    for item in ordered:
        provider = str(item["provider"])
        slot = _at(item)
        chosen = item
        if not _urgent(item) and _cross_topic_conflict(
            item,
            provider=provider,
            slot=slot,
            selected=selected,
            cooldown_minutes=cooldown,
        ):
            alternative = _replacement(
                item,
                slot=slot,
                selected=selected,
                candidates=candidates,
                reserved_originals=reserved_originals,
                used=used,
                cooldown_minutes=cooldown,
            )
            if alternative is not None:
                chosen = {
                    **alternative,
                    "run_at": item["run_at"],
                    "display_timezone": item.get("display_timezone"),
                }
                replacements.append({
                    "provider": provider,
                    "run_at": item["run_at"],
                    "replaced_campaign": item["campaign"],
                    "replacement_campaign": alternative["campaign"],
                    "reason": "cross-platform topic cooldown",
                })
        used.add((str(chosen["campaign"]), provider))
        selected.append(chosen)

    selected.sort(key=lambda item: (item["run_at"], item["provider"], item["campaign"]))
    return {
        **result,
        "plan": selected,
        "cross_platform": cross,
        "cross_platform_replacements": replacements,
    }
