from datetime import timedelta
import copy
import hashlib
import json
import os
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch
from test_portfolio_queue import candidate, fixture, NOW
from ocpf_post import local_store, publication_reconcile as pr, portfolio_queue as queue
from ocpf_post.state import state_dir


def schedule(i, provider='x', **extra):
    text = 'Approved copy ' + str(i)
    return {'schedule_id': 'sch_' + str(i), 'campaign': 'TEST-' + str(i),
            'provider': provider, 'account_id': '123', 'post_id': str(100 + i),
            'status': 'published_unverified', 'text': text,
            'text_sha256': hashlib.sha256(text.encode()).hexdigest(),
            'run_at': (NOW + timedelta(minutes=i)).isoformat(), **extra}


class HostDiagnosticTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        env = patch.dict(os.environ, OCPF_POST_STATE_DIR=self.temp.name,
                         OCPF_POST_CONFIG_DIR=self.temp.name + '/config')
        env.start(); self.addCleanup(env.stop)

    def client(self, post):
        return SimpleNamespace(account=lambda: SimpleNamespace(account_id='123', username='owner'),
                               _bearer=lambda *args, **kwargs: (200, {'data': post}))

    def test_aged_same_project_same_age_band_serves_earlier_expiry_before_priority(self):
        common = {'queue_first_eligible_at': '2026-09-07T11:00:00Z'}
        vault = candidate('vault', 'alpha', priority=50, expires_at='2026-09-14T11:00:00Z', **common)
        generated = candidate('generated', 'alpha', priority=90, expires_at='2026-09-22T11:00:00Z', **common)
        inputs, policy = fixture([generated, vault])
        result = queue.fair_plan(inputs, policy)
        previous = queue.fair_plan(inputs, policy, expiry_ties=False)
        self.assertEqual(previous['plan'][0]['campaign'], 'generated')
        self.assertEqual(result['plan'][0]['campaign'], 'vault')
        self.assertEqual(result['plan'][0]['expires_at'], vault['expires_at'])
        self.assertEqual(inputs['candidates'], [generated, vault])

    def test_missing_ids_do_not_starve_newer_known_readback(self):
        rows = [schedule(i, post_id=None) for i in range(25)] + [schedule(26)]
        post = {'id': rows[-1]['post_id'], 'author_id': '123', 'text': rows[-1]['text']}
        with patch.object(pr, 'publication_inputs', return_value=({}, rows, [])), \
             patch.object(pr, 'for_account', return_value=self.client(post)) as factory:
            result = pr.reconcile(apply=True, now=NOW)
        self.assertTrue(any(r['schedule_id']=='sch_26' and r['status']=='verified' for r in result['results']))
        self.assertEqual(factory.call_count, 1)
        self.assertEqual(rows[0]['status'], 'published_unverified')

    def test_readback_rotates_past_first_twenty_and_keeps_each_attempt_durable(self):
        rows = [schedule(i) for i in range(25)]
        calls = []
        def bearer(url, **kwargs):
            calls.append(url)
            return 200, {'data': {'id': 'wrong', 'author_id': '123', 'text': 'mismatch'}}
        client = SimpleNamespace(account=lambda: SimpleNamespace(account_id='123', username='owner'), _bearer=bearer)
        with patch.object(pr, 'publication_inputs', return_value=({}, rows, [])), patch.object(pr, 'for_account', return_value=client):
            first = pr.reconcile(apply=True, now=NOW)
            second = pr.reconcile(apply=True, now=NOW+timedelta(minutes=1))
        first_ids = {r['schedule_id'] for r in first['results'] if r['status']=='readback_mismatch'}
        second_ids = {r['schedule_id'] for r in second['results'] if r['status']=='readback_mismatch'}
        self.assertEqual(len(first_ids), 20)
        self.assertEqual(len(first_ids | second_ids), 25)
        self.assertEqual(len(local_store.read(state_dir()/'publication-readbacks.json')['observations']), 25)

    def test_mismatch_details_distinguish_author_text_and_id_without_relaxing_verification(self):
        row = schedule(1); before=copy.deepcopy(row)
        with patch.object(pr, 'publication_inputs', return_value=({}, [row], [])), \
             patch.object(pr, 'for_account', return_value=self.client({'id':row['post_id'], 'author_id':'other', 'text':row['text']})):
            result=pr.reconcile(apply=True,now=NOW)['results'][0]
        self.assertEqual(result['status'],'readback_mismatch')
        self.assertEqual(result['mismatch_fields'],['author'])
        self.assertTrue(result['matches']['text'])
        self.assertEqual(row,before)
        self.assertFalse((state_dir()/'schedule-events.jsonl').exists())
        self.assertFalse((state_dir()/'publish-receipts.jsonl').exists())

    def test_linkedin_exact_readback_and_access_denial_are_observed_separately(self):
        rows = [schedule(i, 'linkedin', post_id='urn:li:share:'+str(100+i)) for i in range(3)]
        client = self.client({}); client._headers=lambda: {}
        post = {'id':rows[-1]['post_id'], 'author':'123', 'commentary':rows[-1]['text'], 'lifecycleState':'PUBLISHED'}
        with patch.object(pr,'publication_inputs',return_value=({},rows,[])), \
             patch.object(pr,'for_account',return_value=client), \
             patch('ocpf_post.providers.http.request_json',side_effect=[(200,{},post),(403,{}, {'access_token':'never retain raw body'})]) as get:
            result=pr.reconcile(apply=True,now=NOW)
        self.assertEqual(get.call_count,2)
        self.assertEqual(result['results'][0]['status'],'verified')
        self.assertEqual(result['results'][1]['status'],'readback_access_denied')
        self.assertEqual(result['results'][2]['status'],'account_readback_deferred')
        self.assertNotIn('access_token',json.dumps(result))
        self.assertIn('urn%3Ali%3Ashare%3A102',get.call_args_list[0].args[0])
        with patch.object(pr,'publication_inputs',return_value=({},rows,[])), \
             patch.object(pr,'for_account',side_effect=AssertionError('Account backoff must prevent all calls')):
            later=pr.reconcile(apply=True,now=NOW+timedelta(minutes=1))
        self.assertEqual(later['reads_attempted'],0)
        self.assertTrue(any(r.get('cached') for r in later['results']))

    def test_linkedin_draft_or_wrong_author_never_verifies(self):
        row=schedule(1,'linkedin',post_id='urn:li:share:101')
        client=self.client({});client._headers=lambda: {}
        post={'id':row['post_id'],'author':'123','commentary':row['text'],'lifecycleState':'DRAFT'}
        with patch.object(pr,'publication_inputs',return_value=({},[row],[])), \
             patch.object(pr,'for_account',return_value=client), \
             patch('ocpf_post.providers.http.request_json',return_value=(200,{},post)):
            result=pr.reconcile(apply=True,now=NOW)['results'][0]
        self.assertEqual(result['mismatch_fields'],['lifecycle_state'])
        self.assertEqual(result['status'],'readback_mismatch')

    def test_completed_reads_survive_later_process_interruption(self):
        rows=[schedule(i) for i in range(3)]
        client=SimpleNamespace(account=lambda:SimpleNamespace(account_id='123',username='owner'))
        post={'post_id':rows[-1]['post_id'],'author':'123','text':rows[-1]['text']}
        with patch.object(pr,'publication_inputs',return_value=({},rows,[])), \
             patch.object(pr,'for_account',return_value=client), \
             patch.object(pr,'_read',side_effect=[(200,post),KeyboardInterrupt()]):
            with self.assertRaises(KeyboardInterrupt):pr.reconcile(apply=True,now=NOW)
        saved=local_store.read(state_dir()/'publication-readbacks.json')['observations']
        self.assertEqual(saved[pr._key(rows[-1])]['status'],'verified')
        self.assertEqual(saved[pr._key(rows[-2])]['status'],'readback_started')

    def test_time_budget_defers_remaining_work_without_calls(self):
        with patch.object(pr,'publication_inputs',return_value=({},[schedule(1)],[])), \
             patch.object(pr.time,'monotonic',side_effect=[0,100]), \
             patch.object(pr,'for_account',side_effect=AssertionError('Budget exhausted')):
            result=pr.reconcile(apply=True,now=NOW)
        self.assertEqual(result['reads_attempted'],0)
        self.assertEqual(result['results'][0]['reason'],'cycle_budget')

    def test_account_identity_denial_backs_off_other_posts_and_later_runs(self):
        from ocpf_post.providers.base import ProviderRejected
        rows=[schedule(i) for i in range(3)]
        with patch.object(pr,'publication_inputs',return_value=({},rows,[])), \
             patch.object(pr,'for_account',side_effect=ProviderRejected(403,'private provider detail')) as factory:
            first=pr.reconcile(apply=True,now=NOW)
            second=pr.reconcile(apply=True,now=NOW+timedelta(minutes=1))
        self.assertEqual(factory.call_count,1)
        self.assertEqual(first['reads_attempted'],1)
        self.assertEqual(second['reads_attempted'],0)
        self.assertNotIn('private provider detail',json.dumps(first))

    def test_runner_lock_observation_never_exposes_token_or_removes_lock(self):
        from ocpf_post.open_work import runner_lock
        from ocpf_post.scheduler import runner_lock_file
        data=json.dumps({'pid':os.getpid(),'token':'private-lock-token','created_epoch':NOW.timestamp()})
        path=runner_lock_file();path.write_text(data)
        result=runner_lock(NOW)
        self.assertTrue(result['present'])
        self.assertTrue(result['pid_alive'])
        self.assertNotIn('private-lock-token',json.dumps(result))
        self.assertEqual(path.read_text(),data)

    def test_deadline_tie_does_not_buy_extra_project_turns_or_revive_expired_work(self):
        common={'queue_first_eligible_at':'2026-09-07T11:00:00Z'}
        rows=[candidate('vault'+str(i),'alpha',priority=40,expires_at='2026-09-14T11:00:00Z',**common) for i in range(20)]
        rows += [candidate('other','beta',priority=90,expires_at='2026-09-22T11:00:00Z',**common),
                 candidate('expired','gamma',priority=100,expires_at='2026-09-09T11:00:00Z',**common)]
        inputs,policy=fixture(rows)
        result=queue.fair_plan(inputs,policy)
        self.assertEqual({r['project'] for r in result['plan'][:2]},{'alpha','beta'})
        self.assertNotIn('expired',[r['campaign'] for r in result['plan']])
