from datetime import datetime, timedelta, timezone
import os
import tempfile
import unittest
from unittest.mock import patch

from ocpf_post import local_store
from ocpf_post import performance_feedback as feedback
from ocpf_post import performance_review as review
from ocpf_post import learning_supply
from ocpf_post.performance import append_snapshot
from ocpf_post.state import append_receipt, state_dir

NOW = datetime(2026, 9, 13, 16, tzinfo=timezone.utc)


class EffectivePublicationVerificationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        env = patch.dict(os.environ, OCPF_POST_STATE_DIR=self.temp.name,
                         OCPF_POST_CONFIG_DIR=self.temp.name + '/config')
        env.start(); self.addCleanup(env.stop)
        self.receipt = {
            'campaign': 'TEST-AUTO-01Q-ABC1234', 'provider': 'x', 'account_id': '123',
            'schedule_id': 'sch-1', 'post_id': 'post-1', 'text_sha256': 'payload-sha',
            'status': 'published_unverified', 'readback_verified': False,
            'recorded_at': (NOW - timedelta(hours=24)).isoformat(),
        }
        append_receipt(self.receipt)

    def readback(self, **overrides):
        row = {**self.receipt, 'status': 'verified', 'observed_at': NOW.isoformat(), **overrides}
        local_store.write(state_dir() / 'publication-readbacks.json', {
            'schema_version': 1, 'observations': {'one': row}})

    def test_exact_sidecar_projects_verified_read_model_without_rewriting_receipt(self):
        self.readback()
        before = list(review.iter_receipts())
        key = (self.receipt['campaign'], 'x', '123', 'post-1')
        publication = review.publications()[key]
        self.assertTrue(publication['effective_verified'])
        self.assertEqual(publication['verification_basis'], 'readback')
        self.assertEqual(publication['receipt']['status'], 'published_verified')
        self.assertTrue(publication['receipt']['readback_verified'])
        self.assertEqual(publication['receipt']['ledger_status'], 'published_unverified')
        self.assertFalse(publication['receipt']['ledger_readback_verified'])
        self.assertEqual(list(review.iter_receipts()), before)

    def test_sidecar_identity_mismatch_fails_closed(self):
        self.readback(text_sha256='different')
        key = (self.receipt['campaign'], 'x', '123', 'post-1')
        publication = review.publications()[key]
        self.assertFalse(publication['effective_verified'])
        self.assertEqual(publication['verification_basis'], 'unverified')
        self.assertEqual(publication['receipt']['status'], 'published_unverified')
        self.assertFalse(publication['receipt']['readback_verified'])

    def test_feedback_accepts_exact_sidecar_verified_publication(self):
        self.readback()
        append_snapshot({
            **self.receipt, 'schema_version': 1, 'captured_at': NOW.isoformat(),
            'target_age_hours': 24, 'availability': {'status': 'available'},
            'metrics': {'impressions': 1000, 'likes': 20, 'reposts': 2, 'quotes': 1},
            'editorial': {'project': 'sample', 'lane': 'evergreen', 'variant': 'question',
                          'revision': 'abc', 'topic': 'topic-1', 'text_sha256': 'payload-sha',
                          'template_version': feedback.TEMPLATE_VERSION},
        })
        result = feedback.build(now=NOW)
        self.assertEqual(len(result['observations']), 1)
        self.assertEqual(result['observations'][0]['campaign'], self.receipt['campaign'])
        self.assertNotIn('no_verified_matching_receipt', result['excluded_counts'])

    def test_sampling_budget_accepts_measured_exact_readback_verified_predecessor(self):
        self.readback()
        manifest = {
            'campaign': self.receipt['campaign'], 'project': 'sample', 'providers': ['x'],
            'payload_sha256': {'x': 'payload-sha'}, 'allocation': {'lane': 'evergreen'},
            'source': {'type': 'repository_product_truth', 'source_sha': 'abc',
                       'source_id': 'sample-README-abc-1-insight',
                       'comparison_variant': 'question'},
        }
        append_snapshot({
            **self.receipt, 'schema_version': 1, 'captured_at': NOW.isoformat(),
            'target_age_hours': 24, 'availability': {'status': 'available'},
            'metrics': {'impressions': 1000, 'likes': 20, 'reposts': 2, 'quotes': 1},
            'editorial': {'project': 'sample', 'lane': 'evergreen', 'variant': 'insight',
                          'revision': 'abc', 'topic': 'sample-README-abc-1',
                          'text_sha256': 'payload-sha', 'template_version': feedback.TEMPLATE_VERSION,
                          'comparison_variant': 'question'},
        })
        later = NOW + timedelta(hours=25)
        feedback.build(apply=True, now=later)
        with patch.object(learning_supply, 'campaign_ids', return_value=[self.receipt['campaign']]), \
             patch.object(learning_supply, 'builtin_manifest', return_value=manifest):
            budget = learning_supply.SamplingBudget(later)
            item = {'predecessor_source_id': 'sample-README-abc-1-insight',
                    'comparison_variant': 'question', 'sha': 'abc'}
            self.assertIsNone(budget.reason(item, 'x', '123', 'sample'))


if __name__ == '__main__':
    unittest.main()
