#!/usr/bin/env python3
"""Opt-in, bounded private GitHub telemetry. Never publishes social content."""
from __future__ import annotations
import argparse
import base64
from collections import defaultdict
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import signal
import socket
import ssl
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
REPO = 'AyobamiH/post-once'
REPO_ID = 1359390886
BRANCH = 'ops/editorial-consumption'
PATH = 'editorial/consumption.json'
KIND = 'post-once-editorial-consumption-v1'
MAX_BYTES = 256_000
MAX_API_RESPONSE_BYTES = 512_000
MAX_PUBLICATIONS = 160
MAX_AUDIENCE_EXPORT = 60
CAPTURE_ATTEMPTS = 3
CAPTURE_RETRY_DELAY_SECONDS = 0.5
CONFIG = {'schema_version': 1, 'enabled': True, 'repository': REPO,
          'repository_id': REPO_ID, 'branch': BRANCH, 'path': PATH}
PUBLICATION_FIELDS = ('project', 'campaign', 'provider', 'account_id', 'post_id',
                      'schedule_id', 'text_sha256', 'published_at', 'status')
SAFE_ERROR_TYPES = frozenset(('OSError', 'ValueError', 'RuntimeError', 'KeyError',
                             'TypeError', 'AttributeError', 'TimeoutError',
                             'JSONDecodeError', 'UnicodeDecodeError', 'UnicodeEncodeError',
                             'FileNotFoundError', 'PermissionError', 'ReplenisherError',
                             'SubprocessError', 'CalledProcessError', 'TimeoutExpired'))


class CaptureError(ValueError):
    """Only bounded protocol metadata crosses the child-process boundary."""
    def __init__(self, code, diagnostic):
        super().__init__(code)
        self.diagnostic = diagnostic


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False, allow_nan=False)


def digest(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def strict_json(raw):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError('duplicate_json_key')
            result[key] = value
        return result
    def constant(value):
        raise ValueError('nonfinite_json_value')
    result = json.loads(raw, object_pairs_hook=pairs, parse_constant=constant)
    if not isinstance(result, dict):
        raise ValueError('json_object_required')
    return result


def at(value):
    stamp = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
    if stamp.tzinfo is None:
        raise ValueError('timestamp_timezone_required')
    return stamp.astimezone(timezone.utc)


def _fields(row, names):
    return {key: row[key] for key in names if key in row}


def read_status(path):
    """Read old PR-115 failure reports without relaxing shared state or authority."""
    from ocpf_post import local_store
    try:
        return local_store.read(path)
    except ValueError:
        with path.open('rb') as stream:
            raw = stream.read(1025)
        if len(raw) > 1024:
            raise ValueError('invalid_handoff_status') from None
        try:
            legacy = strict_json(raw)
            valid = (set(legacy) == {'status', 'observed_at', 'error_type'}
                     and legacy['status'] == 'attention'
                     and isinstance(legacy['error_type'], str)
                     and legacy['error_type'] in SAFE_ERROR_TYPES)
            if not valid or not isinstance(legacy['observed_at'], str):
                raise ValueError('invalid_handoff_status')
            at(legacy['observed_at'])
        except (ValueError, TypeError, KeyError):
            raise ValueError('invalid_handoff_status') from None
        return {'schema_version': 1, **legacy, 'legacy_schema_missing': True}


def persist_status(path, record):
    """Every new report is readable by the real store; retain the known old error."""
    from ocpf_post import local_store
    previous = read_status(path)
    legacy = previous.get('legacy_failure')
    if previous.get('legacy_schema_missing') is True:
        legacy = _fields(previous, ('status', 'observed_at', 'error_type'))
    value = {**record, 'schema_version': 1}
    if legacy is not None:
        value['legacy_failure'] = legacy
    local_store.write(path, value)


def decode_workpack(raw, exit_code):
    diagnostic = {'child_exit_code': exit_code, 'output_bytes': len(raw)}
    if len(raw) > 5_000_000:
        raise CaptureError('workpack_output_too_large', diagnostic)
    try:
        pack = strict_json(raw)
    except (ValueError, UnicodeError):
        raise CaptureError('workpack_output_invalid', diagnostic) from None
    status = pack.get('status')
    diagnostic['child_status'] = status if isinstance(status, str) and status in {
        'observed', 'unavailable', 'snapshot_changed', 'stale_or_future_evidence'
    } else 'unexpected_status'
    for field, allowed in (
        ('error_type', SAFE_ERROR_TYPES),
        ('stage', {'load_coverage', 'collect_local_coverage', 'build_editorial_workpack', 'review_batch'}),
        ('coverage_status', {'observed', 'unavailable', 'snapshot_changed'}),
    ):
        value = pack.get(field)
        if isinstance(value, str) and value in allowed:
            diagnostic[field] = value
    if exit_code != 0 or status != 'observed':
        raise CaptureError('workpack_capture_unavailable', diagnostic)
    if type(pack.get('schema_version')) is not int or pack['schema_version'] != 1:
        raise CaptureError('workpack_output_invalid', diagnostic)
    return pack


def recent_publications(publications, manifests, now):
    """Bounded canonical receipt projection, not fresh provider observation."""
    rows, claims = [], defaultdict(set)
    unresolved = 0
    for publication in publications.values():
        receipt = publication['receipt']
        identity = tuple(receipt.get(k) for k in ('campaign', 'provider', 'account_id', 'post_id'))
        if not all(isinstance(v, str) and v for v in identity):
            raise ValueError('incomplete_publication_identity')
        stamp = publication['at']
        if stamp.tzinfo is None or stamp > now:
            raise ValueError('invalid_publication_time')
        claims[identity[1:]].add(identity[0])
        if stamp < now - timedelta(days=7):
            continue
        project = manifests.get(identity[0], {}).get('project')
        if not isinstance(project, str) or not project:
            unresolved += 1
            continue
        rows.append({**_fields(receipt, PUBLICATION_FIELDS), 'project': project,
                     'published_at': stamp.isoformat(),
                     'status': 'published_verified' if publication.get('effective_verified') is True
                     and receipt.get('readback_verified') is True else 'published_unverified'})
    conflicts = {key for key, ids in claims.items() if len(ids) != 1}
    accepted = [r for r in rows if (r['provider'], r['account_id'], r['post_id']) not in conflicts]
    unresolved += len(rows) - len(accepted)
    accepted.sort(key=lambda r: (at(r['published_at']), r['provider'], r['account_id'], r['campaign']), reverse=True)
    omitted = max(0, len(accepted) - MAX_PUBLICATIONS)
    return {'status': 'partial' if omitted or unresolved else 'observed',
            'lookback_days': 7, 'total_matched': len(accepted), 'omitted_count': omitted,
            'unresolved_count': unresolved, 'records': accepted[:MAX_PUBLICATIONS],
            'boundary': 'Canonical local receipt/readback projection. Absence is unknown, not unpublished. '
                        'Match campaign, provider, account and exact payload; never replay an uncertain effect.'}


def _audience_projection(raw):
    rows = raw.get('records', []) if isinstance(raw, dict) else []
    projected = []
    for row in rows[:MAX_AUDIENCE_EXPORT]:
        if not isinstance(row, dict):
            continue
        item = _fields(row, ('project', 'campaign', 'provider', 'account_id', 'post_id', 'text_sha256',
                             'published_at', 'status', 'editorial_assessment_recorded', 'assessment_sha256'))
        performance = []
        for snapshot in row.get('performance', [])[:3]:
            if not isinstance(snapshot, dict):
                continue
            metrics = snapshot.get('metrics') if isinstance(snapshot.get('metrics'), dict) else {}
            performance.append({**_fields(snapshot, ('captured_at', 'target_age_hours', 'availability')),
                                'metrics': {str(k): v for k, v in metrics.items()
                                            if v is None or type(v) in (int, float)}})
        item['performance'] = performance
        inbound = row.get('inbound') if isinstance(row.get('inbound'), dict) else {}
        item['inbound'] = _fields(inbound, ('observed_count', 'status_counts', 'max_conversation_depth'))
        outcomes = row.get('business_outcomes') if isinstance(row.get('business_outcomes'), dict) else {}
        item['business_outcomes'] = _fields(outcomes, ('event_counts', 'revenue_minor_by_currency'))
        projected.append(item)
    omitted = int(raw.get('omitted_count', 0) or 0) + max(0, len(rows) - MAX_AUDIENCE_EXPORT) if isinstance(raw, dict) else 0
    return {'schema_version': 1, 'status': 'partial' if omitted else str(raw.get('status') or 'observed'),
            'observed_at': raw.get('observed_at') if isinstance(raw, dict) else None,
            'records': projected, 'omitted_count': omitted,
            'boundary': 'Campaign-linked descriptive response evidence only; no sentiment, recognition or causal lift is inferred.'}


def _snapshot_size_diagnostic(data):
    section_names = (
        'supply_reviews', 'request_reconciliation', 'accounts',
        'publication_evidence', 'audience_evidence',
    )
    section_bytes = {
        name: len(canonical(data.get(name)).encode())
        for name in section_names
    }
    publication = data.get('publication_evidence') if isinstance(data.get('publication_evidence'), dict) else {}
    audience = data.get('audience_evidence') if isinstance(data.get('audience_evidence'), dict) else {}
    return {
        'stage': 'final_envelope',
        'output_bytes': len(canonical(data).encode()),
        'limit_bytes': MAX_BYTES,
        'section_bytes': section_bytes,
        'supply_review_count': len(data.get('supply_reviews') or []),
        'account_count': len(data.get('accounts') or []),
        'publication_record_count': len(publication.get('records') or []),
        'audience_record_count': len(audience.get('records') or []),
    }


def make_snapshot(pack, revision, now):
    if pack.get('status') != 'observed' or type(pack.get('schema_version')) is not int or pack['schema_version'] != 1:
        raise ValueError('complete_workpack_required')
    if not 0 <= (now - at(pack['coverage_observed_at'])).total_seconds() <= 3600:
        raise ValueError('stale_or_future_workpack')
    if not re.fullmatch('[0-9a-f]{40}', revision):
        raise ValueError('invalid_source_revision')
    continuity = pack.get('continuity') if isinstance(pack.get('continuity'), dict) else {}
    source_needs = continuity.get('open_requests') if isinstance(continuity.get('open_requests'), list) else pack['supply_reviews']
    needs = []
    for row in source_needs:
        item = _fields(row, ('project', 'provider', 'account_id', 'aliases', 'exclusion_counts',
                             'request_id', 'route_key', 'generation', 'first_requested_at', 'last_observed_at',
                             'demand_reasons', 'runnable', 'reserved', 'stock_floor',
                             'editorial_runway_hours', 'surviving_runway_hours',
                             'expiring_within_48h', 'earliest_runnable_expiry', 'surviving_after_48h',
                             'market_cold_hours', 'market_cold', 'hours_since_last_effect',
                             'last_effect_at', 'last_verified_publication_at', 'next_scheduled_at',
                             'scheduled_count', 'conditions', 'suggested_new_items',
                             'request', 'stock_floor_basis', 'permission_to_replay_or_activate'))
        item['source'] = _fields(row.get('source', {}), ('status', 'head_sha', 'readme_sha', 'pending_events'))
        item['vaults'] = [_fields(v, ('id', 'status', 'valid_until', 'active_entries', 'skipped_entries'))
                          for v in row.get('vaults', [])]
        needs.append(item)
    accounts = []
    for row in pack['accounts']:
        item = _fields(row, ('provider', 'account_id', 'projects', 'runnable', 'reserved',
                             'verified_24h', 'unverified_retained', 'publishing_intent',
                             'empty_inventory', 'no_verified_post_24h'))
        effect = row.get('latest_verified_effect')
        item['latest_verified_effect'] = (_fields(effect, ('campaign', 'post_id', 'published_at')) if effect else None)
        accounts.append(item)
    if len(needs) > 500 or len(accounts) > 100:
        raise ValueError('complete_snapshot_exceeds_route_bound')
    evidence = pack.get('publication_evidence', {'status': 'not_collected', 'records': []})
    publication_evidence = _fields(evidence, ('status', 'lookback_days', 'total_matched', 'omitted_count', 'unresolved_count', 'boundary'))
    publication_evidence['records'] = [_fields(r, PUBLICATION_FIELDS) for r in evidence['records']]
    if len(publication_evidence['records']) > MAX_PUBLICATIONS:
        raise ValueError('publication_evidence_exceeds_bound')
    reconciliation = _fields(continuity, ('status', 'observed_at', 'open_request_count', 'resolved_this_cycle', 'boundary'))
    audience = _audience_projection(pack.get('audience_evidence') if isinstance(pack.get('audience_evidence'), dict) else {'status': 'not_collected', 'records': []})
    data = {'schema_version': 1, 'kind': KIND, 'status': 'observed',
            'observed_at': pack['coverage_observed_at'], 'source_revision': revision,
            'coverage_sha256': pack['coverage_sha256'],
            'project_count': pack['project_count'], 'route_count': pack['route_count'],
            'physical_account_count': pack['physical_account_count'],
            'supply_reviews': needs, 'request_reconciliation': reconciliation,
            'accounts': accounts, 'publication_evidence': publication_evidence,
            'audience_evidence': audience,
            'authority': 'read-only host evidence; not editorial approval, publication authority or product news'}
    data['snapshot_sha256'] = digest(data)
    diagnostic = _snapshot_size_diagnostic(data)
    if diagnostic['output_bytes'] > MAX_BYTES:
        raise CaptureError('complete_snapshot_exceeds_byte_bound', diagnostic)
    return data


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _transport_diagnostic(exc, *, stage, method, write_attempted):
    reason = exc.reason if isinstance(exc, urllib.error.URLError) else exc
    if isinstance(reason, socket.gaierror):
        category = 'dns'
    elif isinstance(reason, ssl.SSLError):
        category = 'tls'
    elif isinstance(reason, TimeoutError):
        category = 'timeout'
    elif isinstance(reason, ConnectionRefusedError):
        category = 'connection_refused'
    elif isinstance(reason, ConnectionResetError):
        category = 'connection_reset'
    elif isinstance(reason, BrokenPipeError):
        category = 'broken_pipe'
    elif isinstance(reason, OSError):
        category = 'socket'
    else:
        category = 'transport'
    return {
        'stage': stage,
        'method': method,
        'network_error': category,
        'write_attempted': bool(write_attempted),
    }


class Api:
    def __init__(self, token, deadline):
        self.token, self.deadline = token, deadline
        self.opener = urllib.request.build_opener(NoRedirect())
        self.write_attempted = False

    def __call__(self, method, resource, payload=None):
        allowed = ('', '/contents/' + PATH + '?ref=' + BRANCH, '/contents/' + PATH)
        if resource not in allowed or method not in {'GET', 'PUT'} or (method == 'PUT' and resource != allowed[2]):
            raise ValueError('unapproved_telemetry_endpoint')
        remaining = self.deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError('handoff_budget_exhausted')
        if not resource:
            stage = 'repository_identity'
        elif method == 'PUT':
            stage = 'snapshot_write'
        elif self.write_attempted:
            stage = 'snapshot_readback'
        else:
            stage = 'snapshot_read'
        if method == 'PUT':
            # Mark before the network call so any later GET is correctly
            # identified as a readback after an uncertain write attempt.
            self.write_attempted = True
        request = urllib.request.Request('https://api.github.com/repos/' + REPO + resource,
            data=canonical(payload).encode() if payload is not None else None, method=method,
            headers={'Authorization': 'Bearer ' + self.token, 'Accept': 'application/vnd.github+json',
                     'Content-Type': 'application/json', 'X-GitHub-Api-Version': '2022-11-28',
                     'User-Agent': 'post-once-editorial-handoff/1'})
        try:
            with self.opener.open(request, timeout=min(8, remaining)) as response:
                raw = response.read(MAX_API_RESPONSE_BYTES + 1)
        except urllib.error.HTTPError as exc:
            raise ValueError('github_http_' + str(exc.code)) from None
        except (urllib.error.URLError, TimeoutError) as exc:
            raise CaptureError(
                'github_transport_unavailable',
                _transport_diagnostic(
                    exc, stage=stage, method=method,
                    write_attempted=self.write_attempted,
                ),
            ) from None
        if len(raw) > MAX_API_RESPONSE_BYTES:
            raise ValueError('github_response_too_large')
        return strict_json(raw)


def remote(api):
    file = api('GET', '/contents/' + PATH + '?ref=' + BRANCH)
    if file.get('path') != PATH or file.get('encoding') != 'base64' or not re.fullmatch('[0-9a-f]{40}', str(file.get('sha', ''))):
        raise ValueError('handoff_destination_mismatch')
    raw = base64.b64decode(''.join(file['content'].split()), validate=True)
    if len(raw) > MAX_BYTES:
        raise ValueError('remote_snapshot_too_large')
    data = strict_json(raw)
    if data.get('kind') != KIND or type(data.get('schema_version')) is not int or data['schema_version'] != 1:
        raise ValueError('remote_ownership_marker_missing')
    if data.get('status') == 'observed':
        expected = digest({k: v for k, v in data.items() if k != 'snapshot_sha256'})
        if data.get('snapshot_sha256') != expected:
            raise ValueError('remote_snapshot_hash_mismatch')
    elif data.get('status') != 'awaiting_host':
        raise ValueError('remote_status_not_supported')
    return file, data


def publish_snapshot(snapshot, api, now):
    if not 0 <= (now - at(snapshot['observed_at'])).total_seconds() <= 3600:
        raise ValueError('stale_or_future_workpack')
    repo = api('GET', '')
    if str(repo.get('id')) != str(REPO_ID) or repo.get('private') is not True or repo.get('full_name', '').lower() != REPO.lower():
        raise ValueError('private_pinned_repository_required')
    file, previous = remote(api)
    if previous['status'] == 'observed':
        old_at = at(previous['observed_at'])
        if old_at > now or old_at > at(snapshot['observed_at']):
            raise ValueError('newer_remote_snapshot_preserved')
        if old_at == at(snapshot['observed_at']) and previous['snapshot_sha256'] != snapshot['snapshot_sha256']:
            raise ValueError('conflicting_snapshot_timestamp')
        if previous['snapshot_sha256'] == snapshot['snapshot_sha256'] or (now - old_at).total_seconds() < 900:
            return {'status': 'observed', 'write_performed': False,
                    'remote_observed_at': previous['observed_at'], 'snapshot_sha256': previous['snapshot_sha256']}
    payload = {'branch': BRANCH, 'sha': file['sha'],
               'message': 'chore(editorial): refresh private consumption evidence',
               'content': base64.b64encode((canonical(snapshot) + '\n').encode()).decode()}
    put_error = None
    try:
        api('PUT', '/contents/' + PATH, payload)
    except (OSError, ValueError) as exc:
        put_error = exc
    _, checked = remote(api)
    if checked.get('snapshot_sha256') != snapshot['snapshot_sha256']:
        if put_error:
            raise put_error
        raise ValueError('handoff_readback_mismatch')
    return {'status': 'observed', 'write_performed': True,
            'remote_observed_at': checked['observed_at'], 'snapshot_sha256': checked['snapshot_sha256']}


def checkout_revision():
    for args in (['git', 'diff', '--quiet'], ['git', 'diff', '--cached', '--quiet']):
        if subprocess.run(args, cwd=ROOT, timeout=3, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode:
            raise ValueError('tracked_checkout_changes')
    return subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, timeout=3, stderr=subprocess.DEVNULL).decode().strip()


def _snapshot_contention(exc):
    if isinstance(exc, CaptureError):
        diagnostic = exc.diagnostic
        return (diagnostic.get('stage') == 'build_editorial_workpack'
                and diagnostic.get('coverage_status') == 'snapshot_changed')
    return isinstance(exc, ValueError) and str(exc) == 'host_snapshot_changed'


def _capture_base():
    """Capture one optimistic read snapshot without writing editorial state."""
    from ocpf_post.operations_snapshot import coverage_fingerprints
    from ocpf_post.performance_review import publications
    from ocpf_post.source_receipts import publication_inputs
    revision, before = checkout_revision(), coverage_fingerprints()
    with tempfile.TemporaryFile() as output:
        p = subprocess.Popen([sys.executable, str(ROOT / 'scripts/editorial-supply.py')], cwd=ROOT,
                             stdout=output, stderr=subprocess.DEVNULL, start_new_session=True)
        try:
            try:
                code = p.wait(timeout=35)
            except subprocess.TimeoutExpired:
                raise TimeoutError('workpack_capture_timed_out') from None
        finally:
            if p.poll() is None:
                try:
                    os.killpg(p.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                p.wait(timeout=2)
        output.seek(0)
        raw = output.read(5_000_001)
    pack = decode_workpack(raw, code)
    manifests, _, _ = publication_inputs()
    pack['publication_evidence'] = recent_publications(publications(), manifests, datetime.now(timezone.utc))
    if before != coverage_fingerprints() or revision != checkout_revision():
        raise ValueError('host_snapshot_changed')
    return pack, revision


def capture():
    """Retry only optimistic-read contention; never retry a provider or write consequence."""
    last_error = None
    for attempt in range(CAPTURE_ATTEMPTS):
        try:
            pack, revision = _capture_base()
            break
        except (CaptureError, ValueError) as exc:
            if not _snapshot_contention(exc):
                raise
            last_error = exc
            if attempt + 1 >= CAPTURE_ATTEMPTS:
                raise
            time.sleep(CAPTURE_RETRY_DELAY_SECONDS)
    else:  # pragma: no cover - loop always returns or raises
        raise last_error
    from ocpf_post import editorial_continuity
    observed_at = datetime.now(timezone.utc)
    pack['continuity'] = editorial_continuity.reconcile(pack, now=observed_at, apply=True)
    pack['audience_evidence'] = editorial_continuity.audience_evidence(now=observed_at)
    return make_snapshot(pack, revision, observed_at)


def policy_enabled(policy):
    return canonical(policy) == canonical(CONFIG)


def run(args):
    from ocpf_post import local_store
    from ocpf_post.state import config_dir, state_dir, ensure_private_dir
    policy_path = config_dir() / 'editorial-handoff.json'
    status_path = state_dir() / 'editorial-handoff-status.json'
    policy = local_store.read(policy_path)
    if args.status:
        return {'enabled': policy_enabled(policy), 'last_cycle': read_status(status_path)}
    if not args.enable and not args.disable and not policy_enabled(policy):
        return {'status': 'disabled', 'reason': 'exact_opt_in_required'}
    if not args.apply:
        return {'status': 'preview_disable' if args.disable else 'preview', 'policy': CONFIG,
                'boundary': 'No network or state writes; --enable --apply is required once on the owner host.'}
    ensure_private_dir(state_dir())
    with (state_dir() / 'editorial-handoff.lock').open('a') as lock:
        os.chmod(lock.name, 0o600)
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return {'status': 'busy', 'write_performed': False}
        policy = local_store.read(policy_path)
        if args.disable:
            local_store.write(policy_path, {**CONFIG, 'enabled': False})
            return {'status': 'disabled', 'write_performed': False}
        if not args.enable and not policy_enabled(policy):
            return {'status': 'disabled', 'reason': 'exact_opt_in_required'}
        read_status(status_path)
        try:
            from ocpf_post.replenisher import _github_token
            token = _github_token()
            if not token:
                raise ValueError('existing_github_credential_unavailable')
            deadline = time.monotonic() + 68
            snapshot = capture()
            result = publish_snapshot(snapshot, Api(token, deadline), datetime.now(timezone.utc))
            persist_status(status_path, {**result, 'recorded_at': datetime.now(timezone.utc).isoformat()})
            if args.enable:
                local_store.write(policy_path, CONFIG)
        except (OSError, ValueError, RuntimeError, KeyError, TypeError, subprocess.SubprocessError) as exc:
            if _snapshot_contention(exc):
                deferred = {'status': 'deferred', 'observed_at': datetime.now(timezone.utc).isoformat(),
                            'reason': 'host_snapshot_changing', 'publishing_unchanged': True}
                if isinstance(exc, CaptureError):
                    deferred['diagnostic'] = exc.diagnostic
                persist_status(status_path, deferred)
                return {'status': 'deferred', 'reason': 'host_snapshot_changing',
                        'write_performed': False, 'publishing_unchanged': True,
                        'enabled': policy_enabled(policy)}
            failure = {'status': 'attention', 'observed_at': datetime.now(timezone.utc).isoformat(),
                       'error_type': type(exc).__name__}
            if isinstance(exc, CaptureError):
                failure['diagnostic'] = exc.diagnostic
            persist_status(status_path, failure)
            raise
        return {**result, 'enabled': True, 'repository': REPO, 'branch': BRANCH, 'path': PATH,
                'boundary': 'Private consumption telemetry only. No social, vault, scheduling, metrics or credential-permission mutation.'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group()
    group.add_argument('--enable', action='store_true')
    group.add_argument('--disable', action='store_true')
    group.add_argument('--status', action='store_true')
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args()
    def deadline_expired(signum, frame):
        raise TimeoutError('handoff_budget_exhausted')
    handlers = {sig: signal.signal(sig, deadline_expired) for sig in (signal.SIGALRM, signal.SIGTERM)}
    previous_timer = signal.setitimer(signal.ITIMER_REAL, 68)
    try:
        try:
            result = run(args)
            code = 2 if result.get('status') in {'busy', 'deferred'} else 0
        except Exception as exc:
            code = 2
            safe = str(exc) if isinstance(exc, ValueError) and re.fullmatch('[a-z0-9_]+', str(exc)) else type(exc).__name__
            result = {'status': 'attention', 'error': safe, 'publishing_unchanged': True}
            if isinstance(exc, CaptureError):
                result['diagnostic'] = exc.diagnostic
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        for sig, handler in handlers.items():
            signal.signal(sig, handler)
        signal.setitimer(signal.ITIMER_REAL, *previous_timer)
    print(json.dumps(result, indent=2))
    return code


if __name__ == '__main__':
    sys.exit(main())
