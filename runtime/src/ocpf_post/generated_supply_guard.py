"""Fail-closed guards for evidence-grounded generated campaign delivery."""
from __future__ import annotations

from datetime import datetime
import re
from typing import Any

SOURCE_TYPE = "evidence_grounded_generation"
LINKEDIN_MIN_CHARS = 350
LINKEDIN_MAX_CHARS = 900
LINKEDIN_MIN_PARAGRAPHS = 2


def editorial_advisories(provider: str, text: str) -> list[str]:
    """Return non-authoritative editorial observations for generated copy.

    These are presentation targets, not publication-integrity or admission rules.
    Provider payload validity is enforced by the publication payload layer.
    """
    if str(provider or "").strip().lower() != "linkedin":
        return []
    value = str(text or "").strip()
    advisories: list[str] = []
    if len(value) < LINKEDIN_MIN_CHARS:
        advisories.append("linkedin_below_editorial_target")
    elif len(value) > LINKEDIN_MAX_CHARS:
        advisories.append("linkedin_above_editorial_target")
    paragraphs = [part.strip() for part in re.split(r"\n\s*\n", value) if part.strip()]
    if len(paragraphs) < LINKEDIN_MIN_PARAGRAPHS:
        advisories.append("linkedin_single_paragraph")
    return advisories


def validate_provider_quality(provider: str, text: str) -> list[str]:
    """Compatibility shim: editorial style is advisory and never grants/denies delivery authority."""
    return editorial_advisories(provider, text)


def admission_state(
    manifest: dict[str, Any], provider: str, account_id: str | None,
) -> tuple[str, dict[str, Any]]:
    """Classify route-specific generated-supply admission provenance."""
    source = manifest.get("source") if isinstance(manifest.get("source"), dict) else {}
    if source.get("type") != SOURCE_TYPE:
        return "not_applicable", {}

    admission = manifest.get("admission")
    if not isinstance(admission, dict):
        return "legacy_unattested", {}
    providers = admission.get("providers")
    if (
        admission.get("schema_version") != 1
        or admission.get("gate") != "scoped_admission"
        or not isinstance(providers, dict)
    ):
        return "invalid_attestation", {}

    provider_name = str(provider or "").strip().lower()
    row = providers.get(provider_name)
    if not isinstance(row, dict):
        return "invalid_attestation", {}

    expected = str(account_id or "").strip()
    observed = str(row.get("account_id") or "").strip()
    if not expected or observed != expected:
        return "invalid_attestation", {"recorded_account_id": observed or None}

    admitted_at = row.get("admitted_at")
    try:
        parsed = datetime.fromisoformat(str(admitted_at).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return "invalid_attestation", {"recorded_account_id": observed}
    if parsed.tzinfo is None:
        return "invalid_attestation", {"recorded_account_id": observed}

    return "attested", {
        "recorded_account_id": observed,
        "admitted_at": admitted_at,
        "scope": row.get("scope"),
        "protected": row.get("protected"),
        "learning_protected": row.get("learning_protected"),
    }


def delivery_guard(
    manifest: dict[str, Any], provider: str, account_id: str | None, text: str,
) -> str | None:
    """Reject generated routes that no longer satisfy current delivery authority."""
    source = manifest.get("source") if isinstance(manifest.get("source"), dict) else {}
    if source.get("type") != SOURCE_TYPE:
        return None

    state, _evidence = admission_state(manifest, provider, account_id)
    if state == "legacy_unattested":
        return "Generated route lacks scoped admission attestation"
    if state != "attested":
        return "Generated route scoped admission attestation is invalid"

    return None
