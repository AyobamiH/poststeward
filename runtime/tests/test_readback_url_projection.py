from collections import Counter
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import hashlib
import os
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch
from urllib.parse import parse_qs, urlparse

from ocpf_post import local_store, publication_reconcile as pr
from ocpf_post.capacity_experiment import ACCOUNTS, delivery_gate
from ocpf_post.readback_details import compare
from ocpf_post.schedule_semantics import classify_schedule
from ocpf_post.source_receipts import source_receipts
from ocpf_post.state import state_dir


NOW = datetime(2026, 9, 13, 10, 40, tzinfo=timezone.utc)


def text_hash(text):
    return hashlib.sha256(text.encode()).hexdigest()


def schedule(schedule_id, *, provider='x', account_id=None, campaign=None, post_id=None,
             status='published_unverified', text='Approved copy', minutes=0, **extra):
    account_id = account_id or (ACCOUNTS['x'] if provider == 'x' else 'urn:li:person:owner')
    post_id = post_id or ('123456' if provider == 'x' else 'urn:li:share:7500000000000000000')
    stamp = (NOW - timedelta(minutes=minutes)).isoformat()
    return {
        'schedule_id': schedule_id,
        'campaign': campaign or 'TEST-' + schedule_id,
        'provider': provider,
        'account_id': account_id,
        'post_id': post_id,
        'status': status,
        'text': text,
        'text_sha256': text_hash(text),
        'run_at': stamp,
        'updated_at': stamp,
        **extra,
    }


def receipt(row, *, status=None, readback_verified=None):
    value = {key: row[key] for key in ('schedule_id', 'campaign', 'provider', 'account_id', 'post_id', 'text_sha256')}
    value.update(status=status or row['status'], recorded_at=row['updated_at'])
    if readback_verified is not None:
        value['readback_verified'] = readback_verified
    return value


class ReadbackUrlProjectionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        env = patch.dict(os.environ, OCPF_POST_STATE_DIR=self.temp.name,
                         OCPF_POST_CONFIG_DIR=self.temp.name + '/config')
        env.start()
        self.addCleanup(env.stop)

    def save_verified_readback(self, row):
        observation = {key: row[key] for key in
                       ('schedule_id', 'campaign', 'provider', 'account_id', 'post_id', 'text_sha256')}
        observation['status'] = 'verified'
        local_store.write(state_dir() / 'publication-readbacks.json',
                          {'schema_version': 1, 'observations': {'proof': observation}})

    def test_x_expands_only_explicit_url_entities_then_compares_exactly(self):
        expected = {'post_id': '123', 'author': 'owner',
                    'text': 'Start here: https://opstruth.io\nProof & State.'}
        observed = {
            'post_id': '123', 'author': 'owner',
            'text': 'Start here: https://t.co/g25xLBY1mR\nProof &amp; State.',
            'entities': {'urls': [{'url': 'https://t.co/g25xLBY1mR',
                                   'expanded_url': 'https://opstruth.io'}]},
        }
        original = deepcopy(observed)

        result = compare(expected, observed, provider='x')
        self.assertEqual(result['mismatch_fields'], [])
        self.assertTrue(result['matches']['text'])
        self.assertEqual(observed, original)

        without_entities = compare(expected, {k: v for k, v in observed.items() if k != 'entities'}, provider='x')
        self.assertEqual(without_entities['mismatch_fields'], ['text'])
        threads = compare(expected, observed, provider='threads')
        self.assertEqual(threads['mismatch_fields'], ['text'])

    def test_x_read_requests_entities_and_returns_mapping(self):
        calls = []
        entity = {'url': 'https://t.co/abc', 'expanded_url': 'https://example.com'}
        client = SimpleNamespace(_bearer=lambda url: (calls.append(url) or (200, {
            'data': {'id': '123', 'author_id': 'owner', 'text': 'https://t.co/abc',
                     'entities': {'urls': [entity]}}
        })))

        status, observed = pr._read(client, 'x', '123')
        self.assertEqual(status, 200)
        query = parse_qs(urlparse(calls[0]).query)
        self.assertEqual(query['tweet.fields'], ['author_id,entities'])
        self.assertEqual(observed['entities'], {'urls': [entity]})

    def test_linkedin_5xx_circuit_breaks_remaining_known_id_reads(self):
        rows = [schedule('li-' + str(i), provider='linkedin',
                         post_id='urn:li:share:' + str(7500000000000000000 + i), minutes=i)
                for i in range(3)]
        client = SimpleNamespace(
            account=lambda: SimpleNamespace(account_id='urn:li:person:owner', username=None),
            _headers=lambda: {'Authorization': 'Bearer private'},
        )
        with patch.object(pr, 'publication_inputs', return_value=({}, rows, [])), \
             patch.object(pr, 'for_account', return_value=client), \
             patch('ocpf_post.providers.http.request_json', return_value=(500, {}, {})) as request:
            result = pr.reconcile(apply=True, now=NOW)

        self.assertEqual(result['reads_attempted'], 1)
        self.assertEqual(request.call_count, 1)
        counts = Counter(row['status'] for row in result['results'])
        self.assertEqual(counts['unavailable'], 1)
        self.assertEqual(counts['account_readback_deferred'], 2)
        self.assertTrue(all(row.get('http_status') == 500 for row in result['results']))

    def test_verified_sidecar_projects_without_mutating_schedule(self):
        row = schedule('projected', campaign='PAS-AUTO-01Q-03D7F7D', post_id='2098831582605783182')
        before = deepcopy(row)
        self.save_verified_readback(row)

        meaning = classify_schedule(row, now=NOW)
        self.assertEqual(meaning.state, 'published_verified_by_readback')
        self.assertTrue(meaning.readback_verified)
        self.assertFalse(meaning.requires_review)
        self.assertEqual(row, before)

        wrong = deepcopy(row)
        wrong['text_sha256'] = '0' * 64
        self.assertTrue(classify_schedule(wrong, now=NOW).requires_review)

    def test_delivery_observation_and_gate_consume_projection_without_rewrite(self):
        projected = schedule('projected', campaign='PAS-AUTO-01Q-03D7F7D', post_id='2098831582605783182')
        self.save_verified_readback(projected)
        projected_before = deepcopy(projected)
        projected_receipt = receipt(projected, status='published_unverified', readback_verified=False)

        manifest = {
            'project': 'proof-and-state', 'runtime_imported': True,
            'vault': {'id': 'proof-and-state-gtm', 'document_id': 'doc', 'key': 'key',
                      'base_campaign': 'PAS-AUTO-01Q-03D7F7D', 'revision': 'rev'},
        }
        delivery = source_receipts(
            'proof-and-state', reviewed_vaults=True,
            inputs=({projected['campaign']: manifest}, [projected], [projected_receipt]), now=NOW)
        self.assertEqual(delivery['readback_verified_count'], 1)
        self.assertTrue(delivery['scheduled_publications'][0]['readback_verified'])
        self.assertEqual(delivery['scheduled_publications'][0]['effective_schedule_state'],
                         'published_verified_by_readback')

        success = schedule('known-good', campaign='KNOWN-GOOD', post_id='2098000000000000000',
                           status='published_verified', minutes=5, readback_verified=True)
        success_receipt = receipt(success, status='published_verified', readback_verified=True)
        gate = delivery_gate('x', NOW,
                             receipts=[success_receipt, projected_receipt],
                             schedules=[success, projected])
        self.assertTrue(gate['healthy'])
        self.assertEqual(gate['recent_failed_or_uncertain'], 0)
        self.assertEqual(gate['blockers'], [])
        self.assertEqual(projected, projected_before)


if __name__ == '__main__':
    unittest.main()
