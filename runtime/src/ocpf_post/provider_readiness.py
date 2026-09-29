"""Read-only provider readiness projections for Setup & Recovery.

Observers in this module may perform provider reads using existing credential material,
but they must never authorize, refresh, revoke, publish, reply or delete.
"""
from __future__ import annotations

from datetime import datetime, timezone
import os
from typing import Any

from ocpf_post.model import AccountIdentity
from ocpf_post.providers.base import ProviderRejected, ProviderUnavailable
from ocpf_post.providers.linkedin import LinkedInProvider
from ocpf_post.providers.threads import ThreadsProvider
from ocpf_post.providers.x import XProvider
from ocpf_post.state import read_json

UTC = timezone.utc
SCHEMA_VERSION = 1
OBSERVATION_MODE = "read_only_no_refresh"

FENCING_STRENGTH = {
    "x": "assisted",
    "threads": "manual_only",
    "linkedin": "manual_only",
}

DEVICE_FLOW = {
    "x": "unsupported_in_v1",
    "threads": "unsupported_in_v1",
    "linkedin": "unsupported_in_v1",
}


def _now_iso() -> str:
    return datetime.now(UTC).replace(microsecond=0).strftime("%Y-%m-%dT%H:%M:%SZ")


def _scopes(raw: Any) -> set[str] | None:
    if raw is None:
        return None
    if isinstance(raw, str):
        return {item for item in raw.replace(",", " ").split() if item}
    if isinstance(raw, (list, tuple, set)):
        return {str(item).strip() for item in raw if str(item).strip()}
    return set()


def _scope_state(scopes: set[str] | None, required: set[str]) -> str:
    if scopes is None:
        return "unknown"
    return "granted" if required.issubset(scopes) else "missing"


def _identity_state(expected: str | None, observed: str | None) -> str:
    if not observed:
        return "unknown"
    if expected is None:
        return "observed"
    return "match" if expected == observed else "mismatch"


def _token_expiry_state(expires_at: Any) -> str:
    if type(expires_at) is not int or expires_at <= 0:
        return "unknown"
    now = int(datetime.now(UTC).timestamp())
    if expires_at <= now:
        return "expired"
    if expires_at <= now + (7 * 24 * 60 * 60):
        return "near_expiry"
    return "valid"


def _credential_origin(*, stored: dict[str, Any], env_present: bool) -> str:
    if stored.get("access_token"):
        return "stored"
    if env_present:
        return "environment"
    return "missing"


def _base(provider: str, expected_identity: str | None) -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "provider": provider,
        "observed_at": _now_iso(),
        "observation_mode": OBSERVATION_MODE,
        "credential_origin": "unknown",
        "authorization_flow": "unknown",
        "exact_identity_method": "unknown",
        "expected_identity": expected_identity,
        "observed_identity": None,
        "identity_match": "unknown",
        "write_scope_state": "unknown",
        "readback_scope_state": "unknown",
        "token_status_state": "unknown",
        "token_expiry_state": "unknown",
        "refresh_capability": "unknown",
        "known_token_revoke_capability": "unknown",
        "stale_token_family_fence": "not_proven",
        "quota_observation": {"status": "not_observed"},
        "forensic_recent_listing": {"status": "not_observed"},
        "device_flow": DEVICE_FLOW[provider],
        "api_version_state": {"status": "not_applicable"},
        "recovery_fencing_strength": FENCING_STRENGTH[provider],
        "blocking_reasons": [],
        "optional_gaps": [],
    }


def _add_identity(projection: dict[str, Any], identity: AccountIdentity) -> None:
    projection["observed_identity"] = identity.account_id
    projection["identity_match"] = _identity_state(projection["expected_identity"], identity.account_id)
    if projection["identity_match"] == "mismatch":
        projection["blocking_reasons"].append("provider.identity.mismatch")


def _mark_observation_failure(
    projection: dict[str, Any],
    *,
    code: str,
    error: Exception,
    required: bool = True,
) -> None:
    target = "blocking_reasons" if required else "optional_gaps"
    projection[target].append(code)
    projection["token_status_state"] = (
        "unavailable" if isinstance(error, ProviderUnavailable) else "rejected"
    )


def _finish(projection: dict[str, Any]) -> dict[str, Any]:
    if projection["credential_origin"] == "missing":
        projection["blocking_reasons"].append("provider.credential.missing")
    if projection["identity_match"] == "unknown":
        projection["blocking_reasons"].append("provider.identity.unverified")
    if projection["write_scope_state"] == "missing":
        projection["blocking_reasons"].append("provider.write_scope.missing")
    elif projection["write_scope_state"] == "unknown":
        projection["blocking_reasons"].append("provider.write_scope.unproven")
    if projection["token_expiry_state"] == "expired":
        projection["blocking_reasons"].append("provider.token.expired")
    projection["blocking_reasons"] = sorted(set(projection["blocking_reasons"]))
    projection["optional_gaps"] = sorted(set(projection["optional_gaps"]))
    projection["ready_for_write_configuration"] = not projection["blocking_reasons"]
    projection["boundary"] = (
        "Read-only provider readiness evidence only. No authorization, refresh, revocation, "
        "publication, reply, deletion, host activation or publishing-authority change was attempted."
    )
    return projection


def observe_x(
    provider: XProvider,
    *,
    expected_identity: str | None,
    forensic_window: tuple[str, str] | None = None,
) -> dict[str, Any]:
    value = _base("x", expected_identity)
    stored = read_json(provider.token_file)
    value.update({
        "credential_origin": _credential_origin(
            stored=stored,
            env_present=bool(
                (None if provider.scoped else os.environ.get("X_USER_ACCESS_TOKEN"))
                or (None if provider.scoped else os.environ.get("X_OAUTH2_ACCESS_TOKEN"))
            ),
        ),
        "authorization_flow": "oauth2_authorization_code_pkce",
        "exact_identity_method": "GET /2/users/me",
        "token_expiry_state": _token_expiry_state(stored.get("expires_at")),
        "refresh_capability": "available" if stored.get("refresh_token") else "unavailable",
        "known_token_revoke_capability": "supported",
        "api_version_state": {"status": "pinned", "version": "v2"},
    })
    scopes = _scopes(stored.get("scope"))
    value["write_scope_state"] = _scope_state(scopes, {"tweet.write", "users.read", "offline.access"})
    value["readback_scope_state"] = _scope_state(scopes, {"tweet.read", "users.read"})

    try:
        identity = provider.readonly_account()
        value["token_status_state"] = "authenticated"
        _add_identity(value, identity)
    except (ProviderRejected, ProviderUnavailable) as exc:
        _mark_observation_failure(value, code="provider.identity.unverified", error=exc)
        return _finish(value)

    try:
        value["quota_observation"] = {"status": "observed", **provider.readonly_usage(days=7)}
    except (ProviderRejected, ProviderUnavailable):
        value["quota_observation"] = {"status": "unknown"}
        value["optional_gaps"].append("provider.quota.unknown")

    if forensic_window is not None:
        start_time, end_time = forensic_window
        try:
            listing = provider.recent_posts(
                account_id=value["observed_identity"],
                start_time=start_time,
                end_time=end_time,
            )
            value["forensic_recent_listing"] = {
                "status": "observed",
                "posts_observed": len(listing.get("posts", [])),
                "reads": listing.get("reads"),
                "truncated": listing.get("truncated"),
            }
        except (ProviderRejected, ProviderUnavailable):
            value["forensic_recent_listing"] = {"status": "unavailable"}
            value["optional_gaps"].append("provider.readback.optional_missing")

    if value["readback_scope_state"] != "granted":
        value["optional_gaps"].append("provider.readback.optional_missing")
    value["optional_gaps"].append("provider.stale_authority.unresolved")
    return _finish(value)


def observe_threads(
    provider: ThreadsProvider,
    *,
    expected_identity: str | None,
    forensic_window: tuple[int, int] | None = None,
) -> dict[str, Any]:
    value = _base("threads", expected_identity)
    stored = provider._stored()
    env_present = bool(None if provider.scoped else os.environ.get("THREADS_ACCESS_TOKEN"))
    value.update({
        "credential_origin": _credential_origin(stored=stored, env_present=env_present),
        "authorization_flow": "oauth2_authorization_code",
        "exact_identity_method": "GET /me",
        "token_expiry_state": _token_expiry_state(stored.get("expires_at")),
        "refresh_capability": "available" if value["credential_origin"] != "missing" else "unavailable",
        "known_token_revoke_capability": "not_established",
        "api_version_state": {"status": "pinned", "version": "v1.0"},
    })
    scopes = _scopes(stored.get("scope"))
    value["write_scope_state"] = _scope_state(scopes, {"threads_content_publish"})
    value["readback_scope_state"] = _scope_state(scopes, {"threads_basic"})

    try:
        identity = provider.readonly_account()
        value["token_status_state"] = "authenticated"
        _add_identity(value, identity)
    except (ProviderRejected, ProviderUnavailable) as exc:
        _mark_observation_failure(value, code="provider.identity.unverified", error=exc)
        return _finish(value)

    try:
        value["quota_observation"] = {"status": "observed", **provider.publishing_limit()}
    except (ProviderRejected, ProviderUnavailable):
        value["quota_observation"] = {"status": "unknown"}
        value["optional_gaps"].append("provider.quota.unknown")

    if forensic_window is not None:
        since_epoch, until_epoch = forensic_window
        try:
            listing = provider.recent_threads(
                since_epoch=since_epoch,
                until_epoch=until_epoch,
            )
            value["forensic_recent_listing"] = {
                "status": "observed",
                "posts_observed": len(listing.get("posts", [])),
                "reads": listing.get("reads"),
                "truncated": listing.get("truncated"),
            }
            value["readback_scope_state"] = "observed"
        except (ProviderRejected, ProviderUnavailable):
            value["forensic_recent_listing"] = {"status": "unavailable"}
            value["optional_gaps"].append("provider.readback.optional_missing")

    value["optional_gaps"].append("provider.stale_authority.unresolved")
    return _finish(value)


def observe_linkedin(
    provider: LinkedInProvider,
    *,
    expected_identity: str | None,
    actor_urn: str | None = None,
    require_recovery_readback: bool = False,
) -> dict[str, Any]:
    value = _base("linkedin", expected_identity)
    stored = provider._stored()
    env_present = bool(os.environ.get("LINKEDIN_ACCESS_TOKEN") or os.environ.get("LINKEDIN_TOKEN"))
    actor = str(actor_urn or provider.actor_urn or expected_identity or "")
    actor_type = "organization" if actor.startswith(("urn:li:organization:", "urn:li:organizationBrand:")) else "member"
    value.update({
        "credential_origin": _credential_origin(stored=stored, env_present=env_present),
        "authorization_flow": "oauth2_authorization_code",
        "exact_identity_method": (
            "OIDC /v2/userinfo sub + exact organization URN"
            if actor_type == "organization"
            else "OIDC /v2/userinfo sub"
        ),
        "token_expiry_state": _token_expiry_state(stored.get("expires_at")),
        "refresh_capability": (
            "available"
            if stored.get("refresh_token") and provider._client_id() and provider._client_secret()
            else "not_guaranteed"
        ),
        "known_token_revoke_capability": "not_established",
        "api_version_state": provider.api_version_evidence(),
        "actor_type": actor_type,
        "actor_urn": actor or None,
    })

    introspection: dict[str, Any] | None = None
    try:
        introspection = provider.readonly_introspect()
        value["token_status_state"] = "active" if introspection["active"] else "inactive"
        scopes = _scopes(introspection.get("scope"))
        if introspection.get("active") is not True:
            value["blocking_reasons"].append("provider.token.inactive")
    except ProviderRejected:
        value["token_status_state"] = "unknown"
        value["optional_gaps"].append("provider.token_status.unknown")
        scopes = _scopes(stored.get("scope"))

    write_required = {"w_organization_social"} if actor_type == "organization" else {"w_member_social"}
    read_required = {"r_organization_social"} if actor_type == "organization" else {"r_member_social"}
    value["write_scope_state"] = _scope_state(scopes, write_required)
    value["readback_scope_state"] = _scope_state(scopes, read_required)

    try:
        info = provider.readonly_userinfo()
        member_urn = f"urn:li:person:{info['sub']}"
        if actor_type == "member":
            observed = member_urn
        else:
            observed = actor
            value["authenticated_member_identity"] = member_urn
        value["observed_identity"] = observed
        value["identity_match"] = _identity_state(expected_identity, observed)
        if value["identity_match"] == "mismatch":
            value["blocking_reasons"].append("provider.identity.mismatch")
    except ProviderRejected as exc:
        _mark_observation_failure(value, code="provider.identity.unverified", error=exc)
        return _finish(value)

    if actor_type == "organization":
        if value["readback_scope_state"] == "granted":
            try:
                evidence = provider.readonly_organisation_access(actor)
                value["forensic_recent_listing"] = {
                    "status": "actor_reachable",
                    "elements_observed": evidence.get("elements_observed"),
                }
            except ProviderRejected:
                value["forensic_recent_listing"] = {"status": "unavailable"}
                gap = (
                    "provider.readback.recovery_required"
                    if require_recovery_readback
                    else "provider.readback.optional_missing"
                )
                (value["blocking_reasons"] if require_recovery_readback else value["optional_gaps"]).append(gap)
        else:
            gap = (
                "provider.readback.recovery_required"
                if require_recovery_readback
                else "provider.readback.optional_missing"
            )
            (value["blocking_reasons"] if require_recovery_readback else value["optional_gaps"]).append(gap)
    elif value["readback_scope_state"] != "granted":
        gap = (
            "provider.readback.recovery_required"
            if require_recovery_readback
            else "provider.readback.optional_missing"
        )
        (value["blocking_reasons"] if require_recovery_readback else value["optional_gaps"]).append(gap)

    if value["api_version_state"].get("state") == "attention":
        value["optional_gaps"].append("provider.api_version.attention")
    value["quota_observation"] = {"status": "unknown_or_portal_only"}
    value["optional_gaps"].append("provider.quota.unknown")
    value["optional_gaps"].append("provider.stale_authority.unresolved")
    return _finish(value)


def observe_provider(
    provider: XProvider | ThreadsProvider | LinkedInProvider,
    *,
    expected_identity: str | None,
    forensic_window: tuple[Any, Any] | None = None,
    actor_urn: str | None = None,
    require_recovery_readback: bool = False,
) -> dict[str, Any]:
    if isinstance(provider, XProvider):
        window = forensic_window if forensic_window is None else (str(forensic_window[0]), str(forensic_window[1]))
        return observe_x(provider, expected_identity=expected_identity, forensic_window=window)
    if isinstance(provider, ThreadsProvider):
        if forensic_window is not None and not all(type(item) is int for item in forensic_window):
            raise ValueError("Threads forensic window must use epoch integers")
        return observe_threads(
            provider,
            expected_identity=expected_identity,
            forensic_window=forensic_window,  # type: ignore[arg-type]
        )
    if isinstance(provider, LinkedInProvider):
        return observe_linkedin(
            provider,
            expected_identity=expected_identity,
            actor_urn=actor_urn,
            require_recovery_readback=require_recovery_readback,
        )
    raise ValueError("Unsupported provider readiness observer")
