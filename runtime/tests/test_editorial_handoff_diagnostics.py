"""Real CLI/store integration, with all runtime files isolated from the host."""
import contextlib
from datetime import datetime, timezone
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from ocpf_post import local_store

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / 'scripts/editorial-handoff.py'
SUPPLY = ROOT / 'scripts/editorial-supply.py'
spec = importlib.util.spec_from_file_location('handoff_diagnostic_regressions', SCRIPT)
h = importlib.util.module_from_spec(spec)
spec.loader.exec_module(h)


def args(**values):
    return SimpleNamespace(**{'enable': False, 'disable': False, 'status': False, 'apply': False, **values})


class HandoffDiagnosticTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.state = self.root / 'state'
        self.config = self.root / 'config'
        self.status = self.state / 'editorial-handoff-status.json'
        self.env = {**os.environ, 'HOME': str(self.root),
                    'OCPF_POST_CONFIG_DIR': str(self.config), 'OCPF_POST_STATE_DIR': str(self.state)}
        # The real child must bootstrap itself, not inherit the test runner's
        # PYTHONPATH. Never pass any existing GitHub credential into it.
        for key in ('PYTHONPATH', 'GITHUB_TOKEN', 'GH_TOKEN'):
            self.env.pop(key, None)
        self.environment = patch.dict(os.environ, self.env, clear=True)
        self.environment.start()
        self.addCleanup(self.environment.stop)
        self.legacy = {'status': 'attention', 'observed_at': '2026-09-15T15:00:00+00:00',
                       'error_type': 'ValueError'}

    def seed(self, value):
        self.state.mkdir(exist_ok=True)
        raw = json.dumps(value).encode()
        self.status.write_bytes(raw)
        return raw

    def test_legacy_failure_is_read_only_and_does_not_enable(self):
        raw = self.seed(self.legacy)
        with patch.object(h, 'capture', side_effect=AssertionError('capture forbidden')), \
             patch.object(h, 'publish_snapshot', side_effect=AssertionError('network forbidden')):
            result = h.run(args(status=True))
        self.assertFalse(result['enabled'])
        self.assertEqual(result['last_cycle']['status'], 'attention')
        self.assertTrue(result['last_cycle']['legacy_schema_missing'])
        self.assertEqual(self.status.read_bytes(), raw)
        self.assertFalse(self.config.exists())

    def test_recovery_preserves_original_failure_and_versions_new_report(self):
        self.seed(self.legacy)
        h.persist_status(self.status, {'status': 'attention', 'error_type': 'TimeoutError'})
        saved = local_store.read(self.status)
        self.assertEqual(saved['schema_version'], 1)
        self.assertEqual(saved['legacy_failure'], self.legacy)
        h.persist_status(self.status, {'status': 'observed', 'write_performed': True})
        saved = local_store.read(self.status)
        self.assertEqual(saved['legacy_failure'], self.legacy)
        self.assertEqual(saved['status'], 'observed')
        self.assertEqual(self.status.stat().st_mode & 0o777, 0o600)

    def test_unrecognised_legacy_content_is_not_reset_or_promoted(self):
        for value in ({'status': 'observed'}, {**self.legacy, 'extra': 'PRIVATE'},
                      {**self.legacy, 'error_type': 'PRIVATE'},
                      {**self.legacy, 'observed_at': '2026-09-15T15:00:00'},
                      {**self.legacy, 'schema_version': 2}):
            with self.subTest(value=value):
                raw = self.seed(value)
                with self.assertRaises(ValueError): h.read_status(self.status)
                with self.assertRaises(ValueError): h.persist_status(self.status, {'status': 'attention'})
                self.assertEqual(self.status.read_bytes(), raw)

    def test_duplicate_keys_and_malformed_status_are_not_repaired(self):
        self.state.mkdir()
        for raw in (b'{', b'[]', b'{"status":"attention","status":"attention","observed_at":"2026-09-15T15:00:00Z","error_type":"ValueError"}'):
            self.status.write_bytes(raw)
            with self.assertRaises(ValueError): h.read_status(self.status)
            self.assertEqual(self.status.read_bytes(), raw)

    def test_missing_status_does_not_create_files(self):
        self.assertEqual(h.run(args(status=True)), {'enabled': False, 'last_cycle': {}})
        self.assertEqual(list(self.root.iterdir()), [])

    def test_unversioned_policy_is_not_treated_as_legacy_diagnostic(self):
        self.config.mkdir()
        policy = self.config / 'editorial-handoff.json'
        raw = json.dumps({k: v for k, v in h.CONFIG.items() if k != 'schema_version'}).encode()
        policy.write_bytes(raw)
        with self.assertRaises(ValueError): h.run(args(status=True))
        self.assertEqual(policy.read_bytes(), raw)

    def test_corrupt_status_blocks_handoff_before_any_network(self):
        raw = self.seed({'unexpected': 'PRIVATE'})
        with patch.object(h, 'capture', side_effect=AssertionError('capture forbidden')), \
             patch('ocpf_post.replenisher._github_token', side_effect=AssertionError('credential forbidden')):
            with self.assertRaises(ValueError): h.run(args(enable=True, apply=True))
        self.assertEqual(self.status.read_bytes(), raw)
        self.assertFalse((self.config / 'editorial-handoff.json').exists())

    def test_real_status_cli_reads_legacy_record_without_pythonpath(self):
        raw = self.seed(self.legacy)
        result = subprocess.run([sys.executable, str(SCRIPT), '--status'], cwd=ROOT,
                                env=self.env, capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        data = json.loads(result.stdout)
        self.assertFalse(data['enabled'])
        self.assertTrue(data['last_cycle']['legacy_schema_missing'])
        self.assertEqual(self.status.read_bytes(), raw)

    def test_real_supply_child_uses_existing_readers_without_writes(self):
        result = subprocess.run([sys.executable, str(SUPPLY)], cwd=ROOT,
                                env=self.env, capture_output=True, timeout=20)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        data = h.decode_workpack(result.stdout, result.returncode)
        self.assertEqual(data['status'], 'observed')
        self.assertGreater(data['project_count'], 0)
        self.assertEqual(list(self.root.iterdir()), [])

    def test_capture_retries_only_snapshot_contention_then_builds_once(self):
        failure = h.CaptureError('workpack_capture_unavailable', {
            'child_exit_code': 2, 'output_bytes': 120, 'child_status': 'unavailable',
            'stage': 'build_editorial_workpack', 'coverage_status': 'snapshot_changed',
        })
        pack = {'schema_version': 1, 'status': 'observed'}
        with patch.object(h, '_capture_base', side_effect=[failure, (pack, 'a' * 40)]) as capture, \
             patch.object(h.time, 'sleep') as sleep, \
             patch('ocpf_post.editorial_continuity.reconcile', return_value={'status': 'observed'}), \
             patch('ocpf_post.editorial_continuity.audience_evidence', return_value={'status': 'observed'}), \
             patch.object(h, 'make_snapshot', return_value={'status': 'observed'}) as make:
            result = h.capture()
        self.assertEqual(result, {'status': 'observed'})
        self.assertEqual(capture.call_count, 2)
        sleep.assert_called_once_with(h.CAPTURE_RETRY_DELAY_SECONDS)
        make.assert_called_once()

    def test_repeated_snapshot_contention_is_deferred_without_remote_write(self):
        self.config.mkdir()
        local_store.write(self.config / 'editorial-handoff.json', h.CONFIG)
        failure = h.CaptureError('workpack_capture_unavailable', {
            'child_exit_code': 2, 'output_bytes': 120, 'child_status': 'unavailable',
            'stage': 'build_editorial_workpack', 'coverage_status': 'snapshot_changed',
        })
        with patch('ocpf_post.replenisher._github_token', return_value='test-only'), \
             patch.object(h, 'capture', side_effect=failure), \
             patch.object(h, 'publish_snapshot', side_effect=AssertionError('remote write forbidden')):
            result = h.run(args(apply=True))
        self.assertEqual(result['status'], 'deferred')
        self.assertEqual(result['reason'], 'host_snapshot_changing')
        self.assertFalse(result['write_performed'])
        self.assertTrue(result['publishing_unchanged'])
        saved = local_store.read(self.status)
        self.assertEqual(saved['status'], 'deferred')
        self.assertEqual(saved['reason'], 'host_snapshot_changing')
        self.assertEqual(saved['diagnostic']['coverage_status'], 'snapshot_changed')

    def test_real_child_retains_snapshot_changed_reason(self):
        report = self.root / 'coverage.json'
        report.write_text(json.dumps({'schema_version': 1, 'status': 'snapshot_changed'}))
        result = subprocess.run([sys.executable, str(SUPPLY), '--coverage', str(report)],
                                cwd=ROOT, env=self.env, capture_output=True, timeout=10)
        self.assertEqual(result.returncode, 2)
        with self.assertRaises(h.CaptureError) as error:
            h.decode_workpack(result.stdout, result.returncode)
        diagnostic = error.exception.diagnostic
        self.assertEqual(diagnostic['stage'], 'build_editorial_workpack')
        self.assertEqual(diagnostic['coverage_status'], 'snapshot_changed')
        self.assertEqual(diagnostic['error_type'], 'ValueError')

    def test_real_child_missing_file_has_safe_stage_not_private_path(self):
        result = subprocess.run([sys.executable, str(SUPPLY), '--coverage', str(self.root / 'PRIVATE_FILENAME')],
                                cwd=ROOT, env=self.env, capture_output=True, timeout=10)
        self.assertEqual(result.returncode, 2)
        data = json.loads(result.stdout)
        self.assertEqual(data['stage'], 'load_coverage')
        self.assertEqual(data['error_type'], 'FileNotFoundError')
        self.assertNotIn(b'PRIVATE_FILENAME', result.stdout + result.stderr)

    def test_valid_child_output_passes_without_rewriting(self):
        pack = {'schema_version': 1, 'status': 'observed', 'project_count': 20}
        self.assertEqual(h.decode_workpack(json.dumps(pack).encode(), 0), pack)

    def test_nonzero_exit_never_becomes_success_from_json_alone(self):
        with self.assertRaises(h.CaptureError) as error:
            h.decode_workpack(b'{"schema_version":1,"status":"observed"}', 2)
        self.assertEqual(error.exception.diagnostic['child_exit_code'], 2)

    def test_zero_exit_never_overrides_failed_status(self):
        with self.assertRaises(h.CaptureError):
            h.decode_workpack(b'{"schema_version":1,"status":"unavailable"}', 0)

    def test_oversize_is_distinct_from_reader_failure(self):
        with self.assertRaisesRegex(h.CaptureError, 'workpack_output_too_large'):
            h.decode_workpack(b' ' * 5_000_001, 0)

    def test_invalid_or_duplicate_json_never_echoes_output(self):
        for raw in (b'PRIVATE_ERROR', b'[]', b'{"schema_version":1,"status":"observed","status":"unavailable"}'):
            with self.subTest(raw=raw), self.assertRaises(h.CaptureError) as error:
                h.decode_workpack(raw, 2)
            self.assertNotIn('PRIVATE', str(error.exception) + json.dumps(error.exception.diagnostic))

    def test_bool_or_missing_schema_is_not_success(self):
        for pack in ({'status': 'observed'}, {'schema_version': True, 'status': 'observed'}):
            with self.assertRaises(h.CaptureError): h.decode_workpack(json.dumps(pack).encode(), 0)

    def test_diagnostics_drop_unapproved_fields_and_values(self):
        pack = {'status': 'unavailable', 'error_type': 'PRIVATE', 'stage': 'PRIVATE',
                'coverage_status': 'PRIVATE', 'text': 'PRIVATE', 'error': 'PRIVATE'}
        with self.assertRaises(h.CaptureError) as error:
            h.decode_workpack(json.dumps(pack).encode(), 2)
        self.assertNotIn('PRIVATE', json.dumps(error.exception.diagnostic))
        self.assertEqual(set(error.exception.diagnostic), {'child_exit_code', 'output_bytes', 'child_status'})

    def test_capture_diagnostic_is_persisted_with_real_schema(self):
        failure = h.CaptureError('workpack_capture_unavailable', {'child_exit_code': 2, 'output_bytes': 100})
        with patch('ocpf_post.replenisher._github_token', return_value='test-only'), \
             patch.object(h, 'capture', side_effect=failure), \
             patch.object(h, 'publish_snapshot', side_effect=AssertionError('network forbidden')):
            with self.assertRaises(h.CaptureError): h.run(args(enable=True, apply=True))
        saved = local_store.read(self.status)
        self.assertEqual(saved['schema_version'], 1)
        self.assertEqual(saved['diagnostic'], failure.diagnostic)
        self.assertFalse(h.run(args(status=True))['enabled'])

    def test_main_retains_safe_capture_diagnostic_and_nonzero_exit(self):
        failure = h.CaptureError('workpack_capture_unavailable', {'child_exit_code': 2, 'output_bytes': 100})
        output = io.StringIO()
        with patch.object(sys, 'argv', ['editorial-handoff.py', '--enable', '--apply']), \
             patch.object(h, 'run', side_effect=failure), contextlib.redirect_stdout(output):
            self.assertEqual(h.main(), 2)
        result = json.loads(output.getvalue())
        self.assertEqual(result['error'], 'workpack_capture_unavailable')
        self.assertEqual(result['diagnostic'], failure.diagnostic)
        self.assertTrue(result['publishing_unchanged'])


if __name__ == '__main__':
    unittest.main()
