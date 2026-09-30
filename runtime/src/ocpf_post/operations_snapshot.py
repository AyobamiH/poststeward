"""Automatically saved portfolio observations; no publishing or repair authority."""
from datetime import datetime, timezone

from ocpf_post import __version__, local_store
from ocpf_post.state import config_dir, state_dir


def _metadata_fingerprints(paths):
    """Metadata only; never read credential or copy contents."""
    existing = sorted({p for p in paths if p.is_file()})
    return {str(p): (s.st_ino, s.st_size, s.st_mtime_ns, s.st_ctime_ns)
            for p in existing for s in [p.stat()]}


def _fingerprints():
    # Broad operations-report guard. It intentionally observes almost all local
    # structured state because the report projects many independent subsystems.
    paths = set()
    for root in (config_dir(), state_dir()):
        if root.exists():
            paths.update(p for p in root.rglob('*') if p.is_file()
                         and p.suffix in {'.json', '.jsonl', '.txt', '.md'}
                         and not p.name.startswith('operations-report'))
    return _metadata_fingerprints(paths)


def coverage_fingerprints():
    """Only inputs that can change editorial coverage/supply decisions.

    The old workpack guard reused the broad operations fingerprint, so unrelated
    alert, acceptance, reply-worker and report churn could repeatedly invalidate
    an otherwise coherent editorial snapshot. Repository-owned definitions are
    protected separately by the clean exact Git revision in editorial handoff.
    """
    config = config_dir()
    state = state_dir()
    paths = {
        config / 'portfolio-policy.json',
        config / 'account-profiles.json',
        config / 'vaults.json',
        config / 'runtime-sources.json',
        config / 'linkedin-token.json',
        state / 'source-observations.json',
        state / 'vault-observations.json',
        state / 'schedule-events.jsonl',
        state / 'publish-receipts.jsonl',
        state / 'publication-readbacks.json',
        state / 'performance-snapshots.jsonl',
        state / 'performance-feedback.json',
        state / 'performance-window-state.json',
    }
    accounts = config / 'accounts'
    if accounts.exists():
        paths.update(p for p in accounts.rglob('*.json') if p.is_file())
    runtime_campaigns = state / 'runtime-campaigns'
    if runtime_campaigns.exists():
        paths.update(p for p in runtime_campaigns.rglob('*')
                     if p.is_file() and p.suffix in {'.json', '.txt'})
    return _metadata_fingerprints(paths)


def _observe(fn):
    try:
        return fn()
    except (OSError, ValueError, KeyError, TypeError, AttributeError, RuntimeError) as exc:
        return {'status': 'unavailable', 'error_type': type(exc).__name__}


def portfolio_report(*, now=None):
    # Retry the observation only. Never stop writers, rerun allocation or reuse
    # the rejected attempt's publication inputs in a later attempt.
    for attempt in range(1, 4):
        result = _portfolio_attempt(now=now)
        result['snapshot_attempts'] = attempt
        if result['status'] != 'snapshot_changed':
            return result
    return result


def _portfolio_attempt(*, now=None):
    from ocpf_post.registry import load_registry
    from ocpf_post.operations import report
    from ocpf_post.source_receipts import publication_inputs
    from ocpf_post.capacity_experiment import report as capacity_report, delivery_gate, ACCOUNTS
    from ocpf_post.engagement import report as engagement_report
    from ocpf_post.queue_watch import report as queue_report
    from ocpf_post.runtime_attestation import attest
    from ocpf_post.admission import decide as admission_decision
    now = now or datetime.now(timezone.utc)
    result = {'schema_version': 1, 'cli_version': __version__, 'scope': 'portfolio',
              'observed_at': now.isoformat(), 'status': 'observed', 'projects': {},
              'boundary': 'One bounded observation, not completion of all work. Scheduled receipts require exact identity matches; verified and unverified remain distinct. Runtime attestation may make a read-only GitHub revision observation. No social-provider calls, failed-send retries, capacity changes, copy renewal or publication. OAuth longevity and vault-edit validation retain their separate evidence requirements.'}
    try:
        before = _fingerprints()
        inputs = publication_inputs()
        registry = load_registry()
        result['runtime_attestation'] = _observe(lambda: attest(now=now))
        result['admission_pressure'] = _observe(lambda: admission_decision(now=now, persist=False))
        for project in sorted(registry['projects']):
            result['projects'][project] = _observe(lambda p=project: report(
                p, now=now, inputs=inputs, include_account_reports=False))
        from ocpf_post.account_profiles import status as account_status
        result['additional_accounts'] = _observe(account_status)
        result['capacity_experiment'] = _observe(lambda: capacity_report(now=now))
        result['current_delivery_gates'] = {
            p: _observe(lambda p=p: delivery_gate(p, now, receipts=inputs[2], schedules=inputs[1]))
            for p in ACCOUNTS}
        result['engagement'] = _observe(lambda: engagement_report(now=now))
        result['queue_supervision'] = _observe(lambda: queue_report(now=now, include_records=True))
        from ocpf_post.scoped_admission import status as scoped_status
        result['scoped_admission'] = _observe(scoped_status)
        for name, filename in [
            ('calendar', 'operating-calendar.json'),
            ('collection_cycle', 'collection-cycle.json'),
            ('operating_incidents', 'operating-incidents.json'),
            ('reply_worker', 'reply-worker.json'),
            ('performance_feedback', 'performance-feedback.json'),
            ('acceptance_views', 'acceptance-views.json'),
            ('outcome_connectors', 'outcome-connectors.json'),
            ('alert_delivery', 'alert-delivery.json'),
        ]:
            result[name] = _observe(lambda filename=filename: local_store.read(state_dir() / filename))
        after = _fingerprints()
        if before != after:
            # Do not expose mixed-time milestones as closure evidence.
            keys = ('schema_version', 'cli_version', 'scope', 'observed_at', 'boundary', 'runtime_attestation', 'admission_pressure')
            return {**{k: result[k] for k in keys},
                    'status': 'snapshot_changed', 'projects': {}, 'next_action': 'observe_again_on_next_refill',
                    'changed_input_count': sum(before.get(p) != after.get(p) for p in before.keys() | after.keys())}
    except (OSError, ValueError, KeyError, TypeError, AttributeError, RuntimeError) as exc:
        keys = ('schema_version', 'cli_version', 'scope', 'observed_at', 'boundary')
        return {**{k: result[k] for k in keys},
                'status': 'unavailable', 'error_type': type(exc).__name__, 'projects': {},
                'runtime_attestation': result.get('runtime_attestation', {'status': 'unavailable'}),
                'admission_pressure': result.get('admission_pressure', {'status': 'unavailable'})}
    return result


def summary(result):
    projects = result.get('projects', {}) if result.get('scope') == 'portfolio' else {result['project']: result}
    progress = []
    for project, observation in projects.items():
        items = observation.get('items', {})
        for kind, count_key in [('generated_publication', 'generated_campaign_count'),
                                ('brief_publication', 'reviewed_campaign_count'),
                                ('vault_publication', 'vault_campaign_count')]:
            item = items.get(kind, {})
            if item.get(count_key, 0):
                progress.append({'project': project, 'kind': kind, 'status': item['status'],
                                 'campaigns': item[count_key],
                                 'matched_scheduled_receipts': len(item['scheduled_publications']),
                                 'verified_receipts': item['readback_verified_count']})
    queue = result.get('queue_supervision', {})
    return {'status': result.get('status', 'observed'), 'observed_at': result['observed_at'],
            'snapshot_attempts': result.get('snapshot_attempts'),
            'changed_input_count': result.get('changed_input_count'),
            'next_action': result.get('next_action'),
            'runtime_attestation': result.get('runtime_attestation', {}),
            'admission_pressure': result.get('admission_pressure', {}),
            'publication_progress': progress,
            'unavailable_projects': [p for p, r in projects.items() if r.get('status') == 'unavailable'],
            'additional_accounts': result.get('additional_accounts', {}),
            'account_capacity': queue.get('account_capacity', {}),
            'effective_targets': result.get('capacity_experiment', {}).get('effective_targets'),
            'current_delivery_gates': result.get('current_delivery_gates', {}),
            'queue_status': queue.get('status', 'not_in_scope'), 'queue_issue_counts': queue.get('issue_counts', {}),
            'engagement': result.get('engagement', {}).get('counts', {}),
            'reply_worker': result.get('reply_worker', {}).get('last_cycle'),
            'performance_feedback': {k: result.get('performance_feedback', {}).get(k) for k in ('status', 'observed_at', 'selection_adjustment_available')},
            'calendar': {k: result.get('calendar', {}).get(k) for k in ('status', 'observed_at', 'accounts', 'unselected_count')},
            'collection_observed_at': result.get('collection_cycle', {}).get('observed_at'),
            'incident_count': len(result.get('operating_incidents', {}).get('incidents', {})),
            'acceptance_status': result.get('acceptance_views', {}).get('status'),
            'outcome_connector_statuses': {
                key: value.get('status') for key, value in
                result.get('outcome_connectors', {}).get('connectors', {}).items()
                if isinstance(value, dict)
            } if isinstance(result.get('outcome_connectors', {}).get('connectors'), dict) else {},
            'alert_delivery_status': result.get('alert_delivery', {}).get('status'),
            'published_by_command': False}


def save_report(result):
    suffix = '' if result.get('scope') == 'portfolio' else '-' + result['project']
    destination = state_dir() / ('operations-report' + suffix + '.json')
    incomplete = result.get('status') in {'snapshot_changed', 'unavailable'}
    attempt_path = destination.with_name(destination.stem + '-attempt.json')
    with local_store.locked(destination):
        local_store.write(attempt_path, result)
        if not incomplete:
            local_store.write(destination, result)
        previous = _observe(lambda: local_store.read(destination)) if incomplete else {}
        retained = bool(previous and previous.get('status', 'observed') == 'observed')
    return {**summary(result), 'report_file': str(attempt_path if incomplete else destination),
            'last_stable_report_preserved': retained}
