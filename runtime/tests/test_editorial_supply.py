"""Additive editorial tooling; fixtures never authorise or contact a provider."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('editorial_supply', ROOT / 'scripts/editorial-supply.py')
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)
NOW = datetime(2026, 9, 14, 20, 30, tzinfo=timezone.utc)
PACKET = ROOT / 'docs/gtm/editorial-recovery-20260914/batch.json'


def route(account='founder', ready=0, reserved=0, posts=0, intent=True):
    return {'provider': 'x', 'account_id': account, 'aliases': ['x-founder'],
            'runnable': ready, 'active_reservations': reserved,
            'verified_effects_24h': posts, 'unverified_effects_total': 0,
            'publishing_intent': ['enabled_vault'] if intent else [],
            'candidate_exclusion_counts': {'vault entry already consumed or ambiguous on this destination': 3},
            'latest_verified_effect': None}


def coverage():
    return {'schema_version': 1, 'status': 'observed', 'observed_at': NOW.isoformat(),
            'registered_projects': 2, 'projects': [
                {'project': 'alpha', 'source': {'status': 'observed'},
                 'vaults': [{'active_entries': 3, 'status': 'current'}],
                 'routes': [route(), route('brand')]},
                {'project': 'beta', 'source': {'status': 'observed'}, 'vaults': [],
                 'routes': [route(ready=4, posts=2)]}]}


class EditorialSupplyTests(unittest.TestCase):
    def test_shared_account_does_not_hide_empty_project(self):
        result = m.build_workpack(coverage(), NOW)
        self.assertEqual(2, len(result['supply_reviews']))
        self.assertEqual(2, result['physical_account_count'])
        accounts = {r['account_id']: r for r in result['accounts']}
        self.assertFalse(accounts['founder']['empty_inventory'])
        self.assertFalse(accounts['founder']['no_verified_post_24h'])
        self.assertTrue(accounts['brand']['empty_inventory'])
        self.assertTrue(accounts['brand']['no_verified_post_24h'])

    def test_approved_doc_count_does_not_hide_consumption(self):
        result = m.build_workpack(coverage(), NOW)
        self.assertEqual('alpha', result['supply_reviews'][0]['project'])

    def test_active_reservation_is_not_empty(self):
        data = coverage(); data['projects'][0]['routes'][1]['active_reservations'] = 1
        self.assertEqual(1, len(m.build_workpack(data, NOW)['supply_reviews']))

    def test_no_intent_cannot_gain_authority(self):
        data = coverage(); data['projects'][0]['routes'] = [route(intent=False)]
        self.assertEqual([], m.build_workpack(data, NOW)['supply_reviews'])

    def test_no_implicit_replay_or_activation(self):
        self.assertTrue(all(r['permission_to_replay_or_activate'] is False
                            for r in m.build_workpack(coverage(), NOW)['supply_reviews']))

    def test_stale_snapshot_not_current(self):
        self.assertEqual('stale_or_future_evidence', m.build_workpack(coverage(), NOW+timedelta(hours=2))['status'])

    def test_future_snapshot_not_current(self):
        self.assertEqual('stale_or_future_evidence', m.build_workpack(coverage(), NOW-timedelta(seconds=1))['status'])

    def test_bad_counts_are_not_silent_zeroes(self):
        for value in [None, False, -1, 1.5, '0']:
            data = coverage(); data['projects'][0]['routes'][0]['runnable'] = value
            with self.assertRaises(ValueError): m.build_workpack(data, NOW)

    def test_missing_projects_rejected(self):
        data = coverage(); data['projects'].pop()
        with self.assertRaises(ValueError): m.build_workpack(data, NOW)

    def test_duplicate_project_rejected(self):
        data = coverage(); data['projects'][1] = deepcopy(data['projects'][0])
        with self.assertRaises(ValueError): m.build_workpack(data, NOW)

    def test_duplicate_route_rejected(self):
        data = coverage(); data['projects'][0]['routes'].append(route())
        with self.assertRaises(ValueError): m.build_workpack(data, NOW)

    def test_failed_snapshot_rejected(self):
        data = coverage(); data['status'] = 'snapshot_changed'
        with self.assertRaises(ValueError): m.build_workpack(data, NOW)

    def test_linkedin_unverified_not_declared_failed(self):
        data = coverage(); r = data['projects'][0]['routes'][1]
        r['provider'] = 'linkedin'; r['unverified_effects_total'] = 3
        result = m.build_workpack(data, NOW)
        item = next(a for a in result['accounts'] if a['provider'] == 'linkedin')
        self.assertEqual(3, item['unverified_retained'])
        self.assertNotIn('failed', item)

    def test_no_mutation(self):
        data = coverage(); before = deepcopy(data)
        m.build_workpack(data, NOW)
        self.assertEqual(before, data)

    def test_wrapped_report_requires_final_marker(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d)/'report.txt'
            prefix = '=== ALL-PROJECT PUBLISHING AND METRICS AUDIT ===\n'
            p.write_text(prefix+json.dumps(coverage()))
            with self.assertRaises(ValueError): m.load_report(p)
            p.write_text(prefix+json.dumps(coverage())+'\n=== AUDIT COMPLETE ===\n')
            self.assertEqual(coverage(), m.load_report(p))

    def test_duplicate_json_keys_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'bad.json'; p.write_text('{"status":"observed","status":"bad"}')
            with self.assertRaises(ValueError): m.load_report(p)

    def test_six_actual_reviewed_entries(self):
        self.assertEqual(6, len(m.review_batch(m.load_report(PACKET))['entries']))

    def test_long_threads_editorial_copy_uses_whole_publication_contract(self):
        from ocpf_post.publication_payload import build_publication
        text = (
            "Long-form Threads copy should preserve the useful argument instead of being shortened merely "
            "to fit one post. The publishing layer can freeze a native reply chain before the first effect. "
            "That lets editorial review cover the complete approved message while provider execution remains "
            "bounded, inspectable and non-retryable after uncertain effects. "
        ) * 2
        entry = {
            'campaign': 'EX-THREADS-LONG-1', 'provider': 'threads', 'title': 'Lossless Threads copy',
            'text': text.strip(),
            'allocation': {'lane': 'evergreen', 'priority': 72,
                           'prepared_at': NOW.isoformat(), 'expires_at': (NOW + timedelta(days=7)).isoformat()},
            'status': 'APPROVED',
        }
        entry['approval_sha256'] = m.digest(entry)
        review = {
            'campaign': entry['campaign'],
            'payload_sha256': hashlib.sha256(entry['text'].encode()).hexdigest(),
            'audience_response': 'not_observed', 'decision': 'approved_by_agent_editor',
            'audience': 'operators', 'recognisable_situation': 'useful copy exceeds one Threads post',
            'useful_action': 'preserve and segment the complete approved message',
            'product_connection': 'post-once freezes native reply-chain parts before publishing',
            'novelty_rationale': 'tests the new whole-publication editorial boundary',
            'evidence_boundary': 'structural acceptance only; no provider consequence',
        }
        packet = {'schema_version': 1, 'batches': [{
            'project': 'example', 'source_refs': ['source'], 'document_id': 'doc',
            'account_id': 'threads-account', 'destinations': {'threads': 'threads-founder'},
            'entries': [entry], 'editorial_reviews': [review],
        }]}
        result = m.review_batch(packet)
        self.assertEqual('review_records_valid', result['status'])
        publication = build_publication('threads', entry['text'])
        self.assertEqual('thread', publication['publication_type'])
        self.assertGreater(publication['part_count'], 1)

    def test_editorial_copy_beyond_whole_publication_envelope_is_rejected(self):
        text = 'x' * 12501
        entry = {
            'campaign': 'EX-THREADS-TOO-LONG', 'provider': 'threads', 'title': 'Too long',
            'text': text,
            'allocation': {'lane': 'evergreen', 'priority': 72,
                           'prepared_at': NOW.isoformat(), 'expires_at': (NOW + timedelta(days=7)).isoformat()},
            'status': 'APPROVED',
        }
        entry['approval_sha256'] = m.digest(entry)
        review = {
            'campaign': entry['campaign'],
            'payload_sha256': hashlib.sha256(text.encode()).hexdigest(),
            'audience_response': 'not_observed', 'decision': 'approved_by_agent_editor',
            'audience': 'operators', 'recognisable_situation': 'oversized copy',
            'useful_action': 'refuse it', 'product_connection': 'publication safety envelope',
            'novelty_rationale': 'bounds whole publication size',
            'evidence_boundary': 'structural rejection only',
        }
        packet = {'schema_version': 1, 'batches': [{
            'project': 'example', 'source_refs': ['source'], 'document_id': 'doc',
            'account_id': 'threads-account', 'destinations': {'threads': 'threads-founder'},
            'entries': [entry], 'editorial_reviews': [review],
        }]}
        with self.assertRaises(ValueError):
            m.review_batch(packet)

    def test_text_change_invalidates_review_even_if_reapproved(self):
        packet=m.load_report(PACKET); e=packet['batches'][0]['entries'][0]
        e['text']='Replacement words'
        e['approval_sha256']=m.digest({k:v for k,v in e.items() if k!='approval_sha256'})
        with self.assertRaises(ValueError): m.review_batch(packet)

    def test_incomplete_editorial_assessment_rejected(self):
        packet=m.load_report(PACKET)
        del packet['batches'][0]['editorial_reviews'][0]['recognisable_situation']
        with self.assertRaises(ValueError): m.review_batch(packet)

    def test_unapproved_review_rejected(self):
        packet=m.load_report(PACKET)
        packet['batches'][0]['editorial_reviews'][0]['decision']='hold'
        with self.assertRaises(ValueError): m.review_batch(packet)

    def test_reordering_does_not_make_new_angle(self):
        packet=m.load_report(PACKET); b=packet['batches'][0]; e=b['entries'][1]
        e['text']=' '.join(reversed(b['entries'][0]['text'].split()))
        e['approval_sha256']=m.digest({k:v for k,v in e.items() if k!='approval_sha256'})
        b['editorial_reviews'][1]['payload_sha256']=hashlib.sha256(e['text'].encode()).hexdigest()
        with self.assertRaises(ValueError): m.review_batch(packet)


class ExistingVaultContractTests(unittest.TestCase):
    def test_real_prepare_accepts_only_fresh_exact_reviewed_payloads(self):
        from ocpf_post import registry, vault_sync
        packet=m.load_report(PACKET)
        for batch in packet['batches']:
            provider=next(iter(batch['destinations']))
            alias=batch['destinations'][provider]
            # Simulate the separately evidenced host binding; this is not live identity verification.
            def binding(project, requested_alias, expected_provider=None):
                if (project, requested_alias, expected_provider) != (batch['project'], alias, provider):
                    raise ValueError('Unexpected destination')
                return {'provider': provider, 'account_id': batch['account_id'], 'alias': alias}
            prefix='POST-ONCE APPROVED ENTRIES '
            text=prefix+'BEGIN\n'+json.dumps({'schema_version':1,'entries':batch['entries']})+'\n'+prefix+'END\n'
            policy={'id':'test-only','project':batch['project'],'document_id':batch['document_id'],
                    'destinations':batch['destinations']}
            document={'document_id':batch['document_id'],'text':text,'version':'1'}
            with tempfile.TemporaryDirectory() as d, patch.dict(os.environ, {'OCPF_POST_STATE_DIR':d+'/state','OCPF_POST_CONFIG_DIR':d+'/config'}), patch.object(registry,'resolve_account',side_effect=binding):
                prepared, active, skipped=vault_sync.prepare(policy, document, NOW)
                self.assertEqual(3,len(prepared)); self.assertFalse(skipped)
                self.assertEqual([e['text'] for e in batch['entries']], [t[provider] for _,t in prepared])
                self.assertTrue(all(mf['allocation']['priority']==72 for mf,_ in prepared))
                expired,_,skips=vault_sync.prepare(policy, document, NOW+timedelta(days=8))
                self.assertEqual([],expired); self.assertEqual(3,len(skips))
                self.assertEqual([],list(Path(d).rglob('*')))
