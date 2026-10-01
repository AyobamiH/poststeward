from __future__ import annotations

import hashlib
import json
import re
from importlib.resources import files
from pathlib import Path
from typing import Any

from ocpf_post.registry import RegistryError, infer_project, resolve_account, resolve_default_account
from ocpf_post.state import state_dir

CAMPAIGN_ID_RE = re.compile(r"^[A-Z][A-Z0-9-]{2,63}$")
RUNTIME_X_MAX_CHARS = 275
RUNTIME_VARIANT_PRIORITY_OFFSETS = {"I": 0, "Q": -4, "P": -8}


def normalize_campaign_id(value: str) -> str:
    campaign = value.strip().upper()
    if not CAMPAIGN_ID_RE.fullmatch(campaign):
        raise ValueError("Campaign ID must use uppercase letters, numbers and hyphens")
    return campaign


def runtime_campaign_root() -> Path:
    return state_dir() / "runtime-campaigns"


def _packaged_campaign_root(campaign: str):
    campaign = normalize_campaign_id(campaign)
    return files("ocpf_post") / "campaigns" / campaign


def _runtime_campaign_root_for(campaign: str) -> Path:
    return runtime_campaign_root() / normalize_campaign_id(campaign)


def campaign_ids() -> list[str]:
    from ocpf_post.product_runtime import standalone_product_active

    values: set[str] = set()
    if not standalone_product_active():
        packaged = files("ocpf_post") / "campaigns"
        for child in packaged.iterdir():
            try:
                if child.is_dir():
                    values.add(normalize_campaign_id(child.name))
            except (OSError, ValueError):
                continue
    runtime = runtime_campaign_root()
    if runtime.exists():
        for child in runtime.iterdir():
            try:
                if child.is_dir():
                    values.add(normalize_campaign_id(child.name))
            except (OSError, ValueError):
                continue
    return sorted(values)


def _fit_runtime_x(text: str) -> str:
    """Compatibility helper retained without destructive truncation.

    X over-length handling now belongs to the frozen publication payload layer,
    which turns approved copy into a reply chain instead of discarding text.
    """
    return text.strip()


def builtin_text(campaign: str, provider: str) -> str | None:
    campaign = normalize_campaign_id(campaign)
    provider = provider.strip().lower()
    from ocpf_post.product_runtime import standalone_product_active

    if not standalone_product_active():
        packaged = _packaged_campaign_root(campaign) / f"{provider}.txt"
        if packaged.is_file():
            return packaged.read_text(encoding="utf-8").strip()
    runtime = _runtime_campaign_root_for(campaign) / f"{provider}.txt"
    if not runtime.is_file():
        return None
    text = runtime.read_text(encoding="utf-8").strip()
    manifest_path = runtime.parent / "manifest.json"
    if manifest_path.is_file():
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if isinstance(manifest, dict) and (manifest.get("runtime_imported") is True or "runtime_source" in manifest or manifest.get("payload_frozen") is True):
            hashes = manifest.get("payload_sha256")
            expected = hashes.get(provider) if isinstance(hashes, dict) else None
            if hashlib.sha256(text.encode("utf-8")).hexdigest() != expected:
                return None
            return text
    return text


def _runtime_priority(campaign: str, data: dict[str, Any]) -> dict[str, Any]:
    if data.get("runtime_generated") is not True:
        return data
    match = re.search(r"-AUTO-\d{2}([IQP])-", campaign)
    if not match:
        return data
    allocation = data.get("allocation")
    if not isinstance(allocation, dict):
        return data
    base = int(allocation.get("priority", 50))
    adjusted = max(0, min(100, base + RUNTIME_VARIANT_PRIORITY_OFFSETS[match.group(1)]))
    data = dict(data)
    data["allocation"] = dict(allocation)
    data["allocation"]["priority"] = adjusted
    data["allocation"]["variant_priority_offset"] = RUNTIME_VARIANT_PRIORITY_OFFSETS[match.group(1)]
    return data


def builtin_manifest(campaign: str) -> dict[str, Any]:
    campaign = normalize_campaign_id(campaign)
    from ocpf_post.product_runtime import standalone_product_active

    if not standalone_product_active():
        packaged = _packaged_campaign_root(campaign) / "manifest.json"
        if packaged.is_file():
            data = json.loads(packaged.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
    runtime = _runtime_campaign_root_for(campaign) / "manifest.json"
    if not runtime.is_file():
        return {}
    data = json.loads(runtime.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        return {}
    if data.get("runtime_imported") is True or data.get("payload_frozen") is True:
        return data
    data = _runtime_priority(campaign, data)
    if "runtime_source" in data:
        return data
    providers = data.get("providers") if isinstance(data.get("providers"), list) else []
    hashes: dict[str, str] = {}
    for provider_raw in providers:
        provider = str(provider_raw).strip().lower()
        text = builtin_text(campaign, provider)
        if text:
            hashes[provider] = hashlib.sha256(text.encode("utf-8")).hexdigest()
    if hashes:
        data = dict(data)
        data["payload_sha256"] = hashes
    return data


def campaign_project(campaign: str) -> str | None:
    campaign = normalize_campaign_id(campaign)
    manifest = builtin_manifest(campaign)
    explicit = str(manifest.get("project") or "").strip().lower()
    if explicit:
        return explicit
    return infer_project(campaign)


def _provider_declared(manifest: dict[str, Any], provider: str) -> bool:
    providers = manifest.get("providers")
    if not isinstance(providers, list):
        return False
    return provider in {str(value).strip().lower() for value in providers}


def destination_binding(campaign: str, provider: str) -> dict[str, str] | None:
    campaign = normalize_campaign_id(campaign)
    provider = provider.strip().lower()
    manifest = builtin_manifest(campaign)
    destinations = manifest.get("destinations")
    raw = destinations.get(provider) if isinstance(destinations, dict) else None

    if isinstance(raw, str):
        project_id = campaign_project(campaign)
        if not project_id:
            raise RegistryError(f"Campaign {campaign} uses an account alias but has no project")
        return resolve_account(project_id, raw, expected_provider=provider)

    if isinstance(raw, dict):
        alias = str(raw.get("account_alias") or "").strip()
        if alias:
            project_id = campaign_project(campaign)
            if not project_id:
                raise RegistryError(f"Campaign {campaign} uses an account alias but has no project")
            resolved=resolve_account(project_id, alias, expected_provider=provider)
            if raw.get('account_id') and raw['account_id']!=resolved.get('account_id'):
                return None
            return resolved
        account_id = str(raw.get("account_id") or "").strip()
        if not account_id:
            return None
        result = {"account_id": account_id, "provider": provider}
        for key in ("label", "role"):
            text = str(raw.get(key) or "").strip()
            if text:
                result[key] = text
        return result

    if _provider_declared(manifest, provider):
        project_id = campaign_project(campaign)
        if not project_id:
            raise RegistryError(f"Campaign {campaign} declares {provider} but has no project")
        return resolve_default_account(project_id, provider)

    return None


def destination_binding_error(campaign: str, provider: str, account: Any) -> str | None:
    provider = provider.strip().lower()
    try:
        binding = destination_binding(campaign, provider)
    except RegistryError as exc:
        return f"Registry resolution failed for {normalize_campaign_id(campaign)}/{provider}: {exc}"
    display = str(getattr(account, "display", None) or getattr(account, "account_id", "unknown"))
    account_id = str(getattr(account, "account_id", ""))
    if binding is None:
        return (
            f"No live destination account is bound for {normalize_campaign_id(campaign)}/{provider}. "
            f"Authenticated account is {display} ({account_id}). Refusing live publish."
        )
    if account_id != binding["account_id"]:
        expected = binding.get("label") or binding["account_id"]
        alias = f" via {binding['alias']}" if binding.get("alias") else ""
        return (
            f"Campaign destination mismatch for {normalize_campaign_id(campaign)}/{provider}: "
            f"expected {expected} ({binding['account_id']}){alias}, got {display} ({account_id}). "
            "Refusing live publish."
        )
    return None
