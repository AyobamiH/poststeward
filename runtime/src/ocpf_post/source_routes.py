"""Resolve independent publication routes for one source-backed project.

A project keeps its configured default destination per provider. LinkedIn may also
have additional organisation/Page bindings. Those routes are independent: an
unavailable Page never rewrites a scheduled Page consequence onto the member
account; the member route remains separately admissible so the project does not
lose LinkedIn distribution while Page authority is unavailable.
"""
from __future__ import annotations

import hashlib
from typing import Any


def routes(project: str, profile: dict[str, Any]) -> list[dict[str, Any]]:
    from ocpf_post.registry import load_registry, resolve_account

    providers = [
        str(provider)
        for provider in profile.get("providers", [])
        if str(provider) in {"x", "threads", "linkedin"}
    ]
    destinations = profile.get("destinations")
    if not isinstance(destinations, dict):
        destinations = {}

    registry = load_registry()
    project_row = (registry.get("projects") or {}).get(project)
    accounts = project_row.get("accounts") if isinstance(project_row, dict) else {}
    accounts = accounts if isinstance(accounts, dict) else {}

    result: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()

    def add(provider: str, alias: str, *, default: bool) -> None:
        account = resolve_account(project, alias, expected_provider=provider)
        account_id = str(account["account_id"])
        key = (provider, account_id)
        if key in seen:
            return
        seen.add(key)
        result.append({
            "provider": provider,
            "alias": alias,
            "account_id": account_id,
            "default": default,
            "organisation": (
                provider == "linkedin"
                and account_id.startswith(("urn:li:organization:", "urn:li:organizationBrand:"))
            ),
        })

    for provider in providers:
        alias = destinations.get(provider)
        if isinstance(alias, str) and alias:
            add(provider, alias, default=True)

    linkedin_rows = [
        (str(alias), row)
        for alias, row in sorted(accounts.items())
        if isinstance(row, dict) and row.get("provider") == "linkedin"
    ]
    organisation_aliases = [
        alias for alias, row in linkedin_rows
        if str(row.get("account_id") or "").startswith(
            ("urn:li:organization:", "urn:li:organizationBrand:")
        )
    ]

    # A project that has a Page binding always gets an independent member route as
    # well. This is the safe form of fallback: candidate authority exists on both
    # routes, so an unavailable Page cannot strand the project, but a scheduled Page
    # consequence is never silently rewritten onto the member identity.
    if organisation_aliases and not any(
        row["provider"] == "linkedin" and row.get("default") is True for row in result
    ):
        preferred = None
        default_accounts = (
            project_row.get("default_accounts")
            if isinstance(project_row, dict) and isinstance(project_row.get("default_accounts"), dict)
            else {}
        )
        candidate = default_accounts.get("linkedin")
        if isinstance(candidate, str):
            row = accounts.get(candidate)
            if (
                isinstance(row, dict)
                and row.get("provider") == "linkedin"
                and str(row.get("account_id") or "").startswith("urn:li:person:")
            ):
                preferred = candidate
        if preferred is None:
            preferred = next(
                (
                    alias for alias, row in linkedin_rows
                    if str(row.get("account_id") or "").startswith("urn:li:person:")
                ),
                None,
            )
        if preferred:
            add("linkedin", preferred, default=True)

    for alias in organisation_aliases:
        add("linkedin", alias, default=False)

    return result

def route_key(route: dict[str, Any]) -> str:
    return str(route["provider"]) + ":" + str(route["account_id"])


def campaign_suffix(route: dict[str, Any]) -> str:
    provider = str(route["provider"]).upper()
    if route.get("default") is True:
        return provider
    digest = hashlib.sha256(str(route["account_id"]).encode("utf-8")).hexdigest()[:8].upper()
    return provider + "-A" + digest
