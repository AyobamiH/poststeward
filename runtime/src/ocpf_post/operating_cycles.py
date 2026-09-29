"""Bounded collection and local summaries outside the reservation critical path."""
from __future__ import annotations

import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timedelta, timezone

from ocpf_post import local_store
from ocpf_post.state import state_dir

ROOT = Path(__file__).resolve().parents[2]


def outcome(payload):
    """Normalise CLI evidence, retaining provider and no-work outcomes."""
    if not isinstance(payload, dict):
        return 'unknown', {}
    states = []
    for field, status_key in (('vaults', 'result'), ('projects', 'status'), ('observations', 'result')):
        states += [r.get(status_key, 'unknown') for r in payload.get(field, []) if isinstance(r, dict)]
    states += [r.get('status', 'unknown') for r in payload.get('polls', {}).values() if isinstance(r, dict)]
    if payload.get('status'):
        states.append(payload['status'])
    from collections import Counter
    counts = dict(Counter(str(s) for s in states))
    bad = {'unavailable', 'unknown', 'stale', 'collection_unavailable', 'source_guard_failed', 'history_gap',
           'pending_overflow', 'profile_changed_review_required', 'snapshot_changed', 'attention', 'failed', 'unsupported'}
    good = {'available', 'observed', 'ok', 'completed', 'synced', 'signals_available'}
    if set(states) & bad:
        return ('partial' if set(states) & good else 'attention'), counts
    if 'partial' in states:
        return 'partial', counts
    if 'insufficient_evidence' in states or payload.get('missed_windows'):
        return 'insufficient_evidence', counts
    if not states and any(k in payload for k in ('observations', 'vaults', 'projects', 'polls')):
        return 'idle', counts
    if states and set(states) <= {'no_recent_local_publications', 'disabled', 'due', 'already_running'}:
        return 'idle', counts
    return 'completed', counts


def stage(name, args, seconds):
    started = time.monotonic()
    counts = {}
    # Every child gets the repository src path explicitly. The shell CLI already
    # does this for public commands; internal read-only workers use the same
    # isolated subprocess boundary without depending on an editable install.
    pythonpath = str(ROOT / 'src')
    if os.environ.get('PYTHONPATH'):
        pythonpath += os.pathsep + os.environ['PYTHONPATH']
    env = {**os.environ, 'PYTHONPATH': pythonpath}
    # Private, disk-backed output avoids holding large vault/operations responses
    # in memory. Never print provider output, credentials or candidate copy here.
    with tempfile.TemporaryFile() as output:
        process = subprocess.Popen(args, cwd=ROOT, env=env, stdout=output, stderr=output, start_new_session=True)
        try:
            code = process.wait(timeout=seconds)
            status = "completed" if code == 0 else "attention"
            output.seek(0)
            bounded = output.read(1_000_001)
            if code == 0 and len(bounded) <= 1_000_000:
                try:
                    payload = json.loads(bounded)
                except (ValueError, UnicodeDecodeError):
                    payload = None
                status, counts = outcome(payload)
            elif code == 0:
                status = 'unknown'
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGTERM)
            try:
                process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait(timeout=3)
            code, status = None, "timed_out"
    return {"stage": name, "status": status, "exit_code": code, "outcome_counts": counts,
            "duration_seconds": round(time.monotonic() - started, 2), "budget_seconds": seconds}


def _campaign_readback_pending():
    from ocpf_post.publication_reconcile import pending
    return pending()


def _reply_readback_pending():
    from ocpf_post.engagement import read
    return any(row.get('status') in {'published_unverified', 'ambiguous_effect', 'sending'}
               for row in read()['inbox'].values())


def _stamp():
    return datetime.now(timezone.utc).isoformat()


def collect():
    from ocpf_post.vault_sync import policies
    path = state_dir() / "collection-cycle.json"
    with local_store.try_locked(path) as acquired:
        if not acquired:
            return {
                "schema_version": 1,
                "status": "already_running",
                "observed_at": _stamp(),
                "stages": [],
                "boundary": (
                    "Another collection cycle owns the single-writer lock. "
                    "This invocation performed no collection, readback, metric, reply or publication work."
                ),
            }
        previous = local_store.read(path)
        report = {"schema_version": 1, "started_at": _stamp(), "stages": [], "vault_attempts": previous.get("vault_attempts", {})}
        def record(row):
            report["stages"].append(row)
            report["observed_at"] = _stamp()
            local_store.write(path, report)
        def run(name, command, seconds):
            try:
                row = stage(name, [str(ROOT / "ocpf-post"), *command], seconds)
            except (OSError, ValueError, subprocess.SubprocessError) as exc:
                row = {"stage": name, "status": "unavailable", "error_type": type(exc).__name__}
            record(row)
        def run_internal(name, module, command, seconds):
            try:
                row = stage(name, [sys.executable, '-m', module, *command], seconds)
            except (OSError, ValueError, subprocess.SubprocessError) as exc:
                row = {"stage": name, "status": "unavailable", "error_type": type(exc).__name__}
            record(row)
        def run_if_pending(name, probe, runner):
            try:
                pending = probe()
            except (OSError, ValueError, KeyError, TypeError) as exc:
                record({"stage": name, "status": "unavailable", "error_type": type(exc).__name__})
                return
            if pending:
                runner()
        run("source-observation", ["replenish", "observe", "--apply", "--budget-seconds", "150"], 215)
        # Each vault gets a bounded turn, rotated by previous attempt so one slow
        # document cannot starve the remaining sources. Freshness gates still run
        # at publication and withdrawal reconciliation remains enabled.
        deadline = time.monotonic() + 180
        for identifier, policy in sorted(policies().items(), key=lambda item: (report["vault_attempts"].get(item[0], ""), item[0])):
            if not policy["enabled"] or time.monotonic() >= deadline:
                continue
            report["vault_attempts"][identifier] = _stamp()
            run("vault:" + identifier, ["vault", "sync", "--apply", "--vault-id", identifier], min(30, max(1, int(deadline - time.monotonic()))))
        # Existing-ID reconciliation belongs before performance capture so a
        # previously unverified effect can become eligible for its truthful age
        # window in the same collection cycle. It never creates or retries a post.
        run_if_pending("campaign-readback", _campaign_readback_pending,
                       lambda: run_internal("campaign-readback", "ocpf_post.open_work", ["readback", "--apply"], 105))
        run("metrics", ["performance", "capture-due", "--apply"], 75)
        run("performance-feedback", ["performance", "feedback", "--apply"], 30)
        run("inbound-replies", ["engagement", "sync", "--apply"], 75)
        # Reply reconciliation is a separate existing-ID readback path. Only
        # unresolved existing effects spawn this stage; normal healthy cycles pay
        # no extra provider-read cost.
        run_if_pending("reply-readback", _reply_readback_pending,
                       lambda: run("reply-readback", ["engagement", "reconcile", "--apply"], 60))
        # Registered outcome sources are pull-only and validate every event
        # against an exact verified publication before durable ingestion.
        run_internal("outcome-connectors", "ocpf_post.outcome_connectors", ["--apply"], 45)
        # Compact acceptance read models run only after collection, readback,
        # metrics, persisted feedback and inbound state are current. They write
        # their own local projection and have no provider or consequence path.
        run_internal("acceptance-views", "ocpf_post.acceptance_views", ["--apply"], 20)
        run("operations", ["operations", "--all", "--save"], 75)
        report["completed_at"] = _stamp()
        worker = local_store.read(state_dir() / 'reply-worker.json')
        history = previous.get('history', [])
        history.append({'completed_at': report['completed_at'],
                        'stages': {r['stage']: r['status'] for r in report['stages']},
                        'reply_cycle': worker.get('last_cycle')})
        report['history'] = history[-192:]
        local_store.write(path, report)
    return report


def summary():
    """Deduplicate local incidents; no outgoing message or resend authority."""
    from ocpf_post.scheduler import schedule_records
    from ocpf_post.source_observations import load
    incidents = {}
    for row in schedule_records():
        status = row.get("status")
        if status not in {"ambiguous_effect", "failed", "executing"}:
            continue
        if status == "executing" and datetime.now(timezone.utc) - datetime.fromisoformat(row["updated_at"].replace("Z", "+00:00")) < timedelta(minutes=10):
            continue
        key = "schedule:" + row["schedule_id"]
        incidents[key] = {"code": status, "schedule_id": row["schedule_id"], "provider": row["provider"],
                          "account_id": row.get("account_id"), "campaign": row["campaign"],
                          "action": "Inspect receipt and provider evidence read-only; do not reset or resend"}
    for project, row in load()["projects"].items():
        if row.get("collection_error") or row.get("source_ok") is False or row.get("status") != "observed":
            incidents["source:" + project] = {"code": "source_observation_attention", "project": project,
                                             "action": "Review source identity, authority and buffered history"}
    cycle = local_store.read(state_dir() / "collection-cycle.json")
    for row in cycle.get("stages", []):
        if row["status"] not in {"completed", "idle", "insufficient_evidence"}:
            incidents["collector:" + row["stage"]] = {"code": row["status"], "stage": row["stage"],
                                                      "action": "Inspect the collector locally; publishing has an independent timer"}
    worker = local_store.read(state_dir() / 'reply-worker.json')
    try:
        from ocpf_post.reply_worker import policy as reply_policy
        reply_settings = reply_policy()
    except (OSError, ValueError, KeyError, TypeError):
        reply_settings = {'enabled': False}
    if reply_settings.get('enabled'):
        last_cycle = worker.get('last_cycle') or {}
        try:
            last_at = datetime.fromisoformat(str(last_cycle['observed_at']).replace('Z', '+00:00')).astimezone(timezone.utc)
            stale = datetime.now(timezone.utc) - last_at > timedelta(minutes=45)
        except (KeyError, TypeError, ValueError):
            stale = True
        if stale:
            incidents['reply-worker:stale'] = {
                'code': 'reply_worker_stale',
                'action': 'Inspect the enabled reply worker locally; do not infer missing replies or resend uncertain effects',
            }
    for identity, row in worker.get('items', {}).items():
        if row.get('status') in {'attention', 'review_required', 'ambiguous_effect', 'sending', 'published_unverified', 'rejected'}:
            incidents['reply:' + identity] = {'code': row['status'], 'inbox_id': identity,
                                            'action': 'Inspect engagement worker-status; never resend an uncertain outcome'}
    alert_state = local_store.read(state_dir() / "alert-delivery.json")
    if alert_state.get("status") == "unavailable":
        incidents["alert-delivery:unavailable"] = {
            "code": "alert_delivery_unavailable",
            "action": "Inspect the notification endpoint/credential locally; incident state remains authoritative",
        }
    calendar = local_store.read(state_dir() / "operating-calendar.json")
    for row in calendar.get("deadline_risks", []):
        key = "deadline:" + row["campaign"] + ":" + row["provider"] + ":" + str(row.get("account_id"))
        incidents[key] = {"code": "deadline_without_slot", "campaign": row["campaign"], "provider": row["provider"],
                          "account_id": row.get("account_id"), "expires_at": row["expires_at"],
                          "action": "Review the deadline and available capacity; approval and expiry are unchanged"}
    path = state_dir() / "operating-incidents.json"
    with local_store.locked(path):
        old = local_store.read(path)
        previous = old.get("incidents", {})
        stamp = _stamp()
        for key, row in incidents.items():
            row.update(first_seen_at=previous.get(key, {}).get("first_seen_at", stamp), last_seen_at=stamp)
        result = {"schema_version": 1, "observed_at": stamp, "incidents": incidents,
                  "new_incident_ids": sorted(set(incidents) - set(previous)),
                  "resolved_incident_ids": sorted(set(previous) - set(incidents)),
                  "boundary": "Local deduplicated attention only. Optional alert delivery is a separate notification-only layer; no publication, repair, acknowledgement or retry authority."}
        local_store.write(path, result)
    return result


def main():
    import sys as _sys
    mode = _sys.argv[1]
    if mode == "collect":
        result = collect()
    elif mode == "respond":
        from ocpf_post.reply_worker import run as respond
        report = respond(apply=True)
        result = {'schema_version': 1, 'enabled': report['enabled'],
                  'model_credential_present': report['model_credential_present'],
                  'last_cycle': report['last_cycle'],
                  'report_file': str(state_dir() / 'reply-worker.json')}
    elif mode == "summary":
        result = summary()
    elif mode == "report":
        result = {"schema_version": 1, "stages": [
            stage("queue-watch", [str(ROOT / "ocpf-post"), "portfolio", "watch", "--apply"], 60),
            stage("calendar", [str(ROOT / "ocpf-post"), "portfolio", "calendar", "--save"], 60),
            stage("incidents", [str(ROOT / "scripts/run-operating-cycle"), "summary"], 30),
            stage("alert-delivery", [sys.executable, "-m", "ocpf_post.alert_delivery", "--apply"], 30)]}
    else:
        raise ValueError("Unknown operating cycle")
    if mode == "summary":
        result = {"observed_at": result["observed_at"], "incident_count": len(result["incidents"]),
                  "new_incident_count": len(result["new_incident_ids"]), "report_file": str(state_dir() / "operating-incidents.json")}
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
