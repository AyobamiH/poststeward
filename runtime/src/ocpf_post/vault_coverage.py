"""Read-only comparison of a reviewed vault inventory with local host evidence."""
from datetime import datetime, timezone
import json
from pathlib import Path

from ocpf_post.campaigns import builtin_manifest, builtin_text
from ocpf_post.onboarding import _timestamp
from ocpf_post.vault_sync import policies, observations


def coverage(inventory_file, *, now=None):
    now = now or datetime.now(timezone.utc)
    path = Path(inventory_file)
    if path.stat().st_size > 2_000_000:
        raise ValueError('Vault inventory exceeds 2 MB')
    inventory = json.loads(path.read_text(encoding='utf-8'))
    if not isinstance(inventory, list) or not inventory or len(inventory) > 100:
        raise ValueError('Expected 1..100 inventory projects')
    configured, observed = policies(), observations()
    rows, seen = [], set()
    for expected in inventory:
        project, document = expected['id'], expected['document_id']
        if project in seen:
            raise ValueError('Duplicate inventory project')
        seen.add(project)
        matches = [(key, p) for key, p in configured.items()
                   if p.get('project') == project and p.get('document_id') == document]
        if expected['approval_state'] != 'APPROVED':
            rows.append({'project': project, 'status': 'held', 'expected_entries': 0,
                         'enabled_policy_present': any(p.get('enabled') for _, p in matches)})
            continue
        expected_keys = {(c, provider) for c in expected['campaigns'] for provider in expected['destinations']}
        if not expected_keys or len(expected_keys) != expected['entries']:
            raise ValueError('Inventory entry count does not match campaigns and destinations')
        identifier, policy = matches[0] if len(matches) == 1 else (None, {})
        observation = observed.get(identifier, {})
        authority_matches = bool(policy.get('enabled') and policy.get('destinations') == expected['destinations'])
        fresh = False
        try:
            fresh = (observation.get('document_id') == document and observation.get('project') == project
                     and now < _timestamp(observation.get('valid_until'), 'vault expiry'))
        except (ValueError, TypeError):
            pass
        entries = []
        for campaign, provider in sorted(expected_keys):
            key = campaign + ':' + provider
            cid = observation.get('active', {}).get(key)
            intact = False
            if cid:
                manifest = builtin_manifest(cid)
                vault = manifest.get('vault') or {}
                intact = (manifest.get('campaign') == cid and manifest.get('project') == project
                          and vault.get('id') == identifier and vault.get('document_id') == document
                          and vault.get('key') == key and vault.get('base_campaign') == campaign
                          and manifest.get('destinations', {}).get(provider) == expected['destinations'][provider]
                          and provider in manifest.get('providers', []) and bool(builtin_text(cid, provider)))
            entries.append({'key': key, 'campaign': cid, 'import_intact': bool(intact)})
        complete = authority_matches and fresh and all(e['import_intact'] for e in entries)
        rows.append({'project': project, 'vault_id': identifier, 'document_id': document,
                     'status': 'coverage_observed' if complete else 'incomplete',
                     'authority_matches': authority_matches, 'observation_fresh': fresh,
                     'observed_at': observation.get('observed_at'), 'version': observation.get('version'),
                     'expected_entries': len(expected_keys), 'intact_imports': sum(e['import_intact'] for e in entries),
                     'entries': entries, 'skipped': observation.get('skipped', [])})
    approved = [r for r in rows if r['status'] != 'held']
    return {'schema_version': 1, 'observed_at': now.isoformat(),
            'status': 'coverage_observed' if approved and all(r['status'] == 'coverage_observed' for r in approved)
                      and not any(r.get('enabled_policy_present') for r in rows) else 'incomplete',
            'expected_projects': len(approved), 'covered_projects': sum(r['status'] == 'coverage_observed' for r in approved),
            'expected_entries': sum(r['expected_entries'] for r in approved),
            'intact_imports': sum(r['intact_imports'] for r in approved), 'projects': rows,
            'boundary': 'Local expected inventory, policy, freshness and payload integrity only. No Google read, registration, repair, allocation or publication. Import coverage is not a scheduled publication receipt. HOLD projects gain no authority.'}
