from datetime import datetime, timedelta, timezone
import unittest
from unittest.mock import patch

from ocpf_post import learning_supply

NOW = datetime(2026, 9, 13, 16, tzinfo=timezone.utc)
SOURCE_ID = 'sample-README-abc-1-insight'


def manifest(*, comparison_variant='practical'):
    source = {
        'type': 'repository_product_truth',
        'source_id': SOURCE_ID,
        'source_sha': 'abc',
    }
    if comparison_variant is not None:
        source['comparison_variant'] = comparison_variant
    return {
        'campaign': 'SAMPLE-AUTO-01I-ABC1234-X',
        'project': 'sample',
        'providers': ['x'],
        'payload_sha256': {'x': 'payload-sha'},
        'allocation': {'lane': 'evergreen'},
        'source': source,
    }


def publication(*, hours=72, status='published_verified', readback_verified=True,
                campaign='SAMPLE-AUTO-01I-ABC1234-X', post_id='post-1'):
    receipt = {
        'campaign': campaign,
        'provider': 'x',
        'account_id': '123',
        'post_id': post_id,
        'status': status,
        'readback_verified': readback_verified,
        'text_sha256': 'payload-sha',
    }
    return (campaign, 'x', '123', post_id), {
        'receipt': receipt,
        'at': NOW - timedelta(hours=hours),
    }


def measurement(*, campaign='SAMPLE-AUTO-01I-ABC1234-X', post_id='post-1',
                comparison_variant='practical'):
    return {
        'campaign': campaign,
        'provider': 'x',
        'account_id': '123',
        'post_id': post_id,
        'target_age_hours': 24,
        'exposure': 1000,
        'editorial': {
            'project': 'sample',
            'lane': 'evergreen',
            'variant': 'insight',
            'revision': 'abc',
            'topic': 'sample-README-abc-1',
            'text_sha256': 'payload-sha',
            'template_version': 'repository-static-v1',
            'comparison_variant': comparison_variant,
        },
    }


class LearningSupplyDiagnosticsTests(unittest.TestCase):
    def make_budget(self, *, source_manifest=None, publications=None, observations='default'):
        source_manifest = source_manifest or manifest()
        publications = publications or {}
        if observations == 'default':
            observations = [measurement()]
        with patch.object(learning_supply, 'enabled', return_value=True), \
             patch.object(learning_supply, 'campaign_ids', return_value=['SAMPLE-AUTO-01I-ABC1234-X']), \
             patch.object(learning_supply, 'builtin_manifest', return_value=source_manifest), \
             patch.object(learning_supply, 'publications', return_value=publications), \
             patch.object(learning_supply, '_feedback_observations', return_value=observations):
            return learning_supply.SamplingBudget(NOW)

    def item(self, **overrides):
        return {
            'predecessor_source_id': SOURCE_ID,
            'comparison_variant': 'practical',
            'sha': 'abc',
            **overrides,
        }

    def test_reports_no_published_predecessor(self):
        budget = self.make_budget()
        self.assertEqual(
            budget.reason(self.item(), 'x', '123', 'sample'),
            'predecessor_not_published',
        )

    def test_reports_unverified_predecessor(self):
        key, pub = publication(status='published_unverified', readback_verified=False)
        budget = self.make_budget(publications={key: pub})
        self.assertEqual(
            budget.reason(self.item(), 'x', '123', 'sample'),
            'predecessor_not_verified',
        )

    def test_reports_legacy_predecessor_missing_experiment_assignment(self):
        key, pub = publication()
        budget = self.make_budget(source_manifest=manifest(comparison_variant=None), publications={key: pub})
        self.assertEqual(
            budget.reason(self.item(), 'x', '123', 'sample'),
            'predecessor_missing_experiment_assignment',
        )

    def test_reports_arm_mismatch_without_admitting(self):
        key, pub = publication()
        budget = self.make_budget(source_manifest=manifest(comparison_variant='question'), publications={key: pub})
        self.assertEqual(
            budget.reason(self.item(), 'x', '123', 'sample'),
            'predecessor_experiment_arm_mismatch',
        )

    def test_reports_spacing_then_allows_same_verified_assignment_with_measurement(self):
        key, pub = publication(hours=47)
        budget = self.make_budget(publications={key: pub})
        self.assertEqual(budget.reason(self.item(), 'x', '123', 'sample'), 'sampling_spacing')

        key, pub = publication(hours=49)
        budget = self.make_budget(publications={key: pub})
        self.assertIsNone(budget.reason(self.item(), 'x', '123', 'sample'))

    def test_verified_predecessor_without_comparable_measurement_stays_deferred(self):
        key, pub = publication(hours=49)
        budget = self.make_budget(publications={key: pub}, observations=[])
        self.assertEqual(
            budget.reason(self.item(), 'x', '123', 'sample'),
            'predecessor_comparable_measurement_required',
        )

    def test_stale_or_unavailable_measurement_state_fails_closed(self):
        key, pub = publication(hours=49)
        budget = self.make_budget(publications={key: pub}, observations=None)
        self.assertEqual(
            budget.reason(self.item(), 'x', '123', 'sample'),
            'predecessor_measurement_state_unavailable',
        )

    def test_wrong_post_measurement_does_not_unlock_challenger(self):
        key, pub = publication(hours=49)
        budget = self.make_budget(publications={key: pub}, observations=[measurement(post_id='other')])
        self.assertEqual(
            budget.reason(self.item(), 'x', '123', 'sample'),
            'predecessor_comparable_measurement_required',
        )

    def test_multiple_predecessor_publications_are_ambiguous(self):
        key1, pub1 = publication(post_id='post-1')
        key2, pub2 = publication(campaign='SAMPLE-AUTO-01I-ABC1234-X-ALT', post_id='post-2')
        second_manifest = manifest()
        manifests = {
            'SAMPLE-AUTO-01I-ABC1234-X': manifest(),
            'SAMPLE-AUTO-01I-ABC1234-X-ALT': second_manifest,
        }
        with patch.object(learning_supply, 'enabled', return_value=True), \
             patch.object(learning_supply, 'campaign_ids', return_value=list(manifests)), \
             patch.object(learning_supply, 'builtin_manifest', side_effect=lambda cid: manifests[cid]), \
             patch.object(learning_supply, 'publications', return_value={key1: pub1, key2: pub2}), \
             patch.object(learning_supply, '_feedback_observations', return_value=[measurement()]):
            budget = learning_supply.SamplingBudget(NOW)
        self.assertEqual(
            budget.reason(self.item(), 'x', '123', 'sample'),
            'predecessor_publication_ambiguous',
        )


if __name__ == '__main__':
    unittest.main()
