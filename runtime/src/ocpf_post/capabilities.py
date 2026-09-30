"""Machine-readable capability and readiness registry for Post-Once."""
from __future__ import annotations

from typing import Any

from ocpf_post import local_store
from ocpf_post.ledger import LedgerIntegrityError
from ocpf_post.state import provider_token_file, state_dir, read_json, iter_receipts

STATE_VOCABULARY = ("implemented", "configured", "authorised", "observed", "accepted", "blocked")


def _bool_state(value: bool, positive: str, negative: str = "blocked") -> str:
    return positive if value else negative


def _acceptance() -> dict[str, Any]:
    value = local_store.read(state_dir() / "acceptance-views.json")
    sections = value.get("sections", {}) if isinstance(value.get("sections"), dict) else {}
    return {key: row.get("status") for key, row in sections.items() if isinstance(row, dict)}


def _provider_credentials() -> dict[str, bool]:
    result = {}
    for name in ("x", "threads", "linkedin"):
        try:
            result[name] = bool(read_json(provider_token_file(name)).get("access_token"))
        except (OSError, ValueError):
            result[name] = False
    return result


def _readiness_summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    core_blockers = [
        row["id"] for row in rows
        if row.get("required_for_core") is True and row.get("evidence") == "blocked"
    ]
    activated_optional_blockers = [
        row["id"] for row in rows
        if row.get("required_for_core") is False
        and row.get("activated") is True
        and row.get("evidence") == "blocked"
    ]
    optional_pending = [
        row["id"] for row in rows
        if row.get("required_for_core") is False
        and row.get("activated") is not True
        and row.get("acceptance") != "accepted"
    ]
    return {
        "status": "attention" if core_blockers or activated_optional_blockers else "observed",
        "core_status": "attention" if core_blockers else "observed",
        "core_blockers": core_blockers,
        "activated_optional_blockers": activated_optional_blockers,
        "optional_pending_count": len(optional_pending),
        "optional_pending": optional_pending,
        "accepted_capability_count": sum(row.get("acceptance") == "accepted" for row in rows),
    }


def report(*, state_override: dict[str, Any] | None = None,
           connector_state_override: dict[str, Any] | None = None,
           alert_state_override: dict[str, Any] | None = None,
           outcomes_override: dict[str, Any] | None = None,
           engagement_state_override: dict[str, Any] | None = None,
           receipt_summary_override: dict[str, Any] | None = None) -> dict[str, Any]:
    from ocpf_post import account_profiles, alert_delivery, business_outcomes, engagement, outcome_connectors
    from ocpf_post.state_registry import verify as verify_state

    acceptance = _acceptance()
    credentials = _provider_credentials()
    if receipt_summary_override is None:
        receipt_integrity = "observed"
        try:
            receipts = list(iter_receipts())
        except LedgerIntegrityError:
            receipts = []
            receipt_integrity = "blocked"
        published_effects = sum(
            row.get("status") in {"published_verified", "published_unverified"}
            for row in receipts
        )
        verified_effects = sum(
            row.get("status") == "published_verified" and row.get("readback_verified") is True
            for row in receipts
        )
    else:
        receipt_integrity = str(receipt_summary_override.get("integrity") or "unknown")
        published_effects = int(receipt_summary_override.get("published_effects", 0) or 0)
        verified_effects = int(receipt_summary_override.get("verified_effects", 0) or 0)
    profiles = list(account_profiles.profiles().values())
    linkedin_pages = [row for row in profiles if row.get("provider") == "linkedin" and str(row.get("account_id", "")).startswith(("urn:li:organization:", "urn:li:organizationBrand:"))]
    enabled_pages = [row for row in linkedin_pages if row.get("enabled") is True]
    connected_pages = [row for row in linkedin_pages if account_profiles.credential_present(row)]
    engagement_state = engagement_state_override if engagement_state_override is not None else engagement.report(include_items=False)
    linkedin_poll = engagement_state.get("linkedin", {}) if isinstance(engagement_state.get("linkedin"), dict) else {}
    connector_state = connector_state_override if connector_state_override is not None else outcome_connectors.report()
    alert_state = alert_state_override if alert_state_override is not None else alert_delivery.report()
    outcomes = outcomes_override if outcomes_override is not None else business_outcomes.report()
    state = state_override if state_override is not None else verify_state()
    linkedin_read_authority = {}
    try:
        from ocpf_post.providers.linkedin import LinkedInProvider
        linkedin_provider = LinkedInProvider()
        for purpose in ("posts", "comments"):
            permission = linkedin_provider.read_permission(purpose)
            linkedin_read_authority[purpose] = {
                key: permission.get(key)
                for key in ("status", "actor_type", "scope_recorded", "required_scope")
            }
    except (OSError, ValueError, KeyError, TypeError):
        linkedin_read_authority = {
            "posts": {"status": "unknown"},
            "comments": {"status": "unknown"},
        }

    rows: list[dict[str, Any]] = [
        {
            "id": "provider-publishing", "label": "Provider publishing",
            "required_for_core": True, "activated": True,
            "implementation": "implemented",
            "configuration": "configured" if any(credentials.values()) else "blocked",
            "authority": "authorised" if any(credentials.values()) else "blocked",
            "evidence": "observed" if published_effects else "blocked",
            "acceptance": "accepted" if verified_effects else "blocked",
            "detail": {
                "credential_presence": credentials,
                "published_effects": published_effects,
                "verified_effects": verified_effects,
                "receipt_integrity": receipt_integrity,
                "linkedin_recorded_read_authority": linkedin_read_authority,
            },
        },
        {
            "id": "continuous-acceptance", "label": "PC-01 through PC-06 acceptance",
            "required_for_core": True, "activated": True,
            "implementation": "implemented",
            "configuration": "configured",
            "authority": "authorised",
            "evidence": "observed" if acceptance else "blocked",
            "acceptance": "accepted" if acceptance and all(status == "observed" for status in acceptance.values()) else "blocked",
            "detail": {"sections": acceptance},
        },
        {
            "id": "linkedin-page-actors", "label": "LinkedIn Page actors",
            "required_for_core": False, "activated": bool(enabled_pages),
            "implementation": "implemented",
            "configuration": "configured" if linkedin_pages else "blocked",
            "authority": "authorised" if enabled_pages else "blocked",
            "evidence": "observed" if linkedin_poll.get("status") == "observed" else "blocked",
            "acceptance": "accepted" if enabled_pages and linkedin_poll.get("status") == "observed" else "blocked",
            "detail": {
                "registered": len(linkedin_pages), "credential_ready": len(connected_pages),
                "enabled": len(enabled_pages), "inbound_status": linkedin_poll.get("status"),
            },
        },
        {
            "id": "outcome-connectors", "label": "Business outcome connectors",
            "required_for_core": False, "activated": any(row.get("enabled") for row in connector_state.get("connectors", [])),
            "implementation": "implemented",
            "configuration": "configured" if connector_state.get("connectors") else "blocked",
            "authority": "authorised" if any(row.get("enabled") for row in connector_state.get("connectors", [])) else "blocked",
            "evidence": "observed" if outcomes.get("status") == "observed" else "blocked",
            "acceptance": "accepted" if outcomes.get("status") == "observed" else "blocked",
            "detail": {"connector_status": connector_state.get("status"), "event_counts": outcomes.get("event_counts", {})},
        },
        {
            "id": "alert-delivery", "label": "Operating incident alert delivery",
            "required_for_core": False, "activated": alert_state.get("enabled") is True,
            "implementation": "implemented",
            "configuration": "configured" if alert_state.get("status") not in {None, "not_configured"} else "blocked",
            "authority": "authorised" if alert_state.get("enabled") is True else "blocked",
            "evidence": "observed" if alert_state.get("last_delivery_at") else "blocked",
            "acceptance": "accepted" if alert_state.get("last_delivery_at") else "blocked",
            "detail": {key: alert_state.get(key) for key in ("status", "enabled", "last_delivery_at", "retry_at")},
        },
        {
            "id": "ledger-integrity", "label": "Durable ledger integrity",
            "required_for_core": True, "activated": True,
            "implementation": "implemented",
            "configuration": "configured", "authority": "authorised",
            "evidence": "observed" if state.get("critical_ledger_status") == "observed" else "blocked",
            "acceptance": "accepted" if state.get("status") == "observed" else "blocked",
            "detail": {
                "status": state.get("status"),
                "invalid_count": state.get("invalid_count"),
                "scope": state.get("scope") or "full_registry",
            },
        },
        {
            "id": "observability-console", "label": "Read-only execution console",
            "required_for_core": True, "activated": True,
            "implementation": "implemented", "configuration": "configured", "authority": "authorised",
            "evidence": "observed", "acceptance": "accepted",
            "detail": {"mode": "loopback_read_only"},
        },
    ]
    summary = _readiness_summary(rows)
    return {
        "schema_version": 1,
        **summary,
        "vocabulary": list(STATE_VOCABULARY),
        "capabilities": rows,
        "boundary": (
            "Local readiness projection only. Unconfigured optional capabilities remain pending, not platform failures. "
            "A core capability or an explicitly activated optional capability with blocked evidence is attention. "
            "Implemented is not configured; configured is not authorised; authorised is not observed; observed is not accepted."
        ),
    }
