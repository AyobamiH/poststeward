"""Isolated telemetry tests: no GitHub, provider, scheduler or Doc effects."""
import base64
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import importlib.util
import json
import os
import shutil
import socket
import ssl
import subprocess
import urllib.error
from pathlib import Path
import tempfile
from types import SimpleNamespace, ModuleType
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('editorial_handoff', ROOT / 'scripts/editorial-handoff.py')
h = importlib.util.module_from_spec(spec)
spec.loader.exec_module(h)
NOW = datetime(2026, 9, 15, 2, tzinfo=timezone.utc)


def pack():
    return {'schema_version': 1, 'status': 'observed', 'coverage_observed_at': NOW.isoformat(),
            'coverage_sha256': 'a' * 64, 'project_count': 1, 'route_count': 1, 'physical_account_count': 1,
            'supply_reviews': [{'project': 'example', 'provider': 'x', 'account_id': '123', 'aliases': ['x-brand'],
                'source': {'status': 'observed', 'head_sha': 'b' * 40, 'raw_text': 'DO_NOT_EXPORT'},
                'vaults': [{'id': 'example-vault', 'status': 'current', 'credential': 'DO_NOT_EXPORT'}],
                'exclusion_counts': {'consumed': 3}, 'untrusted_extra': 'DO_NOT_EXPORT'}],
            'accounts': [{'provider': 'x', 'account_id': '123', 'projects': ['example'], 'runnable': 0,
                'reserved': 0, 'verified_24h': 3, 'empty_inventory': True, 'publishing_intent': True,
                'latest_verified_effect': {'campaign': 'EX-003', 'post_id': '321', 'published_at': NOW.isoformat(),
                                           'text': 'DO_NOT_EXPORT', 'url': 'https://not-exported.invalid/'}}]}


class FakeApi:
    def __init__(self):
        self.data = {'schema_version': 1, 'kind': h.KIND, 'status': 'awaiting_host'}
        self.calls = []
        self.repo = {'id': h.REPO_ID, 'private': True, 'full_name': h.REPO}
        self.sha = 'c' * 40
        self.failure = None

    def __call__(self, method, resource, payload=None):
        self.calls.append((method, resource, payload))
        if not resource:
            return deepcopy(self.repo)
        if method == 'GET':
            return {'path': h.PATH, 'sha': self.sha, 'encoding': 'base64',
                    'content': base64.b64encode(json.dumps(self.data).encode()).decode()}
        assert method == 'PUT' and resource == '/contents/' + h.PATH
        assert payload['sha'] == self.sha and payload['branch'] == h.BRANCH
        if self.failure == 'conflict':
            raise ValueError('github_http_409')
        if self.failure != 'mismatch':
            self.data = json.loads(base64.b64decode(payload['content']))
        if self.failure == 'lost_response':
            raise TimeoutError('response_lost')
        return {'commit': {'sha': 'd' * 40}}


class HandoffTests(unittest.TestCase):
    def snapshot(self):
        return h.make_snapshot(pack(), 'b' * 40, NOW)

    def test_allowlist_excludes_copy_urls_credentials_and_future_fields(self):
        text = h.canonical(self.snapshot())
        self.assertNotIn('DO_NOT_EXPORT', text)
        self.assertNotIn('https://', text)
        self.assertEqual(self.snapshot()['route_count'], 1)

    def test_hash_binds_entire_snapshot(self):
        s = self.snapshot()
        self.assertEqual(s['snapshot_sha256'], h.digest({k: v for k, v in s.items() if k != 'snapshot_sha256'}))

    def test_failed_workpack_rejected(self):
        p = pack(); p['status'] = 'unavailable'
        with self.assertRaises(ValueError): h.make_snapshot(p, 'b' * 40, NOW)

    def test_boolean_schema_rejected(self):
        p = pack(); p['schema_version'] = True
        with self.assertRaises(ValueError): h.make_snapshot(p, 'b' * 40, NOW)

    def test_stale_snapshot_rejected(self):
        with self.assertRaises(ValueError): h.make_snapshot(pack(), 'b' * 40, NOW + timedelta(hours=2))

    def test_future_snapshot_rejected(self):
        with self.assertRaises(ValueError): h.make_snapshot(pack(), 'b' * 40, NOW - timedelta(seconds=1))

    def test_naive_timestamp_rejected(self):
        p = pack(); p['coverage_observed_at'] = '2026-09-15T02:00:00'
        with self.assertRaises(ValueError): h.make_snapshot(p, 'b' * 40, NOW)

    def test_invalid_commit_rejected(self):
        with self.assertRaises(ValueError): h.make_snapshot(pack(), 'branch-name', NOW)

    def test_complete_export_not_silently_truncated(self):
        p = pack(); p['supply_reviews'] *= 501
        with self.assertRaises(ValueError): h.make_snapshot(p, 'b' * 40, NOW)

    def test_semantic_maxima_can_exceed_legacy_transport_cap_without_truncation(self):
        p = pack()
        p['publication_evidence'] = {
            'status': 'observed', 'lookback_days': 7, 'total_matched': h.MAX_PUBLICATIONS,
            'omitted_count': 0, 'unresolved_count': 0, 'boundary': 'bounded publication evidence',
            'records': [
                {
                    'project': f'project-{i:03d}',
                    'campaign': f'CAMPAIGN-{i:03d}-' + ('c' * 24),
                    'provider': 'linkedin',
                    'account_id': 'urn:li:person:' + ('a' * 20),
                    'post_id': 'p' * 20,
                    'schedule_id': 's' * 20,
                    'text_sha256': 'd' * 64,
                    'published_at': NOW.isoformat(),
                    'status': 'published_verified',
                }
                for i in range(h.MAX_PUBLICATIONS)
            ],
        }
        p['audience_evidence'] = {
            'status': 'observed', 'observed_at': NOW.isoformat(), 'omitted_count': 0,
            'records': [
                {
                    'project': f'project-{i:03d}',
                    'campaign': f'CAMPAIGN-{i:03d}-' + ('c' * 24),
                    'provider': 'linkedin',
                    'account_id': 'urn:li:person:' + ('a' * 20),
                    'post_id': 'p' * 20,
                    'text_sha256': 'd' * 64,
                    'published_at': NOW.isoformat(),
                    'status': 'published_verified',
                    'editorial_assessment_recorded': True,
                    'assessment_sha256': 'e' * 64,
                    'performance': [
                        {
                            'captured_at': NOW.isoformat(),
                            'target_age_hours': age,
                            'availability': {'status': 'available'},
                            'metrics': {
                                'impressions': 123456, 'likes': 1234, 'replies': 123,
                                'reposts': 45, 'clicks': 678, 'bookmarks': None,
                            },
                        }
                        for age in (24, 72, 168)
                    ],
                    'inbound': {
                        'observed_count': 12,
                        'status_counts': {'pending': 3, 'published_verified': 9},
                        'max_conversation_depth': 4,
                    },
                    'business_outcomes': {
                        'event_counts': {'enquiry': 3, 'signup': 2, 'sale': 1},
                        'revenue_minor_by_currency': {'GBP': 123456},
                    },
                }
                for i in range(h.MAX_AUDIENCE_EXPORT)
            ],
        }
        snapshot = h.make_snapshot(p, 'b' * 40, NOW)
        encoded = h.canonical(snapshot).encode()
        self.assertGreater(len(encoded), 120_000)
        self.assertLessEqual(len(encoded), h.MAX_BYTES)
        self.assertEqual(len(snapshot['publication_evidence']['records']), h.MAX_PUBLICATIONS)
        self.assertEqual(len(snapshot['audience_evidence']['records']), h.MAX_AUDIENCE_EXPORT)
        self.assertLess((h.MAX_BYTES * 4 // 3) + 4096, h.MAX_API_RESPONSE_BYTES)

    def test_oversize_envelope_reports_safe_section_sizes_without_content(self):
        p = pack()
        secret_marker = 'PRIVATE-CONTENT-MUST-NOT-CROSS-DIAGNOSTIC'
        row = deepcopy(p['supply_reviews'][0])
        row['aliases'] = [secret_marker + ('x' * 2000)]
        p['supply_reviews'] = [deepcopy(row) for _ in range(500)]
        with self.assertRaises(h.CaptureError) as caught:
            h.make_snapshot(p, 'b' * 40, NOW)
        self.assertEqual(str(caught.exception), 'complete_snapshot_exceeds_byte_bound')
        diagnostic = caught.exception.diagnostic
        self.assertEqual(diagnostic['stage'], 'final_envelope')
        self.assertGreater(diagnostic['output_bytes'], diagnostic['limit_bytes'])
        self.assertEqual(diagnostic['limit_bytes'], h.MAX_BYTES)
        self.assertEqual(diagnostic['supply_review_count'], 500)
        self.assertEqual(
            set(diagnostic['section_bytes']),
            {'supply_reviews', 'request_reconciliation', 'accounts', 'publication_evidence', 'audience_evidence'},
        )
        self.assertNotIn(secret_marker, json.dumps(diagnostic))

    def test_private_repository_required_before_writes(self):
        api = FakeApi(); api.repo['private'] = False
        with self.assertRaises(ValueError): h.publish_snapshot(self.snapshot(), api, NOW)
        self.assertFalse(any(c[0] == 'PUT' for c in api.calls))

    def test_pinned_repository_identity_required(self):
        api = FakeApi(); api.repo['id'] = 1
        with self.assertRaises(ValueError): h.publish_snapshot(self.snapshot(), api, NOW)

    def test_remote_marker_required(self):
        api = FakeApi(); api.data['kind'] = 'unrelated'
        with self.assertRaises(ValueError): h.publish_snapshot(self.snapshot(), api, NOW)
        self.assertFalse(any(c[0] == 'PUT' for c in api.calls))

    def test_remote_hash_checked(self):
        api = FakeApi(); api.data = self.snapshot(); api.data['route_count'] = 9
        with self.assertRaises(ValueError): h.publish_snapshot(self.snapshot(), api, NOW)

    def test_success_requires_matching_readback(self):
        api = FakeApi(); result = h.publish_snapshot(self.snapshot(), api, NOW)
        self.assertEqual(result['status'], 'observed')
        self.assertTrue(result['write_performed'])
        self.assertEqual(sum(c[0] == 'PUT' for c in api.calls), 1)
        self.assertEqual(api.calls[-1][0], 'GET')

    def test_same_snapshot_is_idempotent(self):
        api = FakeApi(); api.data = self.snapshot()
        result = h.publish_snapshot(self.snapshot(), api, NOW)
        self.assertFalse(result['write_performed'])

    def test_newer_remote_evidence_preserved(self):
        api = FakeApi(); api.data = self.snapshot()
        api.data['observed_at'] = (NOW + timedelta(seconds=1)).isoformat()
        api.data['snapshot_sha256'] = h.digest({k: v for k, v in api.data.items() if k != 'snapshot_sha256'})
        with self.assertRaises(ValueError): h.publish_snapshot(self.snapshot(), api, NOW + timedelta(seconds=2))
        self.assertFalse(any(c[0] == 'PUT' for c in api.calls))

    def test_compare_and_swap_conflict_is_not_retried(self):
        api = FakeApi(); api.failure = 'conflict'
        with self.assertRaisesRegex(ValueError, '409'): h.publish_snapshot(self.snapshot(), api, NOW)
        self.assertEqual(sum(c[0] == 'PUT' for c in api.calls), 1)

    def test_lost_write_response_reconciled_without_repeat(self):
        api = FakeApi(); api.failure = 'lost_response'
        self.assertEqual(h.publish_snapshot(self.snapshot(), api, NOW)['status'], 'observed')
        self.assertEqual(sum(c[0] == 'PUT' for c in api.calls), 1)

    def test_mismatched_readback_never_success(self):
        api = FakeApi(); api.failure = 'mismatch'
        with self.assertRaisesRegex(ValueError, 'readback_mismatch'): h.publish_snapshot(self.snapshot(), api, NOW)

    def test_redirects_refused(self):
        self.assertIsNone(h.NoRedirect().redirect_request(None, None, 302, None, None, 'https://other.invalid'))

    def test_unapproved_endpoint_rejected_without_network(self):
        with self.assertRaises(ValueError): h.Api('private', 0)('PUT', '/issues/1', {})

    def test_transport_failures_are_stage_classified_without_raw_error_or_token(self):
        cases = [
            (urllib.error.URLError(socket.gaierror(-2, 'PRIVATE-DNS')), 'dns'),
            (urllib.error.URLError(ssl.SSLError('PRIVATE-TLS')), 'tls'),
            (urllib.error.URLError(TimeoutError('PRIVATE-TIMEOUT')), 'timeout'),
            (urllib.error.URLError(ConnectionResetError('PRIVATE-RESET')), 'connection_reset'),
        ]
        for exc, category in cases:
            with self.subTest(category=category):
                api = h.Api('PRIVATE-TOKEN', h.time.monotonic() + 30)
                api.opener = SimpleNamespace(
                    open=lambda *a, exc=exc, **k: (_ for _ in ()).throw(exc)
                )
                with self.assertRaises(h.CaptureError) as caught:
                    api('GET', '')
                self.assertEqual(str(caught.exception), 'github_transport_unavailable')
                diagnostic = caught.exception.diagnostic
                self.assertEqual(diagnostic, {
                    'stage': 'repository_identity',
                    'method': 'GET',
                    'network_error': category,
                    'write_attempted': False,
                })
                rendered = json.dumps(diagnostic)
                self.assertNotIn('PRIVATE', rendered)
                self.assertNotIn('TOKEN', rendered)

    def test_write_transport_failure_marks_uncertain_write_and_readback_stage(self):
        api = h.Api('PRIVATE-TOKEN', h.time.monotonic() + 30)
        api.opener = SimpleNamespace(
            open=lambda *a, **k: (_ for _ in ()).throw(
                urllib.error.URLError(ConnectionResetError('PRIVATE-RESET'))
            )
        )
        with self.assertRaises(h.CaptureError) as caught:
            api('PUT', '/contents/' + h.PATH, {'ignored': True})
        self.assertEqual(caught.exception.diagnostic, {
            'stage': 'snapshot_write',
            'method': 'PUT',
            'network_error': 'connection_reset',
            'write_attempted': True,
        })

        with self.assertRaises(h.CaptureError) as readback:
            api('GET', '/contents/' + h.PATH + '?ref=' + h.BRANCH)
        self.assertEqual(readback.exception.diagnostic['stage'], 'snapshot_readback')
        self.assertTrue(readback.exception.diagnostic['write_attempted'])

    def test_http_errors_remain_protocol_errors_not_transport_retries(self):
        api = h.Api('PRIVATE-TOKEN', h.time.monotonic() + 30)
        api.opener = SimpleNamespace(
            open=lambda *a, **k: (_ for _ in ()).throw(
                urllib.error.HTTPError(
                    'https://api.github.com/', 403, 'PRIVATE', {}, None
                )
            )
        )
        with self.assertRaisesRegex(ValueError, 'github_http_403'):
            api('GET', '')

    def test_disabled_has_no_capture_network_or_state_write(self):
        state = ModuleType('ocpf_post.state')
        state.config_dir = state.state_dir = lambda: Path('/unused')
        state.ensure_private_dir = lambda p: self.fail('unexpected state creation')
        store = SimpleNamespace(read=lambda path: {}, write=lambda *a: self.fail('unexpected write'))
        package = ModuleType('ocpf_post'); package.local_store = store
        with patch.dict('sys.modules', {'ocpf_post': package, 'ocpf_post.state': state}), patch.object(h, 'capture', side_effect=AssertionError('capture')):
            result = h.run(SimpleNamespace(enable=False, disable=False, apply=True, status=False))
        self.assertEqual(result['status'], 'disabled')

    def _refill(self, refill_exit, telemetry_exit):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td); (root / 'scripts').mkdir(); (root / 'bin').mkdir()
            shutil.copyfile(ROOT / 'scripts/run-portfolio-refill', root / 'scripts/run-portfolio-refill')
            shutil.copyfile(ROOT / 'scripts/runtime-env', root / 'scripts/runtime-env')
            files = {
                'bin/git': '#!/bin/sh\nexit 0\n',
                'bin/python3': '#!/bin/sh\ncase "$1" in *reconcile-rolling-supply.py) echo controller >> "$LOG"; exit "$REFILL_EXIT";; *) exit 0;; esac\n',
                'bin/timeout': '#!/bin/sh\necho telemetry >> "$LOG"\nexit "$TELEMETRY_EXIT"\n',
                'poststeward': '#!/bin/sh\necho "$*" >> "$LOG"\nif [ "$1 $2" = "portfolio refill" ]; then exit "$REFILL_EXIT"; fi\n',
                'scripts/run-operating-cycle': '#!/bin/sh\necho report >> "$LOG"\nexit 0\n',
            }
            for name, text in files.items():
                target = root / name; target.write_text(text); target.chmod(0o700)
            env = {**os.environ, 'PATH': str(root / 'bin') + ':' + os.environ['PATH'],
                   'LOG': str(root / 'calls'), 'REFILL_EXIT': str(refill_exit), 'TELEMETRY_EXIT': str(telemetry_exit)}
            result = subprocess.run(['sh', str(root / 'scripts/run-portfolio-refill')], env=env, capture_output=True, timeout=3)
            self.assertEqual(result.returncode, refill_exit)
            self.assertEqual((root / 'calls').read_text().splitlines(), [
                'portfolio experiment reconcile --apply', 'controller', 'telemetry', 'report'])

    def test_telemetry_failure_cannot_change_successful_refill(self):
        self._refill(0, 2)

    def test_telemetry_timeout_preserves_failed_refill_status(self):
        self._refill(7, 124)


if __name__ == '__main__':
    unittest.main()
