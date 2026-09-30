"""Read-only provenance audit for the current eligible scheduling pool."""
from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
from typing import Any

from ocpf_post.campaigns import builtin_manifest
from ocpf_post.generated_supply_guard import admission_state as _admission_state
from ocpf_post.portfolio import delivery_candidates
from ocpf_post.source_observations import load as load_source_observations

UTC = timezone.utc


def build(*, now: datetime | None = None) -> dict[str, Any]:
    now = (now or datetime.now(UTC)).astimezone(UTC)
    candidates = delivery_candidates(now=now)
    observation_state = load_source_observations()
    source_rows = observation_state.get("projects") if isinstance(observation_state, dict) else {}
    source_rows = source_rows if isinstance(source_rows, dict) else {}
    rows: list[dict[str, Any]] = []
    by_source_type: Counter[str] = Counter()
    by_provider: Counter[str] = Counter()
    provenance: Counter[str] = Counter()

    for candidate in candidates:
        campaign = str(candidate.get("campaign") or "")
        manifest = builtin_manifest(campaign)
        source = manifest.get("source") if isinstance(manifest.get("source"), dict) else {}
        source_type = str(source.get("type") or "unknown")
        provider = str(candidate.get("provider") or "unknown")
        account_id = str(candidate.get("account_id") or "") or None
        state, evidence = _admission_state(manifest, provider, account_id)
        if source_type == "evidence_grounded_generation":
            observed = source_rows.get(str(candidate.get("project") or ""))
            if not isinstance(observed, dict) or not observed.get("readme_sha"):
                revision_state = "unavailable"
                current_readme_sha = None
            else:
                current_readme_sha = str(observed.get("readme_sha") or "")
                revision_state = (
                    "match"
                    if current_readme_sha == str(source.get("source_sha") or "")
                    else "superseded"
                )
        else:
            revision_state = "not_applicable"
            current_readme_sha = None
        by_source_type[source_type] += 1
        by_provider[provider] += 1
        provenance[state] += 1
        rows.append({
            "campaign": campaign,
            "project": candidate.get("project"),
            "provider": provider,
            "account_id": account_id,
            "lane": candidate.get("lane"),
            "expires_at": candidate.get("expires_at"),
            "source_type": source_type,
            "source_repository": source.get("repository"),
            "source_path": source.get("path"),
            "source_sha": source.get("source_sha"),
            "current_readme_sha": current_readme_sha,
            "source_revision_state": revision_state,
            "admission_provenance": state,
            **evidence,
        })

    generative = [row for row in rows if row["source_type"] == "evidence_grounded_generation"]
    return {
        "schema_version": 1,
        "observed_at": now.replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "eligible_delivery_count": len(rows),
        "by_source_type": dict(sorted(by_source_type.items())),
        "by_provider": dict(sorted(by_provider.items())),
        "admission_provenance_counts": dict(sorted(provenance.items())),
        "generative_delivery_count": len(generative),
        "generative_attested_count": sum(row["admission_provenance"] == "attested" for row in generative),
        "generative_legacy_unattested_count": sum(row["admission_provenance"] == "legacy_unattested" for row in generative),
        "generative_invalid_attestation_count": sum(row["admission_provenance"] == "invalid_attestation" for row in generative),
        "generative_source_revision_match_count": sum(row["source_revision_state"] == "match" for row in generative),
        "generative_source_revision_superseded_count": sum(row["source_revision_state"] == "superseded" for row in generative),
        "generative_source_revision_unavailable_count": sum(row["source_revision_state"] == "unavailable" for row in generative),
        "deliveries": rows,
        "boundary": (
            "Read-only local provenance audit. It does not admit, disable, schedule, publish, retry, "
            "reconcile, refresh a source, change quotas or contact a social provider."
        ),
    }
