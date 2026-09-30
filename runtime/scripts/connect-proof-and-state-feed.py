#!/usr/bin/env python3
"""Connect the existing Proof & State vault to its verified Threads brand."""
import json
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))

from ocpf_post import account_profiles, vault_sync
from ocpf_post.registry import resolve_account

ACCOUNT = '38453495070963373'
VAULT = 'proof-and-state-gtm'
DOCUMENT = '1qrjfZxzk8QWwhfJXRa6lKVTEUy_KCxbDLukxcOjBwOg'


def connect(*, reader=vault_sync.read_document, now=None):
    binding = resolve_account('proof-and-state', 'threads-brand', expected_provider='threads')
    profile = account_profiles.profile('threads', ACCOUNT)
    if binding['account_id'] != ACCOUNT or not profile or account_profiles.unavailable('threads', ACCOUNT):
        raise ValueError('The verified Proof & State Threads account must be enabled first')
    policy = vault_sync.policies().get(VAULT, {})
    if policy.get('document_id') != DOCUMENT or policy.get('project') != 'proof-and-state':
        raise ValueError('The canonical Proof & State vault must already be registered')
    preview = vault_sync.extend(VAULT, 'threads', 'threads-brand', reader=reader, now=now)
    brand_copy = [c for c in preview['review']['copy'] if 'threads' in c['texts']]
    if not brand_copy:
        raise ValueError('No fresh approved Threads entries found; no authority changed')
    print(json.dumps(preview, indent=2), flush=True)
    applied = vault_sync.extend(VAULT, 'threads', 'threads-brand', apply=True,
                                expected_sha256=preview['review_sha256'], reader=reader, now=now)
    synced = vault_sync.sync(apply=True, vault_id=VAULT, reader=reader, now=now)
    if len(synced['vaults']) != 1 or synced['vaults'][0]['result'] != 'synced':
        raise ValueError('Vault authority is extended but sync did not complete; rerun this helper')
    return {'result': 'feed_connected', 'authority_result': applied['result'],
            'provider': 'threads', 'account_id': ACCOUNT, 'alias': 'threads-brand',
            'vault': VAULT, 'approved_threads_campaigns': sum('threads' in c['texts'] for c in synced['vaults'][0]['reviewed_copy']), 'sync': synced,
            'boundary': 'Approved vault revisions now follow normal refill. Import is not a publication receipt.'}


def main():
    try:
        print(json.dumps(connect(), indent=2), flush=True)
        subprocess.run([str(ROOT / 'ocpf-post'), 'portfolio', 'refill', '--apply',
                        '--horizon-minutes', '75'], cwd=ROOT, check=True)
        schedules = json.loads(subprocess.check_output(
            [str(ROOT / 'ocpf-post'), 'schedule', 'list', '--all', '--json'], cwd=ROOT, text=True))
        print(json.dumps({'brand_schedules': [{k: row.get(k) for k in
              ('schedule_id', 'campaign', 'provider', 'account_id', 'status', 'run_at')}
              for row in schedules if row.get('provider') == 'threads' and row.get('account_id') == ACCOUNT]}, indent=2))
        subprocess.run([str(ROOT / 'ocpf-post'), 'operations', '--all', '--save'],
                       cwd=ROOT, check=True, stdout=subprocess.DEVNULL)
    except Exception as exc:
        print('Feed setup did not complete (' + type(exc).__name__ +
              '). Existing receipts are preserved. Check vault status and preview before retrying.', file=sys.stderr)
        raise SystemExit(2)


if __name__ == '__main__':
    main()
