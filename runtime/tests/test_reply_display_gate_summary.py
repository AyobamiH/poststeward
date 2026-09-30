from datetime import datetime, timezone
import os
from pathlib import Path
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from ocpf_post import delivery_gates, engagement as e, local_store, reply_reconcile as rr
from ocpf_post.open_work_summary import summary
from ocpf_post.readback_details import compare

NOW = datetime(2026, 9, 13, 11, 30, tzinfo=timezone.utc)
ACCOUNT = '1480506376447315969'


class ReplyDisplayAndGateSummaryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        env = patch.dict(os.environ, OCPF_POST_STATE_DIR=self.temp.name,
                         OCPF_POST_CONFIG_DIR=self.temp.name + '/config')
        env.start(); self.addCleanup(env.stop)

    def test_x_display_range_and_url_entity_preserve_exact_comparison(self):
        expected = {'post_id': '301', 'parent_post_id': '201', 'author': ACCOUNT,
                    'text': 'See https://example.com & finish.'}
        prefix = '@visitor '
        provider_text = prefix + 'See https://t.co/abc &amp; finish.'
        observed = {'post_id': '301', 'parent_post_id': '201', 'author': ACCOUNT,
                    'text': provider_text,
                    'display_text_range': [len(prefix), len(provider_text)],
                    'entities': {'urls': [{'url': 'https://t.co/abc',
                                           'expanded_url': 'https://example.com'}]}}
        self.assertEqual(compare(expected, observed, provider='x')['mismatch_fields'], [])
        without_range = dict(observed); without_range.pop('display_text_range')
        self.assertEqual(compare(expected, without_range, provider='x')['mismatch_fields'], ['text'])

    def test_existing_x_reply_verifies_without_resend_for_hidden_leading_mention(self):
        identity = 'reply-one'
        context = {'post_id': '201', 'parent_post_id': '101', 'author': '999', 'text': '@owner useful point'}
        draft = {'inbox_id': identity, 'provider': 'x', 'account_id': ACCOUNT,
                 'context': context, 'text': 'A useful next check is completed enquiries.',
                 'expires_at': '2026-09-14T11:30:00Z'}
        row = {'id': identity, 'provider': 'x', 'account_id': ACCOUNT, 'campaign': 'TWW-001',
               'context': context, 'status': 'published_unverified', 'draft': draft,
               'review_sha256': e.digest(draft), 'post_id': '301', 'readback_verified': False}
        local_store.write(e.path(), {'schema_version': 1, 'inbox': {identity: row}, 'polls': {}})
        prefix = '@visitor '
        provider_text = prefix + draft['text']
        client = SimpleNamespace(
            account=lambda: SimpleNamespace(account_id=ACCOUNT, username='owner'),
            _bearer=lambda url: (200, {'data': {'id': '301', 'author_id': ACCOUNT,
                'text': provider_text, 'referenced_tweets': [{'type': 'replied_to', 'id': '201'}],
                'display_text_range': [len(prefix), len(provider_text)], 'entities': {'mentions': []}}}))
        with patch.object(rr, 'for_account', return_value=client):
            result = rr.reconcile(apply=True, now=NOW)
        self.assertEqual(result['results'][0]['status'], 'verified')
        saved = e.read()['inbox'][identity]
        self.assertEqual(saved['status'], 'published_verified')
        self.assertTrue(saved['readback_verified'])

    def test_fresh_gate_snapshot_is_read_only_and_terminal_summary_prefers_it(self):
        fresh = {'x': {'healthy': True, 'reason': 'healthy_local_delivery'},
                 'threads': {'healthy': True, 'reason': 'healthy_local_delivery'}}
        with patch.object(delivery_gates, 'delivery_gate', side_effect=lambda provider, now: fresh[provider]):
            gate_report = delivery_gates.report(now=NOW)
        recorded = {'x': {'healthy': False, 'reason': 'delivery_evidence_not_ready'}}
        report = {'observed_at': NOW.isoformat(), 'steps': [
            {'step': 'current-delivery-gates', 'execution_ok': True, 'status': 'completed', 'result': gate_report},
            {'step': 'open-delivery-and-operational-evidence', 'execution_ok': True, 'status': 'completed',
             'result': {'sections': {'capacity': {'status': 'active', 'delivery_gates': recorded,
                         'effective_targets': {'x': 20, 'threads': 24, 'linkedin': 6}},
                         'engagement': {'counts': {}, 'polls': {}, 'items': []},
                         'deliveries': {}, 'queue': {}}}}
        ]}
        result = summary(report, Path('operating-checks.json'))
        self.assertEqual(result['diagnostics']['delivery_gates'], fresh)
        self.assertEqual(result['diagnostics']['recorded_trial_delivery_gates'], recorded)
        self.assertEqual(result['diagnostics']['effective_targets']['x'], 20)


if __name__ == '__main__':
    unittest.main()
