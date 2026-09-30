"""Bounded inbound reply collection and exact-context, explicitly sent responses."""
from datetime import datetime, timedelta, timezone
import hashlib
import json
import re
import math
import time
from pathlib import Path
from urllib.parse import urlencode

from ocpf_post import local_store
from ocpf_post.state import state_dir
from ocpf_post.providers import get_provider
from ocpf_post.providers.base import ProviderRejected
from ocpf_post.capacity_experiment import ACCOUNTS, at, stamp

UTC = timezone.utc


def path():
    return state_dir() / 'engagement.json'


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False).encode()).hexdigest()


def read():
    value = local_store.read(path())
    if value and (not isinstance(value.get('inbox'), dict) or not isinstance(value.get('polls'), dict)):
        raise ValueError('Invalid engagement state; retained for inspection')
    return value or {'schema_version': 1, 'inbox': {}, 'polls': {}}


def post_id(value):
    if not isinstance(value, str) or not re.fullmatch(r'[0-9]{1,30}', value):
        raise ValueError('Expected a provider post ID')
    return value


def linkedin_target(value):
    if not isinstance(value, str):
        raise ValueError('Expected a LinkedIn URN')
    if re.fullmatch(r'urn:li:(?:share|ugcPost|activity):[0-9]{1,30}', value):
        return value
    if re.fullmatch(r'urn:li:comment:\(urn:li:(?:share|ugcPost|activity):[0-9]{1,30},[0-9]{1,30}\)', value):
        return value
    raise ValueError('Expected a LinkedIn post/comment URN')


def provider_effect_id(provider, value):
    return linkedin_target(value) if provider == 'linkedin' else post_id(value)


def normalise(provider, row):
    if provider == 'linkedin':
        object_urn = str(row.get('object') or '')
        raw_id = str(row.get('id') or '')
        target = row.get('commentUrn')
        if not target and object_urn and raw_id:
            target = f'urn:li:comment:({object_urn},{raw_id})'
        target = linkedin_target(str(target or ''))
        parent = row.get('parentComment') or row.get('__parent_target')
        author = row.get('actor')
        message = row.get('message') if isinstance(row.get('message'), dict) else {}
        text = message.get('text')
        if not isinstance(text, str) or len(text) > 10000 or not author or not parent:
            raise ValueError('Incomplete LinkedIn comment context')
        result = {'post_id': target, 'parent_post_id': linkedin_target(str(parent)),
                  'author': str(author), 'text': text}
        if object_urn:
            result['object_urn'] = object_urn
        return result
    target = post_id(str(row.get('id') or ''))
    if provider == 'x':
        parents = [r['id'] for r in row.get('referenced_tweets', []) if r.get('type') == 'replied_to']
        parent = parents[0] if len(parents) == 1 else None
        author = row.get('author_id')
    else:
        parent = (row.get('replied_to') or {}).get('id')
        author = row.get('username')
    text = row.get('text')
    if not isinstance(text, str) or len(text) > 10000 or not author or not parent:
        raise ValueError('Incomplete reply context')
    return {'post_id': target, 'parent_post_id': post_id(str(parent)), 'author': str(author), 'text': text}


def lookup(provider, target, *, parent_hint=None):
    if provider.name == 'linkedin':
        target = linkedin_target(target)
        status, payload = provider.comment_lookup(target)
        if parent_hint and not payload.get('parentComment'):
            payload = {**payload, '__parent_target': parent_hint}
    else:
        target = post_id(target)
        if provider.name == 'x':
            status, body = provider._bearer('https://api.x.com/2/tweets/' + target + '?' + urlencode({
                'tweet.fields': 'author_id,referenced_tweets'}))
            payload = body.get('data', {})
        else:
            status, _, payload = provider._bearer('https://graph.threads.net/v1.0/' + target,
                query={'fields': 'id,text,username,replied_to'})
    if not 200 <= status < 300:
        raise ProviderRejected(status, 'Reply context lookup unavailable')
    return normalise(provider.name, payload)


def own_publications(provider, account_id, now):
    from ocpf_post.performance_review import publications
    return {k[3]: {'campaign': k[0], 'published_at': stamp(v['at'])} for k, v in publications().items()
            if k[1:3] == (provider, account_id) and now - timedelta(days=30) <= v['at'] <= now}


def conversation_targets(provider, account_id, now, data=None):
    """Receipt-backed campaign roots plus verified responses on the same account.

    Follow only our own verified outgoing posts. A stranger's arbitrary thread,
    a merely drafted response or an ambiguous effect never becomes a send root.
    """
    data = read() if data is None else data
    roots = {pid: {**value, 'conversation_root_id': pid, 'depth': 0}
             for pid, value in own_publications(provider, account_id, now).items()}
    active_roots = {pid for pid, r in roots.items() if now - timedelta(days=7) <= at(r['published_at']) <= now}
    remaining = [r for r in data['inbox'].values()
                 if (r.get('provider'), r.get('account_id')) == (provider, account_id)
                 and r.get('status') == 'published_verified' and r.get('readback_verified') is True]
    for _ in range(20):
        changed = False
        for row in remaining:
            draft = row.get('draft', {})
            original = draft.get('context', {})
            parent = roots.get(original.get('parent_post_id'))
            pid = row.get('post_id')
            if not parent or not pid or pid in roots or parent['depth'] >= 20:
                continue
            if (draft.get('provider'), draft.get('account_id'), draft.get('inbox_id')) != (provider, account_id, row['id']):
                continue
            if draft.get('inbox_id') != digest([provider, account_id, original.get('post_id')]):
                continue
            if row.get('campaign') != parent['campaign'] or not isinstance(draft.get('text'), str):
                continue
            try:
                if not now - timedelta(days=30) <= at(row['published_at']) <= now:
                    continue
                provider_effect_id(provider, pid)
            except (ValueError, KeyError, TypeError):
                continue
            roots[pid] = {**parent, 'depth': parent['depth'] + 1,
                          'published_at': row['published_at'], 'reply_inbox_id': row['id']}
            if at(row['published_at']) >= now - timedelta(days=7):
                active_roots.add(parent['conversation_root_id'])
            changed = True
        if not changed:
            break
    return {pid: r for pid, r in roots.items() if r['conversation_root_id'] in active_roots}


def conversation(identity, *, data=None, now=None):
    """Ordered known ancestors, preserving frozen sent copy rather than later edits."""
    data = read() if data is None else data
    row = data['inbox'][identity]
    now = now or datetime.now(UTC)
    roots = conversation_targets(row['provider'], row['account_id'], now, data)
    parent = roots.get(row['context']['parent_post_id'])
    if not parent:
        raise ValueError('Conversation is outside the receipt-backed collection scope')
    history = []
    cursor = parent
    while cursor.get('reply_inbox_id'):
        old = data['inbox'][cursor['reply_inbox_id']]
        history[0:0] = [{'role': 'commenter', **old['draft']['context']},
                        {'role': 'owner', 'post_id': old['post_id'], 'text': old['draft']['text']}]
        cursor = roots[old['draft']['context']['parent_post_id']]
    return {'inbox_id': identity, 'provider': row['provider'], 'account_id': row['account_id'],
            'campaign': row['campaign'], 'root_post_id': parent['conversation_root_id'],
            'depth': parent['depth'], 'history': history, 'incoming': row['context']}


def x_problem(payload):
    """Keep allowlisted error categories/parameter names, never values or prose."""
    known = {'start_time', 'end_time', 'since_id', 'until_id', 'pagination_token',
             'max_results', 'tweet.fields', 'post.fields', 'expansions', 'id'}
    parameters = set()
    rows = [payload] + (payload.get('errors', []) if isinstance(payload.get('errors'), list) else [])
    for row in rows:
        if not isinstance(row, dict):
            continue
        if isinstance(row.get('parameter'), str) and row['parameter'] in known:
            parameters.add(row['parameter'])
        if isinstance(row.get('parameters'), dict):
            parameters.update(set(row['parameters']) & known)
    kinds = {'invalid-request', 'unsupported-authentication', 'not-authorized-for-resource',
             'resource-not-found', 'usage-capped', 'client-forbidden'}
    kind = next((name for name in sorted(kinds) if payload.get('type') in (
        'https://api.x.com/2/problems/' + name,
        'https://api.twitter.com/2/problems/' + name)), 'unclassified')
    return {'problem': kind, 'invalid_parameters': sorted(parameters)}


def fetch_page(client, account, poll, roots, now):
    """One X mentions page or one receipt-backed conversation root page."""
    if client.name == 'x':
        query = {'max_results': '100', 'tweet.fields': 'author_id,referenced_tweets,created_at'}
        if poll.get('since_id'):
            query['since_id'] = post_id(poll['since_id'])
        else:
            start = at(poll['cycle_start']) if poll.get('cycle_start') else now - timedelta(days=7)
            query['start_time'] = stamp(start.astimezone(UTC).replace(microsecond=0))
        if poll.get('next_token'):
            query['pagination_token'] = poll['next_token']
        status, payload = client._bearer(
            'https://api.x.com/2/users/' + post_id(account.account_id) + '/mentions?' + urlencode(query)
        )
        if not 200 <= status < 300 or payload.get('errors'):
            error = ProviderRejected(status, 'X mentions unavailable or partial; cursor retained')
            error.diagnostic = x_problem(payload)
            raise error
        if not isinstance(payload.get('data', []), list):
            raise ValueError('Invalid mentions page')
        meta = payload.get('meta', {})
        updated = dict(poll)
        newest = [v for v in (poll.get('cycle_newest'), meta.get('newest_id')) if v]
        if newest:
            updated['cycle_newest'] = max(newest, key=lambda v: int(post_id(v)))
        if meta.get('next_token'):
            updated['next_token'] = meta['next_token']
            updated['cycle_start'] = query.get('start_time')
        else:
            if updated.get('cycle_newest'):
                updated['since_id'] = updated['cycle_newest']
            for key in ('next_token', 'cycle_start', 'cycle_newest'):
                updated.pop(key, None)
        return payload.get('data', []), updated, bool(meta.get('next_token'))

    ordered = sorted(roots)
    if not ordered:
        return [], dict(poll), False
    scans = poll.get('root_scans', {})
    root = min(ordered, key=lambda k: (scans.get(k, ''), k))
    cursors = poll.get('root_cursors', {})
    updated = dict(poll)
    updated['root_scans'] = {k: v for k, v in scans.items() if k in roots}
    updated['root_cursors'] = {k: v for k, v in cursors.items() if k in roots}

    if client.name == 'linkedin':
        linkedin_target(root)
        raw_start = updated['root_cursors'].get(root, '0')
        try:
            page_start = int(raw_start)
        except (TypeError, ValueError):
            raise ValueError('Invalid LinkedIn comment pagination cursor') from None
        status, payload = client.comments_page(root, start=page_start, count=100)
        if not 200 <= status < 300:
            error = ProviderRejected(status, 'LinkedIn comments unavailable; root/cursor retained')
            error.reply_root = root
            raise error
        elements = payload.get('elements', [])
        if not isinstance(elements, list):
            raise ValueError('Invalid LinkedIn comments page')
        rows = []
        for raw in elements:
            if isinstance(raw, dict):
                rows.append({**raw, '__parent_target': root})
        paging = payload.get('paging') if isinstance(payload.get('paging'), dict) else {}
        next_start = page_start + len(elements)
        total = paging.get('total')
        links = paging.get('links') if isinstance(paging.get('links'), list) else []
        more = (type(total) is int and next_start < total) or any(
            isinstance(link, dict) and link.get('rel') == 'next' for link in links
        )
        updated['root_scans'][root] = stamp(now)
        if more:
            updated['root_cursors'][root] = str(next_start)
        else:
            updated['root_cursors'].pop(root, None)
        updated['root_count'] = len(roots)
        updated['roots_not_yet_scanned'] = len(set(roots) - set(updated['root_scans']))
        updated['root_errors'] = {k: v for k, v in poll.get('root_errors', {}).items()
                                  if k in roots and k != root}
        return rows, updated, more

    query = {'fields': 'id,text,username,replied_to,timestamp', 'limit': '100'}
    if cursors.get(root):
        query['after'] = cursors[root]
    status, _, payload = client._bearer(
        'https://graph.threads.net/v1.0/' + post_id(root) + '/replies', query=query
    )
    if not 200 <= status < 300 or payload.get('error'):
        error = ProviderRejected(status, 'Threads replies unavailable; root/cursor retained')
        error.reply_root = root
        raise error
    if not isinstance(payload.get('data'), list):
        raise ValueError('Invalid Threads replies page')
    paging = payload.get('paging', {})
    more = bool(paging.get('next'))
    cursor = paging.get('cursors', {}).get('after') if more else None
    if more and not cursor:
        raise ValueError('Missing reply pagination cursor')
    updated['root_scans'][root] = stamp(now)
    if more:
        updated['root_cursors'][root] = cursor
    else:
        updated['root_cursors'].pop(root, None)
    updated['root_count'] = len(roots)
    updated['roots_not_yet_scanned'] = len(set(roots) - set(updated['root_scans']))
    updated['root_errors'] = {k: v for k, v in poll.get('root_errors', {}).items()
                              if k in roots and k != root}
    return payload['data'], updated, more


def sync(*, apply=False, now=None, factory=get_provider, budget_seconds=65):
    now = now or datetime.now(UTC)
    if not apply:
        return report(now=now)
    with local_store.locked(path()):
        data = read()
        from ocpf_post.account_profiles import profiles, credential_present
        from ocpf_post.providers import for_account
        from ocpf_post.registry import load_registry
        accounts_by_scope = {(p, str(a)): (p, p, str(a)) for p, a in ACCOUNTS.items()}
        # Registered LinkedIn actors remain visible even when their credential or
        # restricted feed permission is absent. A receipt-backed scope then
        # becomes an explicit unavailable poll instead of silently disappearing.
        for project in load_registry()['projects'].values():
            for binding in project.get('accounts', {}).values():
                if binding.get('provider') != 'linkedin':
                    continue
                account_id = str(binding.get('account_id') or '')
                if account_id:
                    accounts_by_scope.setdefault(
                        ('linkedin', account_id),
                        ('linkedin:' + account_id, 'linkedin', account_id),
                    )
        # Additional profiles include LinkedIn organization/page actors with
        # isolated credentials, policies and cursors.
        for key, row in profiles().items():
            if row['enabled'] and credential_present(row):
                accounts_by_scope[(row['provider'], str(row['account_id']))] = (
                    key, row['provider'], str(row['account_id'])
                )
        accounts = list(accounts_by_scope.values())
        deadline = time.monotonic() + budget_seconds
        for account_index, (poll_key, name, expected) in enumerate(accounts):
            account_deadline = time.monotonic() + max(0, deadline - time.monotonic()) / max(1, len(accounts) - account_index)
            previous = data['polls'].get(poll_key, {})
            # Avoid surprising new-account discovery: require a local publication.
            roots = conversation_targets(name, expected, now, data)
            if not roots:
                data['polls'][poll_key] = {**previous, 'status': 'no_recent_local_publications', 'observed_at': stamp(now)}
                continue
            if name == 'linkedin' and factory is get_provider:
                try:
                    from ocpf_post.providers.linkedin import recorded_read_permission
                    permission = recorded_read_permission(expected, 'comments')
                except (OSError, ValueError, KeyError, TypeError):
                    permission = None
                if isinstance(permission, dict) and permission.get('status') == 'missing':
                    gated = dict(previous)
                    for key in ('retry_at', 'http_status', 'error_type', 'diagnostic'):
                        gated.pop(key, None)
                    gated.update(
                        status='permission_required',
                        observed_at=stamp(now),
                        error_stage='reply_collection',
                        required_scope=permission.get('required_scope'),
                        scope_recorded=permission.get('scope_recorded'),
                        automatic_retry=False,
                        next_action='provider_approval_required_before_reauthorise',
                    )
                    data['polls'][poll_key] = gated
                    local_store.write(path(), data)
                    continue
            if previous.get('retry_at') and now < at(previous['retry_at']):
                continue
            try:
                stage = 'account_verification'
                client = for_account(name, expected, factory=factory); account = client.account()
                if account.account_id != expected:
                    raise ValueError('Authenticated account does not match owner binding')
                pages = 1 if name == 'x' else min(50, max(10, math.ceil(len(roots) / 2)))
                pages = min(pages, len(roots)) if name in {'threads', 'linkedin'} else pages
                pages_completed = 0
                stage = 'reply_collection'
                for page in range(pages):
                    if time.monotonic() >= account_deadline:
                        break
                    rows, updated, more = fetch_page(client, account, previous, roots, now + timedelta(microseconds=page))
                    skipped = 0
                    for raw in rows:
                        try:
                            context = normalise(name, raw)
                        except (ValueError, KeyError, TypeError):
                            skipped += 1; continue
                        parent = roots.get(context['parent_post_id'])
                        own_author = expected if name in {'x', 'linkedin'} else account.username
                        if not parent or context['author'] == own_author:
                            continue
                        identity = digest([name, expected, context['post_id']])
                        old = data['inbox'].get(identity)
                        if old:
                            if old['context'] != context:
                                old['context'] = context
                                old['context_changed_at'] = stamp(now)
                                if old['status'] in {'pending', 'drafted'}:
                                    old['status'] = 'pending'; old.pop('draft', None)
                            old['last_seen_at'] = stamp(now)
                        else:
                            data['inbox'][identity] = {'id': identity, 'provider': name, 'account_id': expected,
                                'campaign': parent['campaign'], 'context': context, 'status': 'pending',
                                'first_seen_at': stamp(now), 'last_seen_at': stamp(now),
                                'conversation_root_id': parent['conversation_root_id'],
                                'conversation_depth': parent['depth']}
                    for key in ('diagnostic', 'error_stage', 'http_status', 'error_type'):
                        updated.pop(key, None)
                    data['polls'][poll_key] = {**updated, 'status': 'partial' if more or skipped or updated.get('roots_not_yet_scanned') or updated.get('root_cursors') or updated.get('root_errors') else 'observed',
                        'skipped_malformed': skipped, 'observed_at': stamp(now), 'retry_at': stamp(now + timedelta(minutes=15)),
                        'scope': 'Replies to recent receipt-backed campaign posts and our verified responses, up to 20 turns; bounded scan, not complete account coverage.'}
                    pages_completed += 1
                    data['polls'][poll_key].update(pages_completed=pages_completed, pages_budget=pages,
                        target_sweep_minutes=30, request_limit_per_cycle=50)
                    previous = data['polls'][poll_key]
                    local_store.write(path(), data)
            except Exception as exc:
                status = exc.status if isinstance(exc, ProviderRejected) else None
                if name in {'threads', 'linkedin'} and status == 404 and getattr(exc, 'reply_root', None):
                    root = exc.reply_root
                    previous = deepcopy_poll(previous)
                    previous.setdefault('root_scans', {})[root] = stamp(now)
                    previous.setdefault('root_errors', {})[root] = {'status': 404, 'observed_at': stamp(now)}
                diagnostic = getattr(exc, 'diagnostic', {})
                if (name == 'x' and status == 400 and previous.get('next_token')
                        and 'pagination_token' in diagnostic.get('invalid_parameters', [])):
                    previous = dict(previous)
                    for key in ('next_token', 'cycle_newest', 'cycle_start'):
                        previous.pop(key, None)
                    previous['pagination_restart_at'] = stamp(now)
                    # Restart from the last completed since_id; inbox IDs deduplicate.
                data['polls'][poll_key] = {**previous, 'status': 'unavailable', 'observed_at': stamp(now),
                    'http_status': status, 'error_type': type(exc).__name__, 'error_stage': stage,
                    'diagnostic': diagnostic, 'retry_at': stamp(now + timedelta(hours=1))}
            local_store.write(path(), data)
        local_store.write(path(), data)
    return report(now=now)


def deepcopy_poll(value):
    return json.loads(json.dumps(value))


def draft(identity, text, *, now=None, expected_context_sha256=None, only_pending=False):
    now = now or datetime.now(UTC)
    with local_store.locked(path()):
        data = read(); row = data['inbox'][identity]
        if row['status'] not in {'pending', 'drafted'}:
            raise ValueError('Reply already resolved or has a possible external effect')
        if only_pending and row['status'] != 'pending':
            raise ValueError('Another draft exists; retained for review')
        if expected_context_sha256 is not None and digest(row['context']) != expected_context_sha256:
            raise ValueError('Context changed before draft lock')
        text = text.strip()
        if not text or len(text) > (280 if row['provider'] == 'x' else 500):
            raise ValueError('Reply text is empty or exceeds conservative provider length')
        value = {'inbox_id': identity, 'provider': row['provider'], 'account_id': row['account_id'],
                 'context': row['context'], 'text': text, 'expires_at': stamp(now + timedelta(hours=24))}
        row['draft'] = value; row['review_sha256'] = digest(value); row['status'] = 'drafted'
        local_store.write(path(), data)
        return {'result': 'drafted', 'draft': value, 'review_sha256': row['review_sha256'], 'published': False}


def send(identity, *, expected_sha256=None, live=False, now=None, factory=get_provider):
    now = now or datetime.now(UTC)
    with local_store.locked(path()):
        data = read(); row = data['inbox'][identity]; value = row.get('draft')
        if row['status'] != 'drafted' or not value:
            raise ValueError('No sendable draft; possible effects cannot be blindly retried')
        if not live:
            return {'result': 'preview', 'draft': value, 'review_sha256': digest(value), 'published': False}
        if (not expected_sha256 or digest(value) != expected_sha256 or row['context'] != value['context'] or now >= at(value['expires_at'])
                or (value['provider'], value['account_id'], value['inbox_id']) != (row['provider'], row['account_id'], identity)):
            raise ValueError('Reply review changed or expired')
        from ocpf_post.providers import for_account
        client = for_account(row['provider'], row['account_id'], factory=factory); account = client.account()
        if account.account_id != row['account_id']:
            raise ValueError('Reply account binding mismatch')
        current = (
            lookup(client, value['context']['post_id'],
                   parent_hint=value['context'].get('parent_post_id'))
            if row['provider'] == 'linkedin'
            else lookup(client, value['context']['post_id'])
        )
        if current != value['context']:
            raise ValueError('Live reply context changed; review again')
        # Require root to remain a locally recorded publication on this account.
        if conversation_targets(row['provider'], account.account_id, now, data).get(current['parent_post_id'], {}).get('depth', 20) >= 20:
            raise ValueError('Parent publication is outside the supported recent receipt scope')
        # Persist before POST, including crash between provider effect and receipt.
        row['status'] = 'sending'; row['attempted_at'] = stamp(now)
        local_store.write(path(), data)
        try:
            if row['provider'] == 'linkedin':
                created = client.reply(
                    value['text'],
                    current['post_id'],
                    root_post_id=row['conversation_root_id'],
                )
            else:
                created = client.reply(value['text'], current['post_id'])
            published_id = provider_effect_id(row['provider'], str(created.get('id') or ''))
        except Exception as exc:
            # Even a rejection is retained for inspection, not automatically retried.
            row['status'] = 'rejected' if isinstance(exc, ProviderRejected) else 'ambiguous_effect'
            row['error_type'] = type(exc).__name__
            local_store.write(path(), data)
            return {'result': row['status'], 'inbox_id': identity, 'automatic_retry': False}
        reply_url = (
            client.post_url(account, row['conversation_root_id'])
            if row['provider'] == 'linkedin'
            else client.post_url(account, published_id)
        )
        row.update(status='published_unverified', post_id=published_id,
                   published_at=stamp(now), url=reply_url, readback_verified=False)
        local_store.write(path(), data)
        try:
            observed = (
                lookup(client, published_id, parent_hint=current['post_id'])
                if row['provider'] == 'linkedin'
                else lookup(client, published_id)
            )
            own_author = account.account_id if row['provider'] in {'x', 'linkedin'} else account.username
            if (observed['text'] == value['text'] and observed['parent_post_id'] == current['post_id']
                    and observed['author'] == own_author and observed['post_id'] == published_id):
                row.update(status='published_verified', readback_verified=True)
                local_store.write(path(), data)
        except Exception:
            pass
        return {'result': row['status'], 'inbox_id': identity, 'post_id': published_id, 'url': row['url'],
                'readback_verified': row['readback_verified'], 'automatic_retry': False}


def dismiss(identity, reason):
    if not reason.strip() or len(reason) > 500:
        raise ValueError('Provide a short dismissal reason')
    with local_store.locked(path()):
        data = read(); row = data['inbox'][identity]
        if row['status'] not in {'pending', 'drafted'}:
            raise ValueError('Cannot erase a possible external effect')
        row.update(status='dismissed', dismissal_reason=reason.strip())
        local_store.write(path(), data)
    return {'result': 'dismissed', 'inbox_id': identity}


def report(*, now=None, include_items=False):
    now = now or datetime.now(UTC)
    data = read(); counts = {}; overdue = 0
    for row in data['inbox'].values():
        counts[row['status']] = counts.get(row['status'], 0) + 1
        if row['status'] in {'pending', 'drafted'} and now - at(row['first_seen_at']) >= timedelta(hours=24):
            overdue += 1
    polls = {k: dict(v) for k, v in data['polls'].items()}
    for poll in polls.values():
        scans = poll.get('root_scans', {})
        if scans:
            ages = [max(0, (now - at(v)).total_seconds()) for v in scans.values()]
            poll['oldest_target_scan_age_seconds'] = max(ages)
            poll['targets_older_than_30m'] = sum(age > 1800 for age in ages)
            if poll['targets_older_than_30m'] and poll.get('status') == 'observed':
                poll['status'] = 'partial'
        if poll.get('observed_at') and now - at(poll['observed_at']) > timedelta(hours=2):
            poll['status'] = 'stale'
    linkedin_scopes = [
        value for key, value in polls.items()
        if str(key).startswith('linkedin:') or str(key) == 'linkedin'
    ]
    if not linkedin_scopes:
        linkedin = {'status': 'not_configured', 'scope_count': 0}
    else:
        statuses = {str(row.get('status') or 'not_observed') for row in linkedin_scopes}
        linkedin = {
            'status': ('attention' if 'unavailable' in statuses else
                       'partial' if statuses & {'partial', 'stale', 'permission_required'} else 'observed'),
            'scope_count': len(linkedin_scopes),
            'scope_statuses': dict((name, sum(str(row.get('status') or 'not_observed') == name
                                             for row in linkedin_scopes))
                                   for name in sorted(statuses)),
            'permission_boundary': 'Member comment reads require restricted r_member_social_feed authority; organization/page comment reads require approved page role plus r_organization_social_feed. Missing recorded authority is incomplete coverage, never zero comments.',
        }
    result = {'schema_version': 1, 'counts': counts, 'waiting_over_24h': overdue, 'polls': polls,
              'linkedin': linkedin,
              'boundary': 'Inbound text is untrusted data. Sync never drafts or sends. Replies require exact reviewed text/context and explicit --live. LinkedIn recurring automatic send is not enabled. No DMs, follows, unsolicited replies or Metricool. Separate reply receipts do not consume campaign slots.'}
    result['nested_pending'] = sum(r['status'] in {'pending', 'drafted'} and r.get('conversation_depth', 0) > 0 for r in data['inbox'].values())
    result['conversation_scope'] = 'Campaign roots at most 30 days old, active within seven days through a verified response or initial publication; max 20 response turns. API coverage remains bounded.'
    if include_items:
        result['items'] = list(data['inbox'].values())
    return result


def add_parsers(sub):
    from ocpf_post.onboarding import _run_cli
    group = sub.add_parser('engagement', help='Collect inbound replies and explicitly respond to reviewed context')
    commands = group.add_subparsers(dest='engagement_command', required=True)
    status = commands.add_parser('status'); status.add_argument('--items', action='store_true')
    status.set_defaults(func=lambda a: _run_cli(lambda: report(include_items=a.items)))
    poll = commands.add_parser('sync'); poll.add_argument('--apply', action='store_true')
    poll.set_defaults(func=lambda a: _run_cli(lambda: sync(apply=a.apply)))
    thread = commands.add_parser('conversation'); thread.add_argument('--id', required=True)
    thread.set_defaults(func=lambda a: _run_cli(lambda: conversation(a.id)))
    from ocpf_post.reply_worker import add_parsers as worker_parsers
    worker_parsers(commands)
    edit = commands.add_parser('draft'); edit.add_argument('--id', required=True); edit.add_argument('--text-file', required=True)
    edit.set_defaults(func=lambda a: _run_cli(lambda: draft(a.id, Path(a.text_file).read_text())))
    publish = commands.add_parser('send'); publish.add_argument('--id', required=True)
    publish.add_argument('--expected-sha256'); publish.add_argument('--live', action='store_true')
    publish.set_defaults(func=lambda a: _run_cli(lambda: send(a.id, expected_sha256=a.expected_sha256, live=a.live)))
    from ocpf_post.reply_reconcile import add_parser as reconcile_parser
    reconcile_parser(commands)
    skip = commands.add_parser('dismiss'); skip.add_argument('--id', required=True); skip.add_argument('--reason', required=True)
    skip.set_defaults(func=lambda a: _run_cli(lambda: dismiss(a.id, a.reason)))
