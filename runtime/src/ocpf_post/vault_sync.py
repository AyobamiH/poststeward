"""Approved Google Doc entries to immutable campaigns and guarded schedules."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import json
import re

from ocpf_post import onboarding as ob
from ocpf_post.campaigns import builtin_manifest, destination_binding
from ocpf_post.google_vault import read_document, write_entry_statuses, credential_capabilities
from ocpf_post.scheduler import RunnerLock, cancel_schedule, schedule_records
from ocpf_post.state import config_dir, state_dir, read_json, write_private_json, iter_receipts, TERMINAL_EFFECT_STATUSES

BEGIN = 'POST-ONCE APPROVED ENTRIES BEGIN'
END = 'POST-ONCE APPROVED ENTRIES END'


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False).encode()).hexdigest()


def policies():
    return read_json(config_dir() / 'vaults.json').get('vaults', {})


def observations():
    return read_json(state_dir() / 'vault-observations.json').get('vaults', {})


def register(path, *, apply=False, expected_sha256=None, enable=False):
    value, sha = ob.read_input(path, apply=apply, expected_sha256=expected_sha256)
    fields = {'schema_version', 'id', 'project', 'document_id', 'destinations', 'max_age_minutes'}
    ob._object(value, fields, fields, 'vault policy')
    ob._slug(value['id'], 'vault ID'); ob._slug(value['project'], 'project')
    if type(value['schema_version']) is not int or value['schema_version'] != 1:
        raise ValueError('Unsupported vault schema')
    if not re.fullmatch(r'[A-Za-z0-9_-]{10,150}', value['document_id']):
        raise ValueError('Invalid document ID')
    if type(value['max_age_minutes']) is not int or not 15 <= value['max_age_minutes'] <= 360:
        raise ValueError('Vault freshness must be between 15 and 360 minutes')
    from ocpf_post.registry import resolve_account
    if not isinstance(value['destinations'], dict) or not value['destinations']:
        raise ValueError('Vault requires destination bindings')
    for provider, alias in value['destinations'].items():
        if provider not in ob.PROVIDERS:
            raise ValueError('Unsupported vault provider')
        resolve_account(value['project'], alias, expected_provider=provider)
    value = {**value, 'enabled': enable}
    def check():
        current = policies()
        if value['id'] in current and current[value['id']] != value:
            raise ValueError('Vault policy already exists with different authority')
        if any(p['document_id'] == value['document_id'] and key != value['id'] for key, p in current.items()):
            raise ValueError('Document is already registered')
        return current
    current = check()
    if apply:
        with ob._import_lock('vault'):
            current = check(); current[value['id']] = value
            write_private_json(config_dir() / 'vaults.json', {'schema_version': 1, 'vaults': current})
    return {'result': 'registered' if apply else 'preview', 'input_sha256': sha, 'policy': value,
            'boundary': 'Enable trusts approved entries authored by editors of this designated document. No publication or Google credential transfer.'}


def extend(vault_id, provider, alias, *, apply=False, expected_sha256=None,
           reader=read_document, now=None):
    """Add one provider destination, binding review to existing authority and copy."""
    from ocpf_post.registry import resolve_account
    from ocpf_post.account_profiles import unavailable
    ob._slug(vault_id, 'vault ID'); ob._slug(alias, 'account alias')
    if provider not in ob.PROVIDERS:
        raise ValueError('Unsupported vault provider')
    now = now or datetime.now(timezone.utc)

    def review():
        current = policies().get(vault_id)
        if not current or not current['enabled']:
            raise ValueError('An enabled existing vault is required')
        existing = current['destinations'].get(provider)
        if existing is not None and existing != alias:
            raise ValueError('Existing vault destinations cannot be replaced')
        proposed = {**current, 'destinations': {**current['destinations'], provider: alias}}
        accounts = {p: resolve_account(current['project'], a, expected_provider=p)
                    for p, a in proposed['destinations'].items()}
        if unavailable(provider, accounts[provider]['account_id']):
            raise ValueError('Additional account must be connected and enabled first')
        document = reader(current['document_id'])
        if document['document_id'] != current['document_id']:
            raise ValueError('Document identity changed')
        packages, _, skipped = prepare(proposed, document, now)
        reviewed = {'before': current, 'after': proposed, 'accounts': accounts,
                    'document_version': document['version'],
                    'copy': [{'campaign': m['campaign'], 'texts': t} for m, t in packages],
                    'skipped': skipped}
        return current, proposed, reviewed

    if apply:
        with ob._import_lock('vault'):
            current, proposed, reviewed = review()
            if not expected_sha256 or digest(reviewed) != expected_sha256:
                raise ValueError('Vault extension review changed; preview again')
            if current != proposed:
                configured = policies(); configured[vault_id] = proposed
                write_private_json(config_dir() / 'vaults.json', {'schema_version': 1, 'vaults': configured})
    else:
        current, proposed, reviewed = review()
    return {'result': ('already_present' if current == proposed else 'extended') if apply else 'preview',
            'review_sha256': digest(reviewed), 'review': reviewed,
            'boundary': 'Adds future publication authority for this provider only. Existing destinations, campaigns and receipts are retained. Run vault sync to import approved copy.'}


def _section_entries(text, begin, end, *, optional=False):
    lines = text.replace('\r\n', '\n').splitlines()
    starts = [i for i, s in enumerate(lines) if s.strip() == begin]
    ends = [i for i, s in enumerate(lines) if s.strip() == end]
    if optional and not starts and not ends:
        return []
    if len(starts) != 1 or len(ends) != 1 or starts[0] >= ends[0]:
        raise ValueError('Vault requires exactly one complete approved entries section across all tabs')
    raw = '\n'.join(lines[starts[0]+1:ends[0]]).encode()
    if len(raw) > ob.MAX_INPUT_BYTES:
        raise ValueError('Approved section exceeds size limit')
    value = ob._decode(raw)
    ob._object(value, {'schema_version', 'entries'}, {'schema_version', 'entries'}, 'vault section')
    if type(value['schema_version']) is not int or value['schema_version'] != 1 or not isinstance(value['entries'], list) or len(value['entries']) > 100:
        raise ValueError('Invalid vault section; maximum 100 entries')
    return value['entries']


def parse_entries(text, *, providers=()):
    entries = _section_entries(text, BEGIN, END)
    for provider in sorted(set(providers)):
        if provider not in ob.PROVIDERS:
            raise ValueError('Unsupported vault provider section')
        prefix = 'POST-ONCE ' + provider.upper() + ' APPROVED ENTRIES '
        extra = _section_entries(text, prefix + 'BEGIN', prefix + 'END', optional=True)
        if any(not isinstance(e, dict) or e.get('provider') != provider for e in extra):
            raise ValueError('Provider section contains another destination')
        entries.extend(extra)
    if len(entries) > 100:
        raise ValueError('Maximum 100 entries across authorised sections')
    return entries


def prepare(policy, document, now):
    prepared, active, seen, skipped = [], {}, set(), []
    for entry in parse_entries(document['text'], providers=policy['destinations']):
        fields = {'campaign', 'provider', 'title', 'text', 'allocation', 'status', 'approval_sha256'}
        ob._object(entry, fields, fields, 'vault entry')
        base = ob._text(entry['campaign'], 'campaign', 40)
        provider = entry['provider']
        if provider not in policy['destinations']:
            raise ValueError('Vault entry destination is outside registered authority')
        key = base + ':' + provider
        if key in seen:
            raise ValueError('Duplicate campaign/destination in approved section')
        seen.add(key)
        if entry['status'] not in ('APPROVED', 'HOLD', 'WITHDRAWN', 'PUBLISHED', 'EXTERNAL_SCHEDULED', 'MANUAL_ONLY'):
            raise ValueError('Unknown vault entry status')
        if entry['status'] != 'APPROVED':
            skipped.append({'key': key, 'reason': entry['status']}); continue
        revision = digest({k: v for k, v in entry.items() if k != 'approval_sha256'})
        if entry['approval_sha256'] != revision:
            # Withdraw the previous revision when an edited entry loses approval.
            skipped.append({'key': key, 'reason': 'approval_hash_mismatch'}); continue
        cid = base + '-V' + revision[:12].upper() + '-' + provider.upper()
        value = {'schema_version': 1, 'campaign': cid, 'project': policy['project'],
                 'title': entry['title'], 'status': 'COPY-READY', 'texts': {provider: entry['text']},
                 'destinations': {provider: policy['destinations'][provider]}, 'allocation': entry['allocation'],
                 'source': {'type': 'owner_approved', 'source_id': 'vault:' + policy['document_id'] + ':' + base}}
        manifest, texts = ob.validate_campaign(value, allocate=False, now=now)
        if not ob._timestamp(entry['allocation']['prepared_at'], 'prepared_at') <= now < ob._timestamp(entry['allocation']['expires_at'], 'expires_at'):
            skipped.append({'key': key, 'reason': 'not_fresh'}); continue
        manifest['allocation']['enabled'] = True
        manifest['vault'] = {'id': policy['id'], 'document_id': policy['document_id'],
                             'key': key, 'base_campaign': base, 'revision': revision}
        prepared.append((manifest, texts)); active[key] = cid
    return prepared, active, skipped


def guard(manifest, provider, *, now=None):
    info = manifest.get('vault')
    if not info:
        return None
    now = now or datetime.now(timezone.utc)
    try:
        policy = policies().get(info['id']); observed = observations().get(info['id'], {})
        if not policy or not policy['enabled'] or policy['document_id'] != info['document_id']:
            return 'vault not enabled for this document'
        if now >= ob._timestamp(observed.get('valid_until'), 'vault observation expiry'):
            return 'vault observation stale'
        if observed.get('active', {}).get(info['key']) != manifest['campaign']:
            return 'vault revision superseded, withdrawn or not approved'
        binding = destination_binding(manifest['campaign'], provider)
        history = set(observed.get('history', {}).get(info['key'], [])) | {info['base_campaign']}
        for r in iter_receipts():
            if (r.get('campaign') in history and r.get('provider') == provider
                    and binding and r.get('account_id') == binding['account_id']
                    and r.get('status') in TERMINAL_EFFECT_STATUSES):
                return 'vault entry already consumed or ambiguous on this destination'
    except (ValueError, KeyError, TypeError, OSError):
        return 'vault authority or observation unavailable'
    return None


def _entry_revision(entry):
    return digest({k: v for k, v in entry.items() if k != 'approval_sha256'})


def receipt_updates(policy, document, *, receipts=None, manifests=None):
    """Find APPROVED vault entries whose exact local provider effect already succeeded.

    Publication consequence remains local-ledger authority. This projection may only
    turn the matching editorial status into PUBLISHED; it never creates a receipt,
    renews approval or changes copy/account authority.
    """
    entries = parse_entries(document['text'], providers=policy['destinations'])
    approved = {}
    for entry in entries:
        if not isinstance(entry, dict) or entry.get('status') != 'APPROVED':
            continue
        key = str(entry.get('campaign') or '') + ':' + str(entry.get('provider') or '')
        try:
            revision = _entry_revision(entry)
        except (TypeError, ValueError):
            continue
        if entry.get('approval_sha256') != revision:
            continue
        approved[key] = (entry, revision)

    if manifests is None:
        from ocpf_post.campaigns import campaign_ids
        manifests = {campaign: builtin_manifest(campaign) for campaign in campaign_ids()}
    receipts = list(iter_receipts()) if receipts is None else list(receipts)
    updates = []
    for campaign, manifest in manifests.items():
        if not isinstance(manifest, dict):
            continue
        vault = manifest.get('vault')
        if not isinstance(vault, dict) or vault.get('id') != policy['id']:
            continue
        providers = manifest.get('providers')
        if not isinstance(providers, list) or len(providers) != 1:
            continue
        provider = str(providers[0] or '')
        key = str(vault.get('key') or '')
        approved_row = approved.get(key)
        if not approved_row:
            continue
        entry, current_revision = approved_row
        if current_revision != vault.get('revision'):
            continue
        try:
            binding = destination_binding(str(campaign), provider)
        except (OSError, ValueError, KeyError, TypeError):
            binding = None
        if not binding:
            continue
        expected_hash = (manifest.get('payload_sha256') or {}).get(provider)
        if not expected_hash:
            continue
        matching = [
            row for row in receipts
            if row.get('campaign') == campaign
            and row.get('provider') == provider
            and str(row.get('account_id') or '') == str(binding.get('account_id') or '')
            and row.get('text_sha256') == expected_hash
            and row.get('status') in {'published_verified', 'published_unverified'}
            and row.get('post_id')
        ]
        if not matching:
            continue
        receipt = matching[-1]
        updates.append({
            'base_campaign': entry['campaign'],
            'provider': provider,
            'campaign': campaign,
            'receipt_status': receipt['status'],
            'account_id': receipt.get('account_id'),
            'text_sha256': receipt.get('text_sha256'),
            'schedule_id': receipt.get('schedule_id'),
            'post_id': receipt.get('post_id'),
        })
    updates.sort(key=lambda row: (row['base_campaign'], row['provider']))
    return updates


def reconcile_receipts(policy, document, *, apply=False, writer=write_entry_statuses):
    updates = receipt_updates(policy, document)
    capabilities = credential_capabilities()
    result = {
        'status': 'no_changes' if not updates else 'pending',
        'candidate_count': len(updates),
        'candidates': [
            {k: row.get(k) for k in (
                'base_campaign', 'provider', 'campaign', 'receipt_status',
                'schedule_id', 'post_id',
            )}
            for row in updates
        ],
        'writeback': capabilities,
        'apply': apply,
        'boundary': (
            'Exact local publication receipts may only terminalise the matching vault '
            'entry to PUBLISHED. Google status never manufactures a local receipt; '
            'writeback failure never retries or changes a social-provider effect.'
        ),
    }
    if not updates or not apply:
        return result
    if not capabilities.get('writeback_enabled'):
        return {**result, 'status': 'disabled'}
    if not capabilities.get('document_write'):
        return {**result, 'status': 'permission_required'}
    try:
        write = writer(document, updates)
    except (OSError, ValueError, KeyError, TypeError, RuntimeError) as exc:
        return {**result, 'status': 'writeback_unavailable', 'error_type': type(exc).__name__}
    status = 'updated' if write.get('result') in {'updated', 'already_reconciled'} else str(write.get('result') or 'unknown')
    return {**result, 'status': status, 'write': write}


def sync(*, apply=False, vault_id=None, reader=read_document, writer=write_entry_statuses, now=None):
    now = now or datetime.now(timezone.utc)
    reports = []
    configured = policies()
    if vault_id and vault_id not in configured:
        raise ValueError('Unknown vault ID')
    for identifier, policy in configured.items():
        if (vault_id and vault_id != identifier) or not policy['enabled']:
            continue
        try:
            document = reader(policy['document_id'])
            if document['document_id'] != policy['document_id']:
                raise ValueError('Document identity changed')
            receipt_reconciliation = reconcile_receipts(
                policy, document, apply=apply, writer=writer,
            )
            if apply and receipt_reconciliation.get('status') == 'updated':
                # Re-read after the guarded write so import/withdrawal decisions use
                # the new canonical editorial state, never the pre-write snapshot.
                document = reader(policy['document_id'])
                if document['document_id'] != policy['document_id']:
                    raise ValueError('Document identity changed after receipt writeback')
                remaining = receipt_updates(policy, document)
                if remaining:
                    raise ValueError('Receipt writeback readback did not terminalise every exact entry')
            packages, active, skipped = prepare(policy, document, now)
            report = {'vault': identifier, 'version': document['version'], 'result': 'preview',
                      'campaigns': [m['campaign'] for m, _ in packages], 'skipped': skipped, 'cancelled': [],
                      'receipt_reconciliation': receipt_reconciliation,
                      'reviewed_copy': [{'campaign': m['campaign'], 'texts': texts, 'revision': m['vault']['revision']}
                                        for m, texts in packages]}
            if apply:
                # Index activation and cancellation are serialised with the real runner.
                with ob._import_lock('vault'), ob._import_lock(), RunnerLock():
                    if policies().get(identifier) != policy:
                        raise ValueError('Vault authority changed during sync')
                    current = observations(); old = current.get(identifier, {})
                    if int(document['version']) < int(old.get('version', 0)):
                        raise ValueError('Vault version moved backwards')
                    history = {k: list(v) for k, v in old.get('history', {}).items()}
                    for manifest, texts in packages:
                        ob._existing_campaign(manifest, texts)
                    from ocpf_post.scoped_admission import vault_budget
                    from ocpf_post.registry import resolve_account
                    report['deferred'] = []
                    with vault_budget(now) as budget:
                        for manifest, texts in packages:
                            if not ob._existing_campaign(manifest, texts):
                                provider = manifest['providers'][0]
                                alias = manifest['destinations'][provider]
                                account = resolve_account(policy['project'], alias, expected_provider=provider)
                                gate = budget.admit(
                                    policy['project'], provider, str(account['account_id']),
                                    expires_at=manifest['allocation'].get('expires_at'),
                                ) if budget else {
                                    'admitted': False,
                                    'reasons': ['admission_observation_unavailable'],
                                    'error_type': 'MissingBudget',
                                }
                                if not gate['admitted']:
                                    deferred = {
                                        'campaign': manifest['campaign'],
                                        'provider': provider,
                                        'reasons': gate['reasons'],
                                    }
                                    if gate.get('error_type'):
                                        deferred['error_type'] = gate['error_type']
                                    report['deferred'].append(deferred)
                                    continue
                                ob._save_campaign(manifest, texts)
                            ids = history.setdefault(manifest['vault']['key'], [])
                            if manifest['campaign'] not in ids:
                                ids.append(manifest['campaign'])
                    deferred_ids = {row['campaign'] for row in report['deferred']}
                    committed_active = {
                        key: campaign for key, campaign in active.items()
                        if campaign not in deferred_ids
                    }
                    current[identifier] = {'project': policy['project'], 'document_id': policy['document_id'],
                        'version': document['version'], 'observed_at': now.isoformat(),
                        'valid_until': (now + timedelta(minutes=policy['max_age_minutes'])).isoformat(),
                        'active': committed_active, 'history': history, 'skipped': skipped,
                        'deferred': report['deferred']}
                    # Deferred packages are not active authority. They remain in the
                    # Doc for a later bounded sync, but telemetry must not claim an
                    # unsaved campaign as runnable/active.
                    write_private_json(state_dir() / 'vault-observations.json', {'schema_version': 1, 'vaults': current})
                    for scheduled in schedule_records():
                        if scheduled.get('status') != 'scheduled':
                            continue
                        manifest = builtin_manifest(scheduled['campaign'])
                        if (manifest.get('vault') or {}).get('id') == identifier and guard(manifest, scheduled['provider'], now=now):
                            cancel_schedule(scheduled['schedule_id'], now=now)
                            report['cancelled'].append(scheduled['schedule_id'])
                    report['campaigns'] = [c for c in report['campaigns'] if c not in deferred_ids]
                    report['result'] = 'synced'
                    if old and old.get('active', {}) != committed_active:
                        from ocpf_post import local_store
                        lifecycle_path = state_dir() / 'vault-lifecycle.json'
                        with local_store.locked(lifecycle_path):
                            lifecycle = local_store.read(lifecycle_path) or {'schema_version': 1, 'events': {}}
                            changes = [{'key': key, 'previous_campaign': campaign,
                                        'current_campaign': committed_active.get(key),
                                        'disposition': 'superseded' if key in active else 'withdrawn'}
                                       for key, campaign in old.get('active', {}).items() if committed_active.get(key) != campaign]
                            if changes:
                                event = {'vault': identifier, 'project': policy['project'],
                                         'previous_version': old.get('version'), 'version': document['version'],
                                         'observed_at': now.isoformat(), 'changes': changes,
                                         'cancelled_schedules': report['cancelled']}
                                lifecycle['events'][digest([identifier, document['version'], changes])] = event
                                local_store.write(lifecycle_path, lifecycle)
            reports.append(report)
        except Exception as exc:
            # A document or provider error cannot interrupt other portfolio sources.
            # Do not print arbitrary provider response bodies or credential content.
            reports.append({'vault': identifier, 'result': 'unavailable', 'error_type': type(exc).__name__,
                            'detail': str(exc) if isinstance(exc, ValueError) else 'Sync unavailable; previous observation expires normally'})
    return {'schema_version': 1, 'apply': apply, 'vaults': reports}
