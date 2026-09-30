#!/usr/bin/env python3
"""Owner-requested brand onboarding; no automatic activation or social sends."""
import argparse
from copy import deepcopy
import getpass
import json
from pathlib import Path
import sys
import tempfile
import time
import warnings

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))

from ocpf_post import account_profiles as accounts, local_store
from ocpf_post.portfolio import load_policy
from ocpf_post.providers import get_provider
from ocpf_post.state import read_json

BRANDS = {
    'threads': {'handle': 'proofandstate', 'project': 'proof-and-state', 'alias': 'threads-brand'},
    'x': {'handle': 'oneclickposty', 'project': 'oneclickpostfactory', 'alias': 'x-brand'},
}


class SetupError(ValueError):
    pass


def hidden(prompt):
    # Never fall back to an echoed token on a noninteractive terminal.
    with warnings.catch_warnings():
        warnings.simplefilter('error', getpass.GetPassWarning)
        return getpass.getpass(prompt).strip()


def readiness(provider):
    brand = BRANDS[provider]
    binding = {'project': brand['project'], 'alias': brand['alias']}
    rows = [r for r in accounts.profiles().values() if r['provider'] == provider
            and (r['label'].casefold() == '@' + brand['handle'] or binding in r['bindings'])]
    if len(rows) > 1 or (rows and (rows[0]['label'].casefold() != '@' + brand['handle'] or rows[0]['bindings'] != [binding])):
        raise SetupError('Conflicting brand identity or bindings; inspect accounts list')
    row = rows[0] if rows else None
    connected = bool(row and accounts.credential_present(row))
    return {'result': 'enabled' if row and row['enabled'] and connected else 'connected_inactive' if connected else 'registered_disconnected' if row else 'not_connected',
            'provider': provider, 'handle': '@' + brand['handle'], 'project': brand['project'], 'alias': brand['alias'],
            'account_id': row['account_id'] if row else None, 'enabled': bool(row and row['enabled']),
            'credential_present': connected, 'policy': row['policy'] if row else None,
            'api_billing': 'not_observed',
            'boundary': 'Local connection state only; no provider request or proof of API credits/publication. Existing founder routes and schedules are unchanged.'}


def activate_brand(provider):
    current = readiness(provider)
    if not current['credential_present']:
        raise SetupError('Connect the brand account before activation')
    account_id = current['account_id']
    # Existing activation validates the actual numeric identity twice and binds
    # apply to the exact immutable profile. No separate activation mechanism.
    preview = accounts.activation(provider, account_id)
    print(json.dumps(preview, indent=2))
    return accounts.activation(provider, account_id, apply=True, expected_sha256=preview['review_sha256'])


def setup(provider, *, token=None, client_id=None, client_secret=None, expires_at=None):
    brand = BRANDS[provider]
    # Staging has mode 700; no owner credential file is read or overwritten here.
    with tempfile.TemporaryDirectory(prefix='post-once-brand-') as temporary:
        stage = Path(temporary)
        if provider == 'threads':
            if expires_at is not None and (type(expires_at) is not int or expires_at <= int(time.time()) + 7 * 86400):
                raise SetupError('Use the actual Meta expiry timestamp, more than seven days in the future')
            if not token or len(token) > 16384:
                raise SetupError('Provide the brand Threads long-lived access token')
            local_store.write(stage / 'token.json', {'access_token': token})
            client = get_provider(provider, credential_dir=stage)
            account = client.account()
        else:
            if not client_id or len(client_id) > 1024:
                raise SetupError('Provide the brand X OAuth client ID')
            if client_secret is not None and (not isinstance(client_secret, str) or not client_secret.strip() or len(client_secret) > 4096):
                raise SetupError('X Client Secret must be a nonempty private value when supplied')
            client = get_provider(provider, credential_dir=stage, client_id=client_id, client_secret=client_secret)
            account = client.authorize()
        if account.provider != provider or (account.username or '').casefold().lstrip('@') != brand['handle']:
            raise SetupError('Authenticated account is not @' + brand['handle'] + '; nothing registered')
        account_id = str(account.account_id)
        accounts.key(provider, account_id)
        old = accounts.profile(provider, account_id)
        if old:
            if (old['label'] != '@' + brand['handle'] or old['bindings'] != [{'project': brand['project'], 'alias': brand['alias']}]):
                raise SetupError('Existing profile has different bindings; inspect accounts list')
            if old['enabled']:
                raise SetupError('Account is already enabled; existing connection left unchanged')
        if provider == 'threads':
            # Refresh validates that this is a refreshable long-lived token and
            # supplies the actual expiry; never invent a sixty-day lifetime.
            if expires_at is None:
                client.refresh(quiet=True)
            else:
                local_store.write(stage / 'token.json', {'access_token': token, 'expires_at': expires_at})
            refreshed = client.account()
            if str(refreshed.account_id) != account_id or (refreshed.username or '').casefold() != brand['handle']:
                raise SetupError('Account changed during token refresh; nothing registered')
        base_policy = deepcopy(load_policy(effective=False)['providers'][provider])
        policy = {k: base_policy[k] for k in ('daily_target', 'window_start', 'window_end', 'development_max', 'commercial_min')}
        policy['minimum_spacing_minutes'] = max(30, int(base_policy.get('minimum_spacing_minutes', 0)))
        value = ({k: old[k] for k in ('schema_version', 'provider', 'account_id', 'label', 'bindings', 'policy')} if old else {
            'schema_version': 1, 'provider': provider, 'account_id': account_id,
            'label': '@' + brand['handle'], 'bindings': [{'project': brand['project'], 'alias': brand['alias']}],
            'policy': policy,
        })
        reviewed = accounts.register(value)
        print(json.dumps({'result': 'identity_checked', 'profile': reviewed['account'], 'input_sha256': reviewed['input_sha256']}, indent=2))
        bundle = {**read_json(stage / 'token.json'), **read_json(stage / 'settings.json')}
        bundle = {k: bundle[k] for k in ('access_token', 'refresh_token', 'expires_at', 'client_id', 'client_secret') if bundle.get(k) is not None}
        credential_file = stage / 'verified-credentials.json'
        local_store.write(credential_file, bundle)
        accounts.register(value, apply=True, expected_sha256=reviewed['input_sha256'])
        result = accounts.connect(provider, account_id, credential_file=credential_file)
        return {**result, 'handle': '@' + brand['handle'], 'project': brand['project'], 'alias': brand['alias'],
                'next_command': f'./ocpf-post accounts enable --provider {provider} --account-id {account_id}',
                'boundary': 'Connected but inactive. Review the independent posting policy before activation. X stays inactive until API publishing access is ready. Existing copy, defaults and schedules are unchanged.'}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--provider', required=True, choices=sorted(BRANDS))
    parser.add_argument('--token-expires-at', type=int, help='Actual Unix expiry from Meta, for a new long-lived token that cannot refresh yet')
    action = parser.add_mutually_exclusive_group()
    action.add_argument('--status', action='store_true', help='Read local brand readiness without credentials prompts or provider calls')
    action.add_argument('--activate', action='store_true', help='Activate an already connected brand; confirms API publishing access and billing are ready')
    args = parser.parse_args(argv)
    try:
        current = readiness(args.provider)
        if args.status:
            result = current
        elif args.activate:
            result = activate_brand(args.provider)
        elif current['credential_present']:
            result = current
        elif args.provider == 'threads':
            token = hidden('Long-lived Threads access token for @proofandstate (hidden): ')
            result = setup('threads', token=token, expires_at=args.token_expires_at)
        else:
            client_id = input('OAuth client ID for the @oneclickposty app: ').strip()
            client_secret = hidden('OAuth Client Secret (hidden; Enter for a public/native app): ') or None
            print('Authorise as @oneclickposty. Callback: http://127.0.0.1:8765/callback')
            print('Remote host: keep a loopback SSH tunnel to port 8765 open from the browser machine.')
            result = setup('x', client_id=client_id, client_secret=client_secret)
        print(json.dumps(result, indent=2))
    except SetupError as exc:
        print('Error: ' + str(exc), file=sys.stderr)
        raise SystemExit(2) from None
    except (KeyboardInterrupt, EOFError):
        print('Connection cancelled; no account was activated.', file=sys.stderr)
        raise SystemExit(2) from None
    except Exception as exc:
        guidance = ('For a new Threads long-lived token, supply --token-expires-at with its actual Meta expiry timestamp, or retry once refreshable.' if args.provider == 'threads'
                    else 'Check X user OAuth configuration, exact callback, app billing/access and the intended brand login. Keep credentials private.')
        print('Account operation did not complete (' + type(exc).__name__ + '). ' + guidance, file=sys.stderr)
        raise SystemExit(2) from None


if __name__ == '__main__':
    main()
