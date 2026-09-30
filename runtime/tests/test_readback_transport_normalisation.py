from datetime import datetime, timezone
import hashlib
import os
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from ocpf_post import publication_reconcile as pr
from ocpf_post.readback_details import compare


NOW = datetime(2026, 9, 13, 10, 30, tzinfo=timezone.utc)


class ReadbackTransportNormalisationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        env = patch.dict(os.environ, OCPF_POST_STATE_DIR=self.temp.name,
                         OCPF_POST_CONFIG_DIR=self.temp.name + '/config')
        env.start()
        self.addCleanup(env.stop)

    def test_x_decodes_one_html_entity_layer_without_relaxing_other_providers(self):
        expected = {'post_id': '123', 'author': 'owner',
                    'text': 'Proof & State keeps <evidence> exact.'}
        observed = {'post_id': '123', 'author': 'owner',
                    'text': 'Proof &amp; State keeps &lt;evidence&gt; exact.'}
        original = dict(observed)

        x_result = compare(expected, observed, provider='x')
        self.assertEqual(x_result['mismatch_fields'], [])
        self.assertTrue(x_result['matches']['text'])
        self.assertEqual(observed, original)

        threads_result = compare(expected, observed, provider='threads')
        self.assertEqual(threads_result['mismatch_fields'], ['text'])

    def test_x_reconcile_verifies_entity_serialized_provider_text(self):
        text = 'The system should not self-certify. Proof & State keeps roles separate.'
        schedule = {
            'schedule_id': 'sch_x_entity', 'campaign': 'PAS-ENTITY', 'provider': 'x',
            'account_id': '123', 'post_id': '456', 'status': 'published_unverified',
            'text': text, 'text_sha256': hashlib.sha256(text.encode()).hexdigest(),
            'run_at': NOW.isoformat(),
        }
        client = SimpleNamespace(account=lambda: SimpleNamespace(account_id='123', username='owner'))
        observed = {'post_id': '456', 'author': '123',
                    'text': 'The system should not self-certify. Proof &amp; State keeps roles separate.'}

        with patch.object(pr, 'publication_inputs', return_value=({}, [schedule], [])), \
             patch.object(pr, 'for_account', return_value=client), \
             patch.object(pr, '_read', return_value=(200, observed)):
            result = pr.reconcile(apply=True, now=NOW)

        self.assertEqual(result['results'][0]['status'], 'verified')
        self.assertEqual(result['results'][0]['mismatch_fields'], [])
        self.assertEqual(schedule['status'], 'published_unverified')

    def test_linkedin_read_uses_encoded_urn_and_author_view_context(self):
        client = SimpleNamespace(_headers=lambda: {'Authorization': 'Bearer test-token'})
        identity = 'urn:li:share:7504578732846678016'
        payload = {'id': identity, 'author': 'urn:li:person:owner',
                   'commentary': 'Exact copy', 'lifecycleState': 'PUBLISHED'}

        with patch('ocpf_post.providers.http.request_json', return_value=(200, {}, payload)) as request:
            status, observed = pr._read(client, 'linkedin', identity)

        self.assertEqual(status, 200)
        self.assertEqual(observed['post_id'], identity)
        self.assertEqual(request.call_args.kwargs['query'], {'viewContext': 'AUTHOR'})
        self.assertIn('urn%3Ali%3Ashare%3A7504578732846678016', request.call_args.args[0])
        self.assertEqual(request.call_args.kwargs['headers'], {'Authorization': 'Bearer test-token'})


if __name__ == '__main__':
    unittest.main()
