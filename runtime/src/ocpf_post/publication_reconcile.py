"""Read known campaign IDs without altering historical sends or retry authority."""
from datetime import datetime, timedelta, timezone
import hashlib
import re
import time
from urllib.parse import quote, urlencode
from ocpf_post import local_store
from ocpf_post.state import state_dir
from ocpf_post.source_receipts import publication_inputs
from ocpf_post.providers import get_provider, for_account
from ocpf_post.providers.base import ProviderRejected, ProviderUnavailable
from ocpf_post.engagement import digest, post_id, at
from ocpf_post.readback_details import compare

KEYS = ('schedule_id', 'campaign', 'provider', 'account_id', 'post_id', 'text_sha256')
MAX_READS = 20
MAX_FORENSIC_SEARCHES = 2
FORENSIC_WINDOW_SECONDS = 10 * 60
BUDGET_SECONDS = 90
FORENSIC_REVIEW_REQUIRED = {
    'forensic_no_match',
    'forensic_multiple_matches',
    'forensic_window_truncated',
    'forensic_readback_mismatch',
}


def _key(row):
    return digest([row.get(k) for k in KEYS])


def _read(client, provider, identity):
    if provider == 'x':
        status, body = client._bearer('https://api.x.com/2/tweets/' + post_id(identity) +
                                     '?' + urlencode({'tweet.fields': 'author_id,entities'}))
        post = body.get('data', {})
        return status, {'post_id': str(post.get('id') or ''), 'author': post.get('author_id'),
                        'text': post.get('text'), 'entities': post.get('entities')}
    if provider == 'threads':
        status, _, post = client._bearer('https://graph.threads.net/v1.0/' + post_id(identity),
                                         query={'fields': 'id,text,username'})
        return status, {'post_id': str(post.get('id') or ''), 'author': post.get('username'), 'text': post.get('text')}
    # The publishing adapter already uses this endpoint for readback. Access is
    # observed, never inferred from w_member_social or an OAuth connection.
    if not re.fullmatch(r'urn:li:(share|ugcPost):[0-9]{1,30}', identity):
        raise ValueError('Invalid LinkedIn post identity')
    from ocpf_post.providers.http import request_json
    from ocpf_post.providers.linkedin import POSTS_URL
    status, _, post = request_json(POSTS_URL + '/' + quote(identity, safe=''),
                                   headers=client._headers(), query={'viewContext': 'AUTHOR'})
    return status, {'post_id': str(post.get('id') or ''), 'author': post.get('author'),
                    'text': post.get('commentary'), 'lifecycle_state': post.get('lifecycleState')}


def _read_forensic_thread(client, identity):
    status, _, post = client._readonly_bearer(
        'https://graph.threads.net/v1.0/' + post_id(identity),
        query={'fields': 'id,text,username,permalink,timestamp'},
    )
    return status, {
        'post_id': str(post.get('id') or ''),
        'author': post.get('username'),
        'text': post.get('text'),
        'permalink': post.get('permalink'),
        'timestamp': post.get('timestamp'),
    }


def _forensic_window(schedule):
    anchor = at(schedule.get('updated_at') or schedule.get('run_at'))
    if anchor is None:
        raise ValueError('forensic_time_anchor_required')
    return (
        anchor - timedelta(seconds=FORENSIC_WINDOW_SECONDS),
        anchor + timedelta(seconds=FORENSIC_WINDOW_SECONDS),
    )


def _forensic_candidates(posts, schedule, account, start, end):
    matches = []
    for row in posts:
        try:
            identity = post_id(str(row.get('id') or ''))
            observed_at = at(row.get('timestamp'))
        except (ValueError, TypeError):
            continue
        if observed_at is None or not start <= observed_at <= end:
            continue
        if row.get('text') != schedule.get('text'):
            continue
        if account.username and row.get('username') != account.username:
            continue
        matches.append({
            'post_id': identity,
            'permalink': row.get('permalink'),
            'timestamp': observed_at.isoformat(),
        })
    return matches


def _linkedin_read_permission(account_id, *, purpose='posts', factory=get_provider):
    """Inspect recorded LinkedIn read authority without contacting LinkedIn."""
    # Injected providers are synthetic/test boundaries and must not be coupled
    # to the operator host's credential metadata.
    if factory is not get_provider:
        return None
    try:
        from ocpf_post.providers.linkedin import recorded_read_permission
        value = recorded_read_permission(account_id, purpose)
    except (OSError, ValueError, KeyError, TypeError):
        return None
    return value if isinstance(value, dict) else None


def pending():
    _, schedules, _ = publication_inputs()
    saved = local_store.read(state_dir() / 'publication-readbacks.json') or {'observations': {}}
    observations = saved.get('observations', {}) if isinstance(saved.get('observations'), dict) else {}
    for schedule in schedules:
        if schedule.get('status') not in {'published_unverified', 'ambiguous_effect', 'executing'}:
            continue
        if schedule.get('publication_type') == 'thread':
            # Part-level consequence evidence lives in the schedule ledger.
            # Generic one-post reconciliation cannot safely complete a thread.
            continue
        row = {k: schedule.get(k) for k in KEYS}
        previous = observations.get(_key(row), {})
        if schedule.get('post_id') and previous.get('status') == 'verified':
            continue
        if (
            schedule.get('post_id')
            and schedule.get('provider') == 'linkedin'
            and previous.get('status') == 'readback_permission_required'
        ):
            permission = _linkedin_read_permission(str(schedule.get('account_id') or ''))
            if permission and permission.get('status') == 'missing':
                continue
        if not schedule.get('post_id') and previous.get('status') in (
            {'verified_discovered'} | FORENSIC_REVIEW_REQUIRED
        ):
            continue
        return True
    return False

def reconcile(
    *,
    apply=False,
    now=None,
    factory=get_provider,
    campaign=None,
    provider=None,
    schedule_id=None,
):
    now = now or datetime.now(timezone.utc)
    _, schedules, _ = publication_inputs()
    path = state_dir() / 'publication-readbacks.json'
    saved = local_store.read(path) or {'schema_version': 1, 'observations': {}}
    observations = saved['observations']
    rows = []; targets = []; forensic_targets = []; clients = {}; reads = 0; forensic_searches = 0
    blocked_accounts = {tuple(scope.split(':', 1)): value
                        for scope, value in saved.get('account_backoff', {}).items()
                        if now < at(value['retry_at'])}
    deadline = time.monotonic() + BUDGET_SECONDS
    exact_manual_target = bool(campaign and provider and schedule_id)

    def persist(row):
        if not apply:
            return
        with local_store.locked(path):
            data = local_store.read(path) or {'schema_version': 1, 'observations': {}}
            data['observations'][_key(row)] = row
            local_store.write(path, data)

    def backoff(scope, row):
        blocked_accounts[scope] = {k: row[k] for k in ('http_status', 'retry_at')}
        with local_store.locked(path):
            data = local_store.read(path) or {'schema_version': 1, 'observations': {}}
            data.setdefault('account_backoff', {})[':'.join(scope)] = blocked_accounts[scope]
            local_store.write(path, data)

    for schedule in schedules:
        if schedule.get('status') not in {'published_unverified', 'ambiguous_effect', 'executing'}:
            continue
        if campaign and schedule.get('campaign') != campaign:
            continue
        if provider and schedule.get('provider') != provider:
            continue
        if schedule_id and schedule.get('schedule_id') != schedule_id:
            continue
        if schedule.get('publication_type') == 'thread':
            row = {k: schedule.get(k) for k in KEYS}
            row.update(
                original_status=schedule['status'],
                status='thread_manual_review_required',
                observed_at=now.isoformat(),
                publication_type='thread',
                part_count=schedule.get('part_count'),
                publication_completed_parts=schedule.get('publication_completed_parts'),
                publication_failed_part_index=schedule.get('publication_failed_part_index'),
                publication_part_ids=list(schedule.get('publication_part_ids') or []),
                automatic_retry=False,
            )
            rows.append(row)
            persist(row)
            continue
        row = {k: schedule.get(k) for k in KEYS}
        row.update(original_status=schedule['status'], status='post_id_required', observed_at=now.isoformat())
        previous = observations.get(_key(row), {})
        if row['provider'] not in {'x', 'threads', 'linkedin'}:
            row['status'] = 'unsupported_readback'
        elif not row['post_id'] and row['provider'] == 'threads' and schedule.get('status') == 'ambiguous_effect':
            if not schedule.get('text'):
                row['status'] = 'frozen_payload_required'
            elif hashlib.sha256(schedule['text'].encode()).hexdigest() != row['text_sha256']:
                row['status'] = 'frozen_payload_mismatch'
            elif previous.get('status') == 'verified_discovered':
                rows.append({**previous, 'cached': True})
                continue
            elif previous.get('status') in FORENSIC_REVIEW_REQUIRED and not exact_manual_target:
                rows.append({
                    **previous,
                    'status': 'forensic_review_required',
                    'previous_status': previous.get('status'),
                    'automatic_retry': False,
                    'next_action': 'targeted_manual_review_without_resend',
                    'cached': True,
                })
                continue
            elif previous.get('retry_at') and now < at(previous['retry_at']):
                rows.append({**row, 'status': 'forensic_search_deferred',
                             'retry_at': previous['retry_at'], 'previous_status': previous.get('status')})
                continue
            else:
                forensic_targets.append((schedule, row, previous))
                continue
        elif row['post_id'] and not schedule.get('text'):
            row['status'] = 'frozen_payload_required'
        elif row['post_id'] and schedule.get('text'):
            if hashlib.sha256(schedule['text'].encode()).hexdigest() != row['text_sha256']:
                row['status'] = 'frozen_payload_mismatch'
            elif previous.get('status') == 'verified':
                rows.append({**previous, 'cached': True})
                continue
            elif row['provider'] == 'linkedin':
                permission = _linkedin_read_permission(row['account_id'], factory=factory)
                if permission and permission.get('status') == 'missing':
                    gated = {
                        **row,
                        'status': 'readback_permission_required',
                        'required_scope': permission.get('required_scope'),
                        'scope_recorded': permission.get('scope_recorded'),
                        'automatic_retry': False,
                        'next_action': 'provider_approval_required_before_reauthorise',
                    }
                    rows.append(gated)
                    persist(gated)
                    continue
                if previous.get('retry_at') and now < at(previous['retry_at']):
                    rows.append({**row, 'status': 'readback_deferred', 'retry_at': previous['retry_at'],
                                 'previous_status': previous.get('status')})
                    continue
                targets.append((schedule, row, previous))
                continue
            elif previous.get('retry_at') and now < at(previous['retry_at']):
                rows.append({**row, 'status': 'readback_deferred', 'retry_at': previous['retry_at'],
                             'previous_status': previous.get('status')})
                continue
            else:
                targets.append((schedule, row, previous))
                continue
        rows.append(row)
        persist(row)

    def order(target):
        schedule, _, previous = target
        # Never-checked/newer effects first; then the least recently inspected.
        # Historical unknown IDs do not consume the external-read budget.
        last = at(previous['observed_at']).timestamp() if previous.get('observed_at') else float('-inf')
        created = at(schedule['run_at']).timestamp() if schedule.get('run_at') else 0
        return last, -created, schedule['schedule_id']

    for schedule, row, previous in sorted(forensic_targets, key=order):
        scope = (row['provider'], row['account_id'])
        if scope in blocked_accounts:
            rows.append({**row, 'status': 'account_readback_deferred', **blocked_accounts[scope]})
            continue
        if forensic_searches >= MAX_FORENSIC_SEARCHES or time.monotonic() >= deadline:
            rows.append({**row, 'status': 'forensic_search_deferred', 'reason': 'cycle_budget'})
            continue
        row['status'] = 'forensic_search_due'
        if apply:
            persist({**row, 'status': 'forensic_search_started'})
            forensic_searches += 1
            try:
                if scope not in clients:
                    client = for_account(*scope, factory=factory, require_enabled=False)
                    if not hasattr(client, 'readonly_account') or not hasattr(client, 'recent_threads'):
                        raise ValueError('threads_forensic_reader_unavailable')
                    account = client.readonly_account()
                    if account.account_id != row['account_id']:
                        raise ValueError('account_mismatch')
                    if not account.username:
                        raise ValueError('account_username_required')
                    clients[scope] = client, account
                client, account = clients[scope]
                start, end = _forensic_window(schedule)
                listing = client.recent_threads(
                    since_epoch=int(start.timestamp()),
                    until_epoch=int(end.timestamp()),
                    limit=50,
                    max_pages=3,
                )
                reads += int(listing.get('reads') or 0)
                row.update(
                    forensic_window_start=start.isoformat(),
                    forensic_window_end=end.isoformat(),
                    forensic_pages_read=int(listing.get('reads') or 0),
                )
                if listing.get('truncated'):
                    row.update(
                        status='forensic_window_truncated',
                        automatic_retry=False,
                        next_action='manual_review_no_resend',
                    )
                else:
                    matches = _forensic_candidates(listing.get('posts', []), schedule, account, start, end)
                    row['candidate_count'] = len(matches)
                    if not matches:
                        row.update(
                            status='forensic_no_match',
                            automatic_retry=False,
                            next_action='manual_review_no_resend',
                        )
                    elif len(matches) > 1:
                        row.update(
                            status='forensic_multiple_matches',
                            automatic_retry=False,
                            next_action='manual_review_no_resend',
                        )
                        row['candidate_post_ids'] = [item['post_id'] for item in matches[:5]]
                    else:
                        candidate = matches[0]
                        row.update(
                            discovered_post_id=candidate['post_id'],
                            discovered_url=candidate.get('permalink'),
                            discovered_timestamp=candidate.get('timestamp'),
                        )
                        status, observed = _read_forensic_thread(client, candidate['post_id'])
                        reads += 1
                        row['http_status'] = status
                        if 200 <= status < 300:
                            expected = {
                                'post_id': candidate['post_id'],
                                'author': account.username,
                                'text': schedule['text'],
                            }
                            row.update(compare(expected, observed, provider='threads'))
                            row['status'] = (
                                'verified_discovered'
                                if not row['mismatch_fields']
                                else 'forensic_readback_mismatch'
                            )
                            if row['status'] == 'forensic_readback_mismatch':
                                row.update(
                                    automatic_retry=False,
                                    next_action='manual_review_no_resend',
                                )
                        else:
                            row['status'] = (
                                'readback_access_denied' if status in {401, 403}
                                else 'readback_rate_limited' if status == 429
                                else 'unavailable'
                            )
                            row['retry_at'] = (
                                now + timedelta(hours=24 if status in {401, 403} else 1)
                            ).isoformat()
                            if status in {401, 403, 429} or status >= 500:
                                backoff(scope, row)
            except ProviderRejected as exc:
                row.update(
                    http_status=exc.status,
                    status=(
                        'readback_access_denied' if exc.status in {401, 403}
                        else 'readback_rate_limited' if exc.status == 429
                        else 'unavailable'
                    ),
                    retry_at=(now + timedelta(hours=24 if exc.status in {401, 403} else 1)).isoformat(),
                )
                if exc.status in {401, 403, 429} or exc.status >= 500:
                    backoff(scope, row)
            except ProviderUnavailable:
                row.update(
                    status='unavailable',
                    error_type='ProviderUnavailable',
                    retry_at=(now + timedelta(hours=1)).isoformat(),
                )
            except Exception as exc:
                row.update(status='unavailable', error_type=type(exc).__name__)
            persist(row)
        rows.append(row)

    for schedule, row, previous in sorted(targets, key=order):
        scope = (row['provider'], row['account_id'])
        if scope in blocked_accounts:
            rows.append({**row, 'status': 'account_readback_deferred', **blocked_accounts[scope]})
            continue
        if reads >= MAX_READS or time.monotonic() >= deadline:
            rows.append({**row, 'status': 'readback_deferred', 'reason': 'cycle_budget'})
            continue
        row['status'] = 'readback_due'
        if apply:
            # Persist before a potentially interrupted GET. Completed observations
            # survive later process timeouts; the next run rotates past this item.
            persist({**row, 'status': 'readback_started'})
            reads += 1
            try:
                if scope not in clients:
                    client = for_account(*scope, factory=factory, require_enabled=False)
                    account = client.account()
                    if account.account_id != row['account_id']:
                        raise ValueError('account_mismatch')
                    if row['provider'] == 'threads' and not account.username:
                        raise ValueError('account_username_required')
                    clients[scope] = client, account
                client, account = clients[scope]
                status, observed = _read(client, row['provider'], row['post_id'])
                row['http_status'] = status
                if 200 <= status < 300:
                    expected = {'post_id': row['post_id'],
                                'author': account.username if row['provider'] == 'threads' else account.account_id,
                                'text': schedule['text']}
                    if row['provider'] == 'linkedin':
                        expected['lifecycle_state'] = 'PUBLISHED'
                    row.update(compare(expected, observed, provider=row['provider']))
                    row['status'] = 'verified' if not row['mismatch_fields'] else 'readback_mismatch'
                else:
                    row['status'] = ('readback_access_denied' if status in {401, 403} else
                                     'readback_rate_limited' if status == 429 else 'unavailable')
                    row['retry_at'] = (now + timedelta(hours=24 if status in {401, 403} else 1)).isoformat()
                    if status in {401, 403, 429} or 500 <= status < 600:
                        backoff(scope, row)
            except Exception as exc:
                row.update(status='unavailable', error_type=type(exc).__name__)
                status = getattr(exc, 'status', None)
                if status in {401, 403, 429} or (isinstance(status, int) and 500 <= status < 600):
                    row.update(http_status=status,
                               status='readback_rate_limited' if status == 429 else
                                      'readback_access_denied' if status in {401, 403} else 'unavailable',
                               retry_at=(now + timedelta(hours=1 if status == 429 or status >= 500 else 24)).isoformat())
                    backoff(scope, row)
            persist(row)
        rows.append(row)
    statuses = {r['status'] for r in rows}
    hard = {'unavailable', 'readback_access_denied', 'readback_rate_limited'}
    incomplete = {
        'post_id_required', 'unsupported_readback', 'frozen_payload_required', 'frozen_payload_mismatch',
        'readback_mismatch', 'readback_deferred', 'account_readback_deferred', 'readback_started',
        'readback_permission_required',
        'forensic_search_due', 'forensic_search_deferred', 'forensic_search_started',
        'forensic_no_match', 'forensic_multiple_matches', 'forensic_window_truncated',
        'forensic_readback_mismatch', 'forensic_review_required', 'thread_manual_review_required',
    }
    status = ('attention' if statuses & hard else 'partial' if statuses & incomplete else
              'observed' if rows else 'idle')
    return {
        'schema_version': 1,
        'status': status,
        'results': rows,
        'reads_attempted': reads,
        'max_reads': MAX_READS,
        'forensic_searches_attempted': forensic_searches,
        'max_forensic_searches': MAX_FORENSIC_SEARCHES,
        'boundary': (
            'Known provider IDs use exact account and frozen-payload readback. '
            'Threads ambiguous effects without an ID may use a bounded GET-only own-post search in a narrow '
            'time window, and resolve only on one exact text/username match followed by exact known-ID readback. '
            'Zero, multiple, truncated or mismatched candidates stay unresolved and become manual-review-only; '
            'unattended collection does not repeat the same terminal forensic search. Existing token material is used '
            'without forensic refresh. A fully targeted campaign/provider/schedule command may explicitly re-check. '
            'LinkedIn known-ID reads fail closed on explicitly missing recorded read scope before any provider GET; '
            'unknown scope metadata retains the existing observed-provider path. '
            'Separate evidence preserves original schedules/receipts. No reset, replacement post, resend, delete or '
            'automatic capacity change.'
        ),
        'filters': {
            'campaign': campaign,
            'provider': provider,
            'schedule_id': schedule_id,
        },
    }
