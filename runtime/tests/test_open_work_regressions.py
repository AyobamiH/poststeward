"""Regressions for the four operating audit findings; no real provider calls."""
from datetime import datetime, timedelta, timezone
import copy
import json
import os
import sys
import tempfile
import unittest
from unittest.mock import patch
from ocpf_post import local_store, performance_review as review, performance_feedback as feedback
from ocpf_post import source_pipeline, source_observations, replenisher, operating_cycles, health
from ocpf_post.state import append_receipt
from ocpf_post.performance import append_snapshot
from ocpf_post.campaigns import builtin_manifest
from ocpf_post.portfolio_queue import _fair_choice
from ocpf_post.portfolio_diversity import DEFAULT_DIVERSITY

NOW = datetime(2026, 9, 1, 12, tzinfo=timezone.utc)


class OpenWorkTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        env = patch.dict(os.environ, OCPF_POST_STATE_DIR=self.temp.name,
                         OCPF_POST_CONFIG_DIR=self.temp.name + '/config')
        env.start(); self.addCleanup(env.stop)

    def publication(self):
        row = {'campaign': 'TEST-001', 'provider': 'x', 'account_id': '123',
               'post_id': '1', 'status': 'published_verified', 'recorded_at': NOW.isoformat()}
        append_receipt(row)
        return row

    def test_failed_capture_retries_in_original_window_then_success_deduplicates(self):
        row = self.publication()
        def fail(*args):
            raise ValueError('unavailable')
        first = review.capture_due(apply=True, now=NOW + timedelta(hours=22), capture_fn=fail)
        self.assertEqual(first['observations'][0]['result'], 'unavailable')
        self.assertFalse(review.capture_due(now=NOW + timedelta(hours=22, minutes=5))['observations'])
        self.assertEqual(review.capture_due(now=NOW + timedelta(hours=22, minutes=15))['observations'][0]['attempt'], 2)
        append_snapshot({**row, 'schema_version': 1, 'captured_at': (NOW + timedelta(hours=24)).isoformat(),
                         'availability': {'status': 'available'}, 'metrics': {}})
        self.assertFalse(review.capture_due(now=NOW + timedelta(hours=25))['observations'])
        self.assertFalse(review.capture_due(now=NOW + timedelta(hours=28))['missed_windows'])

    def test_retry_budget_provider_backoff_and_missed_window_survive_restart(self):
        row = self.publication()
        def snapshot(minutes, **availability):
            append_snapshot({**row, 'schema_version': 1,
                'captured_at': (NOW + timedelta(hours=22, minutes=minutes)).isoformat(),
                'availability': {'status': 'unavailable', **availability}, 'metrics': {}})
        snapshot(0, retry_after_seconds=3600)
        self.assertFalse(review.capture_due(now=NOW + timedelta(hours=22, minutes=30))['observations'])
        self.assertTrue(review.capture_due(now=NOW + timedelta(hours=23))['observations'])
        for minute in (60, 90, 120, 180):
            snapshot(minute)
        end = review.capture_due(now=NOW + timedelta(hours=26))
        self.assertFalse(end['observations'])
        self.assertEqual(end['deferred'][0]['result'], 'attempt_budget_exhausted')
        missed = review.capture_due(now=NOW + timedelta(hours=28))
        self.assertEqual(missed['missed_windows'][0]['attempts'], 5)

    def test_real_subprocess_stage_distinguishes_provider_outcomes(self):
        for payload, expected in [
            ({'observations': [{'result': 'unavailable'}]}, 'attention'),
            ({'polls': {'threads': {'status': 'unavailable'}}}, 'attention'),
            ({'observations': [{'result': 'available'}, {'result': 'unavailable'}]}, 'partial'),
            ({'polls': {'threads': {'status': 'partial'}}}, 'partial'),
            ({'observations': []}, 'idle'),
            ({'status': 'insufficient_evidence'}, 'insufficient_evidence')]:
            row = operating_cycles.stage('probe', [sys.executable, '-c', 'print(' + repr(json.dumps(payload)) + ')'], 3)
            self.assertEqual(row['exit_code'], 0)
            self.assertEqual(row['status'], expected)
        row = operating_cycles.stage('probe', [sys.executable, '-c', "print('private malformed output')"], 3)
        self.assertEqual(row['status'], 'unknown')
        self.assertNotIn('private malformed', json.dumps(row))

    def test_reply_policy_controls_units_and_staleness(self):
        from ocpf_post.reply_worker import DEFAULT
        for enabled in (True, False):
            local_store.write(health.config_dir() / 'reply-worker-policy.json', {**DEFAULT, 'enabled': enabled})
            local_store.write(health.state_dir() / 'reply-worker.json', {
                'schema_version': 1, 'items': {}, 'opt_outs': {}, 'usage': {},
                'last_cycle': {'status': 'completed', 'observed_at': (NOW - timedelta(hours=1)).isoformat()}})
            def unit(name):
                return {'available': True, 'LoadState': 'loaded', 'Result': 'success',
                        'ActiveState': 'inactive' if name == 'ocpf-post-replies.timer' else 'active'}
            with patch.object(health, 'timer_state', side_effect=unit):
                result = health.report(now=NOW)
            self.assertEqual('ocpf-post-replies.timer' in result['units'], enabled)
            codes = {f['code'] for f in result['findings']}
            self.assertEqual('reply_worker_stale' in codes, enabled)
            self.assertEqual(any(f.get('unit') == 'ocpf-post-replies.timer' for f in result['findings']), enabled)

    def test_active_catalogue_supplies_pairs_and_changes_selection(self):
        profiles = copy.deepcopy(replenisher.source_profiles()['projects'])
        synthetic_sha = 'a' * 40
        expected_first = sum(
            1
            for profile in profiles.values()
            for item in profile.get('inventory', [])
            if not item.get('source_sha') or item.get('source_sha') == synthetic_sha
        )
        # Use real approved inventory, isolated test destinations and observations.
        for name, profile in profiles.items():
            profile.update(project=name, providers=['x'], destinations={'x': 'x-founder'})
        state = {'schema_version': 1, 'projects': {name: {
            'readme_sha': synthetic_sha, 'readme_observed_at': NOW.isoformat(), 'observed_at': NOW.isoformat(),
            'profile_sha256': source_observations.fingerprint(profile), 'status': 'observed',
            'source_ok': True, 'pending': []} for name, profile in profiles.items()}}
        local_store.write(source_observations.path(), state)
        identity = lambda *a, **kw: {'account_id': '123', 'provider': 'x', 'label': 'test'}
        with patch('ocpf_post.portfolio_source_loader.merged_source_profiles', return_value={'projects': profiles}), \
             patch('ocpf_post.registry.resolve_account', side_effect=identity), \
             patch('ocpf_post.campaigns.resolve_account', side_effect=identity), \
             patch('ocpf_post.portfolio.delivery_candidates', return_value=[]):
            first = source_pipeline.refresh(apply=True, now=NOW)
            self.assertEqual(len(first['static_campaigns']), expected_first)
            self.assertTrue(all(builtin_manifest(r['campaign'])['source']['source_id'].endswith('-insight')
                                for r in first['static_campaigns']))
            records = []; next_id = 1000
            def publish(items, when):
                nonlocal next_id
                for item in items:
                    manifest = builtin_manifest(item['campaign'])
                    receipt = {'campaign': item['campaign'], 'provider': 'x', 'account_id': '123',
                        'post_id': str(next_id), 'status': 'published_verified', 'readback_verified': True,
                        'text_sha256': manifest['payload_sha256']['x'], 'recorded_at': when.isoformat()}
                    next_id += 1; append_receipt(receipt)
                    meta = feedback.metadata(manifest, receipt)
                    append_snapshot({**receipt, 'schema_version': 1,
                        'captured_at': (when + timedelta(hours=24)).isoformat(), 'editorial': meta,
                        'availability': {'status': 'available'}, 'metrics': {'impressions': 1000,
                            'likes': 40 if meta['variant'] == 'question' else 10, 'reposts': 1, 'quotes': 0}})
                    records.append((manifest, receipt))
            publish(first['static_campaigns'], NOW)
            def refresh(when):
                state = source_observations.load()
                for obs in state['projects'].values():
                    obs['observed_at'] = when.isoformat()
                local_store.write(source_observations.path(), state)
                return source_pipeline.refresh(apply=True, now=when)
            self.assertFalse(refresh(NOW + timedelta(hours=47))['static_campaigns'])
            feedback.build(apply=True, now=NOW + timedelta(hours=48))
            second = refresh(NOW + timedelta(hours=48))
            self.assertTrue(second['static_campaigns'])
            scopes = [(builtin_manifest(r['campaign'])['project'], r['providers'][0]) for r in second['static_campaigns']]
            self.assertEqual(len(scopes), len(set(scopes)))
            self.assertFalse(refresh(NOW + timedelta(hours=48))['static_campaigns'])
            publish(second['static_campaigns'], NOW + timedelta(hours=48))
            third = refresh(NOW + timedelta(hours=72))
            publish(third['static_campaigns'], NOW + timedelta(hours=72))
            later = NOW + timedelta(hours=97)
            report = feedback.build(apply=True, now=later)
            self.assertEqual(report['status'], 'signals_available')
            manifest, receipt = next((m, r) for m, r in records if
                m['source']['source_id'].endswith('-question') and
                feedback.key('x', '123', feedback.metadata(m, r)) in report['signals'])
            candidate = {'campaign': 'A-preferred', 'provider': 'x', 'account_id': '123',
                'text_sha256': receipt['text_sha256'], 'project': manifest['project'], 'priority': 70,
                'lane': manifest['allocation']['lane'], 'prepared_at': later.isoformat(),
                'expires_at': None, 'family': manifest['project'], 'topic_key': 'unsent-topic'}
            candidate.update(feedback.candidate_preference(candidate, manifest, report['signals'], later))
            other = {**candidate, 'campaign': 'Z-alternative', 'performance_boost': 0}
            self.assertEqual(_fair_choice([{**candidate, 'performance_boost': 0}, other], [], later, DEFAULT_DIVERSITY)['campaign'], 'Z-alternative')
            self.assertEqual(_fair_choice([candidate, other], [], later, DEFAULT_DIVERSITY)['campaign'], 'A-preferred')
            # Old approved payloads remain frozen; sampling never renews source expiry.
            for m, r in records:
                self.assertEqual(builtin_manifest(r['campaign'])['payload_sha256'], m['payload_sha256'])
            feedback.configure(False)
            self.assertFalse(refresh(NOW + timedelta(hours=120))['static_campaigns'])