"""Admit saved, revision-bound evidence without doing network collection."""
from datetime import datetime, timedelta, timezone
import hashlib

from ocpf_post import local_store, replenisher as r, source_observations as observations
from ocpf_post.campaigns import builtin_manifest, campaign_ids
from ocpf_post.learning_admission import LearningBudget, SUPPORTED_PROVIDERS
from ocpf_post.source_routes import campaign_suffix, route_key, routes as source_routes

UTC = timezone.utc


def _known_deliveries():
    from ocpf_post.campaigns import destination_binding

    known = set()
    for cid in campaign_ids():
        m = builtin_manifest(cid)
        source = m.get("source") or {}
        admission = m.get("admission") if isinstance(m.get("admission"), dict) else {}
        admitted = admission.get("providers") if isinstance(admission.get("providers"), dict) else {}
        for provider in m.get("providers", []):
            account_id = ""
            row = admitted.get(provider)
            if isinstance(row, dict):
                account_id = str(row.get("account_id") or "")
            if not account_id:
                try:
                    binding = destination_binding(cid, provider)
                    account_id = str((binding or {}).get("account_id") or "")
                except (OSError, ValueError, KeyError, TypeError):
                    account_id = ""
            known.add((source.get("source_id"), provider, account_id))
    return known

def _items(profile, observation):
    """Catalogue entries retain their first observation time across deferrals."""
    prefix = r._clean_prefix(profile)
    sha = observation["readme_sha"]
    for index, item in enumerate(profile.get("inventory", []), 1):
        pinned_sha = str(item.get("source_sha") or "")
        if pinned_sha and pinned_sha != sha:
            continue
        # Two-thirds of topics receive at most one sequential challenger.
        arm = ('question', 'practical', None)[int(hashlib.sha256(
            f"{profile['project']}:{index}".encode()).hexdigest(), 16) % 3]
        source_id = f"{profile['project']}-README-{sha[:12]}-{index}-insight"
        primary = {"id": f"{prefix}-AUTO-{index:02}I-{sha[:7].upper()}", "source_id": source_id,
                   "sha": sha, "title": str(item.get("title") or item["hook"]) + " [insight]",
                   "lane": item.get("lane", "evergreen"), "priority": int(item.get("priority", 70)),
                   "ttl": int(item.get("ttl_hours", 336)), "at": observation["readme_observed_at"],
                   "type": "repository_product_truth", "item": item,
                   "variant": "insight", "comparison_variant": arm}
        yield primary
        if arm:
            yield {**primary, 'id': f"{prefix}-AUTO-{index:02}{arm[0].upper()}-{sha[:7].upper()}",
                   'source_id': source_id.removesuffix('insight') + arm, 'variant': arm,
                   'title': str(item.get('title') or item['hook']) + f' [{arm}]',
                   'predecessor_source_id': source_id}
    for event in observation.get("pending", []):
        yield {"id": f"{prefix}-EVENT-{event['sha'][:8].upper()}", "source_id": f"{profile['project']}-COMMIT-{event['sha']}",
               "sha": event["sha"], "title": event["title"], "lane": "development",
               "priority": min(94, int(profile.get("event_priority", 94))),
               "ttl": int(profile.get("event_ttl_hours", 30)), "at": event["observed_at"],
               "type": "repository_change_event", "event": event}


def refresh(*, apply, project=None, now=None, source_lock_timeout_seconds=0.0):
    from ocpf_post.portfolio_source_loader import merged_source_profiles
    from ocpf_post.runtime_sources import source_lock, effective_texts
    from ocpf_post.registry import resolve_account
    now = now or datetime.now(UTC)
    profiles = merged_source_profiles()["projects"]
    if project and project not in profiles:
        raise ValueError("Unknown source project")
    result = {"schema_version": 1, "projects": [], "static_campaigns": [], "event_campaigns": [],
        "generative_campaigns": [], "admission": [],
              "boundary": "Admission consumes saved observations per destination. No source network read, expiry renewal or social publication."}
    with source_lock(
        operation="replenish_refresh", timeout_seconds=source_lock_timeout_seconds,
    ):
        state = observations.load()
        budget = LearningBudget(now, apply)
        known = _known_deliveries()
        from ocpf_post.learning_supply import SamplingBudget
        sampling = SamplingBudget(now)
        targets = [project] if project else sorted(profiles)
        for name in targets:
            profile = {**profiles[name], "project": name}
            obs = state["projects"].get(name, {})
            reason = None
            if not obs:
                reason = "source_observation_required"
            elif obs.get("profile_sha256") != observations.fingerprint(profile):
                reason = "profile_changed_review_required"
            elif obs.get("status") != "observed" or obs.get("collection_error"):
                reason = obs.get("status") if not obs.get("collection_error") else "collection_unavailable"
            elif obs.get("source_ok") is not True:
                reason = "source_guard_failed"
            elif not timedelta(0) <= now - datetime.fromisoformat(obs["observed_at"].replace("Z", "+00:00")) <= timedelta(hours=1):
                reason = "source_observation_stale"
            if reason:
                result["projects"].append({"project": name, "status": reason})
                continue
            revision_pinned_inventory_skipped = sum(
                1 for item in profile.get("inventory", [])
                if isinstance(item, dict) and item.get("source_sha") and item.get("source_sha") != obs.get("readme_sha")
            )
            created = 0
            routes = source_routes(name, profile)
            route_keys = {route_key(route) for route in routes}
            default_route_keys = {
                route["provider"]: route_key(route)
                for route in routes if route.get("default") is True
            }
            for item in _items(profile, obs):
                prepared = datetime.fromisoformat(item["at"].replace("Z", "+00:00"))
                event = item.get("event")
                deadline = prepared + timedelta(hours=item["ttl"])
                if event and event.get("source_event_at"):
                    deadline = min(
                        deadline,
                        datetime.fromisoformat(event["source_event_at"].replace("Z", "+00:00"))
                        + timedelta(hours=item["ttl"]),
                    )
                for route in routes:
                    provider = str(route["provider"])
                    account_id = str(route["account_id"])
                    route_id = route_key(route)
                    completed_providers = (
                        event.setdefault("completed_providers", []) if event is not None else []
                    )
                    completed_routes = (
                        event.setdefault("completed_routes", []) if event is not None else []
                    )
                    legacy_completed = (
                        event is not None
                        and route.get("default") is True
                        and provider in completed_providers
                    )
                    if event is not None and (route_id in completed_routes or legacy_completed):
                        continue
                    if (item["source_id"], provider, account_id) in known or now >= deadline:
                        if event is not None:
                            if route_id not in completed_routes:
                                completed_routes.append(route_id)
                            if route.get("default") is True and provider not in completed_providers:
                                completed_providers.append(provider)
                            if now >= deadline:
                                event.setdefault("expired_routes", []).append(route_id)
                        continue

                    campaign = item["id"] + "-" + campaign_suffix(route)
                    learning_role = None
                    if item.get("predecessor_source_id"):
                        reason = sampling.reason(item, provider, account_id, name)
                        if reason:
                            result.setdefault("sampling_deferred", []).append({
                                "source_id": item["source_id"],
                                "provider": provider,
                                "account_id": account_id,
                                "reason": reason,
                            })
                            continue
                        if provider in SUPPORTED_PROVIDERS:
                            learning_role = "challenger"
                    elif (
                        sampling.active
                        and provider in SUPPORTED_PROVIDERS
                        and item.get("variant") == "insight"
                        and item.get("comparison_variant") in {"question", "practical"}
                    ):
                        learning_role = "baseline"

                    gate = budget.admit(
                        name,
                        provider,
                        account_id,
                        expires_at=r._iso(deadline),
                        learning_role=learning_role,
                        learning_campaign=campaign if learning_role else None,
                    )
                    result["admission"].append({
                        **gate,
                        "provider": provider,
                        "account_id": account_id,
                        "route": route_id,
                        "destination_alias": route["alias"],
                    })
                    if not gate["admitted"]:
                        continue

                    raw = (
                        r._render_event(profile, item["title"], provider)
                        if event is not None
                        else r._render_static(profile, item["item"], provider, item["variant"])
                    )
                    texts = effective_texts(profile, {provider: raw})
                    route_profile = {
                        **profile,
                        "destinations": {**profile.get("destinations", {}), provider: route["alias"]},
                    }
                    base_priority = int(item["priority"])
                    manifest_priority = min(100, base_priority + 1) if learning_role else base_priority
                    manifest = r._manifest(
                        campaign=campaign,
                        profile=route_profile,
                        title=item["title"],
                        lane=item["lane"],
                        priority=manifest_priority,
                        source_id=item["source_id"],
                        source_sha=item["sha"],
                        source_type=item["type"],
                        providers=[provider],
                        texts=texts,
                        now=prepared,
                        ttl_hours=item["ttl"],
                    )
                    manifest["source"].update(
                        source_event_at=event.get("source_event_at") if event else None,
                        admitted_at=r._iso(now),
                        profile_sha256=obs["profile_sha256"],
                        profile_review_sha256=(profile.get("runtime_source") or {}).get("review_sha256"),
                        approved_at=None,
                        destination_route=route_id,
                    )
                    manifest["admission"] = {
                        "schema_version": 1,
                        "gate": "scoped_admission",
                        "admitted_at": r._iso(now),
                        "providers": {
                            provider: {
                                "account_id": account_id,
                                "admitted_at": r._iso(now),
                                "scope": gate.get("scope"),
                                "reasons": list(gate.get("reasons") or []),
                            }
                        },
                    }
                    if item.get("comparison_variant"):
                        manifest["source"]["comparison_variant"] = item["comparison_variant"]
                    if item.get("predecessor_source_id"):
                        manifest["source"]["sampling_predecessor"] = item["predecessor_source_id"]
                        sampling.record(name, provider)
                    manifest["payload_frozen"] = True
                    manifest["allocation"]["expires_at"] = r._iso(deadline)
                    if learning_role:
                        manifest["allocation"]["learning_base_priority"] = base_priority
                        manifest["allocation"]["learning_dedupe_priority_bump"] = (
                            manifest_priority - base_priority
                        )
                    if event:
                        manifest["source"]["event_group"] = (
                            name
                            + ":development:"
                            + str(event.get("source_event_at") or event["observed_at"])[:10]
                        )
                    if apply:
                        r._write_runtime_campaign(campaign, manifest, texts)
                    known.add((item["source_id"], provider, account_id))
                    if event is not None:
                        if route_id not in completed_routes:
                            completed_routes.append(route_id)
                        if route.get("default") is True and provider not in completed_providers:
                            completed_providers.append(provider)
                    field = "event_campaigns" if event is not None else "static_campaigns"
                    result[field].append({
                        "campaign": campaign,
                        "providers": [provider],
                        "account_id": account_id,
                        "destination_alias": route["alias"],
                        "lane": item["lane"],
                        "title": item["title"],
                        **({"learning_role": learning_role} if learning_role else {}),
                    })
                    created += 1
            try:
                generated, generative_status = r._generative_campaigns_for_profile(
                    profile,
                    source_sha=obs["readme_sha"],
                    now=now,
                    apply=apply,
                    admission_budget=budget if apply else None,
                    admission_results=result["admission"] if apply else None,
                )
            except ValueError as exc:
                generated, generative_status = [], str(exc)
            result["generative_campaigns"].extend(generated)
            created += sum(len(row.get("providers") or []) for row in generated)

            # Completed evidence is compacted only after its per-destination
            # decisions are durable. The retained counters expose expired work.
            retained = []
            for event in obs.get("pending", []):
                completed = set(event.get("completed_routes", []))
                for provider in event.get("completed_providers", []):
                    if provider in default_route_keys:
                        completed.add(default_route_keys[provider])
                if route_keys <= completed:
                    if apply:
                        archive = (
                            observations.path().parent
                            / "source-event-outcomes"
                            / name
                            / (event["sha"] + "-" + obs["profile_sha256"][:12] + ".json")
                        )
                        if not archive.exists():
                            local_store.write(
                                archive,
                                {
                                    "schema_version": 1,
                                    "project": name,
                                    "profile_sha256": obs["profile_sha256"],
                                    "event": event,
                                    "recorded_at": r._iso(now),
                                },
                            )
                    obs["processed_event_count"] = obs.get("processed_event_count", 0) + 1
                    obs["expired_delivery_count"] = obs.get("expired_delivery_count", 0) + len(
                        event.get("expired_routes", event.get("expired_providers", []))
                    )
                    obs["last_processed_sha"] = event["sha"]
                else:
                    retained.append(event)
            obs["pending"] = retained
            if apply:
                local_store.write(observations.path(), state)
            blocked = [g for g in result["admission"] if g.get("project") == name and not g["admitted"]]
            result["projects"].append({"project": name, "status": "admission_paused" if blocked and not created else "ok", "created": created, "pending_events": len(retained),
                                        "reasons": sorted({r for g in blocked for r in g["reasons"]}),
                                        "expired_delivery_count": obs.get("expired_delivery_count", 0),
                                        "revision_pinned_inventory_skipped": revision_pinned_inventory_skipped,
                                        "generative_campaigns": len(generated),
                                        "generative_status": generative_status})
    result["generative"] = r.generative_summary(
        result["projects"], result["generative_campaigns"], now=now,
    )
    return result
