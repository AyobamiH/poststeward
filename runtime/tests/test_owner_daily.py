import ast
from datetime import date, datetime, timezone
import hashlib
import importlib.util
from pathlib import Path
import unittest

PATH = Path(__file__).resolve().parents[1] / 'scripts' / 'owner-daily.py'
spec = importlib.util.spec_from_file_location('owner_report', PATH)
r = importlib.util.module_from_spec(spec)
spec.loader.exec_module(r)
DAY = date(2026, 9, 21)
NOW = datetime(2026, 9, 21, 14, tzinfo=timezone.utc)
TEXT = 'A receipt-backed exact message.'
DIGEST = hashlib.sha256(TEXT.encode()).hexdigest()


def publication(at='2026-09-21T08:00:00+00:00', provider='x', campaign='C', verified=True):
    return {'at': datetime.fromisoformat(at), 'effective_verified': verified,
            'verification_basis': 'receipt' if verified else 'unverified',
            'receipt': {'campaign': campaign, 'provider': provider, 'account_id': 'account',
                        'post_id': campaign+'-post', 'text_sha256': DIGEST,
                        'status': 'published_verified' if verified else 'published_unverified'}}


class OwnerReportTests(unittest.TestCase):
    def report(self, pubs, manifest=None, text=TEXT):
        return r.collect(DAY, NOW, pubs, lambda c: manifest or {}, lambda c, p: text)

    def test_three_providers_and_unverified_publications(self):
        pubs = {p: publication(provider=p, verified=p!='linkedin') for p in r.PROVIDERS}
        out = self.report(pubs)
        self.assertEqual(out['counts']['x'], {'published':1,'verified':1,'published_unverified':0})
        self.assertEqual(out['counts']['linkedin'], {'published':1,'verified':0,'published_unverified':1})

    def test_london_midnight_utc_previous_date_is_included(self):
        self.assertEqual(len(self.report({'a': publication(at='2026-09-20T23:15:00+00:00')})['publications']), 1)

    def test_yesterday_local_is_excluded(self):
        self.assertEqual(len(self.report({'a': publication(at='2026-09-20T22:59:59+00:00')})['publications']), 0)

    def test_after_cutoff_is_excluded(self):
        self.assertEqual(len(self.report({'a': publication(at='2026-09-21T15:00:00+00:00')})['publications']), 0)

    def test_matching_copy_is_included(self):
        row = self.report({'a': publication()})['publications'][0]
        self.assertEqual(row['published_text_matching_receipt'], TEXT)
        self.assertEqual(row['copy_status'], 'matches_receipt_sha256')

    def test_changed_copy_is_not_misrepresented(self):
        row = self.report({'a': publication()}, text='Updated content')['publications'][0]
        self.assertIsNone(row['published_text_matching_receipt'])
        self.assertEqual(row['copy_status'], 'current_copy_not_proven_to_match_receipt')

    def test_missing_hash_does_not_claim_copy(self):
        pub = publication(); pub['receipt'].pop('text_sha256')
        self.assertIsNone(self.report({'a':pub})['publications'][0]['published_text_matching_receipt'])

    def test_exact_vault_fields_no_inference_from_name(self):
        manifest = {'vault': {'id':'proof-and-state-gtm', 'document_id':'doc', 'base_campaign':'ENTRY',
                              'key':'KEY', 'revision':'r1', 'secret':'not allowed'}, 'project':'proof-and-state'}
        row = self.report({'a':publication(campaign='ANY-ID')}, manifest)['publications'][0]
        self.assertEqual(row['vault']['id'], 'proof-and-state-gtm')
        self.assertNotIn('secret', row['vault'])
        self.assertEqual(self.report({'a':publication(campaign='VAULT-LOOKING-ID')})['publications'][0]['vault'], {})

    def test_missing_manifest_keeps_publication_without_invented_origin(self):
        def missing(_): raise ValueError('private message')
        out = r.collect(DAY,NOW,{'a':publication()},missing,lambda c,p:None)
        self.assertEqual(out['counts']['x']['published'], 1)
        self.assertEqual(out['publications'][0]['metadata_error_type'], 'ValueError')
        self.assertNotIn('private message', str(out))

    def test_same_campaign_manifest_read_once_for_all_providers(self):
        calls=[]
        r.collect(DAY,NOW,{p:publication(provider=p) for p in r.PROVIDERS},
                  lambda c: calls.append(c) or {},lambda c,p:TEXT)
        self.assertEqual(calls,['C'])

    def test_naive_timestamp_not_treated_as_london_or_utc(self):
        pub = publication(); pub['at']=datetime(2026,9,21,8)
        self.assertEqual(self.report({'a':pub})['publications'],[])

    def test_python_310_grammar(self):
        ast.parse(PATH.read_text(), feature_version=(3,10))


if __name__=='__main__': unittest.main(verbosity=2)
