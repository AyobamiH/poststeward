"""Local evidence inspection. No provider clients, refreshes or repair actions."""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import subprocess

from ocpf_post import __version__, local_store
from ocpf_post.ledger import LedgerIntegrityError, read_jsonl
from ocpf_post.state import config_dir, state_dir

UTC = timezone.utc
PUBLISHED = {"published_verified", "published_unverified"}
TERMINAL = PUBLISHED | {"ambiguous_effect", "partial_effect"}
ACTIVE = {"scheduled", "executing"}


def at(value):
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("Timestamp requires an offset")
    return parsed.astimezone(UTC)


def read_log(path: Path):
    """Use the same fail-closed durable-ledger reader as consequential paths."""
    return read_jsonl(path)


def fold_schedules(events):
    records = {}
    for event in events:
        sid = event.get("schedule_id")
        if not sid:
            raise ValueError("Schedule event missing schedule_id")
        if event.get("event") == "scheduled":
            if sid in records:
                raise ValueError("Repeated schedule creation event")
            records[sid] = dict(event)
        elif sid not in records:
            raise ValueError("Schedule transition without creation event")
        else:
            if event.get("event") == "publication_part_created" and event.get("part_post_id"):
                part_ids = list(records[sid].get("publication_part_ids") or [])
                part_id = str(event["part_post_id"])
                if part_id not in part_ids:
                    part_ids.append(part_id)
                records[sid]["publication_part_ids"] = part_ids
            for field in (
                "status", "post_id", "url", "receipt_status", "readback_verified", "detail",
                "retry_at", "preflight_attempts", "failure_class", "runner_pid",
                "publication_root_post_id", "publication_completed_parts",
                "publication_failed_part_index", "publication_failure_kind",
            ):
                if field in event:
                    records[sid][field] = event[field]
        records[sid]["updated_at"] = event.get("recorded_at")
    return list(records.values())


def effect_key(row):
    values = tuple(row.get(field) for field in ("campaign", "provider", "account_id"))
    if not all(isinstance(v, str) and v for v in values):
        raise ValueError("Evidence missing campaign/provider/account identity")
    return values


def _ambiguity_observation(row, reconciled):
    matches = []
    for observation in reconciled or ():
        if any(
            row.get(field) != observation.get(field)
            for field in ("campaign", "provider", "account_id", "text_sha256")
        ):
            continue
        row_schedule = row.get("schedule_id")
        observed_schedule = observation.get("schedule_id")
        if row_schedule and observed_schedule and row_schedule != observed_schedule:
            continue
        matches.append(observation)
    def order(value):
        try:
            return at(value.get("observed_at"))
        except (TypeError, ValueError):
            return datetime.min.replace(tzinfo=UTC)
    return max(matches, key=order) if matches else None


def _ambiguity_resolution(row, reconciled):
    observation = _ambiguity_observation(row, reconciled)
    if (
        observation
        and observation.get("status") == "verified_discovered"
        and observation.get("discovered_post_id")
    ):
        return observation
    return None


def analyse(schedules, receipts, *, now, hours=24, overdue_minutes=5, executing_minutes=30, reconciled=()):
    findings = []
    def flag(level, code, message, **evidence):
        findings.append(dict(level=level, code=code, message=message, **evidence))
    cutoff = now - timedelta(hours=hours)
    latest = {}
    post_ids = defaultdict(set)
    first_effect_at = {}
    for receipt in receipts:
        key = effect_key(receipt)
        recorded = at(receipt.get("recorded_at"))
        status = receipt.get("status")
        if status not in TERMINAL:
            continue
        latest[key] = receipt
        if recorded > now:
            flag("attention", "future_receipt", "Receipt timestamp is in the future.", campaign=key[0])
        if status in PUBLISHED:
            if not receipt.get("post_id"):
                flag("attention", "missing_post_id", "Published receipt has no provider ID.", campaign=key[0])
            else:
                post_ids[key].add(str(receipt["post_id"]))
                first_effect_at.setdefault((*key, str(receipt["post_id"])), recorded)
        if status == "published_verified" and receipt.get("readback_verified") is not True:
            flag("attention", "false_verified", "Verified receipt lacks verified readback.", campaign=key[0])
    for key, ids in post_ids.items():
        if len(ids) > 1:
            flag("attention", "multiple_post_ids", "One campaign/account has multiple provider IDs; inspect possible duplicate effects.", campaign=key[0], provider=key[1], count=len(ids))
    for key, receipt in latest.items():
        if receipt["status"] == "partial_effect":
            flag(
                "attention",
                "partial_effect",
                "A multi-part publication stopped after at least one durable external part; inspect without replaying the thread.",
                campaign=key[0],
                provider=key[1],
                account_id=key[2],
                schedule_id=receipt.get("schedule_id"),
                post_id=receipt.get("post_id"),
            )
        if receipt["status"] == "ambiguous_effect":
            resolution = _ambiguity_resolution(receipt, reconciled)
            if resolution:
                flag(
                    "warning",
                    "ambiguity_reconciled",
                    "Historical ambiguity was resolved by one unique exact Threads candidate and known-ID readback; original receipt remains immutable.",
                    campaign=key[0],
                    provider=key[1],
                    account_id=key[2],
                    schedule_id=receipt.get("schedule_id"),
                    discovered_post_id=resolution.get("discovered_post_id"),
                )
            else:
                observation = _ambiguity_observation(receipt, reconciled)
                forensic = {}
                if observation:
                    forensic = {
                        "forensic_status": observation.get("status"),
                        "forensic_candidate_count": observation.get("candidate_count"),
                        "forensic_http_status": observation.get("http_status"),
                        "forensic_retry_at": observation.get("retry_at"),
                        "forensic_observed_at": observation.get("observed_at"),
                        "forensic_automatic_retry": observation.get("automatic_retry"),
                        "forensic_next_action": observation.get("next_action"),
                    }
                flag(
                    "attention",
                    "ambiguous_effect",
                    "External effect remains uncertain; do not blindly retry.",
                    campaign=key[0],
                    provider=key[1],
                    account_id=key[2],
                    schedule_id=receipt.get("schedule_id"),
                    **forensic,
                )

    active = Counter()
    recent_states = Counter()
    allowed = ACTIVE | TERMINAL | {"failed", "drift_blocked", "duplicate_blocked", "cancelled"}
    for schedule in schedules:
        key = effect_key(schedule)
        status = schedule.get("status")
        due = at(schedule.get("run_at"))
        updated = at(schedule.get("updated_at"))
        evidence = dict(schedule_id=schedule["schedule_id"], campaign=key[0], provider=key[1], account_id=key[2])
        if status not in allowed:
            flag("unknown", "unknown_schedule_status", "Unrecognised schedule status.", **evidence)
        if cutoff <= updated <= now:
            recent_states[f"{key[1]}:{status}"] += 1
            if status == "failed":
                flag("attention", "failed_schedule", "Recent schedule failed; inspect local schedule detail before deciding recovery.", **evidence)
            elif status in {"drift_blocked", "duplicate_blocked"}:
                flag("warning", "guard_blocked", "A safety guard blocked this schedule; this is not a published post.", **evidence)
        if status in ACTIVE:
            active[key] += 1
            if key in latest:
                flag("attention", "active_terminal_overlap", "Active reservation also has a terminal receipt; inspect before retry.", **evidence)
            retry_at = at(schedule["retry_at"]) if schedule.get("retry_at") else None
            preflight_deferred = (
                status == "scheduled"
                and schedule.get("failure_class") == "provider_unavailable"
                and retry_at is not None
            )
            if preflight_deferred and retry_at > now:
                flag(
                    "warning",
                    "preflight_deferred",
                    "Read-only provider preflight is deferred after a transient outage; no publish request began.",
                    retry_at=retry_at.isoformat(),
                    preflight_attempts=int(schedule.get("preflight_attempts") or 0),
                    **evidence,
                )
            elif status == "scheduled" and due < now - timedelta(minutes=overdue_minutes):
                flag("attention", "overdue", "Schedule is overdue beyond the configured grace period.", **evidence)
            if status == "executing" and updated < now - timedelta(minutes=executing_minutes):
                flag("attention", "stuck_executing", "Execution is stale and may have produced an effect; never reset automatically.", **evidence)
            text = schedule.get("text")
            if not isinstance(text, str) or hashlib.sha256(text.encode()).hexdigest() != schedule.get("text_sha256"):
                flag("attention", "payload_integrity", "Scheduled payload does not match its stored hash.", **evidence)
        if status in PUBLISHED:
            receipt = latest.get(key)
            if not receipt or receipt.get("post_id") != schedule.get("post_id") or receipt.get("text_sha256") != schedule.get("text_sha256"):
                flag("attention", "receipt_mismatch", "Published schedule does not match durable receipt evidence.", **evidence)
            elif status == "published_verified" and (receipt.get("status") != status or receipt.get("readback_verified") is not True or schedule.get("readback_verified") is not True):
                flag("attention", "schedule_readback_mismatch", "Verified schedule is not supported by matching readback evidence.", **evidence)
        if status == "partial_effect":
            flag(
                "warning",
                "partial_schedule",
                "Schedule represents a terminal partial multi-part publication; completed parts remain external effects and must not be blindly replayed.",
                **evidence,
            )
        if status == "ambiguous_effect":
            resolution = _ambiguity_resolution(schedule, reconciled)
            receipt = latest.get(key)
            if resolution:
                flag(
                    "warning",
                    "ambiguous_schedule",
                    "Schedule remains historically ambiguous but separate forensic evidence verified one exact provider post.",
                    discovered_post_id=resolution.get("discovered_post_id"),
                    **evidence,
                )
            elif receipt and receipt.get("status") == "ambiguous_effect":
                flag(
                    "warning", "ambiguous_schedule",
                    "Schedule and durable receipt describe the same uncertain external effect; inspect the root ambiguity without retrying.",
                    **evidence,
                )
            else:
                flag("attention", "ambiguous_schedule", "Schedule effect is uncertain; inspect without retrying.", **evidence)
    for key, count in active.items():
        if count > 1:
            flag("attention", "duplicate_reservations", "Multiple active reservations exist for one campaign/account.", campaign=key[0], provider=key[1], count=count)

    publications = []
    for key, receipt in latest.items():
        if receipt["status"] not in PUBLISHED or not receipt.get("post_id"):
            continue
        first = first_effect_at[(*key, str(receipt["post_id"]))]
        if cutoff <= first <= now:
            publications.append({**{k: receipt.get(k) for k in ("campaign", "provider", "status", "readback_verified", "post_id")}, "recorded_at": first.isoformat()})
    if not publications:
        flag("unknown", "no_recent_publications", "No durable published effects in this window; runtime success is unproven.")
    return dict(findings=findings, recent_schedule_states=dict(recent_states), publications=publications,
                published_by_provider_status=dict(Counter(f"{p['provider']}:{p['status']}" for p in publications)))


def timer_state(unit):
    try:
        result = subprocess.run(["systemctl", "--user", "show", unit, "--no-pager",
                                 "--property=LoadState,ActiveState,SubState,Result,ExecMainStatus,ExecMainCode,ExecMainStartTimestamp,ExecMainExitTimestamp,LastTriggerUSec,NextElapseUSecRealtime,NextElapseUSecMonotonic"],
                                capture_output=True, text=True, timeout=5, check=False)
    except (OSError, subprocess.SubprocessError):
        return {"available": False}
    if result.returncode:
        return {"available": False}
    return {"available": True, **dict(line.split("=", 1) for line in result.stdout.splitlines() if "=" in line)}


def _queue_issue_level(code):
    return (
        "warning"
        if code in {"waiting_too_long", "expiry_approaching", "blocked", "expired_unpublished", "ambiguous_effect"}
        else "attention"
    )


def _queue_issue_message(code):
    if code == "expired_unpublished":
        return "Closed queue history remains visible; no publication authority exists."
    if code == "ambiguous_effect":
        return "Queue projection repeats an already-durable ambiguity; inspect the root effect without retrying."
    return "Recorded queue exception requires attention; normal publishing continues."


def report(*, now=None, hours=24, overdue_minutes=5, executing_minutes=30, source_age_minutes=60, check_timers=True):
    now = now or datetime.now(UTC)
    output = dict(schema_version=1, cli_version=__version__, generated_at=now.isoformat(), window_hours=hours,
                  consequence="READ_ONLY", findings=[], checks={}, limitations=[
                      "Local snapshot only; no live provider readback, token refresh, publishing or repair.",
                      "LinkedIn published_unverified stays unverified; missing analytics are not zero.",
                      "Founder admission flow uses a hard safety ceiling, not a volume target; content still requires normal admission.",
                      "Current manifest metadata cannot reconstruct historical edits; diversity counts are observational.",
                      "Sources are compared with the last local observation, not live GitHub heads."])
    def flag(level, code, message, **evidence):
        output["findings"].append(dict(level=level, code=code, message=message, **evidence))
    def collect(name, fn):
        try:
            value = fn()
            output["checks"][name] = "observed"
            return value
        except Exception as exc:
            # Exception messages may contain payloads, paths or provider credentials.
            output["checks"][name] = "unavailable"
            flag("unknown", "unavailable", f"{name} could not be inspected ({type(exc).__name__}); inspect locally.")
            return None
    paths = [state_dir() / name for name in ("schedule-events.jsonl", "publish-receipts.jsonl", "portfolio-allocation-events.jsonl")]
    paths.append(config_dir() / "source-watch-state.json")
    paths.append(state_dir() / "queue-watch.json")
    paths.append(state_dir() / "publication-readbacks.json")
    paths.extend(state_dir() / name for name in ('capacity-experiment.json', 'engagement.json'))
    def fingerprints():
        return [(p.stat().st_mtime_ns, p.stat().st_size) if p.exists() else None for p in paths]
    before = collect("snapshot_start", fingerprints)
    schedules = collect("schedules", lambda: fold_schedules(read_log(paths[0])))
    receipts = collect("receipts", lambda: read_log(paths[1]))
    readbacks = collect(
        "publication_readbacks",
        lambda: local_store.read(state_dir() / "publication-readbacks.json")
        or {"schema_version": 1, "observations": {}},
    )
    reconciled = (
        list(readbacks.get("observations", {}).values())
        if isinstance(readbacks, dict) and isinstance(readbacks.get("observations"), dict)
        else []
    )
    permission_gates = {}
    for observation in reconciled:
        if observation.get("status") != "readback_permission_required":
            continue
        key = (
            str(observation.get("provider") or ""),
            str(observation.get("account_id") or ""),
            str(observation.get("required_scope") or ""),
        )
        permission_gates[key] = observation
    for (provider, account_id, required_scope), _observation in sorted(permission_gates.items()):
        flag(
            "warning",
            "readback_permission_required",
            "Known publication readback is blocked by explicitly missing recorded provider read authority; automatic provider GET is suppressed.",
            provider=provider,
            account_id=account_id,
            required_scope=required_scope or None,
        )
    if schedules is not None and receipts is not None:
        result = collect(
            "effect_analysis",
            lambda: analyse(
                schedules,
                receipts,
                now=now,
                hours=hours,
                overdue_minutes=overdue_minutes,
                executing_minutes=executing_minutes,
                reconciled=reconciled,
            ),
        )
        if result:
            output["findings"].extend(result.pop("findings"))
            output.update(result)

    def portfolio_checks():
        from ocpf_post import portfolio
        from ocpf_post.campaigns import builtin_manifest, builtin_text, campaign_ids
        from ocpf_post.portfolio_cross_platform import plan_refill
        from ocpf_post.portfolio_diversity import _topic_key
        from ocpf_post.portfolio_source_loader import merged_source_profiles
        from zoneinfo import ZoneInfo
        policy = portfolio.load_policy()
        try:
            status = portfolio.portfolio_status(now=now, policy=policy)
            plan = plan_refill(now=now, policy=policy)
        except LedgerIntegrityError:
            # Source freshness remains independently inspectable even when
            # schedule/effect evidence is corrupt. Never invent capacity from
            # the missing ledger; expose it as unknown and continue source checks.
            flag("unknown", "portfolio_effect_ledger_unavailable",
                 "Portfolio capacity could not be reconstructed because durable schedule/effect evidence failed integrity verification.")
            status = {"eligible_by_lane": {}}
            plan = {"capacity": {}, "account_capacity": {}, "horizon_minutes": policy["horizon_minutes"], "plan": []}
        output["capacity"] = plan["capacity"]
        output["account_capacity"] = plan.get("account_capacity", {})
        output["eligible_by_lane"] = status["eligible_by_lane"]
        output["horizon_minutes"] = plan["horizon_minutes"]
        output["unreserved_plan_slots"] = len(plan["plan"])
        output["timezone"] = policy["timezone"]
        for provider, values in plan["capacity"].items():
            if values["daily_target"] > 0 and values["eligible_unscheduled"] == 0:
                flag("warning", "inventory_empty", "No eligible unreserved inventory; inspect replenishment before the next refill.", provider=provider)
        manifests = {cid: builtin_manifest(cid) for cid in campaign_ids()}
        allocation_events = read_log(paths[2])
        owned = {e["schedule_id"] for e in allocation_events if e.get("event") == "scheduled" and e.get("schedule_id")}
        for s in schedules or []:
            if s.get("status") != "scheduled":
                continue
            manifest = manifests.get(s["campaign"], {})
            if s["schedule_id"] in owned:
                for moment in (now, at(s["run_at"])):
                    eligible, reason = portfolio._eligible_manifest(manifest, now=moment)
                    if not eligible:
                        flag("warning", "stale_reservation", "Allocator reservation is ineligible now or at its due time.", schedule_id=s["schedule_id"], reason=reason)
                        break
            text = builtin_text(s["campaign"], s["provider"])
            if s.get("payload_source") == "builtin_campaign" and (text is None or hashlib.sha256(text.encode()).hexdigest() != s.get("text_sha256")):
                flag("attention", "campaign_drift", "Current payload differs from the authorised schedule.", schedule_id=s["schedule_id"])
        state = json.loads(paths[3].read_text()) if paths[3].exists() else {}
        from ocpf_post.source_observations import repository_observations
        repositories = repository_observations(state.get("repositories", {}))
        sources = merged_source_profiles()["projects"]
        for project, profile in sources.items():
            observation = repositories.get(profile["repository"], {})
            if not observation:
                flag("unknown", "source_unobserved", "No local source observation.", project=project)
            elif observation.get("status") in {"history_gap", "pending_overflow", "profile_changed_review_required"}:
                flag("attention", "source_evidence_gap", "Source evidence requires review before new campaign admission.", project=project, reason=observation["status"])
            elif observation.get("collection_error"):
                flag("warning", "source_collection_unavailable", "Latest source collection failed; the previous observation is retained.", project=project)
            elif observation.get("source_ok") is not True:
                flag("attention", "source_guard_failed", "Latest local source observation failed its guard.", project=project)
            elif at(observation.get("observed_at")) < now - timedelta(minutes=source_age_minutes):
                flag("warning", "source_stale", "Local source observation is older than the configured threshold.", project=project)
        runtime = {cid: m for cid, m in manifests.items() if m.get("runtime_generated") is True}
        output["runtime_campaigns"] = len(runtime)
        published = output.get("publications", [])
        output["runtime_published_effects"] = sum(p["campaign"] in runtime for p in published)
        if runtime and not output["runtime_published_effects"]:
            flag("unknown", "source_to_receipt_unproven", "Runtime campaigns exist but none has a published effect in this window.")
        for cid, manifest in runtime.items():
            source = manifest.get("source", {})
            latest = repositories.get(source.get("repository"), {})
            if source.get("type") == "repository_product_truth" and latest.get("readme_sha") and latest["readme_sha"] != source.get("source_sha") and manifest.get("allocation", {}).get("enabled") is True:
                flag("warning", "superseded_inventory", "Runtime campaign still enabled after its README observation changed.", campaign=cid)
        output["publication_mix"] = {}
        zone = ZoneInfo(policy["timezone"])
        for provider, raw in policy["providers"].items():
            from ocpf_post.account_profiles import matches_scope
            rows = [p for p in published if p["provider"] == provider and matches_scope(provider, p.get("account_id"))]
            today = [p for p in rows if at(p["recorded_at"]).astimezone(zone).date() == now.astimezone(zone).date()]
            output["publication_mix"][provider] = {
                "published_today_within_window": len(today),
                "flow_mode": portfolio._flow_mode(raw),
                "legacy_mix_target": raw["daily_target"],
                "daily_ceiling": portfolio._daily_limit(raw),
                "by_project": dict(Counter(manifests.get(p["campaign"], {}).get("project", "unknown") for p in rows)),
                "by_lane": dict(Counter(manifests.get(p["campaign"], {}).get("allocation", {}).get("lane", "unknown") for p in rows)),
                "by_topic": dict(Counter(_topic_key(p["campaign"], manifests.get(p["campaign"], {})) for p in rows))}
    collect("portfolio_sources_freshness_mix", portfolio_checks)
    from ocpf_post.queue_watch import report as queue_report
    queue = collect("queue_supervision", lambda: queue_report(now=now))
    if queue is not None:
        output["queue_watch"] = queue
        if queue["status"] in {"not_observed", "stale"}:
            flag("unknown", "queue_watch_" + queue["status"], "Automatic queue supervision has no fresh completed evaluation.",
                 last_evaluated_at=queue.get("last_evaluated_at"))
        for code, count in queue["issue_counts"].items():
            level = _queue_issue_level(code)
            message = _queue_issue_message(code)
            flag(level, "queue_" + code, message,
                 count=count, examples=[{"campaign": r["campaign"], "provider": r["provider"]}
                                        for r in queue["issues"] if r["code"] == code][:5])
    from ocpf_post.capacity_experiment import report as trial_report
    trial = collect("capacity_experiment", lambda: trial_report(now=now))
    output['capacity_experiment'] = trial
    if trial and trial['status'] in {'stale', 'policy_changed'}:
        flag('warning', 'capacity_trial_' + trial['status'], 'Capacity increase is inactive; stored base policy applies.')
    from ocpf_post.engagement import report as engagement_report
    inbox = collect('engagement', lambda: engagement_report(now=now))
    output['engagement'] = inbox
    if inbox:
        if inbox['waiting_over_24h']:
            flag('warning', 'replies_waiting', 'Collected replies await a reviewed response or dismissal.', count=inbox['waiting_over_24h'])
        for provider, observation in inbox['polls'].items():
            if observation['status'] == 'permission_required':
                flag(
                    'warning',
                    'reply_collection_permission_required',
                    'LinkedIn inbound collection is blocked by explicitly missing recorded read authority; automatic provider GET is suppressed.',
                    provider=provider,
                    required_scope=observation.get('required_scope'),
                )
            elif observation['status'] in {'unavailable', 'stale'}:
                flag('unknown', 'reply_collection_' + observation['status'], 'Reply collection is incomplete; missing replies are not zero.', provider=provider)
        for state in ('sending', 'ambiguous_effect', 'rejected'):
            if inbox['counts'].get(state):
                flag('attention', 'reply_' + state, 'Reply outcome requires inspection; automatic retry is blocked.', count=inbox['counts'][state])
    from ocpf_post.reply_worker import policy as reply_policy, state as reply_state
    settings = collect('reply_worker_policy', reply_policy)
    worker = collect('reply_worker_state', reply_state)
    reply_enabled = settings is not None and settings['enabled']
    output['reply_worker'] = {'enabled': settings['enabled'] if settings else None,
                              'last_cycle': worker.get('last_cycle') if worker else None}
    if reply_enabled:
        cycle = output['reply_worker']['last_cycle'] or {}
        try:
            age = (now - at(cycle['observed_at'])).total_seconds()
        except (ValueError, KeyError, TypeError):
            age = None
        if age is None or age < 0:
            flag('unknown', 'reply_worker_not_observed', 'Enabled reply worker has no valid completed-cycle timestamp.')
        elif age > 45 * 60:
            flag('attention', 'reply_worker_stale', 'Enabled reply worker has not completed a cycle within 45 minutes.', age_seconds=age)
        if cycle.get('status') not in {None, 'completed'}:
            flag('attention', 'reply_worker_attention', 'Last enabled reply cycle needs attention.', cycle_status=cycle['status'])
    if check_timers:
        output["units"] = {}
        stems = ["ocpf-post-run-due", "ocpf-post-portfolio-refill", "ocpf-post-collection"]
        if reply_enabled:
            stems.append('ocpf-post-replies')
        for stem in stems:
            for suffix in ("timer", "service"):
                unit = f"{stem}.{suffix}"
                state = timer_state(unit)
                output["units"][unit] = state
                if not state["available"]:
                    flag("unknown", "systemd_unavailable", "User systemd state could not be read.", unit=unit)
                elif state.get("LoadState") != "loaded" or (suffix == "timer" and state.get("ActiveState") != "active"):
                    flag("attention", "timer_unavailable", "Required automation unit is missing or timer is inactive.", unit=unit)
                elif state.get("ActiveState") == "failed" or state.get("Result", "success") not in {"", "success"}:
                    flag("attention", "service_failed", "Last automation service execution failed.", unit=unit)
    else:
        flag("unknown", "timers_not_checked", "Timer inspection was explicitly skipped.")
    from ocpf_post.state_registry import verify as verify_state
    integrity = collect('state_integrity', verify_state)
    output['state_integrity'] = integrity
    if integrity and integrity.get('status') == 'attention':
        flag('unknown', 'state_integrity', 'Structured local state or a durable ledger failed integrity verification.',
             invalid_count=integrity.get('invalid_count'), critical_ledger_status=integrity.get('critical_ledger_status'))

    from ocpf_post.outcome_connectors import report as outcome_connector_report
    connectors = collect('outcome_connectors', outcome_connector_report)
    output['outcome_connectors'] = connectors
    if connectors:
        for connector in connectors.get('connectors', []):
            if connector.get('enabled') and connector.get('last_status') == 'unavailable':
                flag('attention', 'outcome_connector_unavailable',
                     'An enabled business-outcome connector is unavailable; imported outcomes remain unchanged.',
                     connector_id=connector.get('id'))

    from ocpf_post.alert_delivery import report as alert_report
    alerts = collect('alert_delivery', alert_report)
    output['alert_delivery'] = alerts
    if alerts and alerts.get('enabled') and alerts.get('status') == 'unavailable':
        flag('attention', 'alert_delivery_unavailable',
             'Configured incident notification delivery is unavailable; local incident state remains authoritative.')

    from ocpf_post.capabilities import report as capability_report
    output['capabilities'] = collect('capabilities', capability_report)

    from ocpf_post.cli_catalog import catalogue
    metadata = catalogue()
    output["catalogue_command_count"] = len(metadata["commands"])
    if not any(c["path"] == "health" and c["consequence"] == "READ_ONLY" for c in metadata["commands"]):
        flag("attention", "catalogue_drift", "Health command is missing its read-only discovery contract.")
    after = collect("snapshot_end", fingerprints)
    if before != after:
        flag("unknown", "concurrent_change", "Local logs changed during inspection; rerun before relying on cross-file comparisons.")
    levels = {f["level"] for f in output["findings"]}
    output["status"] = "attention" if "attention" in levels else "unknown" if "unknown" in levels else "warning" if "warning" in levels else "ok"
    return output


def positive(value):
    number = int(value)
    if number <= 0:
        raise argparse.ArgumentTypeError("Must be a positive integer")
    return number


def build_parser():
    parser = argparse.ArgumentParser(prog="ocpf-post health", description=__doc__)
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--hours", type=positive, default=24)
    parser.add_argument("--overdue-minutes", type=positive, default=5)
    parser.add_argument("--executing-minutes", type=positive, default=30)
    parser.add_argument("--source-age-minutes", type=positive, default=60)
    parser.add_argument("--skip-timers", action="store_true")
    return parser


def main():
    args = build_parser().parse_args()
    result = report(hours=args.hours, overdue_minutes=args.overdue_minutes,
                    executing_minutes=args.executing_minutes, source_age_minutes=args.source_age_minutes,
                    check_timers=not args.skip_timers)
    if args.json:
        print(json.dumps(result, indent=2, ensure_ascii=False))
    else:
        print(f"post-once {result['cli_version']} health: {result['status'].upper()}")
        print(f"Observed at {result['generated_at']}; publication window {result['window_hours']} hours")
        for name in ("published_by_provider_status", "recent_schedule_states", "capacity", "account_capacity", "publication_mix"):
            print(f"{name}: {json.dumps(result.get(name), ensure_ascii=False)}")
        for f in result["findings"]:
            context = {k: v for k, v in f.items() if k not in {"level", "code", "message"}}
            print(f"[{f['level'].upper()}] {f['code']}: {f['message']} {json.dumps(context)}")
        for limitation in result["limitations"]:
            print(f"Scope: {limitation}")
    raise SystemExit({"ok": 0, "warning": 1, "unknown": 2, "attention": 3}[result["status"]])
