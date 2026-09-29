from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from ocpf_post import capacity_experiment as trial, portfolio, local_store
from ocpf_post.portfolio_queue import fair_plan


class CapacityExperimentTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(); self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        env = patch.dict(os.environ, {'OCPF_POST_CONFIG_DIR': str(self.root/'config'), 'OCPF_POST_STATE_DIR': str(self.root/'state')})
        env.start(); self.addCleanup(env.stop)
        self.now = datetime(2026, 9, 10, 12, tzinfo=timezone.utc)
        self.policy = deepcopy(portfolio.DEFAULT_POLICY); self.policy['selection'] = 'fair'
        # Exercise the historical 20->24 trial in its original fixed-capacity
        # mode; admission flow supersedes this trial in production.
        for provider, row in self.policy['providers'].items():
            row['flow_mode'] = 'fixed'
            row['hard_daily_ceiling'] = int(row['daily_target'])
        portfolio.write_private_json(portfolio.policy_file(), self.policy)
        self.receipts = []; self.schedules = []
        for name in ('iter_receipts', 'schedule_records'):
            p = patch.object(trial, name, side_effect=lambda n=name: self.receipts if n == 'iter_receipts' else self.schedules)
            p.start(); self.addCleanup(p.stop)
        self.success('x', '1')

    def success(self, provider, suffix):
        row = {'campaign': 'TEST-' + suffix, 'provider': provider, 'account_id': trial.ACCOUNTS[provider],
               'schedule_id': 'sch-' + provider + suffix, 'post_id': suffix, 'text_sha256': 'a'*64,
               'status': 'published_verified', 'readback_verified': True,
               'recorded_at': trial.stamp(self.now-timedelta(minutes=30)), 'updated_at': trial.stamp(self.now-timedelta(minutes=30))}
        self.receipts.append(dict(row)); self.schedules.append(dict(row))

    def start(self):
        return trial.reconcile(apply=True, now=self.now)

    def test_scoped_activation_overlay_and_raw_policy_preserved(self):
        before = portfolio.policy_file().read_bytes()
        result = self.start()
        self.assertEqual(result['effective_targets'], {'x': 24, 'threads': 20, 'linkedin': 6})
        self.assertEqual(before, portfolio.policy_file().read_bytes())
        self.assertEqual(trial.overlay(self.policy, now=self.now)['providers']['x']['minimum_spacing_minutes'], 30)
        again = trial.reconcile(apply=True, now=self.now+timedelta(minutes=5))
        self.assertEqual(result['started_at'], again['started_at'])
        self.assertEqual(len(trial.read()['history']), 1)

    def test_no_start_without_owner_policy_receipts_or_matching_binding(self):
        self.receipts.clear()
        self.assertEqual(self.start()['status'], 'not_started')
        self.assertFalse(trial.path().exists())
        self.success('x', '1'); self.policy['providers']['x']['daily_target'] = 22
        portfolio.write_private_json(portfolio.policy_file(), self.policy)
        self.assertEqual(self.start()['reason'], 'baseline_targets_changed')

    def test_threads_three_unique_matched_receipts_gate_and_new_failure_fallback(self):
        self.success('threads', '10')
        self.receipts += [dict(self.receipts[-1])]*4
        self.assertFalse(trial.delivery_gate('threads', self.now)['healthy'])
        self.success('threads', '11'); self.success('threads', '12')
        self.assertEqual(self.start()['effective_targets']['threads'], 24)
        self.schedules.append({'provider': 'threads', 'account_id': trial.ACCOUNTS['threads'],
                              'status': 'failed', 'updated_at': trial.stamp(self.now)})
        result = trial.reconcile(apply=True, now=self.now+timedelta(minutes=15))
        self.assertEqual(result['effective_targets']['threads'], 20)
        self.assertEqual(result['effective_targets']['x'], 24)

    def test_no_success_from_wrong_account_hash_or_unverified_receipt(self):
        for field, wrong in [('account_id', 'wrong'), ('text_sha256', 'b'*64), ('post_id', 'wrong')]:
            with self.subTest(field=field):
                original = self.receipts[0][field]; self.receipts[0][field] = wrong
                self.assertFalse(trial.delivery_gate('x', self.now)['healthy'])
                self.receipts[0][field] = original
        self.receipts[0]['status'] = 'published_unverified'
        self.assertFalse(trial.delivery_gate('x', self.now)['healthy'])

    def test_actual_scheduler_block_statuses_are_identified_without_provider_prose(self):
        for status in ('drift_blocked', 'duplicate_blocked', 'failed', 'executing'):
            with self.subTest(status=status):
                row = {'provider': 'x', 'account_id': trial.ACCOUNTS['x'], 'campaign': 'TEST-BLOCK',
                       'schedule_id': 'sch-block', 'status': status, 'updated_at': trial.stamp(self.now),
                       'detail': 'sensitive provider response', 'text': 'private copy'}
                result = trial.delivery_gate('x', self.now, receipts=self.receipts, schedules=[*self.schedules, row])
                self.assertFalse(result['healthy'])
                blocker = result['blockers'][0]
                self.assertEqual(blocker['schedule_id'], 'sch-block')
                self.assertEqual(blocker['leaves_24h_window_after'], trial.stamp(self.now+timedelta(days=1)))
                self.assertFalse(blocker['automatic_retry'])
                self.assertNotIn('detail', blocker); self.assertNotIn('text', blocker)

    def test_blocker_account_window_and_malformed_time_boundaries(self):
        row = {'provider': 'x', 'account_id': trial.ACCOUNTS['x'], 'status': 'failed',
               'updated_at': trial.stamp(self.now-timedelta(days=1)), 'schedule_id': 'sch-old'}
        def gate(value, now=self.now):
            return trial.delivery_gate('x', now, receipts=self.receipts, schedules=[*self.schedules, value])
        self.assertEqual(len(gate(row)['blockers']), 1)
        self.assertEqual(gate(row, self.now+timedelta(microseconds=1))['blockers'], [])
        self.assertEqual(gate({**row, 'account_id': 'other'})['blockers'], [])
        malformed = gate({**row, 'updated_at': 'broken'})['blockers'][0]
        self.assertIsNone(malformed['leaves_24h_window_after'])

    def test_ambiguous_receipt_is_explained_without_authorizing_resend(self):
        receipt = {'provider': 'x', 'account_id': trial.ACCOUNTS['x'], 'status': 'ambiguous_effect',
                   'recorded_at': trial.stamp(self.now), 'campaign': 'TEST-UNKNOWN'}
        result = trial.delivery_gate('x', self.now, receipts=[*self.receipts, receipt], schedules=self.schedules)
        self.assertFalse(result['healthy'])
        self.assertEqual(result['blockers'][0]['next_action'], 'reconcile_effect_without_resend')

    def test_expiry_staleness_stop_and_policy_changes_never_extend_trial(self):
        self.start(); data = trial.read(); end = trial.at(data['ends_at'])
        self.assertEqual(trial.overlay(self.policy, now=self.now+timedelta(minutes=45))['providers']['x']['daily_target'], 20)
        self.assertEqual(trial.overlay(self.policy, now=end)['providers']['x']['daily_target'], 20)
        self.assertEqual(trial.overlay(self.policy, now=end)['providers']['x']['minimum_spacing_minutes'], 30)
        result = trial.reconcile(apply=True, now=end)
        self.assertEqual(result['status'], 'complete')
        saved = trial.path().read_bytes()
        trial.reconcile(apply=True, now=end+timedelta(days=1))
        self.assertEqual(saved, trial.path().read_bytes())
        self.assertEqual(trial.read()['started_at'], data['started_at'])

    def test_stop_before_start_and_existing_reservations_are_untouched(self):
        trial.stop(now=self.now)
        before = deepcopy(self.schedules)
        self.assertEqual(self.start()['status'], 'stopped')
        self.assertEqual(before, self.schedules)
        self.assertEqual(trial.overlay(self.policy, now=self.now), self.policy)

    def test_operator_policy_edit_stops_trial_without_overwrite(self):
        self.start()
        self.policy['providers']['x']['window_start'] = '08:00'
        portfolio.write_private_json(portfolio.policy_file(), self.policy)
        self.assertEqual(trial.overlay(self.policy, now=self.now), self.policy)
        self.assertEqual(trial.reconcile(apply=True, now=self.now+timedelta(minutes=1))['status'], 'stopped')
        self.assertEqual(portfolio.load_policy(effective=False), self.policy)

    def test_corrupt_state_does_not_raise_capacity_or_restart(self):
        self.start(); trial.path().write_text('{broken')
        self.assertEqual(trial.overlay(self.policy, now=self.now), self.policy)
        with self.assertRaises(ValueError): self.start()
        self.assertEqual(trial.path().read_text(), '{broken')

    def test_corrupt_delivery_log_cannot_qualify_for_increase(self):
        directory = self.root / 'state'; directory.mkdir()
        (directory / 'publish-receipts.jsonl').write_text('{broken\n')
        self.assertFalse(trial.delivery_gate('x', self.now)['healthy'])
        self.assertEqual(self.start()['reason'], 'delivery_ledger_unreadable')
        self.assertFalse(trial.path().exists())

    def test_state_lock_and_clock_regression_do_not_overwrite(self):
        self.start(); before = trial.path().read_bytes()
        with local_store.locked(trial.path()):
            with self.assertRaises(BlockingIOError): self.start()
        with self.assertRaises(ValueError): trial.reconcile(apply=True, now=self.now-timedelta(seconds=1))
        self.assertEqual(before, trial.path().read_bytes())

    def test_policy_selection_write_does_not_persist_trial_target(self):
        self.start()
        from ocpf_post.portfolio_queue import set_selection
        set_selection('legacy')
        self.assertEqual(portfolio.load_policy(effective=False)['providers']['x']['daily_target'], 20)

    def test_transition_spacing_budget_and_development_limits(self):
        # Build complete frozen inputs using the actual capture shape.
        policy = deepcopy(self.policy); policy['providers'] = {'x': dict(policy['providers']['x'])}
        policy['providers']['x'].update(daily_target=24, minimum_spacing_minutes=30)
        now = self.now
        rows = [{'campaign': f'C{i}', 'provider': 'x', 'project': f'p{i}', 'title': 'copy',
                 'lane': 'evergreen', 'priority': 70, 'family': f'p{i}', 'topic_key': f't{i}',
                 'prepared_at': trial.stamp(now-timedelta(days=1)), 'expires_at': trial.stamp(now+timedelta(days=2))} for i in range(50)]
        # An old-grid reservation at 12:20 must exclude trial slots until 12:50.
        occupied = now+timedelta(minutes=20)
        inputs = {'now': trial.stamp(now), 'horizon_minutes': 600, 'candidates': rows,
                  'histories': {'x': [{'at': trial.stamp(occupied), 'campaign': 'OLD', 'project': 'old', 'family': 'old', 'topic_key': 'old'}]},
                  'day_counts': {'x': {'2026-09-10': {'total': 15, 'lanes': {'development': 6, 'evergreen': 5, 'commercial': 4}},
                                       '2026-09-11': {'total': 0, 'lanes': {'development': 0, 'evergreen': 0, 'commercial': 0}}}},
                  'capacity': {'x': {}}}
        result = fair_plan(inputs, policy)
        self.assertLessEqual(len(result['plan']), 9)
        times = sorted([occupied] + [trial.at(r['run_at']) for r in result['plan']])
        self.assertTrue(all((b-a).total_seconds() >= 1800 for a, b in zip(times, times[1:])))

    def test_cohort_exact_identity_age_and_unknown_metrics(self):
        from ocpf_post import performance_review, performance
        keys = [('A', 'x', trial.ACCOUNTS['x'], '101'), ('B', 'x', trial.ACCOUNTS['x'], '102')]
        pub = {k: {'at': self.now-timedelta(days=1), 'receipt': {}} for k in keys}
        good = dict(zip(('campaign', 'provider', 'account_id', 'post_id'), keys[0]))
        good.update(captured_at=trial.stamp(self.now), availability={'status':'available'}, metrics={'impressions':100, 'likes':None})
        wrong = {**good, 'account_id': 'wrong', 'metrics': {'impressions': 999}}
        old = {**good, 'captured_at': trial.stamp(self.now-timedelta(hours=12)), 'metrics': {'impressions': 999}}
        with patch.object(performance_review, 'publications', return_value=pub), patch.object(performance, 'iter_snapshots', return_value=[good, wrong, old]):
            result = trial.cohort(self.now-timedelta(days=2), self.now, 'x', trial.ACCOUNTS['x'], now=self.now)
        self.assertEqual(result['publication_count'], 2)
        self.assertEqual(result['missing_24h_observations'], 1)
        self.assertEqual(result['metrics']['impressions']['observed_total'], 100)
        self.assertEqual(result['metrics']['impressions']['missing_posts'], 1)
        self.assertNotIn('likes', result['metrics'])
        self.assertIsNone(result['signups'])


if __name__ == '__main__': unittest.main()
