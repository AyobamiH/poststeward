import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch
from ocpf_post import vault_coverage as vc


class VaultCoverageTests(unittest.TestCase):
    def report(self, *, policy_change=None, observation_change=None, text='approved copy', manifest_change=None):
        inventory = [{'id': 'alpha', 'document_id': 'doc-alpha', 'approval_state': 'APPROVED',
                      'campaigns': ['A'], 'destinations': {'x': 'founder'}, 'entries': 1},
                     {'id': 'poststeward', 'document_id': 'doc-hold', 'approval_state': 'HOLD'}]
        policy = {'project': 'alpha', 'document_id': 'doc-alpha', 'destinations': {'x': 'founder'}, 'enabled': True}
        observation = {'project': 'alpha', 'document_id': 'doc-alpha', 'valid_until': '2026-09-10T12:00:00Z',
                       'active': {'A:x': 'A-V1-X'}}
        manifest = {'project': 'alpha', 'campaign': 'A-V1-X', 'providers': ['x'], 'destinations': {'x': 'founder'},
                    'vault': {'id': 'alpha-gtm', 'document_id': 'doc-alpha', 'key': 'A:x', 'base_campaign': 'A'}}
        policy.update(policy_change or {})
        observation.update(observation_change or {})
        manifest.update(manifest_change or {})
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'inventory.json'; path.write_text(json.dumps(inventory))
            with patch.object(vc, 'policies', return_value={'alpha-gtm': policy}), \
                 patch.object(vc, 'observations', return_value={'alpha-gtm': observation}), \
                 patch.object(vc, 'builtin_manifest', return_value=manifest), \
                 patch.object(vc, 'builtin_text', return_value=text):
                return vc.coverage(path, now=datetime(2026, 9, 10, 11, tzinfo=timezone.utc))

    def test_intact_imports_and_hold_are_distinct(self):
        result = self.report()
        self.assertEqual(result['status'], 'coverage_observed')
        self.assertEqual(result['covered_projects'], 1)
        self.assertEqual(result['expected_entries'], 1)
        self.assertEqual(result['projects'][1]['status'], 'held')
        self.assertFalse(result['projects'][1]['enabled_policy_present'])

    def test_stale_observation_does_not_close_coverage(self):
        result = self.report(observation_change={'valid_until': '2026-09-10T10:00:00Z'})
        self.assertEqual(result['status'], 'incomplete')
        self.assertEqual(result['intact_imports'], 1)

    def test_authority_or_document_mismatch_does_not_close_coverage(self):
        for kwargs in ({'policy_change': {'enabled': False}}, {'policy_change': {'destinations': {'x': 'other'}}},
                       {'observation_change': {'document_id': 'other'}}):
            with self.subTest(kwargs=kwargs):
                self.assertEqual(self.report(**kwargs)['status'], 'incomplete')

    def test_active_index_without_intact_payload_is_not_import_evidence(self):
        self.assertEqual(self.report(text=None)['intact_imports'], 0)
        self.assertEqual(self.report(manifest_change={'project': 'other'})['intact_imports'], 0)
        self.assertEqual(self.report(observation_change={'active': {}})['intact_imports'], 0)
