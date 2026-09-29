from __future__ import annotations

import copy
from datetime import datetime, timedelta, timezone
import importlib.util
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('coverage_audit', ROOT / 'scripts/audit-portfolio-coverage.py')
audit = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(audit)
NOW = datetime(2026, 9, 14, 17, tzinfo=timezone.utc)


def fixture():
    return {'registry': {'projects': {
        'alpha': {'accounts': {'x': {'provider': 'x', 'account_id': '1'},
                               'threads': {'provider': 'threads', 'account_id': '2'}}},
        'empty': {'accounts': {}}}},
        'sources': {}, 'source_observations': {}, 'manifests': {}, 'vaults': [],
        'publications': [], 'snapshots': [], 'feedback': {}, 'candidates': [], 'exclusions': [],
        'schedules': [], 'target_ages': (24, 72, 168), 'tolerance_hours': 2,
        'window_state': {'activated_at': (NOW-timedelta(days=1)).isoformat()},
        'supported_metric_providers': {'x', 'threads'}, 'active_statuses': {'scheduled', 'executing'},
        'collection_cycle': {}}


def add_publication(data, *, cid='A', provider='x', account='1', age=24, verified=True):
    data['manifests'][cid] = {'project': 'alpha', 'allocation_enabled': True,
                              'bindings': [{'provider': provider, 'account_id': account}]}
    row = {'campaign': cid, 'provider': provider, 'account_id': account, 'post_id': 'post-'+cid,
           'published_at': (NOW-timedelta(hours=age)).isoformat(), 'verified': verified,
           'text_sha256': 'hash', 'verification_basis': 'receipt'}
    data['publications'].append(row)
    return row


def snapshot(publication, *, status='available', target=24, exposure=10):
    return {**{k: publication[k] for k in audit.FIELDS}, 'captured_at': NOW.isoformat(),
            'availability': {'status': status}, 'comparable_target': target,
            'metrics': {'impressions': exposure, 'likes': 0}, 'editorial': None}


def alpha(result):
    return next(p for p in result['projects'] if p['project'] == 'alpha')


class CoverageAuditTests(unittest.TestCase):
    def test_includes_registered_project_with_no_accounts_or_work(self):
        result = audit.build(fixture(), NOW)
        self.assertEqual(result['registered_projects'], 2)
        self.assertEqual(next(p for p in result['projects'] if p['project'] == 'empty')['routes'], [])

    def test_account_routes_never_share_success(self):
        data = fixture(); add_publication(data)
        result = alpha(audit.build(data, NOW))
        self.assertEqual({r['provider']: r['verified_effects_7d'] for r in result['routes']}, {'x': 1, 'threads': 0})

    def test_unverified_effect_is_not_published_verified(self):
        data = fixture(); add_publication(data, verified=False)
        x = next(r for r in alpha(audit.build(data, NOW))['routes'] if r['provider'] == 'x')
        self.assertEqual(x['unverified_effects_total'], 1)
        self.assertEqual(x['verified_effects_total'], 0)

    def test_exact_sidecar_projection_is_accepted_without_ledger_rewrite(self):
        data = fixture(); pub = add_publication(data)
        pub.update(verification_basis='readback', ledger_status='published_unverified')
        before = copy.deepcopy(data)
        x = next(r for r in alpha(audit.build(data, NOW))['routes'] if r['provider'] == 'x')
        self.assertEqual(x['verified_effects_total'], 1)
        self.assertEqual(x['latest_verified_effect']['ledger_status'], 'published_unverified')
        self.assertEqual(data, before)

    def test_numeric_metrics_are_not_qualifying_learning(self):
        data = fixture(); pub = add_publication(data)
        data['snapshots'] = [snapshot(pub, exposure=10)]
        x = next(r for r in alpha(audit.build(data, NOW))['routes'] if r['provider'] == 'x')
        self.assertEqual(x['effects_with_available_numeric_metrics'], 1)
        self.assertEqual(x['fresh_saved_feedback_observations'], 0)
        self.assertEqual(x['window_counts']['24']['captured_available'], 1)

    def test_attempt_marker_is_not_a_success(self):
        data = fixture(); pub = add_publication(data)
        data['snapshots'] = [snapshot(pub, status='attempt_started')]
        x = next(r for r in alpha(audit.build(data, NOW))['routes'] if r['provider'] == 'x')
        self.assertEqual(x['effects_with_available_numeric_metrics'], 0)
        self.assertEqual(x['window_counts']['24']['open_without_available_capture'], 1)

    def test_future_window_is_not_missed(self):
        data = fixture(); add_publication(data, age=3)
        x = next(r for r in alpha(audit.build(data, NOW))['routes'] if r['provider'] == 'x')
        self.assertEqual(x['window_counts']['24'], {'not_due_yet': 1})
        self.assertNotIn('measurement_miss_since_activation', x['inspection_flags'])

    def test_historical_24h_debt_not_recast_as_new_failure(self):
        data = fixture(); add_publication(data, age=100)
        x = next(r for r in alpha(audit.build(data, NOW))['routes'] if r['provider'] == 'x')
        self.assertEqual(x['window_counts']['24'], {'historical_24h_miss': 1})
        self.assertEqual(x['window_counts']['72'], {})

    def test_new_missed_window_is_visible(self):
        data = fixture(); add_publication(data, age=28)
        x = next(r for r in alpha(audit.build(data, NOW))['routes'] if r['provider'] == 'x')
        self.assertEqual(x['window_counts']['24'], {'missed_since_activation': 1})

    def test_unsupported_linkedin_is_not_missing_metrics(self):
        data = fixture()
        data['registry']['projects']['alpha']['accounts']['linkedin'] = {'provider': 'linkedin', 'account_id': '3'}
        add_publication(data, provider='linkedin', account='3', age=100)
        r = next(r for r in alpha(audit.build(data, NOW))['routes'] if r['provider'] == 'linkedin')
        self.assertEqual(r['metrics_support'], 'unsupported')
        self.assertFalse(any(r['window_counts'].values()))
        self.assertNotIn('measurement_miss_since_activation', r['inspection_flags'])

    def test_unmatched_metric_identity_is_not_counted(self):
        data = fixture(); pub = add_publication(data)
        data['snapshots'] = [{**snapshot(pub), 'account_id': 'WRONG'}]
        result = audit.build(data, NOW)
        self.assertTrue(any(f['code'] == 'unmatched_metric_identity' for f in result['findings']))
        self.assertEqual(sum(r['effects_with_available_numeric_metrics'] for r in alpha(result)['routes']), 0)

    def test_multiple_campaign_claims_fail_closed(self):
        data = fixture(); first = add_publication(data); second = add_publication(data, cid='B')
        second['post_id'] = first['post_id']
        result = audit.build(data, NOW)
        self.assertEqual(result['projects_with_verified_publication_7d'], 0)
        self.assertTrue(any(f['code'] == 'post_claimed_by_multiple_campaigns' for f in result['findings']))

    def test_source_fingerprint_change_and_stale_source_are_visible(self):
        data = fixture()
        data['sources']['alpha'] = {'bindings': [], 'fingerprint': 'new', 'inventory_items': 2}
        data['source_observations']['alpha'] = {'status': 'observed', 'profile_sha256': 'old', 'source_ok': True, 'observed_at': NOW.isoformat()}
        self.assertEqual(alpha(audit.build(data, NOW))['source']['status'], 'profile_changed_review_required')
        data['source_observations']['alpha'].update(profile_sha256='new', observed_at=(NOW-timedelta(hours=2)).isoformat())
        self.assertEqual(alpha(audit.build(data, NOW))['source']['status'], 'stale_or_future_observation')

    def test_saved_feedback_freshness_and_exact_hash(self):
        data = fixture(); pub = add_publication(data)
        obs = {**snapshot(pub), 'editorial': {'project': 'alpha', 'text_sha256': 'hash'}}
        data['feedback'] = {'observed_at': (NOW-timedelta(hours=25)).isoformat(), 'observations': [obs]}
        x = next(r for r in alpha(audit.build(data, NOW))['routes'] if r['provider'] == 'x')
        self.assertEqual(x['saved_feedback_observations'], 1)
        self.assertEqual(x['fresh_saved_feedback_observations'], 0)
        obs['editorial']['text_sha256'] = 'wrong'
        result = audit.build(data, NOW)
        self.assertTrue(any(f['code'] == 'unmatched_saved_feedback' for f in result['findings']))

    def test_disabled_vault_does_not_enable_a_route(self):
        data = fixture(); data['vaults'] = [{'project': 'alpha', 'id': 'v', 'enabled': False, 'observation': {}, 'bindings': [{'provider': 'x', 'account_id': '1'}]}]
        result = alpha(audit.build(data, NOW))
        self.assertEqual(result['vaults'][0]['status'], 'disabled')
        self.assertTrue(all(not r['publishing_intent'] for r in result['routes']))

    def test_available_but_all_missing_metrics_not_called_numeric(self):
        data = fixture(); pub = add_publication(data)
        data['snapshots'] = [{**snapshot(pub), 'metrics': {'likes': None, 'impressions': None}}]
        x = next(r for r in alpha(audit.build(data, NOW))['routes'] if r['provider'] == 'x')
        self.assertEqual(x['effects_with_available_numeric_metrics'], 0)

    def test_ambiguous_direct_receipt_remains_visible_without_post_id(self):
        data = fixture(); pub = add_publication(data); data['publications'] = []
        data['receipt_uncertainties'] = [{**pub, 'status': 'ambiguous_effect', 'post_id': None}]
        x = next(r for r in alpha(audit.build(data, NOW))['routes'] if r['provider'] == 'x')
        self.assertIn('uncertain_receipt_no_blind_retry', x['inspection_flags'])
        self.assertEqual(x['verified_effects_total'], 0)

    @unittest.skipUnless((ROOT / 'src/ocpf_post').is_dir(), 'Full repository integration test runs in CI')
    def test_isolated_runtime_audit_never_connects_to_network_or_writes_state(self):
        with tempfile.TemporaryDirectory() as temp:
            config = Path(temp) / 'config'; state = Path(temp) / 'state'
            with patch.dict(os.environ, {'OCPF_POST_CONFIG_DIR': str(config), 'OCPF_POST_STATE_DIR': str(state)}):
                with patch('socket.socket.connect', side_effect=AssertionError('Network forbidden')):
                    result = audit.collect(NOW)
            self.assertEqual(result['status'], 'observed')
            self.assertGreaterEqual(result['registered_projects'], 19)
            self.assertFalse(config.exists())
            self.assertFalse(state.exists())


if __name__ == '__main__':
    unittest.main()
