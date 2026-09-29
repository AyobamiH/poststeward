#!/usr/bin/env python3
"""Save a full eligible-delivery provenance audit and print only a compact summary."""
from __future__ import annotations

import argparse
from collections import Counter
import json
from datetime import datetime, timezone
from pathlib import Path

from ocpf_post.campaigns import builtin_manifest
from ocpf_post.portfolio import delivery_candidates
from ocpf_post.source_observations import load as load_source_observations

UTC = timezone.utc


def admission_state(manifest, provider, account_id):
    source = manifest.get("source") if isinstance(manifest.get("source"), dict) else {}
    if source.get("type") != "evidence_grounded_generation":
        return "not_applicable", {}
    admission = manifest.get("admission")
    if not isinstance(admission, dict):
        return "legacy_unattested", {}
    providers = admission.get("providers")
    if admission.get("schema_version") != 1 or admission.get("gate") != "scoped_admission" or not isinstance(providers, dict):
        return "invalid_attestation", {}
    row = providers.get(provider)
    if not isinstance(row, dict) or str(row.get("account_id") or "") != str(account_id or ""):
        return "invalid_attestation", {}
    try:
        parsed = datetime.fromisoformat(str(row.get("admitted_at")).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return "invalid_attestation", {}
    if parsed.tzinfo is None:
        return "invalid_attestation", {}
    return "attested", {
        "recorded_account_id": str(row.get("account_id")),
        "admitted_at": row.get("admitted_at"),
        "scope": row.get("scope"),
    }


def build(now):
    candidates = delivery_candidates(now=now)
    observations = load_source_observations()
    source_rows = observations.get("projects") if isinstance(observations, dict) else {}
    source_rows = source_rows if isinstance(source_rows, dict) else {}
    rows, by_source, by_provider, provenance = [], Counter(), Counter(), Counter()
    for candidate in candidates:
        campaign = str(candidate.get("campaign") or "")
        manifest = builtin_manifest(campaign)
        source = manifest.get("source") if isinstance(manifest.get("source"), dict) else {}
        source_type = str(source.get("type") or "unknown")
        provider = str(candidate.get("provider") or "unknown")
        account_id = str(candidate.get("account_id") or "") or None
        state, evidence = admission_state(manifest, provider, account_id)
        if source_type == "evidence_grounded_generation":
            observed = source_rows.get(str(candidate.get("project") or ""))
            if not isinstance(observed, dict) or not observed.get("readme_sha"):
                revision_state, current_sha = "unavailable", None
            else:
                current_sha = str(observed.get("readme_sha") or "")
                revision_state = "match" if current_sha == str(source.get("source_sha") or "") else "superseded"
        else:
            revision_state, current_sha = "not_applicable", None
        by_source[source_type] += 1
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
            "current_readme_sha": current_sha,
            "source_revision_state": revision_state,
            "admission_provenance": state,
            **evidence,
        })
    gen = [row for row in rows if row["source_type"] == "evidence_grounded_generation"]
    return {
        "schema_version": 1,
        "observed_at": now.replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "eligible_delivery_count": len(rows),
        "by_source_type": dict(sorted(by_source.items())),
        "by_provider": dict(sorted(by_provider.items())),
        "admission_provenance_counts": dict(sorted(provenance.items())),
        "generative_delivery_count": len(gen),
        "generative_attested_count": sum(row["admission_provenance"] == "attested" for row in gen),
        "generative_legacy_unattested_count": sum(row["admission_provenance"] == "legacy_unattested" for row in gen),
        "generative_invalid_attestation_count": sum(row["admission_provenance"] == "invalid_attestation" for row in gen),
        "generative_source_revision_match_count": sum(row["source_revision_state"] == "match" for row in gen),
        "generative_source_revision_superseded_count": sum(row["source_revision_state"] == "superseded" for row in gen),
        "generative_source_revision_unavailable_count": sum(row["source_revision_state"] == "unavailable" for row in gen),
        "deliveries": rows,
        "boundary": "READ_ONLY: no admission, scheduling, publication, source refresh, retry or provider call.",
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output")
    args = parser.parse_args()
    now = datetime.now(UTC)
    output = Path(args.output).expanduser() if args.output else Path.home() / ("post-once-provenance-audit-" + now.strftime("%Y%m%d-%H%M%S") + ".json")
    value = build(now)
    output.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    for key in (
        "observed_at", "eligible_delivery_count", "by_provider", "by_source_type",
        "admission_provenance_counts", "generative_delivery_count",
        "generative_attested_count", "generative_legacy_unattested_count",
        "generative_invalid_attestation_count", "generative_source_revision_match_count",
        "generative_source_revision_superseded_count", "generative_source_revision_unavailable_count",
    ):
        value_out = json.dumps(value[key], sort_keys=True) if isinstance(value[key], dict) else value[key]
        print(f"{key}={value_out}")
    print("full_report=" + str(output))
    print("consequence=READ_ONLY")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
