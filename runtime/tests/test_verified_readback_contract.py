"""Fail-closed proof: durable receipt integrity gates pending and verified projections."""
from __future__ import annotations
import os
import tempfile
import unittest
from unittest.mock import patch

from ocpf_post import local_store, publication_reconcile, readback_evidence
from ocpf_post.state import state_dir


class VerifiedReadbackContractTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        patcher = patch.dict(os.environ, {'OCPF_POST_STATE_DIR': tmp.name})
        patcher.start()
        self.addCleanup(patcher.stop)
        self.path = state_dir() / 'publication-readbacks.json'
        self.record = {
            'schedule_id': 's1', 'campaign': 'TEST-ABC', 'provider': 'threads',
            'account_id': 'acct1', 'post_id': '12345', 'text_sha256': 'a'*64,
        }
        self.complete = {
            **self.record, 'original_status':'published_unverified',
            'status':'verified','observed_at':'2026-11-13T06:00:00+00:00',
            'http_status':200,'matches':{'post_id':True,'author':True,'text':True},
            'mismatch_fields':[],
        }

    def key(self, row):
        return publication_reconcile._key(row)

    def stored(self,row,*,key=None):
        local_store.write(self.path, {'schema_version':1,'observations':{key or self.key(row):row}})

    def test_complete_known_id_receipt_retains_verified_projection(self):
        self.stored(self.complete)
        self.assertEqual(publication_reconcile._checked_readback_state(self.path)['observations'][self.key(self.complete)], self.complete)
        self.assertTrue(readback_evidence.verified(self.record))

    def test_incomplete_status_only_is_corrupt_without_erasure(self):
        self.stored({'status':'verified'},key=self.key(self.record))
        before=self.path.read_bytes()
        with self.assertRaisesRegex(ValueError,'durable provider-readback state'):
            publication_reconcile._checked_readback_state(self.path)
        with patch.object(publication_reconcile, 'publication_inputs',return_value=([], [
            {**self.record, 'status':'published_unverified'}], [])):
            with self.assertRaisesRegex(ValueError,'durable provider-readback state'):
                publication_reconcile.pending()
            with self.assertRaisesRegex(ValueError,'durable provider-readback state'):
                publication_reconcile.reconcile(apply=False)
        self.assertFalse(readback_evidence.verified(self.record))
        self.assertEqual(self.path.read_bytes(),before)

    def test_matching_identity_without_provider_receipt_is_not_verified(self):
        self.stored({**self.record,'status':'verified'})
        with self.assertRaisesRegex(ValueError,'durable provider-readback state'):
            publication_reconcile._checked_readback_state(self.path)
        self.assertFalse(readback_evidence.verified(self.record))

    def test_wrong_digest_cannot_project_verified(self):
        self.stored(self.complete,key='b'*64)
        with self.assertRaisesRegex(ValueError,'durable provider-readback state'):
            publication_reconcile._checked_readback_state(self.path)
        self.assertFalse(readback_evidence.verified(self.record))

    def test_contradictory_provider_receipt_does_not_verify(self):
        for changes in (
            {'http_status':429}, {'http_status':True},
            {'matches':{'post_id':True,'author':True}},
            {'matches':{'post_id':True,'author':False,'text':True}},
            {'mismatch_fields':['author']}, {'observed_at':'never'},
            {'original_status':'scheduled'},
        ):
            with self.subTest(changes=changes):
                self.stored({**self.complete,**changes})
                with self.assertRaisesRegex(ValueError,'durable provider-readback state'):
                    publication_reconcile._checked_readback_state(self.path)
                self.assertFalse(readback_evidence.verified(self.record))

    def test_unverified_observation_remains_legible_without_terminal_claim(self):
        row={**self.record,'status':'readback_started','original_status':'published_unverified','observed_at':self.complete['observed_at']}
        self.stored(row)
        self.assertEqual(publication_reconcile._checked_readback_state(self.path)['observations'][self.key(row)],row)
        self.assertFalse(readback_evidence.verified(self.record))


if __name__ == '__main__':
    unittest.main()
