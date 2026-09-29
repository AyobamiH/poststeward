"""Recurring account-scoped responses with independent review and durable outcomes."""
from datetime import datetime, timedelta, timezone
import re
import time
from ocpf_post import engagement as e, local_store, reply_model
from ocpf_post.state import state_dir, config_dir

UTC = timezone.utc
DEFAULT = {'schema_version': 1, 'enabled': False, 'accounts': {}, 'daily_model_calls': 40,
           'max_items_per_cycle': 4, 'daily_replies_per_account': 10, 'minimum_spacing_seconds': 180}


def policy():
    value = local_store.read(config_dir() / 'reply-worker-policy.json') or DEFAULT.copy()
    if set(value) != set(DEFAULT) or type(value['enabled']) is not bool or not isinstance(value['accounts'], dict):
        raise ValueError('Invalid reply worker policy')
    for name, low, high in [('daily_model_calls', 2, 200), ('max_items_per_cycle', 1, 10),
                            ('daily_replies_per_account', 1, 50), ('minimum_spacing_seconds', 60, 3600)]:
        if type(value[name]) is not int or not low <= value[name] <= high:
            raise ValueError('Invalid reply worker budget')
    for account, setting in value['accounts'].items():
        if not re.fullmatch(r'(x|threads):[0-9]{1,30}', account) or not isinstance(setting, dict):
            raise ValueError('Invalid reply account')
        if set(setting) != {'mode', 'x_approval_reference'} or setting['mode'] not in {'automatic', 'review'}:
            raise ValueError('Invalid reply account mode')
        ref = setting['x_approval_reference']
        if not isinstance(ref, str) or len(ref) > 500:
            raise ValueError('Invalid X approval reference')
        if account.startswith('x:') and setting['mode'] == 'automatic' and not ref.strip():
            raise ValueError('Recorded explicit owner approval reference required for automatic X replies')
    return value


def path():
    return state_dir() / 'reply-worker.json'


def state():
    data = local_store.read(path()) or {'schema_version': 1, 'items': {}, 'opt_outs': {}, 'usage': {}}
    if any(not isinstance(data.get(k), dict) for k in ('items', 'opt_outs', 'usage')):
        raise ValueError('Invalid reply worker state')
    return data


def report():
    data = state(); settings = policy()
    inbox = e.read()['inbox']
    items = {identity: dict(row) for identity, row in data['items'].items()}
    for identity, row in items.items():
        effect = inbox.get(identity, {})
        if effect.get('status') == 'published_verified' and effect.get('readback_verified') is True:
            row.update(status='published_verified', post_id=effect.get('post_id'), url=effect.get('url'))
    return {'schema_version': 1, 'enabled': settings['enabled'], 'accounts': settings['accounts'],
            'model_credential_present': bool(reply_model.key()), 'model': reply_model.MODEL,
            'last_cycle': data.get('last_cycle'), 'items': items,
            'opt_out_count': len(data['opt_outs']), 'usage': data['usage'],
            'boundary': 'One reply per inbound interaction. Automatic X sending requires a recorded explicit owner approval reference. Unknown effects never retry. Model decisions are not evidence of product capabilities.'}


def opt_out_text(text):
    return bool(re.search(r"\b(stop replying|do not reply|don't reply|no more replies|opt[ -]?out|unsubscribe)\b", text, re.I))


def run(*, apply=False, now=None, model=reply_model.evaluate, sender=None, budget_seconds=210):
    if not apply:
        return report()
    clock = lambda: now or datetime.now(UTC)
    sender = sender or e.send
    settings = policy()
    with local_store.locked(path()):
        data = state(); began = time.monotonic(); processed = 0
        if not settings['enabled']:
            data['last_cycle'] = {'observed_at': e.stamp(clock()), 'status': 'disabled'}
            local_store.write(path(), data); return report()
        inbox = e.read()['inbox']
        # Opt-outs are processed before ANY sends, including later comments by an author.
        for row in inbox.values():
            who = e.digest([row['provider'], row['account_id'], row['context']['author']])
            if opt_out_text(row['context']['text']):
                data['opt_outs'].setdefault(who, {'inbox_id': row['id'], 'observed_at': e.stamp(clock())})
        local_store.write(path(), data)
        for row in sorted(inbox.values(), key=lambda r: (r['first_seen_at'], r['id'])):
            if processed >= settings['max_items_per_cycle'] or time.monotonic() - began >= budget_seconds:
                break
            identity = row['id']; account = row['provider'] + ':' + row['account_id']
            if row['status'] not in {'pending', 'drafted'} or account not in settings['accounts']:
                continue
            who = e.digest([row['provider'], row['account_id'], row['context']['author']])
            if who in data['opt_outs']:
                data['items'][identity] = {'status': 'opted_out', 'context_sha256': e.digest(row['context'])}
                continue
            old = data['items'].get(identity, {})
            context_hash = e.digest(row['context'])
            if old.get('context_sha256') == context_hash:
                if old.get('status') in {'review_required', 'no_response_needed', 'opted_out', 'published_verified', 'published_unverified', 'ambiguous_effect', 'rejected', 'sending'}:
                    continue
                if old.get('retry_at') and clock() < e.at(old['retry_at']):
                    continue
            if row['status'] == 'drafted' and old.get('review_sha256') != row.get('review_sha256'):
                continue  # Never replace a human or another workflow's draft.
            if row.get('draft') and clock() >= e.at(row['draft']['expires_at']):
                data['items'][identity] = {'status': 'review_required', 'reason': 'draft_expired', 'context_sha256': context_hash}
                continue
            record = {'context_sha256': context_hash, 'observed_at': e.stamp(clock())}
            data['items'][identity] = record; processed += 1
            try:
                # A failed preflight has no provider effect and may retain a reviewed
                # draft. Reuse only its exact persisted review, never recompose over it.
                reviewed_draft = (row['status'] == 'drafted'
                    and old.get('context_sha256') == context_hash
                    and old.get('review_sha256') == row.get('review_sha256')
                    and old.get('review_sha256') == e.digest(row['draft'])
                    and old.get('reviewed_at') is not None)
                if not reviewed_draft:
                    if not reply_model.key() and model is reply_model.evaluate:
                        raise ValueError('model_credential_missing')
                    context = e.conversation(identity, now=clock())
                    if context['depth'] >= 20:
                        raise ValueError('conversation_depth_review_required')
                    limit = 280 if row['provider'] == 'x' else 500
                    payload = {'conversation': context, 'max_chars': limit}
                    decision = None
                    for reviewing in (False, True):
                        day = clock().date().isoformat()
                        used = data['usage'].get(day, 0)
                        if type(used) is not int or used < 0:
                            raise ValueError('invalid_model_usage')
                        if used >= settings['daily_model_calls']:
                            raise ValueError('daily_model_budget_reached')
                        data['usage'][day] = used + 1  # Charge before call; crashes cannot refill the budget.
                        local_store.write(path(), data)
                        decision = model(payload, review=reviewing)
                        reply_model.validate(decision)
                        if decision['action'] != 'reply':
                            break
                        if not reply_model.text_allowed(decision['text'], limit):
                            decision = {'action': 'review', 'text': '', 'reason': 'output_constraints'}; break
                        if reviewing and decision['text'] != payload['proposed_text']:
                            raise ValueError('review_changed_proposed_text')
                        payload['proposed_text'] = decision['text']
                    record.update(reason=decision['reason'])
                    if decision['action'] != 'reply':
                        record['status'] = 'no_response_needed' if decision['action'] == 'skip' else 'review_required'
                        continue
                    current = e.read()['inbox'][identity]
                    if current['status'] != 'pending' or e.digest(current['context']) != context_hash:
                        raise ValueError('context_or_draft_changed_during_review')
                    drafted = e.draft(identity, decision['text'], now=clock(), expected_context_sha256=context_hash, only_pending=True)
                    if e.digest(drafted['draft']['context']) != context_hash:
                        raise ValueError('context_changed_during_draft')
                    record.update(status='ready', review_sha256=drafted['review_sha256'], text=decision['text'],
                                  model=reply_model.MODEL, reviewed_at=e.stamp(clock()))
                else:
                    record.update(old)
                    record['status'] = 'ready'
                    record.pop('retry_at', None)
                if settings['accounts'][account]['mode'] != 'automatic':
                    record['status'] = 'review_required'; record['reason'] = 'account_requires_explicit_send'
                    continue
                current_settings = policy()
                if not current_settings['enabled'] or current_settings['accounts'].get(account, {}).get('mode') != 'automatic':
                    record['status'] = 'review_required'; record['reason'] = 'policy_changed'; continue
                latest = e.read()
                if any(r['provider'] + ':' + r['account_id'] == account and r['context']['author'] == row['context']['author']
                       and opt_out_text(r['context']['text']) for r in latest['inbox'].values()):
                    record['status'] = 'opted_out'; continue
                attempts = [r for r in latest['inbox'].values() if r['provider'] + ':' + r['account_id'] == account and r.get('attempted_at')]
                today = [r for r in attempts if e.at(r['attempted_at']).date() == clock().date()]
                if len(today) >= settings['daily_replies_per_account']:
                    record['retry_at'] = e.stamp(clock() + timedelta(hours=1)); continue
                if attempts and (clock() - max(e.at(r['attempted_at']) for r in attempts)).total_seconds() < settings['minimum_spacing_seconds']:
                    record['retry_at'] = e.stamp(clock() + timedelta(minutes=3)); continue
                # Saved reviewed hash and engagement's durable sending marker serialize all callers.
                local_store.write(path(), data)
                result = sender(identity, expected_sha256=record['review_sha256'], live=True, now=clock())
                record.update(status=result['result'], post_id=result.get('post_id'), url=result.get('url'))
            except Exception as exc:
                # Re-read effect state before classifying; never manufacture retry authority.
                status = e.read()['inbox'][identity]['status']
                if status not in {'pending', 'drafted'}:
                    record['status'] = status
                else:
                    record.update(status='attention', reason=(str(exc) if str(exc) in {'model_credential_missing', 'daily_model_budget_reached', 'model_request_unavailable_or_invalid', 'conversation_depth_review_required'} else 'processing_unavailable'), error_type=type(exc).__name__,
                                  retry_at=e.stamp(clock() + timedelta(hours=1)))
            finally:
                local_store.write(path(), data)
        data['last_cycle'] = {'observed_at': e.stamp(clock()), 'status': 'attention' if any(r.get('status') == 'attention' for r in data['items'].values()) else 'completed', 'processed': processed}
        local_store.write(path(), data)
    return report()


def add_parsers(commands):
    from ocpf_post.onboarding import _run_cli
    status = commands.add_parser('worker-status')
    status.set_defaults(func=lambda a: _run_cli(report))
    run_parser = commands.add_parser('process')
    run_parser.add_argument('--apply', action='store_true')
    run_parser.set_defaults(func=lambda a: _run_cli(lambda: run(apply=a.apply)))
