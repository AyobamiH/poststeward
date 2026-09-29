"""Owner-scoped, expiring X/Threads capacity trial. No provider calls or base-policy edits."""
from collections import Counter
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import hashlib
import json
import math
from statistics import median

from ocpf_post import local_store
from ocpf_post.state import state_dir, iter_receipts
from ocpf_post.scheduler import schedule_records
from ocpf_post.schedule_semantics import classify_schedule
from ocpf_post.onboarding import _timestamp

UTC = timezone.utc
TRIAL = 'owner-growth-20260910'
ACCOUNTS = {'x': '1480506376447315969', 'threads': '25914281681582868'}
CONTROLLED_BASE = {'x': 20, 'threads': 20}
LEGACY_BASE = {'x': 20, 'threads': 20, 'linkedin': 6}
# Retained as a compatibility constant for historical tests/state readers.
BASE = LEGACY_BASE


def path():
    return state_dir() / 'capacity-experiment.json'


def stamp(at):
    return at.isoformat().replace('+00:00', 'Z')


def at(value):
    return _timestamp(value, 'experiment timestamp')


def fingerprint(policy):
    return hashlib.sha256(json.dumps(policy, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def _scope_policy(policy):
    """Project a policy onto fields owned by the X/Threads capacity trial.

    LinkedIn policy is deliberately excluded because it is independent operator
    authority. All other current/future policy fields remain in scope so unrelated
    changes still stop the trial rather than silently widening authority.
    """
    scoped = deepcopy(policy)
    providers = scoped.get('providers')
    if isinstance(providers, dict):
        providers.pop('linkedin', None)
    replies = scoped.get('reply_targets')
    if isinstance(replies, dict):
        replies.pop('linkedin', None)
    return scoped


def scope_fingerprint(policy):
    return fingerprint(_scope_policy(policy))


def policy_matches_baseline(policy, data):
    baseline = data.get('baseline_policy')
    if not isinstance(baseline, dict):
        return False
    return scope_fingerprint(policy) == scope_fingerprint(baseline)


def _validate_targets(targets, *, name):
    if not isinstance(targets, dict):
        raise ValueError(f'Invalid {name}')
    keys = frozenset(targets)
    controlled = frozenset(CONTROLLED_BASE)
    legacy = frozenset(LEGACY_BASE)
    if keys not in {controlled, legacy}:
        raise ValueError(f'Invalid {name}')
    for provider in controlled:
        target = targets.get(provider)
        if type(target) is not int or target not in {CONTROLLED_BASE[provider], 24}:
            raise ValueError(f'Invalid {name}')
    if 'linkedin' in targets and targets['linkedin'] != LEGACY_BASE['linkedin']:
        raise ValueError(f'Invalid {name}')
    return targets


def read():
    data = local_store.read(path())
    if data:
        if data.get('id') != TRIAL or data.get('status') not in {'active', 'stopped', 'complete'}:
            raise ValueError('Unknown capacity experiment')
        if not isinstance(data.get('baseline_policy'), dict) or not isinstance(data.get('history'), list):
            raise ValueError('Invalid experiment record')
        # Full stored hash remains immutable tamper evidence, even though runtime
        # equivalence is now scoped to providers this trial actually controls.
        if fingerprint(data['baseline_policy']) != data.get('policy_sha256'):
            raise ValueError('Experiment baseline changed')
        if not timedelta(0) < at(data['ends_at']) - at(data['started_at']) <= timedelta(days=14):
            raise ValueError('Invalid experiment duration')
        at(data['observed_at'])
        _validate_targets(data.get('targets', {}), name='experiment targets')
        for row in data['history']:
            at(row['observed_at'])
            _validate_targets(row.get('targets'), name='target observation')
    return data


def _admission_flow(policy):
    providers = policy.get("providers") if isinstance(policy, dict) else None
    return bool(
        isinstance(providers, dict)
        and any(
            isinstance(providers.get(provider), dict)
            and providers[provider].get("flow_mode") == "admission"
            for provider in ACCOUNTS
        )
    )


def owner_scope(policy):
    from ocpf_post.registry import resolve_account, load_registry
    if policy.get('selection') != 'fair':
        return 'fair_policy_required'
    providers = policy.get('providers')
    if not isinstance(providers, dict):
        return 'baseline_targets_changed'
    if any(
        provider not in providers
        or int(providers[provider].get('daily_target', -1)) != target
        for provider, target in CONTROLLED_BASE.items()
    ):
        return 'baseline_targets_changed'
    for provider, account_id in ACCOUNTS.items():
        account = resolve_account('oneclickpostfactory', provider + '-founder', expected_provider=provider)
        if account['account_id'] != account_id:
            return 'account_binding_changed'
    # Additional profiles have independent captured budgets; the trial overlay
    # remains confined to the original owner accounts. Unknown scopes stop it.
    for project in load_registry()['projects'].values():
        for account in project.get('accounts', {}).values():
            provider = account.get('provider')
            from ocpf_post.account_profiles import profile
            if provider in ACCOUNTS and account.get('account_id') != ACCOUNTS[provider] and not profile(provider, account.get('account_id')):
                return 'additional_account_requires_separate_capacity_scope'
    return None


def delivery_gate(provider, now, receipts=None, schedules=None):
    """24h local evidence, with schedule/receipt identity matched for successes."""
    if receipts is None or schedules is None:
        from ocpf_post.health import read_log, fold_schedules
        try:
            if receipts is None:
                read_log(state_dir() / 'publish-receipts.jsonl')
            if schedules is None:
                fold_schedules(read_log(state_dir() / 'schedule-events.jsonl'))
        except (OSError, ValueError, KeyError, TypeError):
            return {'healthy': False, 'reason': 'delivery_ledger_unreadable'}
    receipts = list(iter_receipts()) if receipts is None else receipts
    schedules = list(schedule_records()) if schedules is None else schedules
    account = ACCOUNTS[provider]
    recent = now - timedelta(hours=24)

    def relevant(row, time_key):
        if row.get('provider') != provider or row.get('account_id') not in {None, account}:
            return False
        try:
            return recent <= at(row[time_key]) <= now
        except (ValueError, KeyError, TypeError):
            # Malformed outcome times cannot establish healthy delivery.
            return True

    failures = [s for s in schedules
                if relevant(s, 'updated_at') and classify_schedule(s, now=now).requires_review]
    uncertain = [r for r in receipts if relevant(r, 'recorded_at') and r.get('status') == 'ambiguous_effect']
    successes = set()
    indexed = {s.get('schedule_id'): s for s in schedules if s.get('schedule_id')}
    for r in receipts:
        if r.get('account_id') != account or not relevant(r, 'recorded_at'):
            continue
        s = indexed.get(r.get('schedule_id'), {})
        keys = ('campaign', 'provider', 'account_id', 'text_sha256', 'post_id')
        if (r.get('status') == 'published_verified' and r.get('readback_verified') is True
                and s.get('status') == 'published_verified' and all(r.get(k) and r.get(k) == s.get(k) for k in keys)):
            try:
                if recent <= at(r['recorded_at']) <= now:
                    successes.add(r['post_id'])
            except (ValueError, KeyError, TypeError):
                pass
    required = 3 if provider == 'threads' else 1
    healthy = not failures and not uncertain and len(successes) >= required
    blockers = []
    for source, rows, time_key in [('schedule', failures, 'updated_at'), ('receipt', uncertain, 'recorded_at')]:
        for row in rows:
            # Deliberately omit provider prose, copy, URLs and raw error bodies.
            item = {k: row.get(k) for k in ('schedule_id', 'campaign', 'provider', 'account_id', 'status', 'post_id')}
            item.update(source=source, observed_at=row.get(time_key), automatic_retry=False)
            try:
                moment = at(row[time_key])
                item['leaves_24h_window_after'] = stamp(moment + timedelta(hours=24))
            except (ValueError, KeyError, TypeError):
                item['leaves_24h_window_after'] = None
            item['next_action'] = ('inspect_execution_without_reset' if row.get('status') == 'executing' else
                                   'reconcile_effect_without_resend' if row.get('status') in {'ambiguous_effect', 'published_unverified'} else
                                   'inspect_schedule_detail_and_matching_receipts')
            blockers.append(item)
    return {'healthy': healthy, 'verified_scheduled_posts_24h': len(successes),
            'required_verified_posts': required, 'recent_failed_or_uncertain': len(failures) + len(uncertain),
            'blockers': blockers, 'blocker_count_basis': 'authoritative schedule semantics plus ambiguous receipt records; one effect can appear in both',
            'window_boundary': 'Leaving this historical window is not repair or permission to retry; all gates are re-evaluated.',
            'reason': 'healthy_local_delivery' if healthy else 'delivery_evidence_not_ready'}


def reconcile(*, apply=False, now=None):
    from ocpf_post.portfolio import load_policy, policy_file
    now = now or datetime.now(UTC)
    if not apply:
        return report(now=now)
    with local_store.locked(path()):
        data = read()
        policy = load_policy(effective=False)
        if _admission_flow(policy):
            if data and data.get("status") == "active":
                data["status"] = "stopped"
                data["stop_reason"] = "superseded_by_admission_flow"
                data["observed_at"] = stamp(now)
                local_store.write(path(), data)
            return report(now=now)
        if not data:
            reason = owner_scope(policy)
            if not policy_file().exists() or reason:
                return {'status': 'not_started', 'reason': reason or 'owner_policy_required'}
            # No running clock until X has actual matching delivery evidence.
            gate = delivery_gate('x', now)
            if not gate['healthy']:
                return {'status': 'not_started', 'reason': gate['reason'], 'x': gate}
            data = {'schema_version': 1, 'id': TRIAL, 'status': 'active', 'started_at': stamp(now),
                    'ends_at': stamp(now + timedelta(days=14)), 'baseline_policy': deepcopy(policy),
                    'policy_sha256': fingerprint(policy), 'history': [], 'targets': dict(CONTROLLED_BASE)}
        elif now < at(data['observed_at']):
            raise ValueError('Clock moved backwards; experiment evidence retained')
        elif data['status'] != 'active':
            return report(now=now)
        if data['status'] == 'active':
            if now >= at(data['ends_at']):
                data['status'] = 'complete'
            elif not policy_matches_baseline(policy, data) or owner_scope(policy):
                data['status'] = 'stopped'
                data['stop_reason'] = 'base_policy_or_account_changed'
        gates = {p: delivery_gate(p, now) for p in ACCOUNTS} if data['status'] == 'active' else {}
        # New observations record only authority this trial actually controls.
        data['targets'] = {
            provider: 24 if gates.get(provider, {}).get('healthy') else CONTROLLED_BASE[provider]
            for provider in ACCOUNTS
        }
        data['observed_at'] = stamp(now)
        data['delivery_gates'] = gates
        entry = {'observed_at': stamp(now), 'targets': data['targets'], 'status': data['status']}
        bucket = int(now.timestamp()) // 900
        if data['history'] and int(at(data['history'][-1]['observed_at']).timestamp()) // 900 == bucket:
            data['history'][-1] = entry
        else:
            data['history'].append(entry)
        # Completed/stopped reconciliation is idempotent, never starts another trial.
        data['history'] = data['history'][-1600:]
        local_store.write(path(), data)
    return report(now=now)


def overlay(policy, *, now=None):
    now = now or datetime.now(UTC)
    try:
        if _admission_flow(policy):
            return policy
        data = read()
        if not data or not policy_matches_baseline(policy, data) or owner_scope(policy):
            return policy
        result = deepcopy(policy)
        active = (data['status'] == 'active' and at(data['started_at']) <= now < at(data['ends_at'])
                  and timedelta(0) <= now - at(data['observed_at']) < timedelta(minutes=45))
        if active:
            # Ignore legacy linkedin:6 observations; LinkedIn is not controlled by
            # this experiment and must never be rewritten by the X/Threads overlay.
            for provider in ACCOUNTS:
                target = data['targets'].get(provider, CONTROLLED_BASE[provider])
                result['providers'][provider]['daily_target'] = target
        for provider in ACCOUNTS:
            raised = any(
                row.get('targets', {}).get(provider, CONTROLLED_BASE[provider]) > CONTROLLED_BASE[provider]
                for row in data['history']
            )
            if raised and at(data['started_at']) <= now < at(data['ends_at']) + timedelta(days=1):
                # Also protect fallback/expiry transitions around prior reservations.
                result['providers'][provider]['minimum_spacing_minutes'] = max(
                    30, int(result['providers'][provider].get('minimum_spacing_minutes', 0)))
        return result
    except (ValueError, OSError, KeyError, TypeError):
        # An unreadable experiment cannot increase publishing authority.
        return policy


def stop(*, now=None):
    now = now or datetime.now(UTC)
    with local_store.locked(path()):
        data = read()
        if not data:
            # A stop before first activation is durable and defeats the timer rollout.
            from ocpf_post.portfolio import load_policy
            policy = load_policy(effective=False)
            data = {'schema_version': 1, 'id': TRIAL, 'started_at': stamp(now),
                    'ends_at': stamp(now + timedelta(days=14)), 'baseline_policy': policy,
                    'policy_sha256': fingerprint(policy), 'history': []}
        data.update(status='stopped', stop_reason='operator_stopped', observed_at=stamp(now),
                    targets=dict(CONTROLLED_BASE))
        local_store.write(path(), data)
    return report(now=now)


def cohort(start, end, provider, account, *, now):
    """Descriptive first-receipt cohorts; missing metrics are never zero-filled."""
    from ocpf_post.performance_review import publications
    from ocpf_post.performance import iter_snapshots
    from ocpf_post.campaigns import builtin_manifest
    pub = {k: v for k, v in publications().items() if k[1:3] == (provider, account) and start <= v['at'] < end}
    selected = {}
    for row in iter_snapshots():
        key = tuple(row.get(k) for k in ('campaign', 'provider', 'account_id', 'post_id'))
        if key not in pub or row.get('availability', {}).get('status') != 'available':
            continue
        try:
            captured = at(row['captured_at'])
            age = (captured - pub[key]['at']).total_seconds() / 3600
        except (ValueError, KeyError, TypeError):
            continue
        if captured > now or abs(age - 24) > 2:
            continue
        if key not in selected or abs(age - 24) < selected[key][0]:
            selected[key] = (abs(age - 24), row)
    metrics = {}
    for _, row in selected.values():
        for key, value in row.get('metrics', {}).items():
            if type(value) in (int, float) and math.isfinite(value) and value >= 0:
                metrics.setdefault(key, []).append(value)
    projects = Counter()
    daily = Counter()
    from zoneinfo import ZoneInfo
    for key, publication in pub.items():
        try:
            project = builtin_manifest(key[0]).get('project') or 'unknown'
        except (ValueError, OSError):
            project = 'unknown'
        projects[project] += 1
        daily[publication['at'].astimezone(ZoneInfo('Europe/London')).date().isoformat()] += 1
    return {'from': stamp(start), 'to': stamp(end), 'publication_count': len(pub),
            'daily_publications': dict(daily), 'by_project': dict(projects),
            'matched_24h_observations': len(selected), 'missing_24h_observations': len(pub) - len(selected),
            'metrics': {k: {'observed_total': sum(v), 'median_per_observed_post': median(v),
                            'observed_posts': len(v), 'missing_posts': len(pub) - len(v)} for k, v in metrics.items()},
            'website_enquiries': None, 'signups': None,
            'boundary': '24h ±2h metrics, exact provider/account/post receipts. Partial days and missing data are explicit. No causal conclusion or inferred conversions.'}


def report(*, now=None, include_metrics=False):
    from ocpf_post.portfolio import load_policy
    now = now or datetime.now(UTC)
    data = read()
    if not data:
        return {'schema_version': 1, 'status': 'not_started', 'id': TRIAL}
    policy = load_policy(effective=False)
    status = "superseded" if _admission_flow(policy) else data['status']
    if status == 'active':
        if now >= at(data['ends_at']):
            status = 'complete'
        elif not policy_matches_baseline(policy, data) or owner_scope(policy):
            status = 'policy_changed'
        elif not timedelta(0) <= now - at(data['observed_at']) < timedelta(minutes=45):
            status = 'stale'
    effective = overlay(policy, now=now)
    result = {k: data.get(k) for k in ('id', 'started_at', 'ends_at', 'observed_at', 'delivery_gates', 'stop_reason')}
    result.update(schema_version=1, status=status,
                  effective_targets={p: s['daily_target'] for p, s in effective['providers'].items()},
                  effective_daily_ceilings={p: (s.get('hard_daily_ceiling') if s.get('flow_mode') == 'admission' else s.get('daily_target')) for p, s in effective['providers'].items()},
                  target_observation_count=len(data['history']),
                  boundary=('Historical owner-scoped X/Threads capacity trial. Admission-driven founder flow supersedes its runtime overlay when enabled; stored trial evidence remains descriptive and immutable. No provider call, automatic extension or base-policy overwrite. Existing reservations retain their prior authority.'))
    if include_metrics:
        result['observed_target_history'] = data['history']
        start = at(data['started_at'])
        end = min(now, at(data['ends_at']))
        accounts = {**ACCOUNTS, 'linkedin': 'urn:li:person:UBgFeo6HdJ'}
        result['cohorts'] = {
            p: {
                'before': cohort(start - timedelta(days=14), start, p, a, now=now),
                'during': cohort(start, max(start, end), p, a, now=now),
            }
            for p, a in accounts.items()
        }
        result['conclusion'] = 'descriptive_only; no automatic capacity increase from metrics'
    return result


def save_report():
    result = report(include_metrics=True)
    destination = state_dir() / 'capacity-experiment-report.json'
    with local_store.locked(destination):
        local_store.write(destination, result)
    return {'status': result['status'], 'report_file': str(destination), 'published': False}


def add_parsers(sub):
    from ocpf_post.onboarding import _run_cli
    parser = sub.add_parser('experiment', help='Run the owner-authorised, reversible 14-day capacity trial')
    commands = parser.add_subparsers(dest='experiment_command', required=True)
    start = commands.add_parser('reconcile', help='Inspect, or start/update the scoped trial from local delivery evidence')
    start.add_argument('--apply', action='store_true')
    start.set_defaults(func=lambda a: _run_cli(lambda: reconcile(apply=a.apply)))
    status = commands.add_parser('status')
    status.set_defaults(func=lambda a: _run_cli(report))
    review = commands.add_parser('report')
    review.add_argument('--save', action='store_true')
    review.set_defaults(func=lambda a: _run_cli(save_report if a.save else lambda: report(include_metrics=True)))
    halt = commands.add_parser('stop', help='Stop the trial durably; future timer runs cannot restart it')
    halt.set_defaults(func=lambda a: _run_cli(stop))
