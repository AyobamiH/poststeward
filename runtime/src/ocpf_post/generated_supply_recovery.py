"""Recover reviewed legacy generated copy without rewriting historical campaign manifests."""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
from typing import Any

from ocpf_post import local_store
from ocpf_post.admission import state_file
from ocpf_post.campaigns import (
    builtin_manifest,
    builtin_text,
    campaign_ids,
    destination_binding,
    normalize_campaign_id,
)
from ocpf_post.copy_guard import campaign_copy_key, occupied_copies
from ocpf_post.generated_supply_guard import admission_state
from ocpf_post.learning_admission import LearningBudget
from ocpf_post.registry import RegistryError
from ocpf_post.scheduler import ACTIVE_STATUSES, schedule_records
from ocpf_post.state import TERMINAL_EFFECT_STATUSES, iter_receipts

UTC = timezone.utc
SUPPORTED_PROVIDERS = {"x", "threads", "linkedin"}


class GeneratedRecoveryError(RuntimeError):
    pass


def _iso(value: datetime) -> str:
    return value.astimezone(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _digest_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _digest_json(value: Any) -> str:
    raw = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _load_review(path: str | Path) -> tuple[list[dict[str, Any]], str]:
    target = Path(path).expanduser()
    raw = target.read_bytes()
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise GeneratedRecoveryError("Review file is not valid JSON") from exc
    if not isinstance(value, list):
        raise GeneratedRecoveryError("Review file must contain a JSON array")
    rows = [row for row in value if isinstance(row, dict)]
    return rows, hashlib.sha256(raw).hexdigest()


def _successor_id(campaign: str, provider: str, text_sha256: str) -> str:
    campaign = normalize_campaign_id(campaign)
    provider = provider.strip().lower()
    prefix = campaign.split("-GEN-", 1)[0]
    code = {"x": "X", "threads": "TH", "linkedin": "LI"}[provider]
    token = hashlib.sha256(f"{campaign}:{provider}:{text_sha256}".encode("utf-8")).hexdigest()[:12].upper()
    suffix = f"-REC-{code}-{token}"
    maximum_prefix = 64 - len(suffix)
    prefix = prefix[:maximum_prefix].rstrip("-")
    return normalize_campaign_id(prefix + suffix)


def _route_snapshot(
    campaign: str,
    provider: str,
    reviewed_text: str,
    *,
    now: datetime,
    schedules: list[dict[str, Any]],
    receipts: list[dict[str, Any]],
    occupied: dict[Any, Any],
) -> dict[str, Any]:
    from ocpf_post.account_profiles import unavailable
    from ocpf_post.portfolio import _eligible_manifest
    from ocpf_post.vault_sync import guard as vault_guard

    campaign = normalize_campaign_id(campaign)
    manifest = builtin_manifest(campaign)
    source = manifest.get("source") if isinstance(manifest.get("source"), dict) else {}
    if source.get("type") != "evidence_grounded_generation":
        return {"campaign": campaign, "provider": provider, "state": "not_evidence_grounded_generation"}

    local_text = builtin_text(campaign, provider)
    if not local_text:
        return {"campaign": campaign, "provider": provider, "state": "missing_local_payload"}
    local_text = local_text.strip()
    reviewed_text = reviewed_text.strip()
    local_sha = _digest_text(local_text)
    expected_sha = str((manifest.get("payload_sha256") or {}).get(provider) or "")

    if reviewed_text != local_text:
        return {
            "campaign": campaign, "provider": provider,
            "state": "reviewed_text_does_not_match_frozen_payload",
            "text_sha256": local_sha,
        }
    if expected_sha != local_sha:
        return {
            "campaign": campaign, "provider": provider,
            "state": "frozen_payload_hash_mismatch",
            "text_sha256": local_sha,
            "manifest_sha256": expected_sha or None,
        }

    active = next((
        row for row in schedules
        if row.get("status") in ACTIVE_STATUSES
        and str(row.get("campaign") or "") == campaign
        and str(row.get("provider") or "") == provider
    ), None)
    if active:
        return {
            "campaign": campaign, "provider": provider,
            "state": "active_schedule_do_not_recover",
            "schedule_id": active.get("schedule_id"),
            "text_sha256": local_sha,
        }

    terminal = next((
        row for row in receipts
        if row.get("status") in TERMINAL_EFFECT_STATUSES
        and str(row.get("campaign") or "") == campaign
        and str(row.get("provider") or "") == provider
    ), None)
    if terminal:
        return {
            "campaign": campaign, "provider": provider,
            "state": "terminal_effect_do_not_recover",
            "terminal_status": terminal.get("status"),
            "schedule_id": terminal.get("schedule_id"),
            "post_id": terminal.get("post_id"),
            "url": terminal.get("url"),
            "text_sha256": local_sha,
        }

    eligible, reason = _eligible_manifest(manifest, now=now)
    if not eligible:
        return {
            "campaign": campaign, "provider": provider,
            "state": "blocked_by_manifest_or_source",
            "reason": reason,
            "text_sha256": local_sha,
        }

    vault_reason = vault_guard(manifest, provider, now=now)
    if vault_reason:
        return {
            "campaign": campaign, "provider": provider,
            "state": "blocked_by_vault_guard",
            "reason": vault_reason,
            "text_sha256": local_sha,
        }

    try:
        binding = destination_binding(campaign, provider)
    except RegistryError:
        binding = None
    account_id = str((binding or {}).get("account_id") or "")
    if not account_id:
        return {
            "campaign": campaign, "provider": provider,
            "state": "destination_binding_unresolved",
            "text_sha256": local_sha,
        }

    try:
        account_problem = unavailable(provider, account_id, now=now)
    except (OSError, ValueError, RuntimeError, KeyError, TypeError):
        account_problem = "account_availability_unknown"
    if account_problem:
        return {
            "campaign": campaign, "provider": provider,
            "state": "account_unavailable",
            "reason": str(account_problem),
            "account_id": account_id,
            "text_sha256": local_sha,
        }

    copy_key = campaign_copy_key(campaign, provider, local_text)
    duplicate = occupied.get(copy_key) if copy_key else None
    if duplicate and str(duplicate.get("campaign") or "") != campaign:
        return {
            "campaign": campaign, "provider": provider,
            "state": "identical_effect_already_reserved_or_recorded",
            "matching": duplicate,
            "account_id": account_id,
            "text_sha256": local_sha,
        }

    state, evidence = admission_state(manifest, provider, account_id)
    if state == "attested":
        return {
            "campaign": campaign, "provider": provider,
            "state": "already_attested",
            "account_id": account_id,
            "text_sha256": local_sha,
            "admission": evidence,
        }
    if state != "legacy_unattested":
        return {
            "campaign": campaign, "provider": provider,
            "state": "invalid_existing_admission",
            "account_id": account_id,
            "text_sha256": local_sha,
            "admission_state": state,
        }

    allocation = manifest.get("allocation") if isinstance(manifest.get("allocation"), dict) else {}
    return {
        "campaign": campaign,
        "provider": provider,
        "state": "recoverable_pending_fresh_admission",
        "project": str(manifest.get("project") or ""),
        "account_id": account_id,
        "text_sha256": local_sha,
        "source_sha": source.get("source_sha"),
        "source_id": source.get("source_id"),
        "expires_at": allocation.get("expires_at"),
        "successor_campaign": _successor_id(campaign, provider, local_sha),
    }


def preview(
    review_file: str | Path,
    *,
    provider: str = "linkedin",
    now: datetime | None = None,
) -> dict[str, Any]:
    provider = provider.strip().lower()
    if provider not in SUPPORTED_PROVIDERS:
        raise GeneratedRecoveryError(f"Unsupported provider: {provider}")
    now_dt = (now or datetime.now(UTC)).astimezone(UTC)
    rows, review_file_sha256 = _load_review(review_file)
    reviewed = [
        row for row in rows
        if "-GEN-" in str(row.get("campaign") or "")
        and str(row.get("text") or "").strip()
    ]

    schedules = list(schedule_records())
    receipts = list(iter_receipts())
    occupied = occupied_copies(schedules, receipts, ACTIVE_STATUSES)

    details = [
        _route_snapshot(
            str(row["campaign"]),
            provider,
            str(row["text"]),
            now=now_dt,
            schedules=schedules,
            receipts=receipts,
            occupied=occupied,
        )
        for row in reviewed
    ]
    recoverable = [
        row for row in details
        if row.get("state") == "recoverable_pending_fresh_admission"
    ]

    review_contract = {
        "schema_version": 1,
        "provider": provider,
        "review_file_sha256": review_file_sha256,
        "recoverable": [
            {
                key: row.get(key)
                for key in (
                    "campaign", "provider", "project", "account_id", "text_sha256",
                    "source_sha", "source_id", "expires_at", "successor_campaign",
                )
            }
            for row in recoverable
        ],
    }
    counts: dict[str, int] = {}
    for row in details:
        state = str(row.get("state") or "unknown")
        counts[state] = counts.get(state, 0) + 1

    return {
        "schema_version": 1,
        "read_only": True,
        "provider": provider,
        "review_file_sha256": review_file_sha256,
        "reviewed_generated_campaigns": len(reviewed),
        "recoverable_count": len(recoverable),
        "states": dict(sorted(counts.items())),
        "review_sha256": _digest_json(review_contract),
        "recoverable": recoverable,
        "details": details,
        "boundary": (
            "Preview only. No campaign, admission state, schedule, receipt or provider effect was changed. "
            "Recovery creates fresh successor inventory; it never rewrites a legacy campaign."
        ),
    }


def _successor_manifest(
    source_manifest: dict[str, Any],
    *,
    source_campaign: str,
    successor_campaign: str,
    provider: str,
    account_id: str,
    text_sha256: str,
    gate: dict[str, Any],
    now: datetime,
    review_file_sha256: str,
    review_sha256: str,
) -> dict[str, Any]:
    manifest = deepcopy(source_manifest)
    manifest["campaign"] = successor_campaign
    manifest["providers"] = [provider]

    destinations = source_manifest.get("destinations")
    if isinstance(destinations, dict):
        manifest["destinations"] = (
            {provider: deepcopy(destinations[provider])}
            if provider in destinations else {}
        )

    manifest["payload_sha256"] = {provider: text_sha256}
    manifest["schedules"] = {}
    manifest["receipts"] = {}
    manifest["status"] = "COPY-READY"
    manifest["payload_frozen"] = True
    manifest["runtime_generated"] = True

    allocation = deepcopy(source_manifest.get("allocation") or {})
    allocation["enabled"] = True
    allocation["prepared_at"] = _iso(now)
    allocation.pop("superseded_by", None)
    allocation.pop("superseded_at", None)
    manifest["allocation"] = allocation

    admitted_at = _iso(now)
    manifest["admission"] = {
        "schema_version": 1,
        "gate": "scoped_admission",
        "admitted_at": admitted_at,
        "providers": {
            provider: {
                "account_id": account_id,
                "admitted_at": admitted_at,
                "scope": gate.get("scope"),
                "protected": bool(gate.get("protected")),
                "learning_protected": bool(gate.get("learning_protected")),
                "reasons": list(gate.get("reasons") or []),
            }
        },
    }
    manifest["recovery"] = {
        "schema_version": 1,
        "type": "legacy_generated_successor",
        "source_campaign": source_campaign,
        "source_provider": provider,
        "source_payload_sha256": text_sha256,
        "review_file_sha256": review_file_sha256,
        "review_sha256": review_sha256,
        "recovered_at": admitted_at,
        "reason": "legacy generated campaign predates scoped admission attestation",
        "historical_manifest_rewritten": False,
        "provider_consequence_attempted": False,
    }
    return manifest


def recover(
    review_file: str | Path,
    *,
    provider: str = "linkedin",
    expected_sha256: str,
    now: datetime | None = None,
) -> dict[str, Any]:
    if not expected_sha256 or len(expected_sha256) != 64:
        raise GeneratedRecoveryError("Apply requires the exact review_sha256 from a fresh preview")

    now_dt = (now or datetime.now(UTC)).astimezone(UTC)
    from ocpf_post.replenisher import _write_runtime_campaign
    from ocpf_post.runtime_sources import source_lock

    with local_store.locked(state_file()):
        with source_lock(operation="recover_generated", timeout_seconds=240.0):
            current = preview(review_file, provider=provider, now=now_dt)
            if current["review_sha256"] != expected_sha256:
                raise GeneratedRecoveryError(
                    "Recovery review changed; run a fresh preview and use its review_sha256"
                )

            budget = LearningBudget(now_dt, True)
            created: list[dict[str, Any]] = []
            blocked: list[dict[str, Any]] = []
            known = set(campaign_ids())

            for row in current["recoverable"]:
                source_campaign = str(row["campaign"])
                successor = str(row["successor_campaign"])
                if successor in known:
                    existing = builtin_manifest(successor)
                    recovery = existing.get("recovery") if isinstance(existing.get("recovery"), dict) else {}
                    if (
                        recovery.get("source_campaign") == source_campaign
                        and recovery.get("source_provider") == provider
                        and recovery.get("source_payload_sha256") == row["text_sha256"]
                    ):
                        created.append({
                            "source_campaign": source_campaign,
                            "successor_campaign": successor,
                            "provider": provider,
                            "status": "already_recovered",
                        })
                        continue
                    blocked.append({
                        "source_campaign": source_campaign,
                        "successor_campaign": successor,
                        "provider": provider,
                        "status": "successor_collision",
                    })
                    continue

                # Recheck the exact route immediately before buying admission authority.
                fresh = preview(review_file, provider=provider, now=now_dt)
                fresh_map = {
                    str(item["campaign"]): item
                    for item in fresh["recoverable"]
                }
                candidate = fresh_map.get(source_campaign)
                if not candidate:
                    blocked.append({
                        "source_campaign": source_campaign,
                        "provider": provider,
                        "status": "no_longer_recoverable",
                    })
                    continue

                gate = budget.admit(
                    str(candidate["project"]),
                    provider,
                    str(candidate["account_id"]),
                    expires_at=candidate.get("expires_at"),
                )
                if gate.get("admitted") is not True:
                    blocked.append({
                        "source_campaign": source_campaign,
                        "provider": provider,
                        "status": "admission_paused",
                        "reasons": list(gate.get("reasons") or []),
                    })
                    continue

                text = builtin_text(source_campaign, provider)
                if not text or _digest_text(text.strip()) != candidate["text_sha256"]:
                    blocked.append({
                        "source_campaign": source_campaign,
                        "provider": provider,
                        "status": "payload_changed_after_review",
                    })
                    continue

                source_manifest = builtin_manifest(source_campaign)
                manifest = _successor_manifest(
                    source_manifest,
                    source_campaign=source_campaign,
                    successor_campaign=successor,
                    provider=provider,
                    account_id=str(candidate["account_id"]),
                    text_sha256=str(candidate["text_sha256"]),
                    gate=gate,
                    now=now_dt,
                    review_file_sha256=str(current["review_file_sha256"]),
                    review_sha256=str(current["review_sha256"]),
                )
                _write_runtime_campaign(successor, manifest, {provider: text.strip()})
                known.add(successor)
                created.append({
                    "source_campaign": source_campaign,
                    "successor_campaign": successor,
                    "provider": provider,
                    "status": "recovered",
                    "text_sha256": candidate["text_sha256"],
                    "account_id": candidate["account_id"],
                    "admission_scope": gate.get("scope"),
                    "expires_at": candidate.get("expires_at"),
                })

    return {
        "schema_version": 1,
        "applied": True,
        "provider": provider,
        "review_sha256": expected_sha256,
        "created_count": sum(row["status"] == "recovered" for row in created),
        "already_recovered_count": sum(row["status"] == "already_recovered" for row in created),
        "blocked_count": len(blocked),
        "created": created,
        "blocked": blocked,
        "boundary": (
            "Recovery created fresh COPY-READY successor inventory only. "
            "No historical campaign was rewritten, no schedule was created, and no provider publication was attempted."
        ),
    }
