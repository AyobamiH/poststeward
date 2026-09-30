"""Auditable reuse of an already-verified publication as a learning baseline.

A verified effect may satisfy a newer frozen baseline only when the provider,
account, payload hash, project, deterministic inventory slot and insight variant
all match. The historical receipt and performance snapshots remain immutable;
this module stores a sidecar attribution used only by the learning read model.
"""
from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any

from ocpf_post import local_store
from ocpf_post.campaigns import builtin_manifest, campaign_ids, destination_binding
from ocpf_post.state import state_dir

UTC = timezone.utc
SCHEMA_VERSION = 1
TEMPLATE_VERSION = "repository-static-v1"
_SOURCE_RE = re.compile(
    r"^(?P<project>.+)-README-(?P<revision>[0-9A-Fa-f]{3,40})-(?P<index>[0-9]+)-"
    r"(?P<variant>insight|question|practical)$"
)


def path():
    return state_dir() / "learning-effect-equivalences.json"


def _empty():
    return {"schema_version": SCHEMA_VERSION, "entries": []}


def load():
    value = local_store.read(path())
    if not value:
        return _empty()
    if value.get("schema_version") != SCHEMA_VERSION or not isinstance(value.get("entries"), list):
        raise ValueError("Invalid learning effect equivalence state")
    if any(not isinstance(row, dict) for row in value["entries"]):
        raise ValueError("Invalid learning effect equivalence entry")
    return value


def source_lineage(source_id: Any):
    match = _SOURCE_RE.fullmatch(str(source_id or ""))
    if not match:
        return None
    return {
        "project": match.group("project"),
        "index": int(match.group("index")),
        "variant": match.group("variant"),
        "revision_token": match.group("revision").lower(),
    }


def _baseline_descriptor(campaign: str, manifest: dict, provider: str):
    if not isinstance(manifest, dict) or manifest.get("payload_frozen") is not True:
        return None
    source = manifest.get("source") if isinstance(manifest.get("source"), dict) else {}
    lineage = source_lineage(source.get("source_id"))
    comparison = source.get("comparison_variant")
    payload = manifest.get("payload_sha256") if isinstance(manifest.get("payload_sha256"), dict) else {}
    digest = payload.get(provider)
    project = str(manifest.get("project") or "")
    if (
        source.get("type") != "repository_product_truth"
        or not lineage
        or lineage["variant"] != "insight"
        or lineage["project"] != project
        or comparison not in {"question", "practical"}
        or not isinstance(digest, str)
        or not digest
        or not source.get("source_sha")
    ):
        return None
    return {
        "baseline_campaign": campaign,
        "baseline_project": project,
        "baseline_source_id": source["source_id"],
        "baseline_source_sha": source["source_sha"],
        "baseline_lane": (manifest.get("allocation") or {}).get("lane", "evergreen"),
        "comparison_variant": comparison,
        "provider": provider,
        "text_sha256": digest,
        "lineage_project": lineage["project"],
        "lineage_index": lineage["index"],
    }


def _publication_row(identity, publication, descriptor, account_id):
    campaign, provider, account, post_id = identity
    if campaign == descriptor["baseline_campaign"]:
        return None  # Direct publication is not an equivalence.
    if provider != descriptor["provider"] or str(account) != str(account_id):
        return None
    receipt = publication.get("receipt") if isinstance(publication, dict) else None
    if not isinstance(receipt, dict):
        return None
    if receipt.get("status") != "published_verified" or receipt.get("readback_verified") is not True:
        return None
    if receipt.get("text_sha256") != descriptor["text_sha256"]:
        return None
    try:
        historical = builtin_manifest(campaign)
    except (OSError, ValueError, KeyError, TypeError):
        return None
    if str(historical.get("project") or "") != descriptor["baseline_project"]:
        return None
    source = historical.get("source") if isinstance(historical.get("source"), dict) else {}
    lineage = source_lineage(source.get("source_id"))
    payload = historical.get("payload_sha256") if isinstance(historical.get("payload_sha256"), dict) else {}
    if (
        source.get("type") != "repository_product_truth"
        or not lineage
        or lineage["project"] != descriptor["lineage_project"]
        or lineage["index"] != descriptor["lineage_index"]
        or lineage["variant"] != "insight"
        or payload.get(provider) != descriptor["text_sha256"]
    ):
        return None
    at = publication.get("at")
    if not isinstance(at, datetime):
        return None
    return {
        **descriptor,
        "account_id": str(account_id),
        "effect_campaign": campaign,
        "effect_post_id": str(post_id),
        "effect_source_id": source.get("source_id"),
        "effect_source_sha": source.get("source_sha"),
        "effect_publication_at": at.astimezone(UTC).isoformat(),
        "effect_verification_basis": publication.get("verification_basis") or receipt.get("verification_basis") or "receipt",
        "basis": "verified_effect_exact_payload_stable_lineage",
    }


def find_verified_equivalent(campaign: str, manifest: dict, provider: str, account_id: str, publications: dict):
    descriptor = _baseline_descriptor(campaign, manifest, provider)
    if not descriptor:
        return {"status": "not_eligible"}
    matches = []
    for identity, publication in publications.items():
        row = _publication_row(identity, publication, descriptor, account_id)
        if row:
            matches.append((identity, publication, row))
    if not matches:
        return {"status": "not_found"}
    if len(matches) != 1:
        return {"status": "ambiguous", "count": len(matches)}
    identity, publication, row = matches[0]
    return {"status": "verified_equivalent", "identity": identity, "publication": publication, "attestation": row}


def _same_effect(row, other):
    fields = ("effect_campaign", "provider", "account_id", "effect_post_id", "text_sha256")
    return all(str(row.get(k)) == str(other.get(k)) for k in fields)


def _same_baseline(row, other):
    fields = ("baseline_campaign", "provider", "account_id", "baseline_source_id", "baseline_source_sha")
    return all(str(row.get(k)) == str(other.get(k)) for k in fields)


def record(attestation: dict, *, now=None):
    now = now or datetime.now(UTC)
    row = {**attestation, "recorded_at": now.astimezone(UTC).isoformat()}
    with local_store.locked(path()):
        state = load()
        for existing in state["entries"]:
            if _same_baseline(existing, row):
                return {"status": "already_recorded", "attestation": existing}
            if _same_effect(existing, row):
                return {"status": "effect_already_bound", "attestation": existing}
        state["entries"].append(row)
        local_store.write(path(), state)
    return {"status": "recorded", "attestation": row}


def lookup_predecessor(source_id: str, provider: str, account_id: str, project: str):
    try:
        entries = load()["entries"]
    except (OSError, ValueError, KeyError, TypeError):
        return None
    rows = [
        row for row in entries
        if row.get("baseline_source_id") == source_id
        and row.get("provider") == provider
        and str(row.get("account_id")) == str(account_id)
        and row.get("baseline_project") == project
    ]
    return rows[0] if len(rows) == 1 else None


def satisfied_baseline(campaign: str, provider: str, account_id: str):
    try:
        entries = load()["entries"]
    except (OSError, ValueError, KeyError, TypeError):
        entries = []
    if any(
        row.get("baseline_campaign") == campaign
        and row.get("provider") == provider
        and str(row.get("account_id")) == str(account_id)
        for row in entries
    ):
        return True
    try:
        from ocpf_post.performance_review import publications
        manifest = builtin_manifest(campaign)
        found = find_verified_equivalent(campaign, manifest, provider, account_id, publications())
        return found.get("status") == "verified_equivalent"
    except (OSError, ValueError, KeyError, TypeError):
        return False


def editorial_from_attestation(row: dict):
    source_id = str(row.get("baseline_source_id") or "")
    if not source_id.endswith("-insight"):
        return None
    required = ("baseline_project", "baseline_source_sha", "baseline_lane", "comparison_variant", "text_sha256")
    if not all(row.get(k) for k in required) or row.get("comparison_variant") not in {"question", "practical"}:
        return None
    return {
        "project": row["baseline_project"],
        "lane": row["baseline_lane"],
        "variant": "insight",
        "revision": row["baseline_source_sha"],
        "topic": source_id.removesuffix("-insight"),
        "text_sha256": row["text_sha256"],
        "template_version": TEMPLATE_VERSION,
        "comparison_variant": row["comparison_variant"],
        "attribution_basis": row.get("basis") or "verified_effect_equivalence",
        "historical_campaign": row.get("effect_campaign"),
        "historical_source_sha": row.get("effect_source_sha"),
    }


def projected_editorial(campaign: str, provider: str, account_id: str, post_id: str, text_sha256: str):
    try:
        entries = load()["entries"]
    except (OSError, ValueError, KeyError, TypeError):
        return None
    rows = [
        row for row in entries
        if row.get("effect_campaign") == campaign
        and row.get("provider") == provider
        and str(row.get("account_id")) == str(account_id)
        and str(row.get("effect_post_id")) == str(post_id)
        and row.get("text_sha256") == text_sha256
    ]
    return editorial_from_attestation(rows[0]) if len(rows) == 1 else None


def projection_map(publications: dict):
    """Build an in-memory effect->editorial projection, failing closed on conflicts."""
    projected = {}
    conflicts = set()
    attestations = []
    for campaign in campaign_ids():
        try:
            manifest = builtin_manifest(campaign)
        except (OSError, ValueError, KeyError, TypeError):
            continue
        providers = manifest.get("providers") if isinstance(manifest.get("providers"), list) else []
        for provider in providers:
            descriptor = _baseline_descriptor(campaign, manifest, str(provider))
            if not descriptor:
                continue
            try:
                binding = destination_binding(campaign, str(provider))
            except Exception:
                continue
            account_id = str((binding or {}).get("account_id") or "")
            if not account_id:
                continue
            found = find_verified_equivalent(campaign, manifest, str(provider), account_id, publications)
            if found.get("status") != "verified_equivalent":
                continue
            key = tuple(found["identity"])
            row = found["attestation"]
            if key in projected and projected[key].get("baseline_source_id") != row.get("baseline_source_id"):
                conflicts.add(key)
                projected.pop(key, None)
                continue
            if key not in conflicts:
                projected[key] = row
                attestations.append(row)
    return {
        "projected": projected,
        "attestations": [row for row in attestations if (
            row["effect_campaign"], row["provider"], row["account_id"], row["effect_post_id"]
        ) not in conflicts],
        "conflicts": [list(key) for key in sorted(conflicts)],
    }
