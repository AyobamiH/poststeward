"""Completion regressions. No GitHub, social provider or owner-host access."""
import base64
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import importlib.util
import json
from pathlib import Path
import tempfile
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('handoff_completion', ROOT / 'scripts/editorial-handoff.py')
h = importlib.util.module_from_spec(spec)
spec.loader.exec_module(h)
NOW = datetime(2026, 9, 15, 10, tzinfo=timezone.utc)


def publication(campaign='EX-1', post='100', verified=True, age=0):
    return {'at': NOW - timedelta(minutes=age), 'effective_verified': verified,
            'receipt': {'campaign': campaign, 'provider': 'x', 'account_id': '123', 'post_id': post,
                        'text_sha256': 'a' * 64, 'schedule_id': 'sch_example', 'readback_verified': verified,
                        'text': 'PRIVATE_COPY', 'url': 'https://not-exported.invalid', 'token': 'PRIVATE_TOKEN'}}


class CompletionTests(unittest.TestCase):
    def test_all_three_batch_receipts_survive_not_only_latest(self):
        pubs = {str(n): publication('EX-' + str(n), str(n), age=n) for n in range(3)}
        manifests = {'EX-' + str(n): {'project': 'example'} for n in range(3)}
        result = h.recent_publications(pubs, manifests, NOW)
        self.assertEqual(result['status'], 'observed')
        self.assertEqual(len(result['records']), 3)
        self.assertEqual({r['campaign'] for r in result['records']}, set(manifests))
        self.assertNotIn('PRIVATE', h.canonical(result))
        self.assertNotIn('https://', h.canonical(result))

    def test_unverified_receipt_does_not_become_verified(self):
        p = publication(verified=False)
        result = h.recent_publications({'p': p}, {'EX-1': {'project': 'example'}}, NOW)
        self.assertEqual(result['records'][0]['status'], 'published_unverified')

    def test_effective_verification_and_readback_both_required(self):
        p = publication(); p['receipt']['readback_verified'] = False
        result = h.recent_publications({'p': p}, {'EX-1': {'project': 'example'}}, NOW)
        self.assertEqual(result['records'][0]['status'], 'published_unverified')

    def test_conflicting_post_claims_excluded_with_explicit_count(self):
        result = h.recent_publications({'a': publication(), 'b': publication('EX-2')},
                                      {'EX-1': {'project': 'example'}, 'EX-2': {'project': 'example'}}, NOW)
        self.assertEqual(result['records'], [])
        self.assertEqual(result['unresolved_count'], 2)
        self.assertEqual(result['status'], 'partial')

    def test_unmapped_manifest_is_unknown_not_invented_project(self):
        result = h.recent_publications({'p': publication()}, {}, NOW)
        self.assertEqual(result['unresolved_count'], 1)
        self.assertEqual(result['records'], [])

    def test_provider_account_identity_keeps_equal_post_ids_separate(self):
        p = publication('EX-2'); p['receipt']['account_id'] = '456'
        result = h.recent_publications({'a': publication(), 'b': p},
                                      {'EX-1': {'project': 'example'}, 'EX-2': {'project': 'other'}}, NOW)
        self.assertEqual(result['status'], 'observed')
        self.assertEqual(len(result['records']), 2)

    def test_history_is_bounded_without_silent_loss(self):
        pubs = {str(n): publication('EX-' + str(n), str(n), age=n) for n in range(165)}
        manifests = {'EX-' + str(n): {'project': 'example'} for n in range(165)}
        result = h.recent_publications(pubs, manifests, NOW)
        self.assertEqual(len(result['records']), 160)
        self.assertEqual(result['total_matched'], 165)
        self.assertEqual(result['omitted_count'], 5)
        self.assertEqual(result['status'], 'partial')
        self.assertEqual(result['records'][0]['campaign'], 'EX-0')

    def test_old_history_not_misrepresented_as_recent(self):
        result = h.recent_publications({'p': publication(age=8*24*60)}, {'EX-1': {'project': 'example'}}, NOW)
        self.assertEqual(result['records'], [])
        self.assertEqual(result['lookback_days'], 7)

    def test_future_publication_rejected(self):
        with self.assertRaisesRegex(ValueError, 'invalid_publication_time'):
            h.recent_publications({'p': publication(age=-1)}, {'EX-1': {'project': 'example'}}, NOW)

    def test_incomplete_identity_rejected(self):
        p = publication(); del p['receipt']['account_id']
        with self.assertRaisesRegex(ValueError, 'incomplete_publication_identity'):
            h.recent_publications({'p': p}, {}, NOW)

    def test_json_duplicate_keys_nonfinite_and_array_refused(self):
        for text in ('{"status":"observed","status":"awaiting_host"}', '{"x":NaN}', '{"x":Infinity}', '[]'):
            with self.subTest(text=text), self.assertRaises(ValueError): h.strict_json(text)

    def test_remote_boolean_schema_is_not_version_one(self):
        data = {'kind': h.KIND, 'schema_version': True, 'status': 'awaiting_host'}
        def api(*args):
            return {'path': h.PATH, 'sha': 'a'*40, 'encoding': 'base64',
                    'content': base64.b64encode(json.dumps(data).encode()).decode()}
        with self.assertRaisesRegex(ValueError, 'ownership_marker'): h.remote(api)

    def test_policy_types_are_exact(self):
        self.assertTrue(h.policy_enabled(deepcopy(h.CONFIG)))
        for key, value in (('schema_version', True), ('enabled', 1), ('repository_id', str(h.REPO_ID))):
            policy = {**h.CONFIG, key: value}
            self.assertFalse(h.policy_enabled(policy))

    def test_expired_outgoing_evidence_stops_before_network(self):
        with self.assertRaisesRegex(ValueError, 'stale_or_future'):
            h.publish_snapshot({'observed_at': (NOW-timedelta(hours=2)).isoformat()},
                               lambda *a: self.fail('unexpected network'), NOW)

    def test_equal_time_conflicting_digest_never_overwrites(self):
        previous = {'kind': h.KIND, 'schema_version': 1, 'status': 'observed', 'observed_at': NOW.isoformat()}
        previous['snapshot_sha256'] = h.digest(previous)
        calls = []
        def api(method, resource, payload=None):
            calls.append(method)
            if not resource: return {'id': h.REPO_ID, 'private': True, 'full_name': h.REPO}
            return {'path': h.PATH, 'sha': 'a'*40, 'encoding': 'base64',
                    'content': base64.b64encode(json.dumps(previous).encode()).decode()}
        snapshot = {**previous, 'snapshot_sha256': 'b'*64}
        with self.assertRaisesRegex(ValueError, 'conflicting_snapshot_timestamp'):
            h.publish_snapshot(snapshot, api, NOW)
        self.assertNotIn('PUT', calls)

    def runtime(self, root, reads, writes, token=None):
        state = ModuleType('ocpf_post.state')
        state.config_dir = state.state_dir = lambda: root
        state.ensure_private_dir = lambda p: None
        store = SimpleNamespace(read=lambda p: reads(p), write=lambda p, v: writes.append((p.name, v)))
        package = ModuleType('ocpf_post'); package.local_store = store
        repl = ModuleType('ocpf_post.replenisher'); repl._github_token = lambda: token
        return {'ocpf_post': package, 'ocpf_post.state': state, 'ocpf_post.replenisher': repl}

    def test_missing_credentials_records_failure_not_stale_success(self):
        with tempfile.TemporaryDirectory() as td:
            writes = []
            modules = self.runtime(Path(td), lambda p: deepcopy(h.CONFIG), writes)
            with patch.dict('sys.modules', modules), patch.object(h, 'capture', side_effect=AssertionError('capture')):
                with self.assertRaisesRegex(ValueError, 'credential_unavailable'):
                    h.run(SimpleNamespace(enable=False, disable=False, status=False, apply=True))
            self.assertEqual(writes[-1][0], 'editorial-handoff-status.json')
            self.assertEqual(writes[-1][1]['status'], 'attention')

    def test_completed_disable_is_rechecked_after_lock(self):
        with tempfile.TemporaryDirectory() as td:
            writes, calls = [], []
            def reads(path):
                calls.append(path)
                return deepcopy(h.CONFIG) if len(calls) == 1 else {**h.CONFIG, 'enabled': False}
            modules = self.runtime(Path(td), reads, writes)
            with patch.dict('sys.modules', modules), patch.object(h, 'capture', side_effect=AssertionError('capture')):
                result = h.run(SimpleNamespace(enable=False, disable=False, status=False, apply=True))
            self.assertEqual(result['status'], 'disabled')
            self.assertEqual(writes, [])

    def test_disable_cannot_race_an_inflight_enable(self):
        with tempfile.TemporaryDirectory() as td:
            writes = []
            modules = self.runtime(Path(td), lambda p: deepcopy(h.CONFIG), writes)
            with patch.dict('sys.modules', modules), patch.object(h.fcntl, 'flock', side_effect=BlockingIOError):
                result = h.run(SimpleNamespace(enable=False, disable=True, status=False, apply=True))
            self.assertEqual(result['status'], 'busy')
            self.assertEqual(writes, [])

    def test_disable_writes_only_policy_never_provider(self):
        with tempfile.TemporaryDirectory() as td:
            writes = []
            modules = self.runtime(Path(td), lambda p: deepcopy(h.CONFIG), writes)
            with patch.dict('sys.modules', modules), patch.object(h, 'capture', side_effect=AssertionError('capture')):
                result = h.run(SimpleNamespace(enable=False, disable=True, status=False, apply=True))
            self.assertEqual(result['status'], 'disabled')
            self.assertEqual(writes, [('editorial-handoff.json', {**h.CONFIG, 'enabled': False})])


if __name__ == '__main__':
    unittest.main()
