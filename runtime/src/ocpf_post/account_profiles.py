"""Explicit additional identities, private credentials and independent budgets."""
from __future__ import annotations

import copy
import hashlib
import json
import re
import tempfile
from pathlib import Path

from ocpf_post import local_store
from ocpf_post.state import config_dir, provider_token_file, read_json


def path():
    return config_dir() / 'account-profiles.json'


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False).encode()).hexdigest()


def _valid_identity(provider, account_id):
    value = str(account_id)
    if provider in {'x', 'threads'}:
        return bool(re.fullmatch(r'[1-9][0-9]{0,39}', value))
    if provider == 'linkedin':
        return bool(re.fullmatch(
            r'urn:li:(?:person:[A-Za-z0-9_-]{2,120}|organization:[1-9][0-9]{0,29}|organizationBrand:[1-9][0-9]{0,29})',
            value,
        ))
    return False


def key(provider, account_id):
    if not _valid_identity(provider, account_id):
        raise ValueError('Require an X/Threads numeric ID or LinkedIn person/organization URN')
    return provider + ':' + str(account_id)


def directory(provider, account_id):
    key(provider, account_id)
    return config_dir() / 'accounts' / provider / str(account_id)


def profiles():
    data = local_store.read(path())
    rows = data.get('accounts', {})
    if not isinstance(rows, dict):
        raise ValueError('Invalid account profiles')
    for identity, row in rows.items():
        if identity != key(row['provider'], row['account_id']) or type(row.get('enabled')) is not bool:
            raise ValueError('Invalid account profile identity/state')
    return rows


def profile(provider, account_id):
    return profiles().get(f'{provider}:{account_id}')


def credential_present(row):
    value = read_json(directory(row['provider'], row['account_id']) / 'token.json')
    if value.get('access_token'):
        return True
    return bool(
        row['provider'] == 'linkedin'
        and value == {'credential_source': 'provider_default'}
        and read_json(provider_token_file('linkedin')).get('access_token')
    )


def status():
    return {'schema_version': 1, 'accounts': [{**row, 'credential_present': credential_present(row)}
            for row in profiles().values()],
            'boundary': 'Additional X/Threads/LinkedIn identities only. LinkedIn organization/brand URNs are scoped actors backed by member OAuth credentials and role verification. Identity verification is not proof of API credits or publication. Existing provider credentials remain separate.'}


def matches_scope(provider, account_id, scope=None):
    """None is the legacy pool; explicit ID is one additional account."""
    if scope is not None:
        return str(account_id) == scope
    return not profile(provider, account_id)


def unavailable(provider, account_id, *, now=None):
    row = profile(provider, account_id)
    if row and (not row['enabled'] or not credential_present(row)):
        return 'Additional account is inactive or has no private credentials'
    from ocpf_post.scheduler import provider_write_circuit
    circuit = provider_write_circuit(provider, str(account_id), now=now)
    if circuit.get("open") is True:
        return (
            "Provider/account write circuit open until "
            f"{circuit.get('retry_at')} after {circuit.get('failure_class')}"
        )
    return None


def merge_bindings(registry):
    result = copy.deepcopy(registry)
    for row in profiles().values():
        for binding in row['bindings']:
            project = result['projects'].get(binding['project'])
            if not project:
                raise ValueError('Account profile project no longer exists')
            value = {'provider': row['provider'], 'account_id': row['account_id'], 'label': row['label']}
            accounts = project.setdefault('accounts', {})
            old = accounts.get(binding['alias'])
            if old and old != value:
                raise ValueError('Account profile cannot overwrite an existing binding')
            accounts[binding['alias']] = value
    return result


def register(value, *, apply=False, expected_sha256=None):
    from ocpf_post.registry import load_registry
    from ocpf_post.portfolio import validate_policy, DEFAULT_POLICY
    if not isinstance(value, dict) or set(value) != {'schema_version', 'provider', 'account_id', 'label', 'bindings', 'policy'} or value['schema_version'] != 1:
        raise ValueError('Invalid account import fields; credentials and activation are separate')
    identity = key(value['provider'], value['account_id'])
    if not isinstance(value['account_id'], str) or not isinstance(value['label'], str) or not 1 <= len(value['label']) <= 160:
        raise ValueError('Account ID and label must be strings')
    if not isinstance(value['bindings'], list) or not value['bindings']:
        raise ValueError('At least one project/alias binding is required')
    policy = value['policy']
    if not isinstance(policy, dict) or set(policy) != {'daily_target', 'window_start', 'window_end', 'development_max', 'commercial_min', 'minimum_spacing_minutes'}:
        raise ValueError('Provide an explicit independent account policy')
    if any(type(policy[k]) is not int for k in ('daily_target', 'development_max', 'commercial_min', 'minimum_spacing_minutes')) or not 1 <= policy['minimum_spacing_minutes'] <= 1440:
        raise ValueError('Account policy requires integer budgets and positive spacing')
    validate_policy({**DEFAULT_POLICY, 'providers': {value['provider']: policy}})
    registry = load_registry()
    existing = profiles().get(identity)
    for project in registry['projects'].values():
        for account in project.get('accounts', {}).values():
            if account.get('provider') == value['provider'] and account.get('account_id') == value['account_id'] and not existing:
                raise ValueError('Existing account identity cannot be replaced by an additional profile')
    seen = set()
    for binding in value['bindings']:
        if not isinstance(binding, dict) or set(binding) != {'project', 'alias'}:
            raise ValueError('Each binding needs project and alias only')
        pair = (binding['project'], binding['alias'])
        if pair in seen or not all(isinstance(v, str) and re.fullmatch(r'[a-z0-9][a-z0-9-]{0,79}', v) for v in pair):
            raise ValueError('Invalid or duplicate binding')
        seen.add(pair)
        project = registry['projects'].get(binding['project'])
        if not project:
            raise ValueError('Register the project before adding an account')
        if binding['alias'] in project.get('accounts', {}) and not existing:
            raise ValueError('Account alias already exists')
    row = {**copy.deepcopy(value), 'enabled': False}
    if existing and {k: existing[k] for k in value} != value:
        raise ValueError('Account imports are add-only; existing identity, bindings and policy are immutable')
    review = digest(value)
    if apply:
        if expected_sha256 != review:
            raise ValueError('Account import review hash changed')
        with local_store.locked(path()):
            current = load_registry()
            rows = profiles()
            if identity not in rows:
                for binding in value["bindings"]:
                    if binding["alias"] in current["projects"][binding["project"]].get("accounts", {}):
                        raise ValueError("Concurrent account alias conflict")
                for project in current["projects"].values():
                    if any(r.get("provider") == value["provider"] and r.get("account_id") == value["account_id"] for r in project.get("accounts", {}).values()):
                        raise ValueError("Concurrent account identity conflict")
            if identity in rows and {k: rows[identity][k] for k in value} != value:
                raise ValueError('Concurrent account import conflict')
            rows.setdefault(identity, row)
            local_store.write(path(), {'schema_version': 1, 'accounts': rows})
    return {'result': 'registered' if apply else 'preview', 'input_sha256': review, 'account': existing or row,
            'boundary': 'Add-only binding for X/Threads identities or LinkedIn person/organization actors; no credentials, default changes, campaign rerouting or publication. New accounts are inactive.'}


def require(provider, account_id):
    row = profiles().get(key(provider, account_id))
    if not row:
        raise ValueError('Register this additional account first')
    return row


def connect(provider, account_id, *, credential_file=None, oauth=False, client_id=None,
            reuse_default=False):
    from ocpf_post.providers import get_provider
    row = require(provider, account_id)
    # Hold the profile writer lock while connecting: activation cannot race the replacement.
    with local_store.locked(path()):
        if profiles()[key(provider, account_id)]['enabled']:
            raise ValueError('Disable this account before replacing credentials')
        target = directory(provider, account_id)
        target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        with tempfile.TemporaryDirectory(dir=target.parent) as temp:
            stage = Path(temp)
            if oauth:
                if provider != 'x' or not client_id or reuse_default or credential_file:
                    raise ValueError('X OAuth requires its client ID and no other credential mode')
                client = get_provider(provider, credential_dir=stage, client_id=client_id)
                account = client.authorize()
            elif reuse_default:
                if credential_file:
                    raise ValueError('Choose one credential source')
                if provider != 'linkedin' or not re.fullmatch(
                    r'urn:li:(?:organization|organizationBrand):[1-9][0-9]{0,29}',
                    str(account_id),
                ):
                    raise ValueError('Default credential reuse is only for LinkedIn organization/page actors')
                if not read_json(provider_token_file('linkedin')).get('access_token'):
                    raise ValueError('Default LinkedIn member credential is unavailable')
                local_store.write(stage / 'token.json', {'credential_source': 'provider_default'})
                client = get_provider(provider, credential_dir=stage, actor_urn=row['account_id'])
                account = client.account()
            else:
                if not credential_file:
                    raise ValueError('Credential file is required')
                source = Path(credential_file).expanduser()
                if source.stat().st_mode & 0o077 or source.stat().st_size > 65536:
                    raise ValueError('Credential JSON must be private (chmod 600) and smaller than 64 KB')
                value = json.loads(source.read_text())
                allowed = {'access_token', 'refresh_token', 'expires_at', 'client_id', 'client_secret'}
                if provider == 'linkedin':
                    allowed |= {'person_urn', 'scope', 'version'}
                if not isinstance(value, dict) or set(value) - allowed or not isinstance(value.get('access_token'), str) or not value['access_token'].strip():
                    raise ValueError('Invalid credential file')
                if any(not isinstance(value[k], str) or not value[k].strip() for k in value if k != 'expires_at'):
                    raise ValueError('Credential fields must be nonempty strings')
                if 'expires_at' in value and type(value['expires_at']) is not int:
                    raise ValueError('expires_at must be an epoch integer')
                local_store.write(stage / 'token.json', value)
                local_store.write(stage / 'settings.json', {k: value[k] for k in ('client_id', 'client_secret') if k in value})
                provider_kwargs = {'credential_dir': stage}
                if provider == 'linkedin':
                    provider_kwargs['actor_urn'] = row['account_id']
                client = get_provider(provider, **provider_kwargs)
                account = client.account()
            if account.provider != provider or str(account.account_id) != row['account_id']:
                raise ValueError('Authenticated account does not match the registered identity; credentials discarded')
            # One credential bundle is used by scoped providers, including client settings.
            bundle = read_json(stage / 'token.json')
            bundle.update({k: v for k, v in read_json(stage / 'settings.json').items() if v is not None})
            local_store.write(target / 'token.json', bundle)
    return {'result': 'identity_verified', 'provider': provider, 'account_id': account_id, 'enabled': False,
            'credential_source': 'provider_default' if reuse_default else 'scoped',
            'boundary': 'Connection checked without posting. LinkedIn Page reuse stores only a non-secret reference to the existing member credential; actor enablement, budgets and cursors stay separate. API write/read authority still requires provider evidence and an ordinary publication receipt.'}


def activation(provider, account_id, *, apply=False, expected_sha256=None, disable=False):
    from ocpf_post.providers import get_provider
    with local_store.locked(path()):
        rows = profiles(); row = require(provider, account_id)
        if disable:
            row['enabled'] = False
            rows[key(provider, account_id)] = row
            local_store.write(path(), {'schema_version': 1, 'accounts': rows})
            return {'result': 'disabled', 'provider': provider, 'account_id': account_id}
        provider_kwargs = {'credential_dir': directory(provider, account_id)}
        if provider == 'linkedin':
            provider_kwargs['actor_urn'] = account_id
        client = get_provider(provider, **provider_kwargs)
        account = client.account()
        if str(account.account_id) != account_id:
            raise ValueError('Account identity mismatch')
        review = digest({k: v for k, v in row.items() if k != 'enabled'})
        if apply:
            if review != expected_sha256:
                raise ValueError('Activation review hash changed')
            row['enabled'] = True; rows[key(provider, account_id)] = row
            local_store.write(path(), {'schema_version': 1, 'accounts': rows})
        return {'result': 'enabled' if apply else 'preview', 'review_sha256': review, 'account': row,
                'boundary': 'Apply authorises this account for future publishing and confirms operator readiness including API billing. No campaign is copied or post created.'}
