import importlib.util
from pathlib import Path
from unittest.mock import patch

import unittest
import test_engagement as fixtures
from ocpf_post import engagement as e

spec = importlib.util.spec_from_file_location('reviewed_replies', Path(__file__).resolve().parents[1] / 'scripts/respond-to-reviewed-replies-20260910.py')
batch = importlib.util.module_from_spec(spec)
spec.loader.exec_module(batch)


class ReviewedReplyBatchTests(unittest.TestCase):
    setUp = fixtures.EngagementTests.setUp
    collect = fixtures.EngagementTests.collect
    def batch_items(self):
        self.collect()
        row = next(iter(e.read()['inbox'].values()))
        return [{'id': row['id'], 'provider': row['provider'], 'account_id': row['account_id'],
                 'context_sha256': e.digest(row['context']), 'text': 'A context-specific response.'}]

    def test_batch_preview_is_read_only_and_live_uses_receipt_guards(self):
        items = self.batch_items()
        original_send = e.send
        with patch.object(batch, 'REPLIES', items), patch.object(e, 'send', side_effect=lambda identity, **kw:
                original_send(identity, **kw, now=self.now, factory=lambda _: self.client)):
            self.assertFalse(batch.execute(now=self.now)['live'])
            self.assertEqual(e.read()['inbox'][items[0]['id']]['status'], 'pending')
            result = batch.execute(live=True, now=self.now)
            self.assertEqual(result['replies'][0]['result'], 'published_verified')
            self.assertEqual(batch.execute(live=True, now=self.now)['replies'][0]['result'], 'already_verified')
            self.assertEqual(len(self.client.sent), 1)

    def test_batch_checks_all_contexts_before_first_effect(self):
        items = self.batch_items()
        with patch.object(batch, 'REPLIES', items + [{**items[0], 'id': 'missing'}]):
            with self.assertRaises(ValueError): batch.execute(live=True, now=self.now)
        self.assertFalse(self.client.sent)
        self.assertEqual(e.read()['inbox'][items[0]['id']]['status'], 'pending')

    def test_batch_preserves_another_draft_and_has_fixed_expiry(self):
        items = self.batch_items()
        e.draft(items[0]['id'], 'Another reviewed response', now=self.now)
        with patch.object(batch, 'REPLIES', items):
            with self.assertRaises(ValueError): batch.execute(live=True, now=self.now)
            with self.assertRaises(ValueError): batch.execute(live=True, now=batch.REVIEW_END)
        self.assertFalse(self.client.sent)

