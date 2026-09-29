"""Bounded inventory admission/backpressure above the portfolio allocator.

The allocator decides which *accepted* item gets service. This module decides
whether ordinary source replenishment may add more accepted inventory at all.
It never deletes inventory, changes approvals/accounts, extends expiry, increases
posting targets, retries schedules or calls a social provider.
"""
from __future__ import annotations

import json
import os
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from ocpf_post import local_store
from ocpf_post.state import config_dir, state_dir

UTC = timezone.utc
SCHEMA_VERSION = 1
DEFAULT_POLICY: dict[str, Any] = {
    "schema_version": 1,
    "global_high_water": 300,
    "global_recovery_water": 180,
    "provider_high_water": {"x": 160, "threads": 160, "linkedin": 60},
    "provider_recovery_water": {"x": 100, "threads": 100, "linkedin": 36},
    "account_high_water": 140,
    "account_recovery_water": 90,
    "account_inventory_reserve": 3,
    "project_high_water": 120,
    "project_recovery_water": 70,
    "aged_high_water": 80,
    "aged_recovery_water": 30,
    "expiry_risk_hours": 24,
    "expiry_risk_high_water": 30,
    "expiry_risk_recovery_water": 10,
    "source_commit_observation_limit": 20,
}


class AdmissionError(RuntimeError):
    pass


def _stamp(value: datetime) -> str:
    return value.astimezone(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _at(value: Any) -> datetime:
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise AdmissionError("Admission state timestamp requires an offset")
    return parsed.astimezone(UTC)


def policy_file() -> Path:
    override = os.environ.get("OCPF_POST_ADMISSION_POLICY")
    return Path(override).expanduser() if override else config_dir() / "admission-policy.json"


def state_file() -> Path:
    return state_dir() / "admission-state.json"


def validate_policy(raw: dict[str, Any]) -> dict[str, Any]:
    if raw.get("schema_version") != 1:
        raise AdmissionError("Unsupported admission policy schema")
    allowed = set(DEFAULT_POLICY)
    if set(raw) - allowed:
        raise AdmissionError("Unknown admission policy field")
    policy = {**DEFAULT_POLICY, **raw}
    for group in ("provider_high_water", "provider_recovery_water"):
        value = policy[group]
        if not isinstance(value, dict) or set(value) != {"x", "threads", "linkedin"}:
            raise AdmissionError(f"{group} must define x, threads and linkedin")
        if any(type(v) is not int or v < 1 for v in value.values()):
            raise AdmissionError(f"{group} values must be positive integers")
    pairs = [
        ("global_recovery_water", "global_high_water"),
        ("account_recovery_water", "account_high_water"),
        ("project_recovery_water", "project_high_water"),
        ("aged_recovery_water", "aged_high_water"),
        ("expiry_risk_recovery_water", "expiry_risk_high_water"),
    ]
    for recovery, high in pairs:
        if type(policy[recovery]) is not int or type(policy[high]) is not int:
            raise AdmissionError(f"{recovery}/{high} must be integers")
        if not 0 <= policy[recovery] < policy[high]:
            raise AdmissionError(f"{recovery} must be lower than {high}")
    for provider in policy["provider_high_water"]:
        if not 0 < policy["provider_recovery_water"][provider] < policy["provider_high_water"][provider]:
            raise AdmissionError(f"Provider recovery must be below high-water for {provider}")
    if type(policy["expiry_risk_hours"]) is not int or not 1 <= policy["expiry_risk_hours"] <= 168:
        raise AdmissionError("expiry_risk_hours must be 1..168")
    if type(policy["source_commit_observation_limit"]) is not int or not 1 <= policy["source_commit_observation_limit"] <= 100:
        raise AdmissionError("source_commit_observation_limit must be 1..100")
    if type(policy["account_inventory_reserve"]) is not int or not 0 <= policy["account_inventory_reserve"] <= 20:
        raise AdmissionError("account_inventory_reserve must be 0..20")
    return policy


def load_policy() -> dict[str, Any]:
    path = policy_file()
    if not path.exists():
        return validate_policy(dict(DEFAULT_POLICY))
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise AdmissionError("Admission policy is unreadable") from exc
    if not isinstance(value, dict):
        raise AdmissionError("Admission policy must be an object")
    return validate_policy(value)


def _load_state() -> dict[str, Any]:
    path = state_file()
    if not path.exists():
        return {"schema_version": SCHEMA_VERSION, "mode": "open"}
    try:
        value = local_store.read(path)
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise AdmissionError("Admission state is unreadable; ordinary replenishment fails closed") from exc
    if value.get("mode") not in {"open", "paused"}:
        raise AdmissionError("Admission state has an invalid mode; ordinary replenishment fails closed")
    if value.get("observed_at"):
        _at(value["observed_at"])
    return value


def _queue_ages(candidates: list[dict[str, Any]], *, now: datetime) -> tuple[int, str]:
    from ocpf_post.queue_watch import identity, load, path as queue_path, DEFAULTS
    if not queue_path().exists():
        return 0, "not_observed"
    try:
        watch = load()
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise AdmissionError("Queue observation is corrupt; ordinary replenishment fails closed") from exc
    records = watch.get("records", {})
    aged = 0
    for candidate in candidates:
        record = records.get(identity(candidate))
        if not record or record.get("state") != "waiting":
            continue
        first = _at(record["first_eligible_at"])
        if now - first >= timedelta(hours=DEFAULTS["wait_warning_hours"]):
            aged += 1
    return aged, "observed" if watch.get("observed_at") else "not_observed"


def snapshot(*, now: datetime | None = None) -> dict[str, Any]:
    from ocpf_post.portfolio import delivery_candidates
    now = (now or datetime.now(UTC)).astimezone(UTC)
    candidates = delivery_candidates(now=now)
    by_provider = Counter(str(row.get("provider") or "") for row in candidates)
    by_project = Counter(str(row.get("project") or "") for row in candidates)
    by_account = Counter(str(row.get("account_id") or "unknown") for row in candidates)
    aged, queue_status = _queue_ages(candidates, now=now)
    expiry_cutoff = now + timedelta(hours=load_policy()["expiry_risk_hours"])
    expiry_risk = 0
    for row in candidates:
        value = row.get("expires_at")
        if not value:
            continue
        try:
            if _at(value) <= expiry_cutoff:
                expiry_risk += 1
        except AdmissionError:
            # Invalid freshness metadata is not permission to add more work.
            expiry_risk += 1
    return {
        "eligible_unreserved": len(candidates),
        "by_provider": dict(by_provider),
        "by_project": dict(by_project),
        "by_account": dict(by_account),
        "aged_count": aged,
        "expiry_risk_count": expiry_risk,
        "queue_observation": queue_status,
    }


def _below_recovery(metrics: dict[str, Any], policy: dict[str, Any]) -> bool:
    if metrics["eligible_unreserved"] > policy["global_recovery_water"]:
        return False
    if metrics["aged_count"] > policy["aged_recovery_water"] or metrics["expiry_risk_count"] > policy["expiry_risk_recovery_water"]:
        return False
    if any(count > policy["provider_recovery_water"].get(provider, 0)
           for provider, count in metrics["by_provider"].items() if provider in policy["provider_recovery_water"]):
        return False
    if any(count > policy["project_recovery_water"] for count in metrics["by_project"].values()):
        return False
    if any(count > policy["account_recovery_water"] for count in metrics["by_account"].values()):
        return False
    return True


def _high_reasons(metrics: dict[str, Any], policy: dict[str, Any], *, project: str | None = None) -> list[str]:
    reasons: list[str] = []
    if metrics["eligible_unreserved"] >= policy["global_high_water"]:
        reasons.append("global_high_water")
    if metrics["aged_count"] >= policy["aged_high_water"]:
        reasons.append("aged_high_water")
    if metrics["expiry_risk_count"] >= policy["expiry_risk_high_water"]:
        reasons.append("expiry_risk_high_water")
    for provider, count in metrics["by_provider"].items():
        high = policy["provider_high_water"].get(provider)
        if high is not None and count >= high:
            reasons.append(f"provider_high_water:{provider}")
    if project and metrics["by_project"].get(project, 0) >= policy["project_high_water"]:
        reasons.append(f"project_high_water:{project}")
    for account, count in metrics["by_account"].items():
        if count >= policy["account_high_water"]:
            reasons.append(f"account_high_water:{account}")
    return reasons


def _project(metrics: dict[str, Any], addition: dict[str, Any]) -> dict[str, Any]:
    result = {
        **metrics,
        "by_provider": dict(metrics["by_provider"]),
        "by_project": dict(metrics["by_project"]),
        "by_account": dict(metrics["by_account"]),
    }
    result["eligible_unreserved"] += int(addition.get("total", 0))
    for field in ("by_provider", "by_project", "by_account"):
        for key, count in (addition.get(field) or {}).items():
            result[field][key] = int(result[field].get(key, 0)) + int(count)
    # New ordinary material is not counted as already aged, but short-lived
    # inventory participates in expiry pressure when the caller can prove it.
    result["expiry_risk_count"] += int(addition.get("expiry_risk", 0))
    return result


def decide(*, project: str | None = None, projected_addition: dict[str, Any] | None = None,
           now: datetime | None = None, persist: bool = False) -> dict[str, Any]:
    now = (now or datetime.now(UTC)).astimezone(UTC)
    policy = load_policy()
    metrics = snapshot(now=now)
    try:
        state = _load_state()
    except AdmissionError as exc:
        return {"schema_version": 1, "admitted": False, "mode": "paused", "reasons": ["admission_state_unreadable"],
                "detail": str(exc), "metrics": metrics, "projected_metrics": metrics, "observed_at": _stamp(now),
                "boundary": "Fail-closed for ordinary source admission only; accepted inventory is untouched."}
    previous_at = _at(state["observed_at"]) if state.get("observed_at") else None
    if previous_at and now < previous_at:
        result = {"schema_version": 1, "admitted": False, "mode": "paused", "reasons": ["clock_regression"],
                  "metrics": metrics, "projected_metrics": metrics, "observed_at": _stamp(now),
                  "boundary": "Clock regression cannot grant ordinary inventory admission."}
        if persist:
            local_store.write(state_file(), {"schema_version": 1, "mode": "paused", "observed_at": _stamp(now), "reasons": result["reasons"]})
        return result
    projected = _project(metrics, projected_addition or {})
    if state.get("mode") == "paused" and not _below_recovery(metrics, policy):
        reasons = ["hysteresis_recovery_not_reached"]
        mode = "paused"
    else:
        reasons = _high_reasons(projected, policy, project=project)
        mode = "paused" if reasons else "open"
    admitted = not reasons and mode == "open"
    result = {
        "schema_version": 1,
        "admitted": admitted,
        "mode": mode,
        "reasons": reasons or ["within_admission_budget"],
        "metrics": metrics,
        "projected_metrics": projected,
        "observed_at": _stamp(now),
        "project": project,
        "boundary": "Admission controls ordinary source inventory only; it does not authorise scheduling or provider consequence.",
    }
    if persist:
        local_store.write(state_file(), {"schema_version": 1, "mode": mode, "observed_at": _stamp(now), "reasons": result["reasons"]})
    return result


def projection_from_preview(project: str, profile: dict[str, Any], preview: dict[str, Any], *, include_race_reserve: bool = True) -> dict[str, Any]:
    rows = (list(preview.get("static_campaigns") or []) + list(preview.get("event_campaigns") or [])
            + list(preview.get("generative_campaigns") or []))
    providers = [p for p in profile.get("providers", []) if p in {"x", "threads", "linkedin"}]
    by_provider = Counter()
    total = 0
    for row in rows:
        row_providers = row.get("providers") if isinstance(row, dict) else None
        use = [p for p in (row_providers or providers) if p in {"x", "threads", "linkedin"}]
        total += len(use)
        by_provider.update(use)
    # Preview and apply are two read-only GitHub observations. Reserve the bounded
    # maximum change between them so a moving source cannot jump a high-water mark.
    race = 0
    if include_race_reserve:
        static_max = len(profile.get("inventory") or []) * 3 * len(providers)
        event_max = (load_policy()["source_commit_observation_limit"] * len(providers)) if profile.get("event_enabled") is True else 0
        race = static_max + event_max
        total += race
        for provider in providers:
            by_provider[provider] += len(profile.get("inventory") or []) * 3
            if profile.get("event_enabled") is True:
                by_provider[provider] += load_policy()["source_commit_observation_limit"]
    by_account = Counter()
    try:
        from ocpf_post.registry import resolve_account
        for provider, count in by_provider.items():
            alias = (profile.get("destinations") or {}).get(provider)
            if alias:
                account = resolve_account(project, str(alias), expected_provider=provider)
                by_account[str(account["account_id"])] += count
    except (OSError, ValueError, KeyError, TypeError):
        # Missing destination proof is intentionally represented by an unknown
        # account bucket; decision policy can only become more conservative.
        by_account["unknown"] += total
    return {"total": total, "by_provider": dict(by_provider), "by_project": {project: total},
            "by_account": dict(by_account), "expiry_risk": 0, "race_reserve": race,
            "preview_delivery_additions": max(0, total - race)}


def controlled_refresh(*, apply: bool, project: str | None = None, now: datetime | None = None) -> dict[str, Any]:
    """Admission-aware source refresh used by production CLI/refill.

    Existing low-level replenisher functions remain consequence-free, but the
    production apply path goes through this gate project by project.
    """
    from ocpf_post.portfolio_source_loader import merged_source_profiles
    from ocpf_post.replenisher import refresh_sources
    now = (now or datetime.now(UTC)).astimezone(UTC)
    profiles = merged_source_profiles()["projects"]
    if project and project not in profiles:
        raise AdmissionError(f"Unknown source project: {project}")
    targets = [project] if project else sorted(profiles)
    result = {"schema_version": 1, "projects": [], "static_campaigns": [], "event_campaigns": [],
              "generative_campaigns": [], "admission": [], "boundary": "Ordinary source admission is pressure-gated before runtime campaign writes or source cursor advancement."}
    for project_id in targets:
        profile = profiles[project_id]
        current = decide(project=project_id, now=now, persist=apply)
        if apply and not current["admitted"]:
            result["projects"].append({"project": project_id, "status": "admission_paused", "reasons": current["reasons"]})
            result["admission"].append(current)
            continue
        preview = refresh_sources(apply=False, project=project_id, now=now)
        projection = projection_from_preview(project_id, profile, preview)
        gate = decide(project=project_id, projected_addition=projection, now=now, persist=apply)
        result["admission"].append({**gate, "projection": projection})
        if apply and not gate["admitted"]:
            result["projects"].append({"project": project_id, "status": "admission_paused", "reasons": gate["reasons"]})
            continue
        observed = refresh_sources(apply=apply, project=project_id, now=now) if apply else preview
        result["projects"].extend(observed.get("projects") or [])
        result["static_campaigns"].extend(observed.get("static_campaigns") or [])
        result["event_campaigns"].extend(observed.get("event_campaigns") or [])
        result["generative_campaigns"].extend(observed.get("generative_campaigns") or [])
    return result
