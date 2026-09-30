from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
import subprocess
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from ocpf_post import queue_watch as watch, portfolio as base
from ocpf_post.portfolio_queue import fair_plan, capture_inputs
from ocpf_post.health import report as health_report
from test_portfolio_queue import candidate, fixture

UTC = timezone.utc
NOW = datetime(2026, 9, 10, 5, tzinfo=UTC)


def queued(name='A', project='alpha', **kwargs):
    return candidate(name, project, account_id='123', text_sha256=hashlib.sha256(('copy-' + name).encode()).hexdigest(), **kwargs)


class QueueWatchTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(); self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        env = patch.dict(os.environ, {'OCPF_POST_STATE_DIR': str(self.root), 'OCPF_POST_CONFIG_DIR': str(self.root/'config')})
        env.start(); self.addCleanup(env.stop)
        self.candidates = [queued()]
        self.policy = fixture(self.candidates)[1]
        self.policy['selection'] = 'fair'
        self.schedules, self.receipts = [], []
        self.projection = {'plan': [], 'capacity': {}}
        self.manifests = {}
        patches = [
            patch.object(base, 'load_policy', side_effect=lambda: self.policy),
            patch('ocpf_post.portfolio_queue.capture_inputs', side_effect=self.inputs),
            patch('ocpf_post.portfolio_cross_platform.plan_refill', side_effect=lambda **kw: self.projection),
            patch.object(watch, 'schedule_records', side_effect=lambda: self.schedules),
            patch.object(watch, 'iter_receipts', side_effect=lambda: self.receipts),
            patch.object(watch, 'builtin_manifest', side_effect=lambda name: self.manifests.get(name, {})),
            patch.object(watch, 'destination_binding', return_value={'account_id': '123'}),
            patch.object(watch, 'builtin_text', side_effect=lambda name, provider: 'copy-' + name),
            patch('urllib.request.urlopen', side_effect=AssertionError('Network forbidden')),
        ]
        for p in patches:
            p.start(); self.addCleanup(p.stop)

    def inputs(self, now, horizon, policy):
        value = fixture(self.candidates)[0]
        value['now'] = now.isoformat()
        value['exclusions'] = []
        return value

    def get(self):
        return next(iter(watch.load()['records'].values()))

    def expire(self):
        item = self.candidates.pop()
        self.manifests[item['campaign']] = {'campaign': item['campaign'], 'status': 'COPY-READY',
                                           'allocation': {'enabled': True}}
        return item

    def test_persistent_age_starts_at_observation_not_old_prepared_date(self):
        watch.observe(now=NOW, evaluate=False)
        self.assertEqual(self.get()['first_eligible_at'], '2026-09-10T05:00:00Z')
        self.assertEqual(self.get()['waiting_evaluations'], 0)
        watch.observe(now=NOW)
        watch.observe(now=NOW+timedelta(minutes=1))
        self.assertEqual(self.get()['waiting_evaluations'], 1)
        watch.observe(now=NOW+timedelta(hours=25))
        record = self.get()
        self.assertEqual(record['waiting_evaluations'], 2)
        self.assertEqual(record['age_hours'], 25)
        self.assertIn('waiting_too_long', watch.report(now=NOW+timedelta(hours=25))['issue_counts'])
        self.assertEqual(watch.path().stat().st_mode & 0o777, 0o600)

    def test_survival_audit_tracks_rescue_window_without_extending_expiry(self):
        self.candidates[0]['lane'] = 'evergreen'
        self.candidates[0]['expires_at'] = (NOW + timedelta(hours=12)).isoformat()
        first = watch.observe(now=NOW)
        record = self.get()
        self.assertEqual(record['rescue_window_evaluations'], 1)
        self.assertEqual(record['rescue_unselected_evaluations'], 1)
        self.assertEqual(first['survival']['rescue_window'], 1)
        self.assertEqual(first['survival']['expired_unpublished'], 0)
        original_expiry = record['expires_at']

        self.expire()
        expired = watch.observe(now=NOW + timedelta(hours=13))
        record = self.get()
        self.assertEqual(record['state'], 'expired_unpublished')
        self.assertEqual(record['expires_at'], original_expiry)
        self.assertEqual(expired['survival']['expired_unpublished'], 1)
        self.assertEqual(expired['survival']['expired_after_rescue_window'], 1)
        self.assertEqual(expired['survival']['expired_after_unselected_rescue_evaluations'], 1)
        issue = next(row for row in expired['issues'] if row['code'] == 'expired_unpublished')
        self.assertEqual(issue['survival_class'], 'expired_after_rescue_window_unselected')

    def test_capacity_pressure_is_visible_before_fixed_deadline_window(self):
        expiry = NOW + timedelta(hours=29)
        self.candidates = [
            queued(
                f"RISK-{i}", project=f"p{i}", lane="evergreen",
                expires_at=expiry.isoformat(),
                queue_first_eligible_at=(NOW - timedelta(days=2)).isoformat(),
            )
            for i in range(9)
        ]
        result = watch.observe(now=NOW)
        self.assertEqual(result["issue_counts"]["expiry_capacity_pressure"], 9)
        self.assertEqual(result["survival"]["capacity_pressure"], 9)
        self.assertGreater(result["survival"]["capacity_shortfall"], 0)
        self.assertEqual(result["survival"]["capacity_pressure_groups"], 1)
        record = next(iter(watch.load()["records"].values()))
        self.assertTrue(record["rescue_active"])
        self.assertEqual(record["rescue_trigger"], "capacity_feasibility")
        self.assertGreater(record["due_before_expiry"], record["authorised_slots_before_expiry"])
        self.assertEqual(record["rescue_window_evaluations"], 1)

    def test_rescue_projection_is_counted_as_projection_not_publication(self):
        self.candidates[0]['lane'] = 'commercial'
        self.candidates[0]['expires_at'] = (NOW + timedelta(hours=12)).isoformat()
        projected = {**self.candidates[0], 'run_at': (NOW + timedelta(hours=2)).isoformat()}
        self.projection['plan'] = [projected]
        self.projection['decisions'] = [{
            'provider': 'x', 'run_at': projected['run_at'],
            'campaign': self.candidates[0]['campaign'], 'reason': 'bounded_rescue_service',
        }]
        result = watch.observe(now=NOW)
        record = self.get()
        self.assertEqual(record['rescue_window_evaluations'], 1)
        self.assertEqual(record['rescue_projected_evaluations'], 1)
        self.assertEqual(record.get('rescue_unselected_evaluations', 0), 0)
        self.assertEqual(record['rescue_turn_evaluations'], 1)
        self.assertEqual(record['service_opportunity_evaluations'], 1)
        self.assertEqual(record['state'], 'waiting')
        self.assertEqual(self.schedules, [])
        self.assertEqual(self.receipts, [])
        self.assertEqual(result['survival']['rescue_window'], 1)

    def test_projection_is_not_reservation_and_deadline_warning_is_automatic(self):
        self.candidates[0]['expires_at'] = (NOW+timedelta(hours=12)).isoformat()
        self.projection['plan'] = [{**self.candidates[0], 'run_at': (NOW+timedelta(hours=2)).isoformat()}]
        result = watch.observe(now=NOW)
        self.assertEqual(self.get()['state'], 'waiting')
        self.assertEqual(self.get()['unselected_evaluations'], 0)
        self.assertEqual(result['issue_counts'], {'expiry_approaching': 1})
        self.assertEqual(self.receipts, [])

    def test_elapsed_forecast_cannot_be_hidden_by_a_new_projection(self):
        first = NOW + timedelta(minutes=30)
        self.projection['plan'] = [{**self.candidates[0], 'run_at': first.isoformat()}]
        watch.observe(now=NOW)
        self.projection['plan'][0]['run_at'] = (NOW + timedelta(hours=3)).isoformat()
        result = watch.observe(now=NOW + timedelta(minutes=31))
        self.assertEqual(result['issue_counts']['projection_elapsed_unreserved'], 1)
        self.assertEqual(self.get()['missed_projection_at'], first.isoformat().replace('+00:00', 'Z'))
        self.schedules.append({**self.candidates[0], 'schedule_id': 'sch-real', 'status': 'scheduled'})
        result = watch.observe(now=NOW + timedelta(minutes=32))
        self.assertNotIn('projection_elapsed_unreserved', result['issue_counts'])
        self.assertEqual(self.get()['outcome']['schedule_id'], 'sch-real')

    def test_expired_record_survives_eligibility_filter_and_no_reapproval_occurs(self):
        self.candidates[0]['expires_at'] = (NOW+timedelta(hours=1)).isoformat()
        watch.observe(now=NOW)
        self.expire()
        result = watch.observe(now=NOW+timedelta(hours=2))
        self.assertEqual(self.get()['state'], 'expired_unpublished')
        self.assertIn('expired_unpublished', result['issue_counts'])
        self.assertEqual(self.schedules, [])
        self.assertEqual(self.receipts, [])
        self.manifests['A']['allocation']['enabled'] = False
        resolved = watch.observe(now=NOW+timedelta(hours=3))
        self.assertEqual(resolved['issue_counts'], {})
        self.assertEqual(self.get()['expiry_observed_at'], '2026-09-10T07:00:00Z')
        watch.observe(now=NOW+timedelta(days=32))
        self.assertEqual(watch.load()['records'], {})

    def test_exact_account_and_payload_are_required_to_resolve(self):
        watch.observe(now=NOW)
        row = {**self.candidates[0], 'status': 'published_unverified', 'post_id': 'p1'}
        self.receipts = [{**row, 'account_id': 'someone-else'}, {**row, 'text_sha256': 'different'}]
        watch.observe(now=NOW+timedelta(minutes=15))
        self.assertEqual(self.get()['state'], 'waiting')
        self.receipts.append(row)
        watch.observe(now=NOW+timedelta(minutes=30))
        self.assertEqual(self.get()['state'], 'published_unverified')
        self.assertNotEqual(self.get()['state'], 'published_verified')

    def test_real_reservation_removes_waiting_warning_then_ambiguity_stays_open(self):
        watch.observe(now=NOW)
        row = {**self.candidates[0], 'status': 'scheduled', 'schedule_id': 'sch-1'}
        self.schedules = [row]
        result = watch.observe(now=NOW+timedelta(hours=25))
        self.assertEqual(self.get()['state'], 'scheduled')
        self.assertEqual(result['issue_counts'], {})
        self.receipts = [{**self.candidates[0], 'status': 'ambiguous_effect'}]
        watch.observe(now=NOW+timedelta(hours=26))
        self.assertEqual(self.get()['state'], 'ambiguous_effect')
        self.assertIn('ambiguous_effect', watch.report(now=NOW+timedelta(hours=26))['issue_counts'])

    def test_changed_identity_cannot_inherit_wait_age(self):
        watch.observe(now=NOW)
        previous_key = watch.identity(self.candidates[0])
        self.candidates[0]['account_id'] = '456'
        watch.observe(now=NOW+timedelta(hours=25))
        records = watch.load()['records']
        self.assertEqual(len(records), 2)
        self.assertEqual(records[watch.identity(self.candidates[0])]['age_hours'], 0)
        self.assertEqual(records[previous_key]['state'], 'removed')

    def test_missing_binding_is_visible_and_cannot_gain_age_preference(self):
        self.candidates[0]['account_id'] = None
        result = watch.observe(now=NOW)
        self.assertEqual(self.get()['state'], 'blocked')
        self.assertEqual(self.get()['reason'], 'destination_identity_unavailable')
        self.assertEqual(watch.hints()[0], {})
        self.assertEqual(result['issue_counts'], {'blocked': 1})

    def test_watch_is_read_only_stale_and_cannot_hide_its_own_absence(self):
        self.assertEqual(watch.report(now=NOW)['status'], 'not_observed')
        self.assertFalse(self.root.joinpath('queue-watch.json').exists())
        watch.observe(now=NOW)
        before = watch.path().read_bytes()
        self.assertEqual(watch.report(now=NOW+timedelta(minutes=46))['status'], 'stale')
        self.assertEqual(watch.path().read_bytes(), before)

    def test_corruption_is_not_reset_and_never_leaks_error_content(self):
        watch.path().write_text('secret-token-do-not-print')
        result = watch.safe_observe(now=NOW)
        self.assertEqual(result['status'], 'unavailable')
        self.assertNotIn('secret-token', json.dumps(result))
        self.assertEqual(watch.path().read_text(), 'secret-token-do-not-print')
        self.assertEqual(watch.hints(), ({}, 'unavailable'))

    def test_writer_lock_and_clock_regression_preserve_evidence(self):
        watch.observe(now=NOW)
        before = watch.path().read_bytes()
        with watch._lock():
            self.assertEqual(watch.safe_observe(now=NOW)['status'], 'unavailable')
        self.assertEqual(watch.safe_observe(now=NOW-timedelta(minutes=1))['status'], 'unavailable')
        self.assertEqual(watch.path().read_bytes(), before)

    def test_health_surfaces_waiting_without_recording_or_network(self):
        watch.observe(now=NOW)
        watch.observe(now=NOW+timedelta(hours=25))
        before = watch.path().read_bytes()
        # Isolate the unrelated planner from this fixture's partial capacity.
        with patch('ocpf_post.portfolio_cross_platform.plan_refill', side_effect=ValueError('unavailable')):
            result = health_report(now=NOW+timedelta(hours=25), check_timers=False)
        self.assertIn('queue_waiting_too_long', {f['code'] for f in result['findings']})
        self.assertEqual(watch.path().read_bytes(), before)

    def test_failed_reservation_remains_an_exception_without_retry(self):
        watch.observe(now=NOW)
        self.schedules = [{**self.candidates[0], 'schedule_id': 'sch-fail', 'status': 'failed'}]
        result = watch.observe(now=NOW+timedelta(hours=1))
        self.assertEqual(self.get()['state'], 'schedule_requires_review')
        self.assertIn('schedule_requires_review', result['issue_counts'])
        self.assertEqual(len(self.schedules), 1)


class WaitingSelectionTests(unittest.TestCase):
    def test_source_failure_does_not_stop_refill_and_bounded_report_runs(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); (root/'scripts').mkdir(); (root/'bin').mkdir()
            (root/'scripts'/'run-portfolio-refill').write_text(Path('scripts/run-portfolio-refill').read_text())
            (root/'scripts'/'runtime-env').write_text(Path('scripts/runtime-env').read_text())
            accepted_sha = 'a' * 40
            (root/'bin'/'git').write_text(
                '#!/bin/sh\n'
                'case "$*" in\n'
                '  "status --porcelain=v1 --untracked-files=all") exit 0 ;;\n'
                f'  "rev-parse HEAD") printf "%s\\n" "{accepted_sha}"; exit 0 ;;\n'
                '  *) exit 0 ;;\n'
                'esac\n'
            )
            (root/'bin'/'git').chmod(0o700)
            # The production wrapper now requires a local immutable-release
            # equality check before any source/refill command. Keep that new
            # safety property present in this failure-path fixture rather than
            # bypassing the guard to preserve the old test shape.
            (root/'bin'/'python3').write_text(f'#!/bin/sh\nprintf "%s\\n" "{accepted_sha}"\n')
            (root/'bin'/'python3').chmod(0o700)
            (root/'poststeward').write_text('#!/bin/sh\nprintf "%s\\n" "$*" >> "$QUEUE_TEST_LOG"\n'
                                         'if [ "$1 $2" = "replenish refresh" ]; then exit 7; fi\n'
                                         'if [ "$1 $2" = "portfolio watch" ]; then exit 3; fi\nexit 0\n')
            (root/'poststeward').chmod(0o700)
            (root/'scripts'/'run-operating-cycle').write_text('#!/bin/sh\nprintf "cycle %s\\n" "$*" >> "$QUEUE_TEST_LOG"\n')
            (root/'scripts'/'run-operating-cycle').chmod(0o700)
            log = root/'calls'
            result = subprocess.run(['sh', str(root/'scripts'/'run-portfolio-refill')],
                                    env={**os.environ, 'PATH': str(root/'bin')+':'+os.environ['PATH'],
                                         'QUEUE_TEST_LOG': str(log)}, capture_output=True, text=True)
            self.assertEqual(result.returncode, 0)
            calls = log.read_text().splitlines()
            self.assertEqual(calls[-1], 'cycle report')
            self.assertIn('portfolio refill --apply --horizon-minutes 75', calls)
            self.assertLess(calls.index('portfolio experiment reconcile --apply'), calls.index('replenish refresh --apply'))

    def test_daily_burst_cannot_forever_displace_observed_old_copy(self):
        waiting = [queued('old', priority=60, queue_first_eligible_at=NOW.isoformat(),
                          expires_at=(NOW+timedelta(days=7)).isoformat())]
        old_day = None
        for day in range(4):
            now = NOW + timedelta(days=day)
            waiting = [c for c in waiting if base._parse_dt(c['expires_at']) > now]
            waiting += [queued(f'day{day}-{i}', priority=94, queue_first_eligible_at=now.isoformat(),
                               expires_at=(now+timedelta(hours=6)).isoformat()) for i in range(30)]
            inputs, policy = fixture(waiting, target=2)
            inputs['now'] = now.isoformat()
            counts = inputs['day_counts']['x']['2026-09-10']
            inputs['day_counts']['x'] = {(now+timedelta(days=i)).date().isoformat(): deepcopy(counts) for i in range(3)}
            result = fair_plan(inputs, policy)
            self.assertLessEqual(len(result['plan']), 2)
            chosen = {c['campaign'] for c in result['plan']}
            if 'old' in chosen:
                old_day = day
            waiting = [c for c in waiting if c['campaign'] not in chosen]
        self.assertIsNotNone(old_day)
        self.assertLessEqual(old_day, 1)

    def test_monitor_failure_does_not_block_allocation_and_final_observation_runs(self):
        expected = {'scheduled': [], 'errors': []}
        with patch.object(watch, 'safe_observe', return_value={'status': 'unavailable'}) as observer, \
             patch.object(base, '_apply_refill', return_value=expected):
            result = base.apply_refill(now=NOW)
        self.assertEqual(result['scheduled'], [])
        self.assertEqual(observer.call_count, 2)
        with patch.object(watch, 'safe_observe', return_value={'status': 'ok'}) as observer, \
             patch.object(base, '_apply_refill', side_effect=base.PortfolioError('refill failed')):
            with self.assertRaises(base.PortfolioError):
                base.apply_refill(now=NOW)
        self.assertEqual(observer.call_count, 2)

    def test_observed_aged_copy_gets_turn_despite_high_priority_arrivals(self):
        old = queued('old', priority=60, queue_first_eligible_at='2026-09-08T00:00:00Z')
        arrivals = [queued(f'new{i}', priority=94, prepared_at='2026-09-10T04:00:00Z',
                           expires_at='2026-09-10T12:00:00Z') for i in range(30)]
        inputs, policy = fixture([old, *arrivals])
        result = fair_plan(inputs, policy)
        self.assertIn('old', [r['campaign'] for r in result['plan'][:2]])
        self.assertLessEqual(len(result['plan']), policy['providers']['x']['daily_target'])

    def test_deadline_turn_preserves_service_for_other_project(self):
        soon = queued('soon', 'alpha', priority=60, expires_at='2026-09-10T07:00:00Z')
        other = queued('other', 'beta', priority=70)
        inputs, policy = fixture([soon, other])
        self.assertEqual(fair_plan(inputs, policy)['plan'][0]['campaign'], 'soon')
        inputs['histories']['x'] = [{'at': '2026-09-10T04:00:00Z', 'campaign': 'served-alpha',
                                    'project': 'alpha', 'topic_key': 'a', 'family': 'alpha'}]
        selected = fair_plan(inputs, policy)['plan']
        self.assertEqual(selected[0]['campaign'], 'soon')
        self.assertIn('other', [r['campaign'] for r in selected])

    def test_urgent_and_lane_limits_still_apply_to_old_waiting_work(self):
        old = queued('old', priority=60, queue_first_eligible_at='2026-09-08T00:00:00Z')
        urgent = queued('urgent', 'beta', priority=99, lane='development')
        inputs, policy = fixture([old, urgent])
        self.assertEqual(fair_plan(inputs, policy)['plan'][0]['campaign'], 'urgent')
        inputs['day_counts']['x']['2026-09-10']['lanes']['development'] = 1
        self.assertEqual(fair_plan(inputs, policy)['plan'][0]['campaign'], 'old')
