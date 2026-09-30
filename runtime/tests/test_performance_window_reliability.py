from datetime import datetime, timedelta, timezone
import os
import tempfile
import unittest
from unittest.mock import patch

from ocpf_post import local_store
from ocpf_post import performance_feedback as feedback
from ocpf_post import performance_windows as windows

NOW = datetime(2026, 9, 13, 12, tzinfo=timezone.utc)


def publication(campaign, *, provider='x', account='123', post_id=None, hours=24):
    post_id = post_id or campaign.replace('-', '')[-12:]
    at = NOW - timedelta(hours=hours)
    receipt = {'campaign': campaign, 'provider': provider, 'account_id': account,
               'post_id': post_id, 'status': 'published_verified', 'readback_verified': True,
               'text_sha256': 'sha-' + campaign, 'recorded_at': at.isoformat()}
    return (campaign, provider, account, post_id), {'receipt': receipt, 'at': at}


class PerformanceWindowReliabilityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        env = patch.dict(os.environ, OCPF_POST_STATE_DIR=self.temp.name,
                         OCPF_POST_CONFIG_DIR=self.temp.name + '/config')
        env.start(); self.addCleanup(env.stop)

    def test_rollout_keeps_legacy_miss_but_does_not_invent_closed_fallback_misses(self):
        key, pub = publication('TEST-OLD', hours=100)
        with patch.object(windows, 'publications', return_value={key: pub}):
            report = windows.capture_sweep(apply=True, now=NOW,
                capture_fn=lambda *a, **k: self.fail('closed fallback window must not be queried'))
        self.assertEqual(report['historical_missed_by_target'], {'24': 1})
        self.assertEqual(report['window_coverage']['72']['enrolled'], 0)
        self.assertEqual(report['window_coverage']['168']['future'], 1)
        state = local_store.read(windows.path())
        self.assertEqual(len(state['historical_missed_windows']), 1)
        self.assertFalse(state['missed_windows'])

    def test_missed_24h_can_be_measured_truthfully_at_72h_without_fake_backfill(self):
        key, pub = publication('TEST-LATE', hours=70)
        calls = []
        def capture_fn(campaign, provider, **kwargs):
            calls.append(kwargs['target_age_hours'])
            return {**dict(zip(('campaign','provider','account_id','post_id'), key)),
                    'captured_at': NOW.isoformat(), 'capture_attempt_id': kwargs['attempt_id'],
                    'target_age_hours': kwargs['target_age_hours'],
                    'availability': {'status': 'available'}, 'metrics': {'impressions': 1000}}
        with patch.object(windows, 'publications', return_value={key: pub}):
            report = windows.capture_sweep(apply=True, now=NOW, capture_fn=capture_fn)
        self.assertEqual(calls, [72])
        self.assertEqual(report['observations'][0]['target_age_hours'], 72)
        self.assertEqual(report['observations'][0]['result'], 'available')
        self.assertEqual(report['historical_missed_by_target'], {'24': 1})
        self.assertEqual(report['window_coverage']['72']['successful'], 1)
        self.assertFalse(any(row['target_age_hours'] == 24 for row in report['observations']))

    def test_same_sweep_coverage_accepts_completion_after_sweep_start_when_still_in_window(self):
        key, pub = publication('TEST-EVENT-TIME', hours=72)
        def capture_fn(campaign, provider, **kwargs):
            return {**dict(zip(('campaign','provider','account_id','post_id'), key)),
                    'captured_at': (NOW + timedelta(seconds=8)).isoformat(),
                    'capture_attempt_id': kwargs['attempt_id'],
                    'target_age_hours': kwargs['target_age_hours'],
                    'availability': {'status': 'available'}, 'metrics': {'impressions': 1000}}
        with patch.object(windows, 'publications', return_value={key: pub}):
            report = windows.capture_sweep(apply=True, now=NOW, capture_fn=capture_fn)
        self.assertEqual(report['observations'][0]['result'], 'available')
        self.assertEqual(report['window_coverage']['72']['successful'], 1)
        self.assertEqual(report['window_coverage']['72']['due'], 0)

    def test_target_label_cannot_override_actual_capture_time_outside_window(self):
        key, pub = publication('TEST-OUTSIDE', hours=72)
        def capture_fn(campaign, provider, **kwargs):
            return {**dict(zip(('campaign','provider','account_id','post_id'), key)),
                    'captured_at': (NOW + timedelta(hours=2, seconds=1)).isoformat(),
                    'capture_attempt_id': kwargs['attempt_id'],
                    'target_age_hours': kwargs['target_age_hours'],
                    'availability': {'status': 'available'}, 'metrics': {'impressions': 1000}}
        with patch.object(windows, 'publications', return_value={key: pub}):
            report = windows.capture_sweep(apply=True, now=NOW, capture_fn=capture_fn)
        self.assertEqual(report['observations'][0]['result'], 'available_outside_window')
        self.assertEqual(report['window_coverage']['72']['successful'], 0)
        self.assertEqual(report['window_coverage']['72']['due'], 1)

    def test_unsupported_linkedin_is_excluded_not_retried_or_counted_as_zero(self):
        key, pub = publication('TEST-LI', provider='linkedin', account='urn:li:person:owner', hours=24)
        with patch.object(windows, 'publications', return_value={key: pub}):
            report = windows.capture_sweep(apply=True, now=NOW,
                capture_fn=lambda *a, **k: self.fail('unsupported analytics must not be queried'))
        self.assertEqual(report['unsupported_publications_by_provider'], {'linkedin': 1})
        self.assertEqual(report['observations'], [])
        self.assertEqual(report['missed_windows'], [])
        self.assertEqual(report['historical_missed_window_count'], 0)

    def test_sweep_is_bounded_and_serves_earliest_closing_windows_first(self):
        pubs = {}
        expected = []
        for i in range(25):
            campaign = f'TEST-{i:02d}'
            key, pub = publication(campaign, post_id=str(1000+i), hours=23 + i / 100)
            pubs[key] = pub
            expected.append((pub['at'], campaign))
        calls = []
        def capture_fn(campaign, provider, **kwargs):
            calls.append(campaign)
            key = next(key for key in pubs if key[0] == campaign)
            return {**dict(zip(('campaign','provider','account_id','post_id'), key)),
                    'captured_at': NOW.isoformat(), 'capture_attempt_id': kwargs['attempt_id'],
                    'target_age_hours': kwargs['target_age_hours'],
                    'availability': {'status': 'available'}, 'metrics': {}}
        with patch.object(windows, 'publications', return_value=pubs):
            report = windows.capture_sweep(apply=True, now=NOW, capture_fn=capture_fn)
        wanted = [campaign for _, campaign in sorted(expected)][:windows.MAX_READS]
        self.assertEqual(calls, wanted)
        self.assertEqual(report['reads_attempted'], windows.MAX_READS)
        self.assertEqual(sum(row.get('result') == 'cycle_budget' for row in report['deferred']), 5)

    def test_nonblocking_capture_lock_prevents_duplicate_sweeps(self):
        key, pub = publication('TEST-LOCK', hours=24)
        with patch.object(windows, 'publications', return_value={key: pub}):
            with local_store.locked(windows.path()):
                report = windows.capture_sweep(apply=True, now=NOW,
                    capture_fn=lambda *a, **k: self.fail('busy sweep must make no provider call'))
        self.assertEqual(report['status'], 'busy')
        self.assertEqual(report['deferred'], [{'result': 'capture_busy'}])

    def test_provider_5xx_opens_durable_account_circuit_breaker(self):
        pubs = dict([publication('TEST-A', post_id='1', hours=24),
                     publication('TEST-B', post_id='2', hours=24)])
        calls = []
        def capture_fn(campaign, provider, **kwargs):
            calls.append(campaign)
            key = next(key for key in pubs if key[0] == campaign)
            return {**dict(zip(('campaign','provider','account_id','post_id'), key)),
                    'captured_at': NOW.isoformat(), 'capture_attempt_id': kwargs['attempt_id'],
                    'target_age_hours': kwargs['target_age_hours'],
                    'availability': {'status': 'unavailable', 'http_status': 500}, 'metrics': {}}
        with patch.object(windows, 'publications', return_value=pubs):
            first = windows.capture_sweep(apply=True, now=NOW, capture_fn=capture_fn)
            second = windows.capture_sweep(apply=True, now=NOW + timedelta(minutes=10), capture_fn=capture_fn)
        self.assertEqual(len(calls), 1)
        self.assertEqual(first['reads_attempted'], 1)
        self.assertTrue(any(row.get('result') == 'account_backoff' for row in first['deferred']))
        self.assertEqual(second['reads_attempted'], 0)
        self.assertTrue(all(row.get('result') in {'account_backoff', 'retry_deferred'} for row in second['deferred']))
        state = local_store.read(windows.path())
        self.assertIn('x:123', state['account_backoff'])


class PerformanceFeedbackAgeCohortTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        env = patch.dict(os.environ, OCPF_POST_STATE_DIR=self.temp.name,
                         OCPF_POST_CONFIG_DIR=self.temp.name + '/config')
        env.start(); self.addCleanup(env.stop)

    def fixtures(self, *, conflict=False):
        pubs = {}; rows = []
        for variant in ('insight', 'question'):
            for i in range(5):
                campaign = f'TEST-{variant.upper()}-{i}'; post_id = f'{variant[0]}{i}'
                published = NOW - timedelta(hours=72)
                receipt = {'campaign': campaign, 'provider': 'threads', 'account_id': '123',
                           'post_id': post_id, 'status': 'published_verified',
                           'text_sha256': post_id, 'recorded_at': published.isoformat()}
                pubs[(campaign, 'threads', '123', post_id)] = {'receipt': receipt, 'at': published}
                editorial = {'project': 'sample', 'lane': 'evergreen', 'variant': variant,
                             'revision': 'abc', 'topic': str(i), 'text_sha256': post_id,
                             'template_version': feedback.TEMPLATE_VERSION}
                late_likes = 30 if variant == 'question' else 10
                rows.append({**receipt, 'captured_at': NOW.isoformat(), 'target_age_hours': 72,
                             'availability': {'status': 'available'},
                             'metrics': {'views': 1000, 'likes': late_likes, 'reposts': 1, 'quotes': 0},
                             'editorial': editorial})
                if conflict:
                    early_likes = 30 if variant == 'insight' else 10
                    rows.append({**receipt, 'captured_at': (published + timedelta(hours=24)).isoformat(),
                                 'target_age_hours': 24, 'availability': {'status': 'available'},
                                 'metrics': {'views': 1000, 'likes': early_likes, 'reposts': 1, 'quotes': 0},
                                 'editorial': editorial})
        return pubs, rows

    def build(self, pubs, rows):
        with patch('ocpf_post.performance_review.publications', return_value=pubs), \
             patch('ocpf_post.performance.iter_snapshots', return_value=rows):
            return feedback.build(now=NOW)

    def test_72h_cohort_can_supply_preference_without_any_24h_snapshot(self):
        pubs, rows = self.fixtures()
        report = self.build(pubs, rows)
        self.assertEqual(report['status'], 'signals_available')
        signal = next(iter(report['signals'].values()))
        self.assertEqual(signal['variant'], 'question')
        self.assertEqual(signal['target_age_hours'], 72)
        self.assertEqual(signal['supporting_target_ages'], [72])
        self.assertTrue(all(row['target_age_hours'] == 72 for row in report['observations']))

    def test_conflicting_24h_and_72h_winners_fail_closed(self):
        pubs, rows = self.fixtures(conflict=True)
        report = self.build(pubs, rows)
        self.assertEqual(report['signals'], {})
        self.assertTrue(report['age_conflicts'])
        self.assertEqual(report['age_conflicts'][0]['target_ages'], [24, 72])
        self.assertEqual({c['status'] for c in report['cohorts']}, {'cross_age_conflict'})


if __name__ == '__main__':
    unittest.main()
