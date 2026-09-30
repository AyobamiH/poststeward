#!/usr/bin/env python3
"""One reviewed batch for five owner-supplied comments; default is read-only."""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from ocpf_post import engagement as e

# Fixed review lifetime; rerunning this script cannot renew authorisation.
REVIEW_END = datetime(2026, 9, 11, 20, 23, tzinfo=timezone.utc)
REPLIES = [{'id': '057af1617c80ff0b7a7deb0ace23eeabc9a5ad2849a452bf1b31481cf38bd65e',
  'provider': 'threads',
  'account_id': '25914281681582868',
  'context_sha256': 'b0cf97584694c4b7175c939604cc5feeb03a521eaf5bd9a701c75aece3346c0e',
  'text': "Agreed. I'd bind that check to the account, required scope and expiry, then recheck "
          'before the consequential step. If any of those change, the previous result no longer '
          'authorises the action.'},
 {'id': 'c19a46de330ea5e1a730a8a2abb63c44f75000c137dec01b5f497db653020fc3',
  'provider': 'threads',
  'account_id': '25914281681582868',
  'context_sha256': '6798d8ecd671a74946ee38c1bc4fef0fecba3e9b92ee64a8e8822f2118c93edd',
  'text': 'That gives the catalogue a useful test: can someone complete a worthwhile task and know '
          "when to come back? I'd prioritise that repeat use before adding more entries."},
 {'id': '9905532e06477d2a7742c6d4a9df086c23150ef34b8f1a82a991a129ec38271c',
  'provider': 'threads',
  'account_id': '25914281681582868',
  'context_sha256': 'bb089fc3cc727fe445b7577fe222b3743195abf8e661b9604d6779c71146bebd',
  'text': 'The dependency graph is the missing piece. A changed requirement should flag the '
          'decisions and checks that relied on it, with an owner for each. Restoring the '
          "conversation alone can't tell you what is still safe to reuse."},
 {'id': '1f0e0900b19b5190548be373b55780dcfa8c37de75b252a26df90476f2581b09',
  'provider': 'threads',
  'account_id': '25914281681582868',
  'context_sha256': '79bdb6704bf73f10c5a040883eca876b45d43ff1d7b452881fe72d1a82399fc4',
  'text': 'Yes, especially when each check has a clear failure condition and runs at the point it '
          'matters. Otherwise a reusable checklist can still leave the team repeating the same '
          'unchecked assumptions.'},
 {'id': 'f04c6258e4206b5fb9a5b5c12d86df9efc5bf7031bd9bbee5b8763a4b2a3094d',
  'provider': 'threads',
  'account_id': '25914281681582868',
  'context_sha256': 'edc9c224a735783ad5b0e38d2e83317bc6da35233366e7346ae2f3d596689d75',
  'text': "Exactly. I'd trace entry point to handler, service and storage, then write a regression "
          'test through that path. A test around the guessed function can pass while the real '
          'request still fails.'}]


def execute(*, live=False, now=None):
    review_clock = lambda: now or datetime.now(timezone.utc)
    if review_clock() >= REVIEW_END:
        raise ValueError('This batch review expired; review the comments again')
    data = e.read()
    plan = []
    # Check the entire batch before the first draft or provider side effect.
    for item in REPLIES:
        row = data['inbox'].get(item['id'])
        if (not row or (row['provider'], row['account_id']) != (item['provider'], item['account_id'])
                or e.digest(row['context']) != item['context_sha256']):
            raise ValueError('A reviewed comment is absent or changed; batch stopped')
        if row['status'] == 'published_verified' and row.get('draft', {}).get('text') == item['text']:
            plan.append({'id': item['id'], 'result': 'already_verified', 'url': row.get('url')})
            continue
        if row['status'] not in {'pending', 'drafted'}:
            raise ValueError('A reply has an unresolved or terminal outcome; inspect it before continuing')
        if row['status'] == 'drafted' and row.get('draft', {}).get('text') != item['text']:
            raise ValueError('A different draft exists; preserve it for review')
        plan.append({'id': item['id'], 'result': 'preview', 'text': item['text']})
    if not live:
        return {'live': False, 'replies': plan}
    for item, result in zip(REPLIES, plan):
        if result['result'] == 'already_verified':
            continue
        if review_clock() >= REVIEW_END:
            raise ValueError('This batch review expired; remaining responses were not sent')
        draft = e.draft(item['id'], item['text'], now=review_clock())
        # A concurrent sync between the preflight and draft cannot change the target.
        if e.digest(draft['draft']['context']) != item['context_sha256']:
            raise ValueError('Comment changed while drafting; no response sent for this item')
        if review_clock() >= REVIEW_END:
            raise ValueError('This batch review expired before sending')
        result.update(e.send(item['id'], expected_sha256=draft['review_sha256'], live=True))
        result.pop('text', None)
        if result['result'] != 'published_verified':
            break  # Durable pending-effect guards prohibit blind retries.
    return {'live': True, 'replies': plan}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--live', action='store_true', help='Send only this exact reviewed batch')
    args = parser.parse_args()
    try:
        result = execute(live=args.live)
    except (ValueError, KeyError, OSError) as exc:
        raise SystemExit(str(exc))
    print(json.dumps(result, indent=2))
    if args.live and any(r['result'] not in {'published_verified', 'already_verified'} for r in result['replies']):
        raise SystemExit(2)


if __name__ == '__main__':
    main()
