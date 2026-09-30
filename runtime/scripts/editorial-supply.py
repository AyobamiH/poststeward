#!/usr/bin/env python3
"""Read-only editorial demand and reviewed-copy checks; never publish or approve."""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import runpy
import sys

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))

from ocpf_post.publication_payload import build_publication

UTC = timezone.utc
MAX_BYTES = 5_000_000


def at(value):
    stamp = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
    if stamp.tzinfo is None:
        raise ValueError('An offset-aware evidence timestamp is required')
    return stamp.astimezone(UTC)


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'),
                                    ensure_ascii=False, allow_nan=False).encode()).hexdigest()


def _pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('Duplicate JSON key')
        result[key] = value
    return result


def load_report(path):
    """Accept pure JSON or the existing tee-wrapped coverage report, not snippets."""
    raw = Path(path).read_bytes()
    if len(raw) > MAX_BYTES:
        raise ValueError('Input exceeds the five-megabyte bound')
    text = raw.decode('utf-8')
    marker = '=== ALL-PROJECT PUBLISHING AND METRICS AUDIT ==='
    if marker in text:
        if text.count(marker) != 1 or text.count('=== AUDIT COMPLETE ===') != 1:
            raise ValueError('Incomplete or repeated audit capture')
        text = text.split(marker, 1)[1].split('=== AUDIT COMPLETE ===', 1)[0]
    return json.loads(text, object_pairs_hook=_pairs,
                      parse_constant=lambda value: (_ for _ in ()).throw(ValueError('Nonfinite JSON value')))


def _count(row, key):
    value = row[key]
    if type(value) is not int or value < 0:
        raise ValueError('Missing or invalid coverage count: ' + key)
    return value


def build_workpack(coverage, now):
    """Do not confuse a successful source read with unused, publishable content."""
    if type(coverage.get('schema_version')) is not int or coverage['schema_version'] != 1 or coverage.get('status') != 'observed':
        raise ValueError('A successful complete coverage snapshot is required')
    age = (now - at(coverage['observed_at'])).total_seconds()
    fresh = 0 <= age <= 3600
    projects = coverage['projects']
    if not isinstance(projects, list) or type(coverage['registered_projects']) is not int or len(projects) != coverage['registered_projects']:
        raise ValueError('Incomplete project denominator')
    seen_projects, seen_routes, accounts, needs, routes = set(), set(), {}, [], []
    for project in projects:
        name = project['project']
        if not isinstance(name, str) or not name or name in seen_projects:
            raise ValueError('Invalid or repeated project identity')
        seen_projects.add(name)
        for route in project['routes']:
            provider, account = route['provider'], route['account_id']
            if provider not in {'x', 'threads', 'linkedin'} or not isinstance(account, str) or not account:
                raise ValueError('Invalid provider/account identity')
            key = (name, provider, account)
            if key in seen_routes:
                raise ValueError('Duplicate project/account route')
            seen_routes.add(key)
            ready = _count(route, 'runnable')
            reserved = _count(route, 'active_reservations')
            verified = _count(route, 'verified_effects_24h')
            unverified = _count(route, 'unverified_effects_total')
            intent = bool(route['publishing_intent'])
            route_view = {'project': name, 'provider': provider, 'account_id': account,
                          'aliases': list(route['aliases']), 'publishing_intent': intent,
                          'runnable': ready, 'reserved': reserved, 'source': project['source'],
                          'vaults': project['vaults'],
                          'exclusion_counts': route['candidate_exclusion_counts']}
            routes.append(route_view)
            item = accounts.setdefault((provider, account), {
                'provider': provider, 'account_id': account, 'projects': [],
                'runnable': 0, 'reserved': 0, 'verified_24h': 0,
                'unverified_retained': 0, 'publishing_intent': False,
                'latest_verified_effect': None})
            item['projects'].append(name)
            for field, value in [('runnable', ready), ('reserved', reserved),
                                 ('verified_24h', verified), ('unverified_retained', unverified)]:
                item[field] += value
            item['publishing_intent'] |= intent
            latest = route.get('latest_verified_effect')
            if latest:
                if at(latest['published_at']) > at(coverage['observed_at']):
                    raise ValueError('Future publication evidence')
                if not item['latest_verified_effect'] or at(latest['published_at']) > at(item['latest_verified_effect']['published_at']):
                    item['latest_verified_effect'] = latest
            # Preserve the original hard-depletion signal. Proactive low-stock
            # and expiry pressure is derived separately by editorial_continuity.
            if intent and ready + reserved == 0:
                needs.append({**route_view,
                              'request': 'review_consumption_then_author_or_resolve_eligibility',
                              'permission_to_replay_or_activate': False})
    for item in accounts.values():
        item['empty_inventory'] = item['publishing_intent'] and item['runnable'] + item['reserved'] == 0
        item['no_verified_post_24h'] = item['publishing_intent'] and item['verified_24h'] == 0
    return {'schema_version': 1, 'status': 'observed' if fresh else 'stale_or_future_evidence',
            'coverage_observed_at': coverage['observed_at'], 'evaluated_at': now.isoformat(),
            'coverage_sha256': digest(coverage), 'project_count': len(seen_projects),
            'route_count': len(seen_routes), 'physical_account_count': len(accounts),
            'supply_reviews': needs, 'route_supply': routes, 'accounts': list(accounts.values()),
            'requires_fresh_host_observation': not fresh,
            'boundary': 'An editorial work request, not proof of a fault or authority to publish. '
                        'APPROVED document counts are not unused stock. Empty project routes and quiet physical '
                        'accounts differ. No posting quota or deadline is introduced. Unverified is not failed; '
                        'unsupported analytics is not a publishing outage. All source, account, expiry, duplicate '
                        'and experiment gates remain authoritative. No approval, import or provider write.'}


def review_batch(packet):
    """Check review completeness and byte identity, not semantic truth or quality."""
    if packet.get('schema_version') != 1 or not packet.get('batches'):
        raise ValueError('Missing reviewed batch')
    seen, texts, result = set(), {}, []
    for batch in packet['batches']:
        if not batch.get('source_refs') or not batch.get('document_id') or not batch.get('account_id'):
            raise ValueError('Source and destination evidence are required')
        reviews = {r['campaign']: r for r in batch['editorial_reviews']}
        if len(reviews) != len(batch['editorial_reviews']) or set(reviews) != {e['campaign'] for e in batch['entries']}:
            raise ValueError('Each entry requires one exact editorial review')
        for entry in batch['entries']:
            expected = {'campaign', 'provider', 'title', 'text', 'allocation', 'status', 'approval_sha256'}
            if set(entry) != expected or entry['provider'] not in batch['destinations']:
                raise ValueError('Invalid entry or destination')
            key = (batch['project'], entry['provider'], batch['account_id'], entry['campaign'])
            if key in seen:
                raise ValueError('Duplicate campaign/account')
            seen.add(key)
            text = entry['text']
            if not isinstance(text, str) or not text or text != text.strip():
                raise ValueError('Invalid platform copy')
            try:
                publication = build_publication(entry['provider'], text)
            except (TypeError, ValueError) as exc:
                raise ValueError('Invalid platform copy') from exc
            if entry['status'] != 'APPROVED' or digest({k: v for k, v in entry.items() if k != 'approval_sha256'}) != entry['approval_sha256']:
                raise ValueError('Reviewed approval digest mismatch')
            if at(entry['allocation']['expires_at']) <= at(entry['allocation']['prepared_at']):
                raise ValueError('Invalid approval interval')
            review = reviews[entry['campaign']]
            if review.get('decision') not in {'approved_by_agent_editor', 'approved_by_human_editor'}:
                raise ValueError('Editorial decision is not approved')
            required = ('audience', 'recognisable_situation', 'useful_action', 'product_connection',
                        'novelty_rationale', 'evidence_boundary', 'audience_response')
            if any(not isinstance(review.get(k), str) or not review[k].strip() for k in required):
                raise ValueError('Incomplete editorial assessment')
            if review['audience_response'] != 'not_observed':
                raise ValueError('Editorial review cannot pre-claim audience response')
            if review.get('payload_sha256') != hashlib.sha256(text.encode()).hexdigest():
                raise ValueError('Copy changed after editorial assessment')
            normal = Counter(re.findall(r'\w+', text.casefold()))
            scope = key[:3]
            if any(normal == previous for previous in texts.get(scope, [])):
                raise ValueError('Reordered or identical copy is not a new angle')
            texts.setdefault(scope, []).append(normal)
            result.append({'project': batch['project'], 'campaign': entry['campaign'],
                           'provider': entry['provider'], 'account_id': batch['account_id'],
                           'text_sha256': review['payload_sha256'],
                           'approval_sha256': entry['approval_sha256'],
                           'assessment_sha256': digest(review),
                           'review_record_present': True,
                           'audience_response': 'not_observed'})
    return {'status': 'review_records_valid', 'entries': result,
            'boundary': 'Structural and integrity checks only. Supplied review prose is not independent semantic '
                        'verification or observed audience response. Actual vault prepare, current authority, '
                        'history reconciliation, novelty review and freshness are still required. No writes.'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group()
    group.add_argument('--coverage', type=Path, help='Saved complete audit JSON or tee capture')
    group.add_argument('--batch', type=Path, help='Validate recorded editorial assessments, without approving')
    args = parser.parse_args()
    stage = 'review_batch' if args.batch else 'load_coverage' if args.coverage else 'collect_local_coverage'
    coverage_status = None
    try:
        if args.batch:
            result = review_batch(load_report(args.batch))
        else:
            coverage = load_report(args.coverage) if args.coverage else runpy.run_path(
                str(ROOT / 'scripts/audit-portfolio-coverage.py'))['collect'](datetime.now(UTC))
            coverage_status = coverage.get('status') if isinstance(coverage, dict) else None
            stage = 'build_editorial_workpack'
            result = build_workpack(coverage, datetime.now(UTC))
        code = 0 if result['status'] in {'observed', 'review_records_valid'} else 2
    except (OSError, ValueError, KeyError, TypeError, AttributeError, RuntimeError) as exc:
        result = {'schema_version': 1, 'status': 'unavailable', 'error_type': type(exc).__name__,
                  'stage': stage,
                  'boundary': 'Incomplete evidence; no action or empty-success inference. No writes.'}
        if isinstance(coverage_status, str) and coverage_status in {'observed', 'snapshot_changed', 'unavailable'}:
            result['coverage_status'] = coverage_status
        code = 2
    print(json.dumps(result, indent=2, ensure_ascii=False, allow_nan=False))
    return code


if __name__ == '__main__':
    raise SystemExit(main())
