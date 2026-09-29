#!/usr/bin/env python3
"""Configure the authorised Threads response loop; X retains explicit review."""
import argparse
import getpass
import json
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from ocpf_post import local_store, reply_worker, reply_model
from ocpf_post.state import config_dir
from ocpf_post.capacity_experiment import ACCOUNTS
from ocpf_post.account_profiles import profiles, credential_present


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--apply', action='store_true')
    parser.add_argument('--disable', action='store_true')
    parser.add_argument('--x-automatic', action='store_true',
                        help='Enable automatic X replies after recording explicit owner approval')
    parser.add_argument('--x-approval-reference',
                        help='Reference to the owner approval for automatic X replies')
    args = parser.parse_args()
    if args.x_automatic and not args.x_approval_reference:
        parser.error('--x-automatic requires --x-approval-reference')
    if args.x_approval_reference and not args.x_automatic:
        parser.error('--x-approval-reference requires --x-automatic')
    settings = reply_worker.policy()
    if args.disable:
        settings = {**settings, 'enabled': False}
    elif not settings['enabled'] and settings['accounts']:
        settings = {**settings, 'enabled': True}
    elif not settings['enabled']:
        settings = {**settings, 'enabled': True, 'accounts': {
            p + ':' + account: {'mode': 'automatic' if p == 'threads' else 'review', 'x_approval_reference': ''}
            for p, account in ACCOUNTS.items()}}
        for account, row in profiles().items():
            if row['enabled'] and credential_present(row):
                settings['accounts'].setdefault(account, {'mode': 'automatic' if row['provider'] == 'threads' else 'review', 'x_approval_reference': ''})
    if args.x_automatic and not args.disable:
        x_identity = 'x:' + ACCOUNTS['x']
        settings = {**settings, 'enabled': True, 'accounts': dict(settings['accounts'])}
        settings['accounts'][x_identity] = {
            'mode': 'automatic',
            'x_approval_reference': args.x_approval_reference.strip(),
        }

    if args.apply:
        if not args.disable and not reply_model.key():
            secret = getpass.getpass('OpenAI API key for reply drafting/review (hidden; Enter skips): ').strip()
            if secret:
                if len(secret) < 20 or any(c.isspace() for c in secret):
                    raise ValueError('Invalid key format')
                with local_store.locked(config_dir() / 'reply-model-key.json'):
                    local_store.write(config_dir() / 'reply-model-key.json', {'schema_version': 1, 'api_key': secret})
        with local_store.locked(config_dir() / 'reply-worker-policy.json'):
            local_store.write(config_dir() / 'reply-worker-policy.json', settings)
    print(json.dumps({'result': 'configured' if args.apply else 'preview', 'policy': settings,
        'model_credential_present': bool(reply_model.key()), 'credential_validity': 'not_observed',
        'boundary': 'Only configured accounts are processed. Public conversation text goes to the OpenAI API for drafting and independent review. Max 40 calls/day by default; no tools or credential data are sent. X remains explicit review unless the owner records explicit approval for automatic replies. No social post created by setup.'}, indent=2))


if __name__ == '__main__':
    main()
