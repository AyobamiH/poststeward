"""Use the real atomic store; mock only credentials, capture and remote transport."""
from datetime import datetime, timezone
import importlib.util
import os
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import patch

from ocpf_post import local_store

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('handoff_state_regression', ROOT / 'scripts/editorial-handoff.py')
h = importlib.util.module_from_spec(spec)
spec.loader.exec_module(h)


def args(**values):
    return SimpleNamespace(**{'enable': False, 'disable': False, 'status': False, 'apply': False, **values})


class HandoffStoreRoundTripTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.state = self.root / 'state'
        self.config = self.root / 'config'
        self.environment = patch.dict(os.environ, {
            'OCPF_POST_CONFIG_DIR': str(self.config),
            'OCPF_POST_STATE_DIR': str(self.state),
        })
        self.environment.start()
        self.addCleanup(self.environment.stop)

    def test_failure_status_round_trips_through_real_reader(self):
        with patch('ocpf_post.replenisher._github_token', return_value='test-only'), \
             patch.object(h, 'capture', side_effect=ValueError('workpack_capture_unavailable')), \
             patch.object(h, 'publish_snapshot', side_effect=AssertionError('network forbidden')):
            with self.assertRaisesRegex(ValueError, 'workpack_capture_unavailable'):
                h.run(args(enable=True, apply=True))
        saved = local_store.read(self.state / 'editorial-handoff-status.json')
        self.assertEqual(saved['schema_version'], 1)
        self.assertEqual(saved['status'], 'attention')
        status = h.run(args(status=True))
        self.assertFalse(status['enabled'])
        self.assertEqual(status['last_cycle'], saved)
        self.assertFalse((self.config / 'editorial-handoff.json').exists())

    def test_success_status_round_trips_through_real_reader(self):
        observed = datetime.now(timezone.utc).isoformat()
        remote = {'status': 'observed', 'write_performed': True,
                  'remote_observed_at': observed, 'snapshot_sha256': 'a' * 64}
        with patch('ocpf_post.replenisher._github_token', return_value='test-only'), \
             patch.object(h, 'capture', return_value={'test_only': True}), \
             patch.object(h, 'publish_snapshot', return_value=remote):
            self.assertTrue(h.run(args(enable=True, apply=True))['enabled'])
        saved = local_store.read(self.state / 'editorial-handoff-status.json')
        self.assertEqual(saved['schema_version'], 1)
        self.assertEqual(saved['status'], 'observed')
        self.assertEqual(local_store.read(self.config / 'editorial-handoff.json'), h.CONFIG)
        status = h.run(args(status=True))
        self.assertTrue(status['enabled'])
        self.assertEqual(status['last_cycle'], saved)


if __name__ == '__main__':
    unittest.main()
