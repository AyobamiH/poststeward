"""Explain the existing local pipeline without inventing historical evidence."""
from __future__ import annotations

import hashlib
import json
import re
import sys

from ocpf_post import portfolio
from ocpf_post.campaigns import builtin_manifest, builtin_text, destination_binding, normalize_campaign_id
from ocpf_post.generated_supply_guard import editorial_advisories
from ocpf_post.registry import RegistryError
from ocpf_post.scheduler import schedule_records
from ocpf_post.state import iter_receipts, read_json
from ocpf_post.replenisher import source_state_file


def explain_campaign(campaign, provider, *, now=None):
    campaign = normalize_campaign_id(campaign)
    if provider not in {"x", "threads", "linkedin"}:
        raise ValueError("Unsupported provider")
    manifest = builtin_manifest(campaign)
    if not manifest or provider not in manifest.get("providers", []):
        raise ValueError("Campaign/provider is not available locally")
    now = now or portfolio._utc_now()
    text = builtin_text(campaign, provider)
    source = manifest.get("source") or {}
    if not isinstance(source, dict):
        source = {}
    source_type = source.get("type")
    if manifest.get("evidence_brief"):
        generation = "Editorial paragraphs and evidence-linked claims from an operator-reviewed brief; exact copy compiled without an LLM"
    elif source_type == "evidence_grounded_generation":
        generation = (
            "Model-authored from allowlisted source-profile evidence; exact generated copy was frozen "
            "in the runtime campaign. Consult recorded source/admission provenance for this campaign."
        )
    elif manifest.get("runtime_generated"):
        generation = ("Approved source-profile hook/body/CTA arranged by a fixed template; README is a guard and revision marker"
                      if source_type == "repository_product_truth" else
                      "Filtered commit title inserted into an approved source-progress template")
    else:
        generation = "Previously authored campaign copy; no runtime model generation is established by this manifest"
    try:
        binding = destination_binding(campaign, provider)
        binding_error = None if binding else "No expected destination is bound"
    except RegistryError:
        binding, binding_error = None, "Expected destination could not be resolved"
    exclusions = []
    candidates = portfolio.delivery_candidates(now=now, exclusions=exclusions)
    eligible = any(row["campaign"] == campaign and row["provider"] == provider for row in candidates)
    reasons = [row for row in exclusions if row["campaign"] == campaign and row["provider"] == provider]
    schedules = [{k: r.get(k) for k in ("schedule_id", "status", "account_id", "run_at", "updated_at", "text_sha256", "url")}
                 for r in schedule_records() if r.get("campaign") == campaign and r.get("provider") == provider]
    receipts = [{k: r.get(k) for k in ("status", "account_id", "recorded_at", "text_sha256", "post_id", "url", "readback_verified", "schedule_id")}
                for r in iter_receipts() if r.get("campaign") == campaign and r.get("provider") == provider]
    digest = hashlib.sha256(text.encode()).hexdigest() if text else None
    nonempty_lines = sum(bool(line.strip()) for line in text.splitlines()) if text else 0
    paragraphs = len([part for part in re.split(r"\n\s*\n", text or "") if part.strip()])
    expected = (manifest.get("payload_sha256") or {}).get(provider)
    policy = portfolio.load_policy()
    budget = portfolio.daily_capacity(provider, now=now, policy=policy) if provider in policy["providers"] else None
    # State is the last local observation, not a fresh request or a historical
    # proof that CI/deployment matched the campaign's source revision.
    state = read_json(source_state_file())
    observations = state.get("repositories", {})
    observation = observations.get(source.get("repository"), {}) if isinstance(observations, dict) else {}
    if not isinstance(observation, dict):
        raise ValueError("Invalid local source observation")
    return {
        "schema_version": 1, "observed_at": now.isoformat(), "campaign": campaign,
        "project": manifest.get("project"), "provider": provider,
        "copy": {"text": text, "generation": generation, "sha256": digest, "manifest_sha256": expected,
                 "matches_manifest": digest == expected if digest and expected else None,
                 "characters": len(text) if text is not None else None,
                 "nonempty_lines": nonempty_lines, "paragraphs": paragraphs,
                 "editorial_advisories": editorial_advisories(provider, text or ""),
                 "editorial_advisories_authoritative": False,
                 "available": text is not None},
        "source": {key: source.get(key) for key in (
            "type", "source_id", "repository", "path", "source_sha", "observed_at",
            "evidence_indexes", "template_version", "comparison_variant", "angle_family", "novelty_rationale",
        )},
        "assistance": manifest.get("assistance"),
        "admission": manifest.get("admission"),
        "last_local_source_observation": {key: observation.get(key) for key in ("head_sha", "readme_sha", "observed_at")},
        "claim_boundary": manifest.get("claim_boundary"),
        "evidence_brief": manifest.get("evidence_brief"),
        "project_evidence": {"implementation_review": "not_assessed", "ci": "not_assessed", "deployment": "not_assessed",
                             "runtime_behaviour": "not_assessed", "business_outcomes": "not_assessed"},
        "destination": {"expected_binding": binding, "error": binding_error, "live_identity_verified": False},
        "allocation": {"policy": manifest.get("allocation"), "eligible_candidate": eligible, "exclusions": reasons,
                       "daily_capacity": budget, "timezone": policy["timezone"],
                       "selection_guaranteed": False},
        "schedules": schedules, "receipts": receipts,
        "boundary": "Local snapshot only; no GitHub/provider calls or writes. Expected account bindings are not live authentication. Publication receipts establish posting outcomes, not whether the advertised project works. Eligibility is not a reservation. Exact-copy allocation protection is account-scoped and does not detect paraphrases or override manual publication.",
    }


def cmd_explain(args):
    try:
        result = explain_campaign(args.campaign, args.provider)
    except (ValueError, OSError, RuntimeError):
        print("Error: Campaign explanation could not read valid local campaign/configuration state", file=sys.stderr)
        raise SystemExit(2)
    if args.json:
        print(json.dumps(result, indent=2, ensure_ascii=False))
        return
    print(f"Campaign: {result['campaign']} / {result['provider']}")
    print(f"Copy: {result['copy']['generation']}")
    print(f"Payload available: {result['copy']['available']}; matches manifest: {result['copy']['matches_manifest']}")
    print("Source: " + json.dumps(result['source'], ensure_ascii=False))
    print("Expected destination: " + json.dumps(result['destination'], ensure_ascii=False))
    print(f"Eligible for allocation: {result['allocation']['eligible_candidate']}")
    for row in result['allocation']['exclusions']:
        print("Reason: " + row['reason'])
    print("Daily allocation budget: " + json.dumps(result['allocation']['daily_capacity']))
    print(f"Schedule records: {len(result['schedules'])}; receipt entries: {len(result['receipts'])}")
    for receipt in result['receipts']:
        print(f"Receipt: {receipt['status']} {receipt.get('url') or ''}")
    if result['evidence_brief']:
        brief = result['evidence_brief']
        print(f"Evidence brief: {brief['brief_id']} / angle {brief['angle']['id']}; revision {brief['revision']}")
        print("Editorial purpose: " + brief['angle']['purpose'])
        print("Evidence is operator-reviewed; independent verification is not established by import.")
    print("Project CI/deployment/runtime outcomes: not assessed by this local command")
    print(result['boundary'])
