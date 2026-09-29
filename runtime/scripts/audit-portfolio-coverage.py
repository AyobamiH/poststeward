#!/usr/bin/env python3
"""Read-only, all-project coverage audit. No collection, publication or policy writes."""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
import json
import math
from pathlib import Path
import sys

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[1]
UTC = timezone.utc
FIELDS = ('campaign', 'provider', 'account_id', 'post_id')


def at(value):
    result = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
    if result.tzinfo is None:
        raise ValueError('Evidence timestamp must include an offset')
    return result.astimezone(UTC)


def identity(row):
    values = tuple(row.get(k) for k in FIELDS)
    if not all(isinstance(v, str) and v for v in values):
        raise ValueError('Incomplete publication identity')
    return values


def numeric(value):
    return type(value) in (int, float) and math.isfinite(value) and value >= 0


def fresh(value, now, seconds):
    return bool(value) and 0 <= (now - at(value)).total_seconds() <= seconds


def project_source(name, profile, observation, now):
    if not profile:
        return {'status': 'no_active_repository_source', 'observed_at': None}
    status = observation.get('status', 'not_observed')
    if observation.get('collection_error'):
        status = 'collection_unavailable'
    elif not observation:
        status = 'not_observed'
    elif observation.get('profile_sha256') != profile['fingerprint']:
        status = 'profile_changed_review_required'
    elif status == 'observed' and observation.get('source_ok') is not True:
        status = 'source_guard_not_confirmed'
    elif status == 'observed' and not fresh(observation.get('observed_at'), now, 3600):
        status = 'stale_or_future_observation'
    return {'status': status, 'repository': profile.get('repository'),
            'observed_at': observation.get('observed_at'),
            'head_sha': observation.get('head_sha'), 'readme_sha': observation.get('readme_sha'),
            'pending_events': len(observation.get('pending', [])),
            'inventory_items': profile.get('inventory_items', 0),
            'revision_pinned_items_skipped': profile.get('revision_pinned_items_skipped', 0)}


def build(data, now):
    """Pure projection of a stable input set; every registry project remains visible."""
    registry = data['registry']['projects']
    manifests = data['manifests']
    rows = {}
    for name, project in sorted(registry.items()):
        rows[name] = {'project': name, 'label': project.get('label', name),
                      'source': project_source(name, data['sources'].get(name),
                                               data['source_observations'].get(name, {}), now),
                      'vaults': [], 'routes': []}
    route_data = {}
    for name, project in registry.items():
        for alias, account in project.get('accounts', {}).items():
            key = (name, account['provider'], account['account_id'])
            if key not in route_data:
                route_data[key] = {'project': name, 'provider': key[1], 'account_id': key[2],
                                   'aliases': [], 'intent': set(), 'campaigns': [], 'schedules': [],
                                   'publications': [], 'snapshots': [], 'feedback': [],
                                   'candidates': [], 'exclusions': [], 'uncertainties': []}
            route_data[key]['aliases'].append(alias)
    findings = []
    def route(name, provider, account, context):
        key = (name, provider, account)
        if key not in route_data:
            findings.append({'code': 'unregistered_route_or_project', 'project': name,
                             'provider': provider, 'account_id': account, 'context': context})
            return None
        return route_data[key]
    for name, profile in data['sources'].items():
        for binding in profile['bindings']:
            r = route(name, binding['provider'], binding['account_id'], 'repository_source')
            if r is not None:
                r['intent'].add('repository_source')
    for vault in data['vaults']:
        name = vault['project']
        if name not in rows:
            findings.append({'code': 'unregistered_vault_project', 'project': name})
            continue
        obs = vault['observation']
        status = ('disabled' if not vault['enabled'] else 'not_observed' if not obs else
                  'current' if obs.get('valid_until') and now < at(obs['valid_until'])
                  and obs.get('observed_at') and at(obs['observed_at']) <= now else 'stale_or_invalid')
        rows[name]['vaults'].append({'id': vault['id'], 'status': status,
                                    'observed_at': obs.get('observed_at'),
                                    'valid_until': obs.get('valid_until'),
                                    'active_entries': len(obs.get('active', {})),
                                    'skipped_entries': len(obs.get('skipped', []))})
        if vault['enabled']:
            for binding in vault['bindings']:
                r = route(name, binding['provider'], binding['account_id'], 'vault')
                if r is not None:
                    r['intent'].add('enabled_vault')
    for cid, manifest in manifests.items():
        for binding in manifest['bindings']:
            r = route(manifest['project'], binding['provider'], binding['account_id'], cid)
            if r is not None:
                r['campaigns'].append(cid)
                if manifest.get('allocation_enabled'):
                    r['intent'].add('allocation_opt_in')
    def campaign_route(item):
        cid = item.get('campaign')
        manifest = manifests.get(cid)
        if not manifest:
            findings.append({'code': 'orphan_campaign_evidence', 'campaign': cid})
            return None
        return route(manifest['project'], item.get('provider'), item.get('account_id'), cid)
    for item in data.get('receipt_uncertainties', []):
        r = campaign_route(item)
        if r is not None:
            r['uncertainties'].append(item)
    for schedule in data['schedules']:
        r = campaign_route(schedule)
        if r is not None:
            r['schedules'].append(schedule)
    pubs = {}
    claims = defaultdict(set)
    for publication in data['publications']:
        key = identity(publication)
        if at(publication['published_at']) > now:
            findings.append({'code': 'future_publication', 'campaign': key[0]})
            continue
        pubs[key] = publication
        claims[key[1:]].add(key[0])
    conflicts = {key for key, campaigns in claims.items() if len(campaigns) != 1}
    for key in conflicts:
        findings.append({'code': 'post_claimed_by_multiple_campaigns', 'provider': key[0],
                         'account_id': key[1], 'post_id': key[2], 'campaigns': sorted(claims[key])})
    for key, publication in pubs.items():
        r = campaign_route(publication)
        if r is not None and key[1:] not in conflicts:
            r['publications'].append(publication)
    for snapshot in data['snapshots']:
        key = identity(snapshot)
        if key not in pubs or key[1:] in conflicts:
            findings.append({'code': 'unmatched_metric_identity', 'campaign': key[0]})
            continue
        captured = at(snapshot['captured_at'])
        if captured > now or captured < at(pubs[key]['published_at']):
            findings.append({'code': 'invalid_metric_time', 'campaign': key[0]})
            continue
        r = campaign_route(snapshot)
        if r is not None:
            r['snapshots'].append(snapshot)
    saved = data['feedback']
    feedback_fresh = fresh(saved.get('observed_at'), now, 86400)
    for observation in saved.get('observations', []):
        key = identity(observation)
        publication = pubs.get(key)
        meta = observation.get('editorial') or {}
        if (publication is None or key[1:] in conflicts or not publication['verified']
                or meta.get('text_sha256') != publication.get('text_sha256')
                or at(observation['captured_at']) > now):
            findings.append({'code': 'unmatched_saved_feedback', 'campaign': key[0]})
            continue
        r = campaign_route(observation)
        if r is not None and meta.get('project') == r['project']:
            r['feedback'].append(observation)
    for item in data['candidates']:
        r = campaign_route(item)
        if r is not None:
            r['candidates'].append(item)
    for item in data['exclusions']:
        cid, provider = item['campaign'], item['provider']
        manifest = manifests.get(cid)
        if manifest:
            for binding in manifest['bindings']:
                if binding['provider'] == provider:
                    r = route(manifest['project'], provider, binding['account_id'], cid)
                    if r is not None:
                        r['exclusions'].append(item)
    targets = data['target_ages']
    tolerance = data['tolerance_hours']
    activated = at(data['window_state']['activated_at']) if data['window_state'].get('activated_at') else None
    for key, r in sorted(route_data.items()):
        published = r['publications']
        verified = [p for p in published if p['verified']]
        recent = lambda p, days: now - timedelta(days=days) <= at(p['published_at']) <= now
        active = [s for s in r['schedules'] if s['status'] in data['active_statuses']]
        available = [s for s in r['snapshots'] if s.get('availability', {}).get('status') == 'available']
        numeric_available = [s for s in available if any(numeric(v) for v in s.get('metrics', {}).values())]
        latest = max(verified, key=lambda p: p['published_at'], default=None)
        last_metric = max(numeric_available, key=lambda s: s['captured_at'], default=None)
        available_windows = {(identity(s), s.get('comparable_target')) for s in available}
        windows = {str(age): Counter() for age in targets}
        next_deadline = None
        for publication in published:
            pk = identity(publication)
            if r['provider'] not in data['supported_metric_providers']:
                continue
            for age in targets:
                opened = at(publication['published_at']) + timedelta(hours=age-tolerance)
                deadline = at(publication['published_at']) + timedelta(hours=age+tolerance)
                if now - deadline > timedelta(days=14):
                    continue
                if (pk, age) in available_windows:
                    state = 'captured_available'
                elif activated is None:
                    state = 'activation_unobserved'
                elif age != 24 and deadline < activated:
                    continue
                elif deadline < activated:
                    state = 'historical_24h_miss'
                elif now < opened:
                    state = 'not_due_yet'
                elif now > deadline:
                    state = 'missed_since_activation'
                else:
                    state = 'open_without_available_capture'
                windows[str(age)][state] += 1
                if state in {'not_due_yet', 'open_without_available_capture'}:
                    if next_deadline is None or deadline < at(next_deadline['closes_at']):
                        next_deadline = {**{f: publication[f] for f in FIELDS}, 'target_age_hours': age,
                                         'opens_at': opened.isoformat(), 'closes_at': deadline.isoformat()}
        flags = []
        if r['intent'] and not any(recent(p, 7) for p in verified):
            flags.append('no_verified_publication_in_7d')
        if any(s['status'] == 'scheduled' and at(s['run_at']) < now - timedelta(minutes=5) for s in active):
            flags.append('past_due_schedule_inspect_retry_state')
        if any(s['status'] in {'failed', 'ambiguous_effect', 'executing', 'drift_blocked'} for s in r['schedules']):
            flags.append('schedule_attention_history_present')
        if r['uncertainties']:
            flags.append('uncertain_receipt_no_blind_retry')
        if not r['intent']:
            flags.append('registered_binding_only_no_current_allocation_intent')
        if r['intent'] and not r['candidates'] and not active:
            flags.append('no_current_runnable_or_reserved_inventory')
        if any(w['missed_since_activation'] for w in windows.values()):
            flags.append('measurement_miss_since_activation')
        if r['provider'] in data['supported_metric_providers'] and published and not numeric_available:
            flags.append('no_available_numeric_metric_observed')
        result = {'provider': r['provider'], 'account_id': r['account_id'], 'aliases': sorted(r['aliases']),
                  'publishing_intent': sorted(r['intent']),
                  'inventory_campaigns': len(r['campaigns']), 'runnable': len(r['candidates']),
                  'active_reservations': len(active),
                  'next_reservation': min(active, key=lambda s: s['run_at'], default=None),
                  'schedule_states': dict(Counter(s['status'] for s in r['schedules'])),
                  'uncertain_receipts': r['uncertainties'],
                  'candidate_exclusion_counts': dict(Counter(e['reason'] for e in r['exclusions'])),
                  'verified_effects_total': len(verified),
                  'verified_effects_24h': sum(recent(p, 1) for p in verified),
                  'verified_effects_7d': sum(recent(p, 7) for p in verified),
                  'unverified_effects_total': len(published)-len(verified), 'latest_verified_effect': latest,
                  'metrics_support': 'supported' if r['provider'] in data['supported_metric_providers'] else 'unsupported',
                  'effects_with_available_numeric_metrics': len({identity(s) for s in numeric_available}),
                  'latest_available_metric': ({k: last_metric.get(k) for k in (*FIELDS, 'captured_at', 'comparable_target')}
                                              if last_metric else None),
                  'metric_http_status_counts': dict(Counter(str(s.get('availability', {}).get('http_status'))
                                                           for s in r['snapshots'] if s.get('availability', {}).get('http_status'))),
                  'window_counts': {age: dict(value) for age, value in windows.items()},
                  'next_measurement_deadline': next_deadline,
                  'saved_feedback_observations': len(r['feedback']),
                  'fresh_saved_feedback_observations': len(r['feedback']) if feedback_fresh else 0,
                  'inspection_flags': flags}
        rows[key[0]]['routes'].append(result)
    return {'schema_version': 1, 'status': 'observed', 'observed_at': now.isoformat(),
            'registered_projects': len(rows), 'projects': list(rows.values()),
            'configured_source_projects': len(data['sources']),
            'source_projects_not_in_registry': sorted(set(data['sources'])-set(registry)),
            'projects_with_verified_publication_7d': sum(any(r['verified_effects_7d'] for r in p['routes']) for p in rows.values()),
            'saved_feedback': {'observed_at': saved.get('observed_at'), 'fresh': feedback_fresh,
                               'status': saved.get('status', 'not_observed'),
                               'excluded_counts': saved.get('excluded_counts', {}),
                               'signal_count': len(saved.get('signals', {}))},
            'collection_cycle': data['collection_cycle'], 'finding_count': len(findings),
            'finding_counts': dict(Counter(f['code'] for f in findings)), 'findings': findings[:50],
            'findings_truncated': len(findings) > 50,
            'boundary': ('Read-only local coverage, not an all-project health certification. Every registered project and account is included, '
                         'even without inventory or receipts. Seven days is an inspection lookback, not a new per-project quota. '
                         'Source observation, inventory, reservation, verified effect, numeric metrics and saved learning observations are distinct. '
                         'Available capture does not imply sufficient exposure, attribution or a winning experiment. '
                         'Unsupported analytics and future windows are not missing captures. No activation, provider calls, retries, scheduling, '
                         'publication, feedback rebuild, policy changes or writes. Historical effects do not establish current connection health.')}


def collect(now):
    """Use the runtime's strict readers and canonical verification; never call providers."""
    sys.path.insert(0, str(ROOT / 'src'))
    from ocpf_post import local_store, performance, performance_feedback, performance_windows
    from ocpf_post.campaigns import destination_binding, campaign_project
    from ocpf_post.health import read_log
    from ocpf_post.operations_snapshot import coverage_fingerprints
    from ocpf_post.performance_review import publications
    from ocpf_post.portfolio import delivery_candidates
    from ocpf_post.portfolio_source_loader import merged_source_profiles
    from ocpf_post.registry import load_registry, resolve_account
    from ocpf_post.source_observations import load, fingerprint
    from ocpf_post.source_receipts import publication_inputs
    from ocpf_post.state import state_dir
    from ocpf_post import vault_sync
    from ocpf_post.schedule_semantics import ACTIVE_STATUSES

    for attempt in range(3):
        before = coverage_fingerprints()
        registry = load_registry()
        manifests, schedules, receipts = publication_inputs()
        observations = load()['projects']
        sources = {}
        for name, profile in merged_source_profiles()['projects'].items():
            sources[name] = {'repository': profile['repository'],
                             'fingerprint': fingerprint({**profile, 'project': name}),
                             'inventory_items': len(profile.get('inventory', [])),
                             'revision_pinned_items_skipped': sum(bool(i.get('source_sha')) and i['source_sha'] != observations.get(name, {}).get('readme_sha') for i in profile.get('inventory', [])),
                             'bindings': [resolve_account(name, alias, expected_provider=p) for p, alias in profile.get('destinations', {}).items()
                                          if p in profile.get('providers', [])]}
        simplified = {}
        for cid, m in manifests.items():
            bindings = []
            for provider in m.get('providers', []):
                binding = destination_binding(cid, provider)
                if not binding:
                    raise ValueError('Campaign destination binding missing')
                bindings.append({'provider': provider, 'account_id': binding['account_id']})
            simplified[cid] = {'project': m.get('project') or campaign_project(cid), 'bindings': bindings,
                               'allocation_enabled': (m.get('allocation') or {}).get('enabled') is True}
        local_store.read(state_dir() / 'publication-readbacks.json')  # Reject corrupt sidecar JSON, never hide it.
        pub_map = publications()
        pub_rows = []
        for p in pub_map.values():
            receipt = p['receipt']
            pub_rows.append({**{k: receipt.get(k) for k in (*FIELDS, 'text_sha256', 'url', 'schedule_id')},
                             'published_at': p['at'].isoformat(),
                             'verified': bool(p.get('effective_verified')) and receipt.get('readback_verified') is True,
                             'verification_basis': p.get('verification_basis'),
                             'ledger_status': receipt.get('ledger_status', receipt.get('status'))})
        snapshots = read_log(performance.performance_file())
        for s in snapshots:
            key = identity(s)
            s['comparable_target'] = performance_windows._snapshot_target(s, pub_map[key]['at'])[0] if key in pub_map else None
        exclusions = []
        candidates = delivery_candidates(now=now, exclusions=exclusions)
        vobs = vault_sync.observations()
        vaults = [{'id': key, 'project': v['project'], 'enabled': v['enabled'], 'observation': vobs.get(key, {}),
                   'bindings': [resolve_account(v['project'], alias, expected_provider=p) for p, alias in v['destinations'].items()]}
                  for key, v in vault_sync.policies().items()]
        cycle = local_store.read(state_dir() / 'collection-cycle.json')
        latest_receipts = {}
        for receipt in receipts:
            latest_receipts[tuple(receipt.get(k) for k in FIELDS[:3])] = receipt
        uncertainties = [{k: row.get(k) for k in (*FIELDS, 'status', 'recorded_at', 'schedule_id')}
                         for row in latest_receipts.values()
                         if row.get('status') == 'ambiguous_effect'
                         or (row.get('status') in {'published_verified', 'published_unverified'} and not row.get('post_id'))]
        data = {'registry': registry, 'sources': sources, 'source_observations': observations, 'manifests': simplified,
                'vaults': vaults, 'schedules': [{k: s.get(k) for k in ('campaign', 'provider', 'account_id', 'schedule_id', 'status', 'run_at', 'retry_at', 'failure_class')} for s in schedules],
                'publications': pub_rows, 'receipt_uncertainties': uncertainties, 'snapshots': snapshots, 'candidates': candidates, 'exclusions': exclusions,
                'feedback': local_store.read(performance_feedback.path()),
                'window_state': local_store.read(performance_windows.path()),
                'target_ages': performance_windows.TARGET_AGES, 'tolerance_hours': performance_windows.TOLERANCE_HOURS,
                'supported_metric_providers': performance_windows.SUPPORTED_METRIC_PROVIDERS,
                'active_statuses': ACTIVE_STATUSES,
                'collection_cycle': {k: cycle.get(k) for k in ('started_at', 'observed_at', 'completed_at', 'stages')}}
        result = build(data, now)
        if before == coverage_fingerprints():
            return {**result, 'snapshot_attempts': attempt+1}
    return {'schema_version': 1, 'status': 'snapshot_changed', 'projects': [],
            'snapshot_attempts': 3, 'observed_at': now.isoformat(),
            'boundary': 'Concurrent host changes detected. No mixed-time coverage certification; rerun this read-only audit.'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--json', action='store_true', help='Emit the complete audit as JSON')
    args = parser.parse_args()
    try:
        result = collect(datetime.now(UTC))
    except (OSError, ValueError, KeyError, TypeError, AttributeError, RuntimeError) as exc:
        result = {'schema_version': 1, 'status': 'unavailable', 'projects': [],
                  'error_type': type(exc).__name__, 'boundary': 'Incomplete or invalid local evidence. No success inferred; no state changed.'}
    if args.json or result['status'] != 'observed':
        print(json.dumps(result, indent=2, ensure_ascii=False, allow_nan=False))
    else:
        print('PROJECT | SOURCE | VERIFIED POSTS (7D) | ACTIVE SCHEDULES | EFFECTS WITH NUMERIC METRICS')
        for p in result['projects']:
            r = p['routes']
            print(f"{p['project']} | {p['source']['status']} | {sum(x['verified_effects_7d'] for x in r)} | {sum(x['active_reservations'] for x in r)} | {sum(x['effects_with_available_numeric_metrics'] for x in r)}")
        print(result['boundary'])
        print('Use --json for every account, excluded route, evidence identity and measurement deadline.')
    return 0 if result['status'] == 'observed' else 2


if __name__ == '__main__':
    raise SystemExit(main())
