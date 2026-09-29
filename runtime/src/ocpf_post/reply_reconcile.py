"""Exact existing-reply readback. Never send, reset an attempt or infer absence."""
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode
from ocpf_post import engagement as e, local_store
from ocpf_post.providers import get_provider, for_account
from ocpf_post.providers.base import ProviderRejected
from ocpf_post.readback_details import compare

MAX_READS = 20
MAX_HISTORY = 20


def _lookup_verification(client, provider, target, *, parent_hint=None):
    if provider != 'x' or not callable(getattr(client, '_bearer', None)):
        return e.lookup(client, target, parent_hint=parent_hint)
    target = e.post_id(target)
    status, body = client._bearer('https://api.x.com/2/tweets/' + target + '?' + urlencode({
        'tweet.fields': 'author_id,referenced_tweets,entities,display_text_range'}))
    if not 200 <= status < 300:
        raise ProviderRejected(status, 'Reply verification lookup unavailable')
    payload = body.get('data', {})
    observed = e.normalise('x', payload)
    observed['entities'] = payload.get('entities')
    observed['display_text_range'] = payload.get('display_text_range')
    return observed


def _retry(now, hours):
    return e.stamp(now + timedelta(hours=hours))


def _provider_failure(exc, now):
    status = exc.status if isinstance(exc, ProviderRejected) else None
    if status in {401, 403}:
        return 'readback_access_denied', _retry(now, 24), True
    if status == 429:
        return 'readback_rate_limited', _retry(now, 1), True
    if isinstance(status, int) and 500 <= status < 600:
        return 'unavailable', _retry(now, 1), True
    return 'unavailable', _retry(now, 1), False


def reconcile(identity=None, *, apply=False, now=None, factory=get_provider):
    """Reconcile only existing reply IDs with bounded, durable retry state.

    Automatic collection can safely call this repeatedly: provider/account
    failures back off and the least-recently-observed effects get the finite
    read budget first. Exact mismatches remain inspectable and may be rechecked
    on a later collection cycle; they never gain resend authority.
    """
    now = now or datetime.now(timezone.utc)
    rows = []; reads = 0; blocked_accounts = {}
    with local_store.locked(e.path()):
        data = e.read()
        selected = [data['inbox'][identity]] if identity else list(data['inbox'].values())
        immediate = []; targets = []
        for row in selected:
            if row['status'] not in {'published_unverified', 'ambiguous_effect', 'sending'}:
                continue
            result = {'inbox_id': row['id'], 'provider': row['provider'], 'account_id': row['account_id'],
                      'post_id': row.get('post_id'), 'original_status': row['status'], 'status': 'post_id_required'}
            value = row.get('draft') or {}
            retry_at = row.get('readback_retry_at')
            if retry_at and now < e.at(retry_at):
                result.update(status='readback_deferred', retry_at=retry_at,
                              previous_status=row.get('readback_last_status'))
                immediate.append((row, result, False))
            elif row.get('post_id') and value:
                targets.append((row, result, value))
            else:
                immediate.append((row, result, False))

        def order(target):
            row = target[0]
            value = row.get('readback_last_observed_at')
            last = e.at(value).timestamp() if value else float('-inf')
            first = e.at(row.get('published_at') or row.get('attempted_at') or row.get('first_seen_at') or now).timestamp()
            return last, first, row['id']

        def retain(row, result):
            if not apply:
                return
            row['readback_last_observed_at'] = e.stamp(now)
            row['readback_last_status'] = result['status']
            history = list(row.get('verification_history') or [])
            history.append({**result, 'observed_at': e.stamp(now)})
            row['verification_history'] = history[-MAX_HISTORY:]
            local_store.write(e.path(), data)

        for row, result, _ in immediate:
            rows.append(result)
            if result['status'] != 'readback_deferred':
                retain(row, result)

        for row, result, value in sorted(targets, key=order):
            scope = (row['provider'], row['account_id'])
            if scope in blocked_accounts:
                result.update(status='account_readback_deferred', retry_at=blocked_accounts[scope])
                row['readback_retry_at'] = blocked_accounts[scope]
                rows.append(result); retain(row, result); continue
            if reads >= MAX_READS:
                result.update(status='readback_deferred', reason='cycle_budget')
                rows.append(result); continue
            result['status'] = 'readback_due'
            if apply:
                # Durable marker before the external GET. A process interruption
                # cannot make the next cycle believe no verification was attempted.
                row['readback_last_observed_at'] = e.stamp(now)
                row['readback_last_status'] = 'readback_started'
                local_store.write(e.path(), data)
                reads += 1
                try:
                    client = for_account(row['provider'], row['account_id'], factory=factory, require_enabled=False)
                    account = client.account()
                    if account.account_id != row['account_id']:
                        raise ValueError('account_mismatch')
                    if (value.get('provider'), value.get('account_id'), value.get('inbox_id')) != (
                            row['provider'], row['account_id'], row['id']):
                        raise ValueError('draft_binding_mismatch')
                    if e.digest(value) != row.get('review_sha256'):
                        raise ValueError('draft_hash_mismatch')
                    observed = (
                        _lookup_verification(
                            client, row['provider'], row['post_id'],
                            parent_hint=value['context']['post_id'],
                        )
                        if row['provider'] == 'linkedin'
                        else _lookup_verification(client, row['provider'], row['post_id'])
                    )
                    expected = {'post_id': row['post_id'], 'parent_post_id': value['context']['post_id'],
                                'text': value['text'],
                                'author': account.account_id if row['provider'] in {'x', 'linkedin'} else account.username}
                    result.update(compare(expected, observed, provider=row['provider']))
                    result['status'] = 'verified' if not result['mismatch_fields'] else 'readback_mismatch'
                    if result['status'] == 'verified':
                        row.update(status='published_verified', readback_verified=True)
                        row.pop('readback_retry_at', None)
                    else:
                        # A mismatch is evidence, not a transport failure. Retain
                        # it without creating retry/send authority; normal future
                        # collection may inspect the existing ID again.
                        row.pop('readback_retry_at', None)
                except Exception as exc:
                    result['error_type'] = type(exc).__name__
                    status, retry_at, block_account = _provider_failure(exc, now)
                    result.update(status=status, retry_at=retry_at)
                    row['readback_retry_at'] = retry_at
                    if block_account:
                        blocked_accounts[scope] = retry_at
                retain(row, result)
            rows.append(result)

    statuses = {r['status'] for r in rows}
    hard = {'unavailable', 'readback_access_denied', 'readback_rate_limited'}
    partial = {'post_id_required', 'readback_mismatch', 'readback_deferred', 'account_readback_deferred'}
    status = ('attention' if statuses & hard else 'partial' if statuses & partial else
              'observed' if rows else 'idle')
    return {'schema_version': 1, 'observed_at': e.stamp(now), 'results': rows,
            'status': status, 'reads_attempted': reads, 'max_reads': MAX_READS,
            'boundary': 'Existing IDs only. Identity, exact frozen text and reply parent must match after provider display metadata is applied. Least-recently-observed effects rotate through a bounded GET budget and provider/account failures back off. Unknown effects stay blocked. No reply, retry, model call or draft replacement.'}


def add_parser(commands):
    from ocpf_post.onboarding import _run_cli
    parser = commands.add_parser('reconcile')
    parser.add_argument('--id')
    parser.add_argument('--apply', action='store_true', help='Read existing provider IDs and append local verification evidence')
    parser.set_defaults(func=lambda a: _run_cli(lambda: reconcile(a.id, apply=a.apply)))
