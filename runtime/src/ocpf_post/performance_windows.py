"""Durable multi-age performance observations without inventing missed history."""
from __future__ import annotations

from collections import Counter
from datetime import datetime, timedelta, timezone
import hashlib
import json
import math
import uuid

from ocpf_post import local_store
from ocpf_post.onboarding import _timestamp
from ocpf_post.performance import append_snapshot, capture, iter_snapshots
from ocpf_post.performance_review import publications
from ocpf_post.state import state_dir

UTC = timezone.utc
TARGET_AGES = (24, 72, 168)
TOLERANCE_HOURS = 2
MAX_READS = 20
MAX_ATTEMPTS = 5
RETENTION_DAYS = 14
POLICY_VERSION = 1
SUPPORTED_METRIC_PROVIDERS = frozenset({'x', 'threads'})


def path():
    return state_dir() / 'performance-window-state.json'


def _stamp(value):
    return value.astimezone(UTC).isoformat()


def _window_id(identity, target_age):
    raw = json.dumps([*identity, target_age], separators=(',', ':'), ensure_ascii=False)
    return hashlib.sha256(raw.encode()).hexdigest()


def _identity(key):
    return dict(zip(('campaign', 'provider', 'account_id', 'post_id'), key))


def _snapshot_target(snapshot, publication_at):
    try:
        captured = _timestamp(snapshot['captured_at'], 'capture time')
    except (ValueError, KeyError):
        return None, None
    age = (captured - publication_at).total_seconds() / 3600
    tagged = snapshot.get('target_age_hours')
    if type(tagged) in (int, float) and math.isfinite(tagged) and tagged in TARGET_AGES:
        target = int(tagged)
        return (target, captured) if abs(age - target) <= TOLERANCE_HOURS else (None, captured)
    if abs(age - 24) <= TOLERANCE_HOURS:
        return 24, captured
    return None, captured


def _attempts_for(existing, key, publication_at, target_age, now):
    rows = []
    for snapshot in existing:
        if tuple(snapshot.get(k) for k in ('campaign', 'provider', 'account_id', 'post_id')) != key:
            continue
        tagged, captured = _snapshot_target(snapshot, publication_at)
        if tagged != target_age or captured is None or captured > now:
            continue
        rows.append((captured, snapshot))
    unique = {}
    for captured, snapshot in sorted(rows, key=lambda item: item[0]):
        marker = snapshot.get('capture_attempt_id') or ('legacy', snapshot.get('captured_at'))
        unique[marker] = (captured, snapshot)
    return list(unique.values())


def _successful(attempts):
    return any(row.get('availability', {}).get('status') == 'available' for _, row in attempts)


def _retry_at(attempts, publication_at, target_age):
    if not attempts:
        return None
    latest_at, latest = max(attempts, key=lambda item: item[0])
    delay = min(60, 15 * 2 ** min(len(attempts) - 1, 2))
    retry_at = latest_at + timedelta(minutes=delay)
    availability = latest.get('availability', {})
    if availability.get('http_status') == 429:
        retry_at = max(retry_at, latest_at + timedelta(hours=1))
    provider_retry = availability.get('retry_at')
    if provider_retry:
        try:
            retry_at = max(retry_at, _timestamp(provider_retry, 'provider retry time'))
        except ValueError:
            retry_at = publication_at + timedelta(hours=target_age + TOLERANCE_HOURS + 1)
    seconds = availability.get('retry_after_seconds')
    if type(seconds) in (int, float) and math.isfinite(seconds) and seconds > 0:
        retry_at = max(retry_at, latest_at + timedelta(seconds=min(seconds, 86400)))
    return retry_at


def _backoff_retry(now, snapshot):
    availability = snapshot.get('availability', {})
    status = availability.get('http_status')
    if status in {401, 403}:
        return now + timedelta(hours=24), 'access_denied'
    if status == 429 or (type(status) is int and 500 <= status < 600):
        retry = now + timedelta(hours=1)
        if availability.get('retry_at'):
            try:
                retry = max(retry, _timestamp(availability['retry_at'], 'provider retry time'))
            except ValueError:
                pass
        seconds = availability.get('retry_after_seconds')
        if type(seconds) in (int, float) and math.isfinite(seconds) and seconds > 0:
            retry = max(retry, now + timedelta(seconds=min(seconds, 86400)))
        return retry, 'rate_limited' if status == 429 else 'provider_unavailable'
    return None, None


def _initial_state(now):
    return {'schema_version': 1, 'policy_version': POLICY_VERSION, 'activated_at': _stamp(now),
            'target_ages': list(TARGET_AGES), 'historical_missed_windows': {},
            'missed_windows': {}, 'account_backoff': {}, 'last_observed_at': _stamp(now)}


def _validate_state(data):
    if not data:
        return
    if (data.get('policy_version') != POLICY_VERSION or data.get('target_ages') != list(TARGET_AGES)
            or not isinstance(data.get('historical_missed_windows'), dict)
            or not isinstance(data.get('missed_windows'), dict)
            or not isinstance(data.get('account_backoff'), dict)):
        raise ValueError('Invalid performance-window state')
    _timestamp(data['activated_at'], 'performance window activation')
    _timestamp(data['last_observed_at'], 'performance window observation')


def _coverage(windows, state):
    by_age = {}
    missed = {**state.get('historical_missed_windows', {}), **state.get('missed_windows', {})}
    for target in TARGET_AGES:
        selected = [row for row in windows if row['target_age_hours'] == target]
        counts = Counter(row['state'] for row in selected)
        closed = counts['successful'] + counts['missed']
        by_age[str(target)] = {
            'enrolled': len(selected), 'successful': counts['successful'], 'missed': counts['missed'],
            'due': counts['due'], 'deferred': counts['deferred'], 'future': counts['future'],
            'closed_coverage': (counts['successful'] / closed) if closed else None,
            'oldest_open_deadline': min((row['deadline_at'] for row in selected
                                         if row['state'] in {'due', 'deferred', 'future'}), default=None),
            'persisted_missed': sum(row.get('target_age_hours') == target for row in missed.values()),
        }
    return by_age


def capture_sweep(*, apply=False, now=None, capture_fn=None):
    """Capture 24h/72h/168h cohorts with durable windows and bounded provider load."""
    now = now or datetime.now(UTC)
    state_path = path()
    try:
        lock = local_store.locked(state_path)
        lock.__enter__()
    except BlockingIOError:
        return {'schema_version': 1, 'apply': apply, 'status': 'busy', 'observations': [],
                'deferred': [{'result': 'capture_busy'}], 'missed_windows': [],
                'target_ages': list(TARGET_AGES),
                'boundary': 'Another capture sweep owns the durable performance lock. No provider call was made.'}
    try:
        state = local_store.read(state_path)
        if state:
            _validate_state(state)
        else:
            state = _initial_state(now)
            if apply:
                local_store.write(state_path, state)
        if now < _timestamp(state['last_observed_at'], 'performance window observation'):
            raise ValueError('Clock moved backwards; performance-window evidence retained')
        activated = _timestamp(state['activated_at'], 'performance window activation')
        existing = list(iter_snapshots()); windows = []; due = []; deferred = []; new_misses = []
        publications_map = publications(); unsupported = Counter()
        for key, publication in publications_map.items():
            if key[1] not in SUPPORTED_METRIC_PROVIDERS:
                unsupported[key[1]] += 1
                continue
            publication_at = publication['at']
            for target in TARGET_AGES:
                opens = publication_at + timedelta(hours=target - TOLERANCE_HOURS)
                deadline = publication_at + timedelta(hours=target + TOLERANCE_HOURS)
                if now - deadline > timedelta(days=RETENTION_DAYS):
                    continue
                attempts = _attempts_for(existing, key, publication_at, target, now)
                success = _successful(attempts); window_id = _window_id(key, target)
                row = {**_identity(key), 'window_id': window_id, 'target_age_hours': target,
                       'opens_at': _stamp(opens), 'deadline_at': _stamp(deadline), 'attempts': len(attempts)}
                if success:
                    row['state'] = 'successful'; windows.append(row); continue
                if target != 24 and deadline < activated:
                    continue
                if deadline < activated and target == 24:
                    historical = {**row, 'result': 'measurement_window_missed',
                                  'first_observed_at': state['activated_at'], 'legacy_before_activation': True}
                    state['historical_missed_windows'].setdefault(window_id, historical)
                    row['state'] = 'missed'; windows.append(row); continue
                if now > deadline:
                    saved = state['missed_windows'].get(window_id)
                    if not saved:
                        saved = {**row, 'result': 'measurement_window_missed',
                                 'first_observed_at': _stamp(now), 'legacy_before_activation': False}
                        if apply:
                            state['missed_windows'][window_id] = saved
                        new_misses.append(saved)
                    row['state'] = 'missed'; windows.append(row); continue
                if now < opens:
                    row['state'] = 'future'; windows.append(row); continue
                retry_at = _retry_at(attempts, publication_at, target)
                if len(attempts) >= MAX_ATTEMPTS:
                    row['state'] = 'deferred'; row['result'] = 'attempt_budget_exhausted'
                    deferred.append({**row}); windows.append(row); continue
                if retry_at and now < retry_at:
                    row['state'] = 'deferred'; row['result'] = 'retry_deferred'; row['retry_at'] = _stamp(retry_at)
                    deferred.append({**row}); windows.append(row); continue
                row['state'] = 'due'; row['attempt'] = len(attempts) + 1
                due.append((deadline, publication_at, key, publication, row)); windows.append(row)

        if apply:
            local_store.write(state_path, state)
        observations = []; backoff = state.get('account_backoff', {})
        for deadline, publication_at, key, publication, row in sorted(
                due, key=lambda item: (item[0], item[1], item[2][1], item[2][2], item[2][0])):
            if len(observations) >= MAX_READS:
                deferred.append({**row, 'result': 'cycle_budget'}); row['state'] = 'deferred'; continue
            scope = f'{key[1]}:{key[2]}'; blocked = backoff.get(scope)
            if blocked:
                try:
                    retry = _timestamp(blocked['retry_at'], 'account retry time')
                except (ValueError, KeyError):
                    retry = now
                if now < retry:
                    item = {**row, 'result': 'account_backoff', 'retry_at': _stamp(retry),
                            'backoff_reason': blocked.get('reason')}
                    deferred.append(item); row['state'] = 'deferred'; continue
            if not apply:
                observations.append({**row, 'result': 'due'}); continue
            attempt_id = uuid.uuid4().hex
            marker = {**_identity(key), 'schema_version': 1, 'captured_at': _stamp(now),
                      'capture_attempt_id': attempt_id, 'target_age_hours': row['target_age_hours'],
                      'metrics': {}, 'availability': {'status': 'attempt_started'}}
            append_snapshot(marker); existing.append(marker)
            try:
                if capture_fn:
                    snapshot = capture_fn(key[0], key[1], publication=publication,
                                          attempt_id=attempt_id, target_age_hours=row['target_age_hours'])
                else:
                    snapshot = capture(key[0], key[1], publication_receipt=publication['receipt'],
                                       attempt_id=attempt_id, target_age_hours=row['target_age_hours'])
                existing.append(snapshot); result = snapshot.get('availability', {}).get('status', 'unknown')
            except (ValueError, OSError):
                snapshot = {**_identity(key), 'schema_version': 1, 'captured_at': _stamp(now),
                            'capture_attempt_id': attempt_id, 'target_age_hours': row['target_age_hours'],
                            'metrics': {}, 'availability': {'status': 'unavailable',
                                                           'detail': 'Capture failed; no metrics inferred'}}
                append_snapshot(snapshot); existing.append(snapshot); result = 'unavailable'
            if result == 'available':
                observed_target, _captured = _snapshot_target(snapshot, publication_at)
                if observed_target == row['target_age_hours']:
                    row['state'] = 'successful'
                else:
                    result = 'available_outside_window'
            observations.append({**row, 'result': result})
            retry, reason = _backoff_retry(now, snapshot)
            if retry:
                backoff[scope] = {'retry_at': _stamp(retry), 'reason': reason, 'observed_at': _stamp(now)}
                state['account_backoff'] = backoff
                local_store.write(state_path, state)

        for row in windows:
            if row['state'] not in {'due', 'deferred'}:
                continue
            key = tuple(row[k] for k in ('campaign', 'provider', 'account_id', 'post_id'))
            publication = publications_map.get(key)
            if publication and _successful(_attempts_for(existing, key, publication['at'], row['target_age_hours'], now)):
                row['state'] = 'successful'
        state['account_backoff'] = backoff; state['last_observed_at'] = _stamp(now)
        if apply:
            local_store.write(state_path, state)
        historical = {**state.get('historical_missed_windows', {}), **state.get('missed_windows', {})}
        return {'schema_version': 1, 'apply': apply, 'status': 'observed',
                'observations': observations, 'deferred': deferred, 'missed_windows': new_misses if apply else [],
                'historical_missed_window_count': len(historical),
                'historical_missed_by_target': dict(Counter(str(row.get('target_age_hours')) for row in historical.values())),
                'unsupported_publications_by_provider': dict(unsupported),
                'supported_metric_providers': sorted(SUPPORTED_METRIC_PROVIDERS),
                'target_ages': list(TARGET_AGES), 'tolerance_hours': TOLERANCE_HOURS,
                'window_coverage': _coverage(windows, state),
                'reads_attempted': len(observations) if apply else 0, 'max_reads': MAX_READS,
                'activated_at': state['activated_at'], 'state_file': str(state_path),
                'boundary': 'Provider metrics are cumulative observations captured only inside explicit comparable age windows. The actual capture timestamp, not the requested target label, determines window eligibility. Historical misses stay durable and unknown; no later cumulative metric is relabelled as a missed earlier point. Providers without a supported analytics adapter are excluded rather than retried or treated as zero. Ages are not pooled here. One non-blocking capture lock, five attempts per window, earliest-deadline-first service, bounded reads and account backoff prevent duplicate work and retry storms. No publication or allocation change.'}
    finally:
        lock.__exit__(None, None, None)
