from __future__ import annotations

import json
from importlib.resources import files
from typing import Any


class RegistryError(ValueError):
    pass


def load_registry() -> dict[str, Any]:
    from ocpf_post.product_runtime import standalone_product_active

    if standalone_product_active():
        data: dict[str, Any] = {"schema_version": 1, "projects": {}}
    else:
        path = files("ocpf_post") / "registry.json"
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict) or data.get("schema_version") != 1:
            raise RegistryError("Unsupported or invalid registry schema")
        projects = data.get("projects")
        if not isinstance(projects, dict):
            raise RegistryError("Registry projects must be an object")
    from ocpf_post.onboarding import merge_runtime_registry
    try:
        from ocpf_post.account_profiles import merge_bindings
        return merge_bindings(merge_runtime_registry(data))
    except (OSError, ValueError) as exc:
        raise RegistryError("Runtime project registry is invalid or conflicts with packaged authority") from exc


def project(project_id: str) -> dict[str, Any]:
    project_id = project_id.strip().lower()
    projects = load_registry()["projects"]
    value = projects.get(project_id)
    if not isinstance(value, dict):
        raise RegistryError(f"Unknown project: {project_id}")
    return value


def infer_project(campaign: str) -> str | None:
    campaign = campaign.strip().upper()
    matches: list[str] = []
    for project_id, value in load_registry()["projects"].items():
        if not isinstance(value, dict):
            continue
        prefixes = value.get("campaign_prefixes")
        if not isinstance(prefixes, list):
            continue
        if any(campaign.startswith(str(prefix).upper()) for prefix in prefixes):
            matches.append(str(project_id))
    if len(matches) > 1:
        raise RegistryError(f"Campaign {campaign} matches multiple projects: {', '.join(matches)}")
    return matches[0] if matches else None


def resolve_account(project_id: str, alias: str, *, expected_provider: str | None = None) -> dict[str, str]:
    alias = alias.strip()
    value = project(project_id)
    accounts = value.get("accounts")
    if not isinstance(accounts, dict):
        raise RegistryError(f"Project {project_id} has no account registry")
    raw = accounts.get(alias)
    if not isinstance(raw, dict):
        raise RegistryError(f"Unknown account alias {project_id}/{alias}")
    provider = str(raw.get("provider") or "").strip().lower()
    account_id = str(raw.get("account_id") or "").strip()
    if not provider or not account_id:
        raise RegistryError(f"Account alias {project_id}/{alias} is incomplete")
    if expected_provider and provider != expected_provider.strip().lower():
        raise RegistryError(
            f"Account alias {project_id}/{alias} is for {provider}, not {expected_provider.strip().lower()}"
        )
    result = {"alias": alias, "provider": provider, "account_id": account_id}
    for key in ("label", "role"):
        text = str(raw.get(key) or "").strip()
        if text:
            result[key] = text
    return result


def resolve_default_account(project_id: str, provider: str) -> dict[str, str] | None:
    provider = provider.strip().lower()
    value = project(project_id)
    defaults = value.get("default_accounts")
    if defaults is None:
        return None
    if not isinstance(defaults, dict):
        raise RegistryError(f"Project {project_id} default_accounts must be an object")
    raw_alias = defaults.get(provider)
    if raw_alias is None:
        return None
    alias = str(raw_alias).strip()
    if not alias:
        raise RegistryError(f"Project {project_id} default account for {provider} is empty")
    return resolve_account(project_id, alias, expected_provider=provider)


def provider_identity_registered(provider: str, account_id: str) -> bool:
    provider = str(provider or "").strip().lower()
    identity = str(account_id or "").strip()
    if not provider or not identity:
        return False
    for value in load_registry()["projects"].values():
        if not isinstance(value, dict):
            continue
        accounts = value.get("accounts")
        if not isinstance(accounts, dict):
            continue
        for row in accounts.values():
            if (
                isinstance(row, dict)
                and str(row.get("provider") or "").strip().lower() == provider
                and str(row.get("account_id") or "").strip() == identity
            ):
                return True
    return False


def resolve_provider_default_identity(provider: str) -> dict[str, Any] | None:
    """Resolve the one global provider-default identity used by founder credentials."""
    provider = provider.strip().lower()
    matches: dict[str, dict[str, Any]] = {}
    registry = load_registry()
    for project_id, value in registry["projects"].items():
        if not isinstance(value, dict):
            continue
        defaults = value.get("default_accounts")
        if defaults is None:
            continue
        if not isinstance(defaults, dict):
            raise RegistryError(f"Project {project_id} default_accounts must be an object")
        raw_alias = defaults.get(provider)
        if raw_alias is None:
            continue
        alias = str(raw_alias).strip()
        if not alias:
            raise RegistryError(f"Project {project_id} default account for {provider} is empty")
        accounts = value.get("accounts")
        if not isinstance(accounts, dict):
            raise RegistryError(f"Project {project_id} has no account registry")
        raw = accounts.get(alias)
        if not isinstance(raw, dict):
            raise RegistryError(f"Unknown account alias {project_id}/{alias}")
        actual_provider = str(raw.get("provider") or "").strip().lower()
        account_id = str(raw.get("account_id") or "").strip()
        if actual_provider != provider or not account_id:
            raise RegistryError(f"Default account {project_id}/{alias} is invalid for {provider}")
        row = matches.setdefault(account_id, {
            "provider": provider,
            "account_id": account_id,
            "projects": [],
            "aliases": [],
        })
        row["projects"].append(str(project_id))
        row["aliases"].append(alias)
    if not matches:
        return None
    if len(matches) != 1:
        identities = ", ".join(sorted(matches))
        raise RegistryError(
            f"Provider {provider} has multiple default account identities ({identities}); "
            "use explicit scoped account profiles instead of provider-default credentials"
        )
    result = next(iter(matches.values()))
    result["projects"] = sorted(set(result["projects"]))
    result["aliases"] = sorted(set(result["aliases"]))
    return result


def project_summary(project_id: str) -> dict[str, Any]:
    value = project(project_id)
    accounts = value.get("accounts") if isinstance(value.get("accounts"), dict) else {}
    defaults = value.get("default_accounts") if isinstance(value.get("default_accounts"), dict) else {}
    return {
        "project": project_id.strip().lower(),
        "label": value.get("label"),
        "campaign_prefixes": value.get("campaign_prefixes", []),
        "default_accounts": defaults,
        "accounts": accounts,
    }
