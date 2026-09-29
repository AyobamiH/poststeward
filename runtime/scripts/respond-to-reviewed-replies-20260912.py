#!/usr/bin/env python3
"""One reviewed batch for four owner-supplied comments; default is read-only."""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from ocpf_post import engagement as e

# Fixed review lifetime; rerunning this script cannot renew authorisation.
REVIEW_END = datetime(2026, 9, 13, 18, 30, tzinfo=timezone.utc)
# Two pasted comments contain a joined word. Only their exact supplied text and
# the explicitly reviewed single-space correction are accepted; no fuzzy matching.
# Live send still compares the complete current provider context byte-for-byte.
REPLIES = [{'id': 'e449e5189cc518a5447f9914e1c735934f67a145d07e7d5f5fdc257a05e91a71',
  'provider': 'threads',
  'account_id': '25914281681582868',
  'context_sha256s': ['03ff516ddf50cb83619d85d38576f8cb9c63b6566262f1bc34f211bea2d2bce2'],
  'text': "I'd look for a named task owner, the exact account and permissions, approval tied to "
          'the proposed action, and a receipt showing what actually happened. The useful test is '
          'whether you can trace one request through those checks, including where it stopped. '
          "Specialist labels alone don't establish safety."},
 {'id': 'dc869edd52acef1a0f2bec8e4f4b3b243c2582498a9b193b8c5929b2d58812a4',
  'provider': 'threads',
  'account_id': '25914281681582868',
  'context_sha256s': ['ce49c1a9f18757a205097056651c3c4072d895b475bc33c8d9e3593c1852f1ce',
                      'bd34b6a6d0e5d3f2879a9473afe88f5cc28dd5892a0f1bbba2dc1cf817c68e4f'],
  'text': "Yes. I'd reuse the same operation ID and payload when the provider supports "
          "idempotency. Otherwise I'd reconcile against provider evidence and leave the outcome "
          "unknown if it can't be established. A timeout alone must never authorise another "
          'write.'},
 {'id': 'edb90b8e91177c953dc1970b2e07e00b492485c25aa61af51fcdee92fc9ddfe7',
  'provider': 'threads',
  'account_id': '25914281681582868',
  'context_sha256s': ['f3bb8a0b4118a47ac78b61a8ec1a0b65836bc139356ac450373f0ffa7f816b93'],
  'text': 'A versioned entry also needs its inputs, expected output and a check someone else can '
          "run. Then reuse has a test: does this revision still do the job? I'd keep the evidence "
          "beside the entry so a polished description doesn't become the proof."},
 {'id': '80dab4b1d7e1222544af0898a3b851f9c006da5e3e37e11fd75cc30ef30c53ee',
  'provider': 'x',
  'account_id': '1480506376447315969',
  'context_sha256s': ['c00995b76fcf04d5693f021482b8a61c528206f9e1192d8b2fc02b82998dc884',
                      'cd0235d7ab8cb697a7a86fd144fd8bde89b7b404a08947e3d7613651fe42658c'],
  'text': 'A useful next check is what happens after the tap: does the call connect, and can '
          "someone finish the form on mobile? I'd track completed enquiries alongside starts, then "
          'fix the biggest observed drop-off.'}]


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
                or e.digest(row['context']) not in item['context_sha256s']):
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
        if e.digest(draft['draft']['context']) not in item['context_sha256s']:
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
