"""Attested health presentation over the existing read-only health analyser."""
from __future__ import annotations

import json

from ocpf_post import health as core
from ocpf_post.schedule_semantics import ACTIVE_STATUSES, PUBLISHED_STATUSES, TERMINAL_EFFECT_STATUSES


def report(**kwargs):
    # Preserve the mature analyser while ensuring the production health path uses
    # the same authoritative state vocabulary as scheduler/capacity reporting.
    core.ACTIVE = set(ACTIVE_STATUSES)
    core.PUBLISHED = set(PUBLISHED_STATUSES)
    core.TERMINAL = set(TERMINAL_EFFECT_STATUSES)
    result = core.report(**kwargs)
    from ocpf_post.runtime_attestation import attest
    from ocpf_post.admission import decide as admission_decision
    now = kwargs.get("now")
    try:
        result["runtime_attestation"] = attest(now=now)
        result["checks"]["runtime_attestation"] = "observed"
    except (OSError, ValueError, KeyError, TypeError, RuntimeError) as exc:
        result["runtime_attestation"] = {"status": "unavailable", "error_type": type(exc).__name__, "publishing_authority": False}
        result["checks"]["runtime_attestation"] = "unavailable"
    try:
        from ocpf_post.scoped_admission import status as scoped_status
        result["scoped_admission"] = scoped_status()
        result["admission_pressure"] = admission_decision(now=now, persist=False)
        result["admission_pressure"]["diagnostic_only"] = True
        result["admission_pressure"]["active_gate"] = "scoped_admission"
        result["checks"]["admission_pressure"] = "observed"
    except (OSError, ValueError, KeyError, TypeError, RuntimeError) as exc:
        result["admission_pressure"] = {"status": "unavailable", "error_type": type(exc).__name__}
        result["checks"]["admission_pressure"] = "unavailable"
    from datetime import datetime, timedelta, timezone
    from ocpf_post import local_store
    from ocpf_post.state import state_dir
    try:
        cycle = local_store.read(state_dir() / "collection-cycle.json")
        result["collection_cycle"] = cycle
        observed = cycle.get("observed_at")
        moment = now or datetime.now(timezone.utc)
        stale = not observed or not timedelta(0) <= moment - datetime.fromisoformat(observed.replace("Z", "+00:00")) <= timedelta(minutes=45)
        failed = [r["stage"] for r in cycle.get("stages", []) if r.get("status") != "completed"]
        if stale or failed:
            result["findings"].append({"level": "warning", "code": "collection_attention",
                                       "message": "Independent collection is stale, unobserved or has incomplete stages.",
                                       "stale": stale, "stages": failed})
            if result["status"] == "ok":
                result["status"] = "warning"
        result["checks"]["collection_cycle"] = "observed" if observed else "not_observed"
    except (OSError, ValueError, KeyError, TypeError):
        result["checks"]["collection_cycle"] = "unavailable"
        result["findings"].append({"level": "warning", "code": "collection_report_unavailable", "message": "The local collection report could not be read."})
        if result["status"] == "ok":
            result["status"] = "warning"
    return result


def main():
    args = core.build_parser().parse_args()
    result = report(hours=args.hours, overdue_minutes=args.overdue_minutes,
                    executing_minutes=args.executing_minutes, source_age_minutes=args.source_age_minutes,
                    check_timers=not args.skip_timers)
    if args.json:
        print(json.dumps(result, indent=2, ensure_ascii=False))
    else:
        print(f"post-once {result['cli_version']} health: {result['status'].upper()}")
        print(f"Observed at {result['generated_at']}; publication window {result['window_hours']} hours")
        att = result.get("runtime_attestation", {})
        print("runtime_attestation: " + json.dumps({
            k: att.get(k) for k in ("git_commit_sha", "allocator_engine", "policy_config_sha256",
                                    "checkout_clean", "repository_main_sha",
                                    "local_matches_repository_main", "repository_has_newer_revision")
        }, ensure_ascii=False))
        admission = result.get("admission_pressure", {})
        print("legacy_admission_diagnostic: " + json.dumps({
            "admitted": admission.get("admitted"), "mode": admission.get("mode"),
            "reasons": admission.get("reasons"),
            "eligible_unreserved": (admission.get("metrics") or {}).get("eligible_unreserved")
        }, ensure_ascii=False))
        scoped = result.get("scoped_admission", {})
        print("scoped_admission: " + json.dumps({"observed_at": scoped.get("observed_at"),
              "global_paused": scoped.get("global_paused"), "eligible_unreserved": scoped.get("eligible_unreserved"),
              "scopes": {key: row.get("mode") for key, row in scoped.get("scopes", {}).items()}}))
        for name in ("published_by_provider_status", "recent_schedule_states", "capacity", "account_capacity", "publication_mix"):
            print(f"{name}: {json.dumps(result.get(name), ensure_ascii=False)}")
        for finding in result["findings"]:
            context = {k: v for k, v in finding.items() if k not in {"level", "code", "message"}}
            print(f"[{finding['level'].upper()}] {finding['code']}: {finding['message']} {json.dumps(context)}")
        for limitation in result["limitations"]:
            print(f"Scope: {limitation}")
    raise SystemExit({"ok": 0, "warning": 1, "unknown": 2, "attention": 3}[result["status"]])
