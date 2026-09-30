from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from types import SimpleNamespace
from ocpf_post import local_store, engagement as e, reply_reconcile, google_connection, business_outcomes
from ocpf_post.open_work import sustained
from ocpf_post.state import append_receipt

NOW = datetime(2026, 9, 13, tzinfo=timezone.utc)


class RecoveryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        env = patch.dict(os.environ, OCPF_POST_STATE_DIR=self.temp.name, OCPF_POST_CONFIG_DIR=self.temp.name + '/config')
        env.start(); self.addCleanup(env.stop)

    def test_existing_reply_readback_verifies_parent_author_and_text_without_send(self):
        context = {'post_id': '10', 'parent_post_id': '9', 'author': 'other', 'text': 'A comment'}
        value = {'inbox_id': 'item', 'provider': 'x', 'account_id': '123', 'context': context,
                 'text': 'Add an integration check.', 'expires_at': NOW.isoformat()}
        row = {'id': 'item', 'provider': 'x', 'account_id': '123', 'status': 'published_unverified',
               'post_id': '11', 'draft': value, 'review_sha256': e.digest(value)}
        local_store.write(e.path(), {'schema_version': 1, 'inbox': {'item': row}, 'polls': {}})
        client = SimpleNamespace(account=lambda: SimpleNamespace(account_id='123'))
        observed = {'post_id': '11', 'parent_post_id': '10', 'author': '123', 'text': value['text']}
        with patch('ocpf_post.reply_reconcile.for_account', return_value=client), patch.object(e, 'lookup', return_value={**observed, 'author': 'wrong'}):
            result = reply_reconcile.reconcile(apply=True, now=NOW)
        self.assertEqual(result['results'][0]['status'], 'readback_mismatch')
        self.assertEqual(e.read()['inbox']['item']['status'], 'published_unverified')
        with patch('ocpf_post.reply_reconcile.for_account', return_value=client), patch.object(e, 'lookup', return_value=observed):
            result = reply_reconcile.reconcile(apply=True, now=NOW)
        self.assertEqual(e.read()['inbox']['item']['status'], 'published_verified')
        self.assertEqual(e.read()['inbox']['item']['draft'], value)
        self.assertEqual(len(e.read()['inbox']['item']['verification_history']), 2)

    def test_unknown_effect_is_not_sent_or_erased(self):
        local_store.write(e.path(), {'schema_version': 1, 'polls': {}, 'inbox': {'item': {
            'id': 'item', 'provider': 'x', 'account_id': '123', 'status': 'ambiguous_effect'}}})
        with patch('ocpf_post.reply_reconcile.for_account', side_effect=AssertionError('No network without ID')):
            report = reply_reconcile.reconcile(apply=True, now=NOW)
        self.assertEqual(report['results'][0]['status'], 'post_id_required')
        self.assertEqual(e.read()['inbox']['item']['status'], 'ambiguous_effect')

    def test_refresh_longevity_is_bound_to_same_credential_and_never_claims_publishing_mode(self):
        credential = {'client_id': 'test-client', 'refresh_token': 'secret-refresh'}
        google_connection.record_refresh(credential, success=True, now=NOW)
        google_connection.record_refresh(credential, success=True, now=NOW + timedelta(days=8))
        report = google_connection.report()
        self.assertTrue(report['refresh_beyond_seven_days_observed'])
        self.assertEqual(report['oauth_publishing_status'], 'not_observed')
        self.assertNotIn('secret-refresh', json.dumps(report))
        google_connection.record_refresh({**credential, 'refresh_token': 'replacement'}, success=True, now=NOW + timedelta(days=8))
        self.assertFalse(google_connection.report()['refresh_beyond_seven_days_observed'])

    def test_sustained_report_needs_day_of_timely_cycles(self):
        rows = [{'completed_at': (NOW - timedelta(minutes=15 * i)).isoformat(), 'stages': {'metrics': 'idle'}} for i in range(97)]
        self.assertEqual(sustained({'history': rows[:2]}, NOW)['status'], 'insufficient_evidence')
        self.assertEqual(sustained({'history': rows}, NOW)['status'], 'observed')
        self.assertEqual(sustained({'history': rows[:10] + rows[15:]}, NOW)['status'], 'insufficient_evidence')
        rows[0]['stages']['source-observation'] = 'attention'
        self.assertEqual(sustained({'history': rows}, NOW)['status'], 'insufficient_evidence')

    def test_business_events_bind_verified_effect_and_are_deduplicated(self):
        receipt = {'campaign': 'TEST-001', 'provider': 'x', 'account_id': '123', 'post_id': '10',
                   'status': 'published_verified', 'recorded_at': (NOW - timedelta(days=1)).isoformat()}
        append_receipt(receipt)
        event = {k: receipt[k] for k in ('campaign', 'provider', 'account_id', 'post_id')}
        event.update(event_id='sale-1', occurred_at=NOW.isoformat(), event_type='sale',
                     evidence_reference='analytics-export:row-1', revenue_minor=1200, currency='GBP')
        data = {'schema_version': 1, 'source': 'owner-analytics', 'events': [event]}
        file = Path(self.temp.name) / 'input.json'; file.write_text(json.dumps(data))
        preview = business_outcomes.ingest(file, now=NOW)
        for _ in range(2):
            business_outcomes.ingest(file, apply=True, expected_sha256=preview['input_sha256'], now=NOW)
        self.assertEqual(business_outcomes.report()['revenue_minor_by_currency'], {'GBP': 1200})
        event['revenue_minor'] = 1300; file.write_text(json.dumps(data))
        preview = business_outcomes.ingest(file, now=NOW)
        with self.assertRaises(ValueError):
            business_outcomes.ingest(file, apply=True, expected_sha256=preview['input_sha256'], now=NOW)
        event['post_id'] = 'wrong'; file.write_text(json.dumps(data))
        with self.assertRaises(ValueError):
            business_outcomes.ingest(file, now=NOW)

    def test_threads_82_targets_rotate_in_two_fast_bounded_cycles(self):
        from ocpf_post.model import AccountIdentity
        account = AccountIdentity(provider='threads', account_id=e.ACCOUNTS['threads'], username='owner')
        calls = []
        def bearer(url, **kwargs):
            calls.append(url)
            return 200, {}, {'data': []}
        client = SimpleNamespace(name='threads', account=lambda: account, _bearer=bearer)
        roots = {str(1000 + i): {'campaign': 'TEST-001', 'published_at': NOW.isoformat()} for i in range(82)}
        with patch.object(e, 'own_publications', side_effect=lambda provider, account, now: roots if provider == 'threads' else {}):
            first = e.sync(apply=True, now=NOW, factory=lambda _: client)
            self.assertEqual(first['polls']['threads']['pages_completed'], 41)
            self.assertEqual(first['polls']['threads']['roots_not_yet_scanned'], 41)
            second = e.sync(apply=True, now=NOW + timedelta(minutes=16), factory=lambda _: client)
            self.assertEqual(second['polls']['threads']['roots_not_yet_scanned'], 0)
            self.assertEqual(len(set(calls)), 82)

    def test_interrupted_metrics_read_consumes_a_durable_attempt(self):
        from ocpf_post import performance_review
        append_receipt({'campaign': 'TEST-001', 'provider': 'x', 'account_id': '123', 'post_id': '10',
                        'status': 'published_verified', 'recorded_at': (NOW - timedelta(hours=22)).isoformat()})
        def interrupted(*args):
            raise RuntimeError('simulated process interruption')
        with self.assertRaises(RuntimeError):
            performance_review.capture_due(apply=True, now=NOW, capture_fn=interrupted)
        early = performance_review.capture_due(now=NOW + timedelta(minutes=5))
        self.assertEqual(early['deferred'][0]['attempts'], 1)
        retry = performance_review.capture_due(now=NOW + timedelta(minutes=15))
        self.assertEqual(retry['observations'][0]['attempt'], 2)

    def test_provider_retry_after_is_retained_as_delay_or_timestamp(self):
        from ocpf_post.performance import _retry_header
        self.assertEqual(_retry_header({'Retry-After': '1200'}), {'retry_after_seconds': 1200})
        self.assertEqual(_retry_header({'retry-after': 'Sun, 13 Sep 2026 01:00:00 GMT'}),
                         {'retry_at': '2026-09-13T01:00:00+00:00'})
        self.assertEqual(_retry_header({'Retry-After': 'invalid'}), {})

    def test_campaign_readback_keeps_original_logs_and_requires_own_author(self):
        import copy
        import hashlib
        from ocpf_post import publication_reconcile
        from ocpf_post.state import state_dir
        schedule = {'schedule_id': 'sch_known', 'campaign': 'TEST-001', 'provider': 'x',
                    'account_id': '123', 'post_id': '10', 'status': 'published_unverified',
                    'text': 'Exact approved copy.', 'text_sha256': hashlib.sha256(b'Exact approved copy.').hexdigest()}
        before = copy.deepcopy(schedule)
        post = {'id': '10', 'author_id': 'other', 'text': schedule['text']}
        client = SimpleNamespace(account=lambda: SimpleNamespace(account_id='123', username='owner'),
                                 _bearer=lambda *a, **kw: (200, {'data': post}))
        with patch.object(publication_reconcile, 'publication_inputs', return_value=({}, [schedule], [])), \
             patch.object(publication_reconcile, 'for_account', return_value=client):
            wrong = publication_reconcile.reconcile(apply=True, now=NOW)
            self.assertEqual(wrong['results'][0]['status'], 'readback_mismatch')
            post['author_id'] = '123'
            good = publication_reconcile.reconcile(apply=True, now=NOW)
            self.assertEqual(good['results'][0]['status'], 'verified')
        self.assertEqual(schedule, before)
        self.assertFalse((state_dir() / 'publish-receipts.jsonl').exists())
        self.assertFalse((state_dir() / 'schedule-events.jsonl').exists())
        evidence = local_store.read(state_dir() / 'publication-readbacks.json')
        self.assertEqual(len(evidence['observations']), 1)

    def test_open_work_report_runs_on_real_empty_local_state_without_network(self):
        from ocpf_post.open_work import report, PRESERVED
        with patch('urllib.request.urlopen', side_effect=AssertionError('No network')), \
             patch('ocpf_post.providers.for_account', side_effect=AssertionError('No provider')):
            result = report(now=NOW)
        self.assertEqual(result['status'], 'observed')
        self.assertEqual(result['preserved_milestones'], PRESERVED)
        self.assertTrue(result['sections']['deliveries'])
        self.assertEqual(result['sections']['business_outcomes']['status'], 'not_connected')
        self.assertEqual(result['sections']['google_connection']['oauth_publishing_status'], 'not_observed')
