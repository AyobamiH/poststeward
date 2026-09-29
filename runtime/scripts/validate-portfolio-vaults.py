#!/usr/bin/env python3
"""Validate the reviewed 10 September vault snapshots without provider effects."""
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
import tempfile
import os

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from ocpf_post.vault_sync import digest, prepare, register
from ocpf_post.registry import infer_project


def main():
    folder = ROOT / 'docs/gtm/vault-expansion-20260910'
    inventory = json.loads((folder / 'inventory.json').read_text())
    total = 0
    identities = set()
    texts = set()
    with tempfile.TemporaryDirectory() as temporary:
        os.environ['XDG_CONFIG_HOME'] = str(Path(temporary) / 'config')
        os.environ['XDG_STATE_HOME'] = str(Path(temporary) / 'state')
        for item in inventory:
            project = item['id']
            section = json.loads((folder / (project + '.json')).read_text())
            if not item['destinations']:
                assert item['approval_state'] == 'HOLD' and not section['entries']
                continue
            policy_path = ROOT / 'examples/portfolio-vaults' / (project + '.json')
            policy = register(policy_path, enable=True)['policy']
            assert policy['document_id'] == item['document_id']
            assert policy['destinations'] == item['destinations']
            for entry in section['entries']:
                assert infer_project(entry['campaign']) == project
                assert entry['approval_sha256'] == digest({k: v for k, v in entry.items() if k != 'approval_sha256'})
                key = (entry['campaign'], entry['provider'])
                assert key not in identities
                identities.add(key)
                copy = (entry['provider'], entry['text'].strip())
                assert copy not in texts
                texts.add(copy)
            document = {'text': (folder / (project + '.txt')).read_text()}
            # Archive validation uses its original review time. Live sync uses now.
            packages, active, skipped = prepare(policy, document, datetime(2026, 9, 10, 11, 0, tzinfo=timezone.utc))
            assert len(packages) == len(section['entries']) == 9
            assert len(active) == 9 and not skipped
            total += len(packages)
    print(json.dumps({'vaults': len(inventory), 'policies': 11,
                      'validated_platform_entries': total, 'held_concepts': 3,
                      'provider_effects': False}))


if __name__ == '__main__':
    main()
