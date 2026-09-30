"""Consolidated evidence for open OPL items; established milestones stay closed."""
from collections import Counter
from datetime import datetime, timezone
import json
from ocpf_post import local_store
from ocpf_post.state import state_dir
from ocpf_post.engagement import at

PRESERVED = ['OPL-01', 'OPL-08', 'OPL-09', 'OPL-15', 'OPL-17', 'OPL-19:first_model_and_nested_response']


def sustained(cycle, now):
    rows = cycle.get('history', [])
    times = sorted(at(r['completed_at']) for r in rows if r.get('completed_at'))
    recent = [r for r in rows if r.get('completed_at') and (now - at(r['completed_at'])).total_seconds() <= 86400]
    gaps = [(b - a).total_seconds() for a, b in zip(times, times[1:])]
    span = (times[-1] - times[0]).total_seconds() if times else 0
    age = (now - times[-1]).total_seconds() if times else None
    bad = sum(any(s in {'attention', 'unavailable', 'timed_out', 'unknown'} for s in r.get('stages', {}).values()) for r in recent)
    return {'status': 'observed' if span >= 86400 and age is not None and 0 <= age <= 2700 and max(gaps or [0]) <= 2700 and not bad else 'insufficient_evidence',
            'retained_cycles': len(rows), 'observed_span_hours': span / 3600,
            'latest_age_seconds': age, 'max_gap_seconds': max(gaps) if gaps else None,
            'attention_cycles_last_24h': bad,
            'boundary': 'Retained collector completion history with gap/error checks; first day remains unobserved. Partial provider coverage is separately reported, not a guarantee of complete collection.'}


def queue_replay():
    from ocpf_post.portfolio_queue import snapshot, replay
    value = snapshot(horizon_minutes=1440)
    result = replay(value)
    root = state_dir() / 'open-work-replays'
    name = value['snapshot_sha256']
    local_store.write(root / (name + '-snapshot.json'), value)
    local_store.write(root / (name + '-replay.json'), result)
    comparison = result['service_comparison']
    expiry_comparison = {}
    for kind, rows in result['expiry_comparison'].items():
        vaults = [r for r in rows if '-VAULT-' in r['campaign'] or '-THVAULT-' in r['campaign']]
        expiry_comparison[kind] = {
            'selected': [{k: r[k] for k in ('campaign', 'provider', 'account_id', 'project', 'projected_run_at', 'expires_at')}
                         for r in vaults if r['status'] == 'selected_in_projection'],
            'waiting_by_project': dict(Counter(r['project'] for r in vaults if r['status'] != 'selected_in_projection'))}
    return {'schema_version': 1, 'status': 'observed', 'snapshot_sha256': name,
            'baseline_reproduced': result['baseline_reproduced'],
            'snapshot_file': str(root / (name + '-snapshot.json')), 'replay_file': str(root / (name + '-replay.json')),
            'previous_brief_selections': [r for r in comparison['previous_fair'] if 'POSTONCE-BRIEF-080-' in r['campaign']],
            'current_brief_selections': [r for r in comparison['current_fair'] if 'POSTONCE-BRIEF-080-' in r['campaign']],
            'fair_summary': result['fair_summary'],
            'vault_expiry_comparison': expiry_comparison,
            'boundary': 'Current frozen host inventory, reproducible comparison only. No reservation, expiry renewal or publication.'}


def runner_lock(now):
    """Read lock ownership without exposing the token, breaking it or starting work."""
    from ocpf_post.scheduler import runner_lock_file, _pid_alive, RUNNER_LOCK_STALE_SECONDS
    path = runner_lock_file()
    try:
        data = json.loads(path.read_text())
    except FileNotFoundError:
        return {'status': 'observed', 'present': False}
    pid = int(data['pid'])
    age = now.timestamp() - float(data['created_epoch'])
    return {'status': 'observed', 'present': True, 'pid': pid,
            'pid_alive': _pid_alive(pid), 'age_seconds': age,
            'stale_seconds': RUNNER_LOCK_STALE_SECONDS,
            'boundary': 'PID presence alone does not establish process identity. No lock removal or retry.'}


def report(*, now=None):
    from ocpf_post.source_receipts import publication_inputs, source_receipts
    from ocpf_post.registry import load_registry
    from ocpf_post.operations_snapshot import _fingerprints
    from ocpf_post import capacity_experiment, google_connection, business_outcomes, engagement, reply_worker
    from ocpf_post.account_profiles import status as account_status
    from ocpf_post.queue_watch import report as queue_report
    now = now or datetime.now(timezone.utc)
    before = _fingerprints()
    result = {'schema_version': 1, 'observed_at': now.isoformat(), 'status': 'observed',
              'preserved_milestones': PRESERVED, 'sections': {}}
    def observe(name, fn):
        try:
            result['sections'][name] = fn()
        except (OSError, ValueError, KeyError, TypeError, RuntimeError) as exc:
            result['sections'][name] = {'status': 'unavailable', 'error_type': type(exc).__name__}
    inputs = publication_inputs()
    def deliveries():
        return {project: {'briefs': source_receipts(project, reviewed_briefs=True, inputs=inputs, now=now),
                          'vaults': source_receipts(project, reviewed_vaults=True, inputs=inputs, now=now)}
                for project in sorted(load_registry()['projects'])}
    observe('deliveries', deliveries)
    fields = ('schedule_id', 'campaign', 'provider', 'account_id', 'status', 'run_at', 'post_id', 'text_sha256', 'readback_verified')
    result['sections']['terminal_and_unverified_schedules'] = [{k: row.get(k) for k in fields} for row in inputs[1]
        if row.get('status') in {'failed', 'ambiguous_effect', 'published_unverified', 'executing'}]
    result['sections']['named_delivery_schedules'] = [{k: row.get(k) for k in fields} for row in inputs[1]
        if row.get('campaign', '').startswith(('POSTONCE-BRIEF-080-', 'AGROLINK-001', 'PAS-004', 'PAS-005', 'PAS-006', 'PAS-THVAULT-20260912-004', 'PAS-THVAULT-20260912-005', 'PAS-THVAULT-20260912-006'))]
    observe('queue', lambda: queue_report(now=now, include_records=True))
    observe('capacity', lambda: capacity_experiment.report(now=now, include_metrics=True))
    observe('accounts', account_status)
    observe('google_connection', google_connection.report)
    observe('business_outcomes', business_outcomes.report)
    observe('engagement', lambda: engagement.report(now=now, include_items=True))
    observe('reply_worker', reply_worker.report)
    observe('runner_lock', lambda: runner_lock(now))
    for name, filename in [
        ('vault_lifecycle', 'vault-lifecycle.json'),
        ('publication_readbacks', 'publication-readbacks.json'),
        ('collection', 'collection-cycle.json'),
        ('incidents', 'operating-incidents.json'),
        ('acceptance_views', 'acceptance-views.json'),
        ('outcome_connectors', 'outcome-connectors.json'),
        ('alert_delivery', 'alert-delivery.json'),
    ]:
        observe(name, lambda filename=filename: local_store.read(state_dir() / filename))
    observe('sustained_operation', lambda: sustained(result['sections']['collection'], now))
    connector = result['sections'].get('outcome_connectors', {})
    alert = result['sections'].get('alert_delivery', {})
    connector_count = len(connector.get('connectors', [])) if isinstance(connector, dict) and isinstance(connector.get('connectors'), list) else 0
    connector_enabled = any(row.get('enabled') for row in connector.get('connectors', [])) if connector_count else False
    alert_configured = isinstance(alert, dict) and alert.get('status') not in {None, 'not_configured'}
    result['sections']['external_prerequisites'] = {
        'google_consent_publishing_configuration': 'Owner Cloud Console evidence required',
        'analytics_source': (
            f'{connector_count} outcome connector(s) registered; ' +
            ('at least one is enabled, observed events still determine acceptance' if connector_enabled else 'none enabled yet')
            if connector_count else
            'No authorised outcome connector configured; reviewed file import remains available'
        ),
        'alert_destination': (
            'Alert destination configured; successful delivery evidence determines acceptance'
            if alert_configured else
            'No authorised notification recipient or endpoint supplied'
        ),
        'x_brand_billing_and_write_readiness': 'Read identity/token scope first; billing/first-send authority cannot be inferred from connection',
        'agentproof_unknown_effect': 'Existing provider post ID/evidence required; never resend to resolve uncertainty',
        'historical_external_manual': 'Reconcile held historical reservations/errors/disabled drafts and provider evidence. Metricool remains retired.',
        'capacity_final': 'Await the original 24 September trial end; do not infer benefit from volume'}
    if any(isinstance(section, dict) and section.get('status') == 'unavailable' for section in result['sections'].values()):
        result['status'] = 'partial'
    after = _fingerprints()
    if before != after:
        result.update(status='snapshot_changed', sections={}, next_action='Run the same observation again; no state was repaired')
    result['boundary'] = 'Open delivery and operational evidence only. Established milestones remain closed. Exact revision/account/payload receipts determine publication. Unknown is not failed; no source activation, social publication, replay of effects or retired-service access.'
    return result


def account_readiness():
    from ocpf_post.account_profiles import profiles, directory
    from ocpf_post.providers import for_account
    from ocpf_post.state import read_json
    rows = []
    for profile in profiles().values():
        row = {k: profile[k] for k in ('provider', 'account_id', 'enabled', 'label')}
        token = read_json(directory(row['provider'], row['account_id']) / 'token.json')
        scopes = token.get('scope', '')
        row.update(credential_present=bool(token.get('access_token')),
                   write_scope='present' if row['provider'] == 'x' and 'tweet.write' in str(scopes).split() else 'not_observed',
                   billing_readiness='not_observed', publishing_readiness='not_observed')
        try:
            account = for_account(row['provider'], row['account_id'], require_enabled=False).account()
            row.update(identity='matched' if account.account_id == row['account_id'] else 'mismatch',
                       username=account.username)
        except Exception as exc:
            row.update(identity='unavailable', error_type=type(exc).__name__)
        rows.append(row)
    return {'schema_version': 1, 'status': 'observed', 'accounts': rows,
            'boundary': 'Read identity using the existing account credential. No activation, OAuth reconnection or test post. Token scope is not proof of API credits; unknown billing remains unknown.'}


def main():
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mode', choices=['report', 'queue', 'readback', 'accounts'])
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args()
    if args.mode == 'queue':
        result = queue_replay()
    elif args.mode == 'accounts':
        result = account_readiness()
    elif args.mode == 'readback':
        from ocpf_post.publication_reconcile import reconcile
        result = reconcile(apply=args.apply)
    else:
        result = report()
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
