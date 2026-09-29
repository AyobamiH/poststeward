"""Compact saved-report diagnostics; never launch a collection or provider call."""
from collections import Counter


def summary(report, destination):
    rows = report.get('steps', [])
    steps = {r['step']: r.get('result', {}) for r in rows}
    evidence = steps.get('open-delivery-and-operational-evidence', {}).get('sections', {})
    progress = {}
    for project, kinds in evidence.get('deliveries', {}).items():
        if not isinstance(kinds, dict):
            continue
        progress[project] = {}
        for kind, value in kinds.items():
            if not isinstance(value, dict):
                continue
            states = value.get('schedule_states', [])
            progress[project][kind] = {
                'status': value.get('status'),
                'campaigns': value.get('reviewed_campaign_count', value.get('vault_campaign_count')),
                'verified': value.get('readback_verified_count'),
                'schedule_states': dict(Counter(r.get('effective_state') or r.get('status') for r in states)),
                'ledger_schedule_states': dict(Counter(r.get('status') for r in states)),
            }
    health = steps.get('timer-and-publishing-health', {})
    metrics = steps.get('metrics-due', {})
    engagement = evidence.get('engagement', steps.get('inbound-replies', {}))
    capacity = evidence.get('capacity', {})
    current_gates = steps.get('current-delivery-gates', {}).get('delivery_gates') or capacity.get('delivery_gates')
    result = {'saved_report': str(destination), 'observed_at': report.get('observed_at'),
              'publication_progress': progress,
              'named_delivery_schedules': evidence.get('named_delivery_schedules', []),
              'queue_issue_counts': evidence.get('queue', {}).get('issue_counts'),
              'reply_outcomes': engagement.get('counts'),
              'trial_status': capacity.get('status'),
              'sustained_operation': evidence.get('sustained_operation'),
              'incomplete_commands': [r['step'] for r in rows if not r['execution_ok']],
              'outcomes_needing_attention': [{'step': r['step'], 'status': r['status']} for r in rows
                                            if r['status'] not in {'completed', 'idle'}]}
    result['diagnostics'] = {
        'health_findings': health.get('findings'), 'units': health.get('units'),
        'runner_lock': evidence.get('runner_lock'),
        'metrics': {'observations': metrics.get('observations'), 'deferred': metrics.get('deferred'),
                    'missed_window_count': len(metrics.get('missed_windows', [])),
                    'missed_by_provider': dict(Counter(r.get('provider') for r in metrics.get('missed_windows', [])))},
        'polls': {k: {a: b for a, b in v.items() if a not in {'root_scans', 'root_cursors'}}
                  for k, v in engagement.get('polls', {}).items()},
        'reply_readback': steps.get('reply-readback'),
        'campaign_readback': steps.get('campaign-readback'),
        'additional_account_readiness': steps.get('additional-account-readiness'),
        'queue_replay': steps.get('current-queue-replay'),
        'delivery_gates': current_gates,
        'recorded_trial_delivery_gates': capacity.get('delivery_gates'),
        'effective_targets': capacity.get('effective_targets'),
        'google_connection': evidence.get('google_connection'),
        'reply_items': [{k: r.get(k) for k in ('id', 'provider', 'account_id', 'status', 'post_id',
                                              'published_at', 'readback_verified')}
                        for r in engagement.get('items', [])]}
    result['next_action'] = 'Inspect diagnostic reasons; full private evidence remains in saved_report. Unknown effects never resend.'
    return result
