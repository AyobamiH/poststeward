"""Receipt-linked performance cohorts at comparable observation ages."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import math
import uuid

from ocpf_post import local_store, readback_evidence
from ocpf_post.onboarding import _timestamp
from ocpf_post.performance import iter_snapshots, capture, append_snapshot
from ocpf_post.state import iter_receipts, TERMINAL_EFFECT_STATUSES, state_dir


def _readback_data():
    try:
        return local_store.read(state_dir() / 'publication-readbacks.json')
    except (OSError, ValueError, TypeError):
        return {}


def publication_verification(receipt, *, readback_data=None):
    """Project immutable receipt + exact sidecar evidence into one verification state."""
    if not isinstance(receipt, dict):
        return {'verified': False, 'basis': 'unverified'}
    if receipt.get('status') == 'published_verified':
        return {'verified': True, 'basis': 'receipt'}
    if readback_evidence.verified(receipt, data=readback_data):
        return {'verified': True, 'basis': 'readback'}
    return {'verified': False, 'basis': 'unverified'}


def _receipt_view(receipt, verification):
    """Return an effective read model without mutating the append-only receipt."""
    view = dict(receipt)
    view['ledger_status'] = receipt.get('status')
    view['ledger_readback_verified'] = receipt.get('readback_verified')
    view['verification_basis'] = verification['basis']
    if verification['verified'] and verification['basis'] == 'readback':
        view['status'] = 'published_verified'
        view['readback_verified'] = True
    return view


def publications():
    rows = {}
    readback_data = _readback_data()
    for receipt in iter_receipts():
        if receipt.get('status') not in {'published_verified', 'published_unverified'}:
            continue
        key = tuple(receipt.get(k) for k in ('campaign', 'provider', 'account_id', 'post_id'))
        if not all(key):
            continue
        try:
            at = _timestamp(receipt['recorded_at'], 'receipt time')
        except (ValueError, KeyError):
            continue
        verification = publication_verification(receipt, readback_data=readback_data)
        view = _receipt_view(receipt, verification)
        # Provisional and readback receipts describe one effect. Keep first time.
        if key not in rows:
            rows[key] = {'receipt': view, 'at': at, 'effective_verified': verification['verified'],
                         'verification_basis': verification['basis']}
        else:
            rows[key]['at'] = min(rows[key]['at'], at)
            current = rows[key]
            if (receipt.get('status') == 'published_verified'
                    or (verification['verified'] and not current.get('effective_verified'))):
                current['receipt'] = view
                current['effective_verified'] = verification['verified']
                current['verification_basis'] = verification['basis']
            elif verification['verified'] and current.get('verification_basis') != 'receipt':
                current['effective_verified'] = True
                current['verification_basis'] = verification['basis']
                current['receipt'] = view
    return rows


def review(campaigns, *, provider, account_id, age_hours=24, tolerance_hours=2):
    if not 0 < age_hours <= 720 or not 0 <= tolerance_hours < age_hours:
        raise ValueError('Require 0 < age <= 720 hours and 0 <= tolerance < age')
    requested = set(campaigns)
    if not 2 <= len(requested) <= 50:
        raise ValueError('Compare 2 to 50 distinct campaigns')
    pub = publications(); selected = {}; rejected = []
    ambiguous = {cid for cid in requested if sum(k[0] == cid and k[1] == provider and k[2] == account_id for k in pub) > 1}
    rejected.extend({'campaign': cid, 'reason': 'multiple_publications_require_explicit_selection'} for cid in sorted(ambiguous))
    for snapshot in iter_snapshots():
        cid = snapshot.get('campaign')
        if cid in ambiguous or cid not in requested or snapshot.get('provider') != provider or snapshot.get('account_id') != account_id:
            continue
        key = (cid, provider, account_id, snapshot.get('post_id'))
        if key not in pub:
            rejected.append({'campaign': cid, 'reason': 'no_matching_publication_receipt'}); continue
        try:
            age = (_timestamp(snapshot['captured_at'], 'capture time') - pub[key]['at']).total_seconds() / 3600
        except (ValueError, KeyError):
            continue
        if abs(age - age_hours) > tolerance_hours:
            continue
        if snapshot.get('availability', {}).get('status') != 'available':
            rejected.append({'campaign': cid, 'reason': 'metrics_unavailable'}); continue
        metrics = {k: v if type(v) in (int, float) and math.isfinite(v) and v >= 0 else None
                   for k, v in snapshot.get('metrics', {}).items()}
        row = {'campaign': cid, 'post_id': key[-1], 'age_hours': age, 'captured_at': snapshot['captured_at'],
               'publication_time_basis': 'first_local_creation_receipt', 'metrics': metrics}
        if cid not in selected or abs(age - age_hours) < abs(selected[cid]['age_hours'] - age_hours):
            selected[cid] = row
    rows = [selected[k] for k in sorted(selected)]
    common = set.intersection(*({k for k, v in r['metrics'].items() if v is not None} for r in rows)) if rows else set()
    complete = len(rows) == len(requested) and bool(common)
    return {'schema_version': 1, 'status': 'comparable' if complete else 'insufficient_evidence',
            'provider': provider, 'account_id': account_id, 'target_age_hours': age_hours,
            'tolerance_hours': tolerance_hours, 'rows': rows, 'common_metrics': sorted(common),
            'missing_campaigns': sorted(requested - set(selected)), 'rejected': rejected,
            'ranking_changed': False, 'boundary': 'Same provider/account and comparable receipt-based ages. Descriptive observations only; no causation, sales or automatic ranking change. Unavailable values stay unknown.'}


def capture_due(*, age_hours=24, tolerance_hours=2, apply=False, now=None, capture_fn=None):
    if not 0 < age_hours <= 720 or not 0 <= tolerance_hours < age_hours:
        raise ValueError('Invalid performance observation age or tolerance')
    now = now or datetime.now(timezone.utc)
    existing = list(iter_snapshots()); rows = []; deferred = []; missed = []
    for key, publication in publications().items():
        age = (now - publication['at']).total_seconds() / 3600
        matching = [s for s in existing if tuple(s.get(k) for k in ('campaign', 'provider', 'account_id', 'post_id')) == key]
        attempts = []
        for s in matching:
            try:
                captured = _timestamp(s['captured_at'], 'capture time')
                observed_age = (captured - publication['at']).total_seconds() / 3600
                if captured <= now and abs(observed_age - age_hours) <= tolerance_hours:
                    attempts.append((captured, s))
            except (ValueError, KeyError):
                continue
        # A durable pre-request marker and its result describe one attempt.
        unique_attempts = {}
        for captured, snapshot in sorted(attempts, key=lambda item: item[0]):
            identity_key = snapshot.get('capture_attempt_id') or ('legacy', snapshot['captured_at'])
            unique_attempts[identity_key] = (captured, snapshot)
        attempts = list(unique_attempts.values())
        identity = dict(zip(('campaign', 'provider', 'account_id', 'post_id'), key))
        if any(s.get('availability', {}).get('status') == 'available' for _, s in attempts):
            continue
        if age > age_hours + tolerance_hours:
            if age <= age_hours + tolerance_hours + 24 * 14:
                missed.append({**identity, 'result': 'measurement_window_missed', 'attempts': len(attempts)})
            continue
        if age < age_hours - tolerance_hours:
            continue
        retry_at = None
        if attempts:
            latest_at, latest = max(attempts, key=lambda a: a[0])
            # Durable attempts: 15m, 30m, 60m, 60m. Never extend the age window.
            retry_at = latest_at + timedelta(minutes=min(60, 15 * 2 ** min(len(attempts) - 1, 2)))
            if latest.get('availability', {}).get('http_status') == 429:
                retry_at = max(retry_at, latest_at + timedelta(hours=1))
            provider_retry = latest.get('availability', {}).get('retry_at')
            if provider_retry:
                try:
                    retry_at = max(retry_at, _timestamp(provider_retry, 'provider retry time'))
                except ValueError:
                    retry_at = publication['at'] + timedelta(hours=age_hours + tolerance_hours + 1)
            seconds = latest.get('availability', {}).get('retry_after_seconds')
            if type(seconds) in (int, float) and math.isfinite(seconds) and seconds > 0:
                retry_at = max(retry_at, latest_at + timedelta(seconds=min(seconds, 86400)))
        if len(attempts) >= 5 or (retry_at and now < retry_at):
            deferred.append({**identity, 'result': 'attempt_budget_exhausted' if len(attempts) >= 5 else 'retry_deferred',
                             'attempts': len(attempts), 'retry_at': retry_at.isoformat() if retry_at else None})
            continue
        row = {**identity, 'age_hours': age, 'result': 'due', 'attempt': len(attempts) + 1}
        if apply:
            attempt_id = uuid.uuid4().hex
            append_snapshot({**identity, 'schema_version': 1, 'captured_at': now.isoformat(),
                'capture_attempt_id': attempt_id, 'metrics': {}, 'availability': {'status': 'attempt_started'}})
            try:
                snapshot = (capture_fn(key[0], key[1]) if capture_fn else
                            capture(key[0], key[1], publication_receipt=publication['receipt'], attempt_id=attempt_id))
                row['result'] = snapshot.get('availability', {}).get('status', 'unknown')
            except (ValueError, OSError):
                row['result'] = 'unavailable'
                append_snapshot({**identity, 'schema_version': 1, 'captured_at': now.isoformat(), 'metrics': {},
                                 'capture_attempt_id': attempt_id,
                                 'availability': {'status': 'unavailable', 'detail': 'Capture failed; no metrics inferred'}})
        rows.append(row)
        if len(rows) >= 20:
            break
    return {'schema_version': 1, 'apply': apply, 'observations': rows, 'deferred': deferred,
            'missed_windows': missed, 'boundary': 'Read-only provider metrics. Five attempts per original age window with exponential retry delay and provider backoff. Successful observations deduplicate; missed windows remain unknown. Never publishes or changes allocation.'}
