"""Reviewed, compare-and-swap mutation of one provider's portfolio policy.

This module changes future allocation authority only. It never creates schedules,
calls a provider, publishes, rewrites receipts, or edits experiment evidence.
"""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json

from ocpf_post import local_store
from ocpf_post.portfolio import PortfolioError, load_policy, policy_file, validate_policy

PROVIDERS = {"x", "threads", "linkedin"}
MUTABLE_FIELDS = ("daily_target", "development_max", "commercial_min", "hard_daily_ceiling", "minimum_spacing_minutes", "flow_mode")


def _digest(value: object) -> str:
    return hashlib.sha256(
        json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        ).encode("utf-8")
    ).hexdigest()


def _integer(value: int | None, field: str) -> int | None:
    if value is None:
        return None
    if type(value) is not int:
        raise PortfolioError(f"{field} must be an integer")
    return value


def _requested_changes(*, daily_target: int | None, development_max: int | None,
                       commercial_min: int | None, hard_daily_ceiling: int | None = None,
                       minimum_spacing_minutes: int | None = None,
                       flow_mode: str | None = None) -> dict:
    if flow_mode is not None and flow_mode not in {"fixed", "admission"}:
        raise PortfolioError("flow_mode must be fixed or admission")
    raw = {
        "daily_target": _integer(daily_target, "daily_target"),
        "development_max": _integer(development_max, "development_max"),
        "commercial_min": _integer(commercial_min, "commercial_min"),
        "hard_daily_ceiling": _integer(hard_daily_ceiling, "hard_daily_ceiling"),
        "minimum_spacing_minutes": _integer(minimum_spacing_minutes, "minimum_spacing_minutes"),
        "flow_mode": flow_mode,
    }
    changes = {key: value for key, value in raw.items() if value is not None}
    if not changes:
        raise PortfolioError("Provider policy patch requires at least one bounded field")
    return changes


def _trial_scope_impact(provider: str, proposed: dict) -> dict:
    """Describe whether a patch alters the active X/Threads trial authority scope."""
    try:
        from ocpf_post import capacity_experiment
        data = capacity_experiment.read()
    except (OSError, ValueError, KeyError, TypeError):
        return {"status": "unknown", "changes_active_trial_scope": None}
    if not data or data.get("status") != "active":
        return {"status": "not_active", "changes_active_trial_scope": False}
    if capacity_experiment._admission_flow(proposed):
        return {"status": "superseded", "changes_active_trial_scope": False}
    try:
        same = capacity_experiment.policy_matches_baseline(proposed, data)
    except (ValueError, KeyError, TypeError):
        return {"status": "unknown", "changes_active_trial_scope": None}
    return {
        "status": "active",
        "changes_active_trial_scope": not same,
        "controlled_providers": sorted(capacity_experiment.ACCOUNTS),
        "provider": provider,
    }


def preview_provider_patch(
    provider: str,
    *,
    daily_target: int | None = None,
    development_max: int | None = None,
    commercial_min: int | None = None,
    hard_daily_ceiling: int | None = None,
    minimum_spacing_minutes: int | None = None,
    flow_mode: str | None = None,
    policy: dict | None = None,
) -> dict:
    provider_name = str(provider or "").strip().lower()
    if provider_name not in PROVIDERS:
        raise PortfolioError(f"Unsupported provider policy: {provider}")
    changes = _requested_changes(
        daily_target=daily_target,
        development_max=development_max,
        commercial_min=commercial_min,
        hard_daily_ceiling=hard_daily_ceiling,
        minimum_spacing_minutes=minimum_spacing_minutes,
        flow_mode=flow_mode,
    )
    current = deepcopy(policy if policy is not None else load_policy(effective=False))
    validate_policy(current)
    if provider_name not in current.get("providers", {}):
        raise PortfolioError(f"Provider is not present in the current policy: {provider_name}")
    proposed = deepcopy(current)
    proposed["providers"][provider_name].update(changes)
    validate_policy(proposed)

    current_digest = _digest(current)
    proposed_digest = _digest(proposed)
    review_payload = {
        "schema_version": 1,
        "operation": "portfolio-provider-policy-patch",
        "provider": provider_name,
        "changes": changes,
        "current_policy_sha256": current_digest,
        "proposed_policy_sha256": proposed_digest,
    }
    impact = _trial_scope_impact(provider_name, proposed)
    return {
        "schema_version": 1,
        "kind": "provider_policy_patch",
        "apply": False,
        "result": "no_change" if current_digest == proposed_digest else "preview",
        "provider": provider_name,
        "changes": changes,
        "before": deepcopy(current["providers"][provider_name]),
        "after": deepcopy(proposed["providers"][provider_name]),
        "current_policy_sha256": current_digest,
        "proposed_policy_sha256": proposed_digest,
        "review_sha256": _digest(review_payload),
        "experiment_impact": impact,
        "policy_file": str(policy_file()),
        "boundary": (
            "Preview only. This is local future-allocation authority; no schedule, provider call, "
            "publication, receipt rewrite, retry, or experiment-state mutation occurred."
        ),
        "external_gate": (
            "Provider quota headroom is not inferred from local policy. For LinkedIn, inspect the "
            "Developer Portal Analytics quota before treating a higher local ceiling as operationally proven."
            if provider_name == "linkedin" else None
        ),
    }


def apply_provider_patch(
    provider: str,
    *,
    daily_target: int | None = None,
    development_max: int | None = None,
    commercial_min: int | None = None,
    hard_daily_ceiling: int | None = None,
    minimum_spacing_minutes: int | None = None,
    flow_mode: str | None = None,
    expected_sha256: str | None,
) -> dict:
    if not expected_sha256:
        raise PortfolioError("--apply requires --expected-sha256 from a fresh policy preview")
    destination = policy_file()
    with local_store.locked(destination):
        preview = preview_provider_patch(
            provider,
            daily_target=daily_target,
            development_max=development_max,
            commercial_min=commercial_min,
            hard_daily_ceiling=hard_daily_ceiling,
            minimum_spacing_minutes=minimum_spacing_minutes,
            flow_mode=flow_mode,
        )
        if preview["review_sha256"] != expected_sha256:
            raise PortfolioError("Policy changed since preview; expected review SHA-256 does not match")
        if preview["experiment_impact"].get("changes_active_trial_scope") is True:
            raise PortfolioError(
                "Patch changes the active X/Threads capacity-trial scope; stop or separately reconcile that trial first"
            )
        current = load_policy(effective=False)
        proposed = deepcopy(current)
        proposed["providers"][preview["provider"]].update(preview["changes"])
        validate_policy(proposed)
        if _digest(proposed) != preview["proposed_policy_sha256"]:
            raise PortfolioError("Policy changed while applying the reviewed patch")
        if preview["result"] != "no_change":
            local_store.write(destination, proposed)
        persisted = load_policy(effective=False)
        if _digest(persisted) != preview["proposed_policy_sha256"]:
            raise PortfolioError("Persisted policy did not match the reviewed proposal")
    return {
        **preview,
        "apply": True,
        "result": "already_present" if preview["result"] == "no_change" else "updated",
        "boundary": (
            "Updated only the reviewed provider policy subtree. Existing schedules, receipts, timers, "
            "campaign authority and provider state were not rewritten and no provider call was made."
        ),
    }
