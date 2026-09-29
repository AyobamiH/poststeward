"""Small evidence-bound selection adjustments, with exploration and expiry."""
from collections import defaultdict
from datetime import datetime, timedelta, timezone
import math
import statistics
import re
from ocpf_post import local_store
from ocpf_post.state import state_dir, config_dir
from ocpf_post.engagement import digest, at, stamp

UTC = timezone.utc
TEMPLATE_VERSION = 'repository-static-v1'
LEARNING_TARGET_AGES = (24, 72, 168)
LEARNING_TOLERANCE_HOURS = 2


def path():
    return state_dir() / 'performance-feedback.json'


def enabled():
    settings = local_store.read(config_dir() / 'performance-feedback-policy.json')
    value = settings.get('enabled', True)
    if type(value) is not bool:
        raise ValueError('Invalid feedback policy')
    return value


def configure(value):
    with local_store.locked(config_dir() / 'performance-feedback-policy.json'):
        local_store.write(config_dir() / 'performance-feedback-policy.json', {'schema_version': 1, 'enabled': value})


def metadata(manifest, receipt):
    """Attribute only a manifest whose payload still matches the publication."""
    source = manifest.get('source') or {}
    sha = receipt.get('text_sha256')
    if not sha or (manifest.get('payload_sha256') or {}).get(receipt['provider']) != sha:
        return None
    match = re.search(r'-(insight|question|practical)$', str(source.get('source_id', '')))
    if (not match or source.get('type') != 'repository_product_truth'
            or not source.get('source_sha') or not manifest.get('project')):
        return None
    result = {'project': manifest['project'], 'lane': (manifest.get('allocation') or {}).get('lane', 'evergreen'),
            'variant': match.group(1), 'revision': source['source_sha'],
            'topic': re.sub(r'-(insight|question|practical)$', '', source['source_id']),
            'text_sha256': sha, 'template_version': TEMPLATE_VERSION}
    if source.get('comparison_variant') in {'question', 'practical'}:
        result['comparison_variant'] = source['comparison_variant']
    return result


def key(provider, account_id, meta):
    # Compare a rendering preference, not product claims. Pair evidence by exact
    # project/revision/topic below; share only the template preference within a lane.
    fields = [provider, account_id, meta['lane'], meta['template_version']]
    if meta.get('comparison_variant'):
        fields.append(meta['comparison_variant'])
    return digest(fields)


def topic_key(row):
    meta = row['editorial']
    return meta['project'], meta['revision'], meta['topic']


def _measurement_age(row, actual_age):
    target = row.get('target_age_hours')
    if target is None:
        return 24 if abs(actual_age - 24) <= LEARNING_TOLERANCE_HOURS else None
    if (type(target) not in (int, float) or not math.isfinite(target)
            or target not in LEARNING_TARGET_AGES
            or abs(actual_age - target) > LEARNING_TOLERANCE_HOURS):
        return None
    return int(target)


def build(*, apply=False, now=None, enable=None):
    from ocpf_post.performance_review import publications
    from ocpf_post.performance import iter_snapshots
    now = now or datetime.now(UTC)
    if enable is not None:
        if not apply or type(enable) is not bool:
            raise ValueError('Feedback policy changes require --apply')
        configure(enable)
    active = enabled(); pub = publications(); observations = {}; exclusions = defaultdict(int)
    post_campaigns = defaultdict(set)
    for identity in pub:
        post_campaigns[identity[1:]].add(identity[0])
    for row in iter_snapshots():
        identity = tuple(row.get(k) for k in ('campaign', 'provider', 'account_id', 'post_id'))
        item = pub.get(identity)
        if len(post_campaigns.get(identity[1:], set())) > 1:
            exclusions['post_claimed_by_multiple_campaigns'] += 1; continue
        if not item or item['receipt'].get('status') != 'published_verified':
            exclusions['no_verified_matching_receipt'] += 1; continue
        if (
            item['receipt'].get('publication_type') == 'thread'
            or int(item['receipt'].get('part_count') or 1) > 1
            or row.get('metric_scope') == 'root_post_only'
        ):
            exclusions['multi_part_metrics_not_comparable'] += 1; continue
        try:
            captured = at(row['captured_at']); age = (captured - item['at']).total_seconds() / 3600
            target_age = _measurement_age(row, age)
            if captured > now or now - captured > timedelta(days=14) or target_age is None:
                exclusions['outside_comparable_age_or_recent_window'] += 1; continue
            if row.get('availability', {}).get('status') != 'available':
                exclusions['metrics_unavailable'] += 1; continue
            meta = row.get('editorial')
            if not meta:
                exclusions['missing_frozen_editorial_attribution'] += 1; continue
            if meta.get('template_version') != TEMPLATE_VERSION:
                exclusions['unknown_template_version'] += 1; continue
            arm = meta.get('comparison_variant')
            if arm is not None and (arm not in {'question', 'practical'} or meta.get('variant') not in {'insight', arm}):
                exclusions['invalid_comparison_arm'] += 1; continue
            if meta.get('text_sha256') != item['receipt'].get('text_sha256'):
                exclusions['payload_mismatch'] += 1; continue
            if meta.get('variant') not in {'insight', 'question', 'practical'} or not all(isinstance(meta.get(k), str) and meta[k] for k in ('project', 'lane', 'revision', 'topic')):
                exclusions['invalid_editorial_attribution'] += 1; continue
            metrics = row.get('metrics', {})
            exposure = metrics.get('impressions' if identity[1] == 'x' else 'views')
            values = [metrics.get(k) for k in ('likes', 'reposts', 'quotes')]
            if not all(type(v) in (int, float) and math.isfinite(v) and v >= 0 for v in [exposure, *values]) or exposure < 100:
                exclusions['unknown_metrics_or_low_exposure'] += 1; continue
            if not math.isfinite(sum(values) / exposure):
                exclusions['invalid_metric_rate'] += 1; continue
            record = {'campaign': identity[0], 'provider': identity[1], 'account_id': identity[2],
                      'post_id': identity[3], 'captured_at': row['captured_at'], 'age_hours': age,
                      'target_age_hours': target_age, 'rate': sum(values) / exposure,
                      'exposure': exposure, 'editorial': meta}
            observation_key = (*identity, target_age)
            old = observations.get(observation_key)
            if not old or abs(age - target_age) < abs(old['age_hours'] - target_age):
                observations[observation_key] = record
        except (ValueError, KeyError, TypeError):
            exclusions['invalid_snapshot'] += 1

    groups = defaultdict(lambda: defaultdict(list))
    for row in observations.values():
        scope = key(row['provider'], row['account_id'], row['editorial'])
        groups[(scope, row['target_age_hours'])][row['editorial']['variant']].append(row)
    cohorts = []; winners_by_scope = defaultdict(list)
    for (scope, target_age), variants in sorted(groups.items()):
        stats = {name: {'posts': len(rows), 'topics': len({topic_key(r) for r in rows}),
                        'median_rate': statistics.median(r['rate'] for r in rows)} for name, rows in variants.items()}
        eligible = {name: values for name, values in stats.items() if values['posts'] >= 5 and values['topics'] >= 3}
        cohort = {'scope': scope, 'target_age_hours': target_age, 'variants': stats,
                  'status': 'insufficient_evidence', 'comparisons': []}
        if len(eligible) >= 2:
            paired = {name: {} for name in eligible}
            for name in eligible:
                grouped = defaultdict(list)
                for row in variants[name]:
                    grouped[topic_key(row)].append(row)
                paired[name] = {topic: rows[0] for topic, rows in grouped.items() if len(rows) == 1}
            winners = []
            for name in sorted(eligible):
                beats_all = True
                for other in sorted(set(eligible) - {name}):
                    common = sorted(paired[name].keys() & paired[other].keys())
                    differences = [(paired[name][t]['rate'] - paired[other][t]['rate']) /
                        max(paired[name][t]['rate'], paired[other][t]['rate'], 1e-12) for t in common]
                    median = statistics.median(differences) if differences else None
                    qualifies = len(common) >= 5 and median > 0.2
                    cohort['comparisons'].append({'variant': name, 'against': other,
                        'matched_topics': len(common), 'median_relative_difference': median})
                    beats_all = beats_all and qualifies
                if beats_all:
                    winners.append(name)
            if len(winners) == 1:
                top = winners[0]; sample = variants[top][0]
                winners_by_scope[scope].append({'variant': top, 'target_age_hours': target_age,
                    'provider': sample['provider'], 'account_id': sample['account_id'],
                    'lane': sample['editorial']['lane'], 'template_version': TEMPLATE_VERSION,
                    'evidence_post_ids': sorted({r['post_id'] for rows in variants.values() for r in rows})})
                cohort['status'] = 'bounded_preference_available'
            else:
                cohort['status'] = 'no_clear_descriptive_preference'
        cohorts.append(cohort)

    signals = {}; age_conflicts = []
    for scope, winners in sorted(winners_by_scope.items()):
        variants = {row['variant'] for row in winners}
        if len(variants) != 1:
            age_conflicts.append({'scope': scope, 'target_ages': sorted(r['target_age_hours'] for r in winners),
                                  'variants': sorted(variants)})
            for cohort in cohorts:
                if cohort['scope'] == scope and cohort['status'] == 'bounded_preference_available':
                    cohort['status'] = 'cross_age_conflict'
            continue
        primary = min(winners, key=lambda row: row['target_age_hours'])
        evidence = sorted({post_id for row in winners for post_id in row['evidence_post_ids']})
        signals[scope] = {**primary, 'boost': 3, 'evidence_post_ids': evidence,
                          'supporting_target_ages': sorted(r['target_age_hours'] for r in winners),
                          'expires_at': stamp(now + timedelta(hours=24))}

    result = {'schema_version': 1, 'observed_at': stamp(now), 'status': 'signals_available' if signals else 'insufficient_evidence',
              'signals': signals, 'cohorts': cohorts, 'observations': list(observations.values()),
              'excluded_counts': dict(exclusions), 'age_conflicts': age_conflicts,
              'selection_adjustment_available': bool(signals) and active, 'enabled': active,
              'boundary': 'Descriptive matched-topic template preference, not causation or sales. Replies excluded. Observations are grouped by explicit 24h, 72h or 168h target age and are never pooled across ages. At least five paired topics at one target age, each matched by project/source revision/topic, with known exposure >=100 per post. Multiple qualifying ages must agree on the same winner; conflicting age cohorts fail closed. Compare median within-topic relative differences, not pooled project rates. Three points above the template base; up to eleven total when restoring legacy variant discounts, with existing fairness and alternate exploration. Signals expire after 24h. No text, receipts, quotas or approval changes.'}
    if apply:
        with local_store.locked(path()):
            previous = local_store.read(path())
            result['previous_signal_digest'] = digest(previous.get('signals', {}))
            local_store.write(path(), result)
    return result


def snapshot(now):
    """Corrupt, missing or stale feedback makes no allocation change."""
    try:
        if not enabled():
            return {}
        result = local_store.read(path())
        if not result or not 0 <= (now - at(result['observed_at'])).total_seconds() <= 86400:
            return {}
        return result.get('signals', {}) if isinstance(result.get('signals'), dict) else {}
    except (ValueError, OSError, KeyError, TypeError):
        return {}


def candidate_boost(candidate, manifest, signals, now):
    meta = metadata(manifest, {'provider': candidate['provider'], 'text_sha256': candidate.get('text_sha256')})
    if not meta:
        return 0
    signal = signals.get(key(candidate['provider'], candidate.get('account_id'), meta), {})
    try:
        if (signal['variant'] != meta['variant'] or signal['boost'] != 3 or type(signal['boost']) is not int
                or at(signal['expires_at']) <= now
                or (signal['provider'], signal['account_id'], signal['lane'], signal['template_version']) !=
                   (candidate['provider'], candidate.get('account_id'), meta['lane'], meta['template_version'])):
            return 0
        offset = (manifest.get('allocation') or {}).get('variant_priority_offset', 0)
        expected_offset = {'insight': 0, 'question': -4, 'practical': -8}[meta['variant']]
        if type(offset) is not int or offset not in {0, expected_offset}:
            return 0
        return 3 - offset
    except (ValueError, KeyError, TypeError):
        return 0


def candidate_preference(candidate, manifest, signals, now):
    boost = candidate_boost(candidate, manifest, signals, now)
    if not boost:
        return {'performance_boost': 0, 'performance_expires_at': None}
    meta = metadata(manifest, {'provider': candidate['provider'], 'text_sha256': candidate.get('text_sha256')})
    signal = signals[key(candidate['provider'], candidate.get('account_id'), meta)]
    return {'performance_boost': boost, 'performance_expires_at': signal['expires_at']}
