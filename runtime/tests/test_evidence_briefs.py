from copy import deepcopy
from datetime import timedelta
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from ocpf_post.campaigns import builtin_manifest, builtin_text, campaign_ids, runtime_campaign_root
from ocpf_post.campaign_explain import explain_campaign
from ocpf_post.evidence_briefs import import_brief
from ocpf_post.onboarding import OnboardingError, import_project, import_campaign
from ocpf_post.portfolio import apply_refill, delivery_candidates
from ocpf_post.portfolio_diversity import _topic_key
from ocpf_post.publication_payload import build_publication, x_weighted_length
from ocpf_post.scheduler import create_schedule, run_due
from ocpf_post.state import iter_receipts
from test_onboarding import NOW, project_input, campaign_input
from test_scheduler import FakeProvider


def brief_input():
    return {'schema_version':1, 'brief_id':'receipt-lessons', 'project':'runtime-test', 'repository':'owner/source',
            'revision':'a'*40, 'claim_boundary':'Source behaviour only.', 'review_notes':'Reviewed distinct operator lessons, copy and source references.',
            'evidence':[{'id':'source','kind':'source','revision':'a'*40,'url':'https://github.com/owner/source/commit/'+'a'*40,
                         'observed_at':(NOW-timedelta(hours=1)).isoformat(),'summary':'The code implements publishing receipts and identity checks.', 'scope':'Source implementation only.'}],
            'claims':[{'id':'receipts','text':'Publishing records preserve the provider outcome.','kind':'source','evidence_ids':['source']},
                      {'id':'accounts','text':'Account checks bind delivery to its authorised destination.','kind':'source','evidence_ids':['source']}],
            'angles':[{'id':'receipt-lesson','campaign':'RTEST-BRIEF-001','title':'A publishing receipt','audience':'Operators',
                       'purpose':'Distinguish queued work from an observed publishing outcome.','destinations':{'x':'x-owner'},
                       'paragraphs':{'x':[{'text':'What happened after you pressed publish?'},{'claim':'receipts'}]},
                       'allocation':{'lane':'evergreen','priority':80,'prepared_at':NOW.isoformat(),'expires_at':(NOW+timedelta(days=1)).isoformat()}},
                      {'id':'account-lesson','campaign':'RTEST-BRIEF-002','title':'The correct destination','audience':'Engineers',
                       'purpose':'Explain destination identity as a prerequisite for authorised publication.','destinations':{'x':'x-owner'},
                       'paragraphs':{'x':[{'text':'Your audience is attached to an account.'},{'claim':'accounts'}]},
                       'allocation':{'lane':'evergreen','priority':75,'prepared_at':NOW.isoformat(),'expires_at':(NOW+timedelta(days=1)).isoformat()}}]}


class EvidenceBriefTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.env = patch.dict(os.environ, {'OCPF_POST_CONFIG_DIR':str(self.root/'config'),'OCPF_POST_STATE_DIR':str(self.root/'state')})
        self.env.start(); self.addCleanup(self.env.stop)
        path = self.write(project_input()); preview = import_project(path)
        import_project(path, apply=True, expected_sha256=preview['input_sha256'])

    def write(self, data):
        path = self.root/'input.json'; path.write_text(json.dumps(data)); return path

    def apply(self, value=None, **kwargs):
        path = self.write(value or brief_input()); preview = import_brief(path, now=NOW, **kwargs)
        return import_brief(path, apply=True, expected_sha256=preview['input_sha256'], now=NOW, **kwargs)

    def test_preview_exact_copy_and_no_state_or_network(self):
        path = self.write(brief_input())
        before = {str(p):p.read_bytes() for p in self.root.rglob('*') if p.is_file()}
        with patch('urllib.request.urlopen', side_effect=AssertionError('network forbidden')):
            result = import_brief(path, now=NOW)
        self.assertEqual(result['campaigns'][0]['texts']['x'], 'What happened after you pressed publish?\n\nPublishing records preserve the provider outcome.')
        self.assertFalse(result['allocation_enabled'])
        self.assertFalse(result['copy_review']['semantic_quality_verified'])
        self.assertEqual(before,{str(p):p.read_bytes() for p in self.root.rglob('*') if p.is_file()})

    def test_apply_requires_hash_and_preserves_reviewed_metadata_idempotently(self):
        path = self.write(brief_input())
        with self.assertRaises(OnboardingError): import_brief(path, apply=True, now=NOW)
        result = self.apply()
        manifest = builtin_manifest('RTEST-BRIEF-001')
        self.assertEqual(manifest['evidence_brief']['input_sha256'], result['input_sha256'])
        self.assertFalse(manifest['allocation']['enabled'])
        before = (runtime_campaign_root()/'RTEST-BRIEF-001'/'manifest.json').stat().st_mtime_ns
        self.assertEqual(self.apply()['result'],'already_present')
        self.assertEqual(before,(runtime_campaign_root()/'RTEST-BRIEF-001'/'manifest.json').stat().st_mtime_ns)
        path.write_text(path.read_text()+' ')
        with self.assertRaises(OnboardingError): import_brief(path, apply=True, expected_sha256=result['input_sha256'],now=NOW)

    def test_different_claims_remain_one_topic_and_reach_scheduler_receipt(self):
        self.apply(allocate=True)
        cids = {'RTEST-BRIEF-001','RTEST-BRIEF-002'}
        self.assertTrue(cids <= {r['campaign'] for r in delivery_candidates(now=NOW)})
        self.assertEqual(len({_topic_key(cid,builtin_manifest(cid)) for cid in cids}),1)
        provider = FakeProvider(account_id='12345')
        allocation = {'schema_version':1,'timezone':'Europe/London','minimum_lead_minutes':1,
                      'providers':{'x':{'daily_target':2,'window_start':'11:02','window_end':'12:00','development_max':0,'commercial_min':0}}}
        def reserve(**kwargs):
            return create_schedule(**kwargs, provider_factory=lambda _:provider)
        with patch('ocpf_post.portfolio._campaign_ids',return_value=sorted(cids)):
            result = apply_refill(now=NOW,horizon_minutes=75,policy=allocation,schedule_creator=reserve)
        self.assertEqual(result['errors'],[])
        self.assertEqual(len(result['scheduled']),2)
        schedule = result['scheduled'][0]
        run_due(now=NOW+timedelta(minutes=3),provider_factory=lambda _:provider)
        receipt = list(iter_receipts())[-1]
        self.assertEqual(receipt['status'],'published_verified')
        self.assertEqual(receipt['schedule_id'],schedule['schedule_id'])
        report = explain_campaign('RTEST-BRIEF-001','x',now=NOW)
        self.assertEqual(report['evidence_brief']['angle']['id'],'receipt-lesson')
        self.assertEqual(report['project_evidence']['runtime_behaviour'],'not_assessed')

    def test_source_evidence_cannot_support_declared_runtime_claim(self):
        value = brief_input(); value['claims'][0]['kind']='runtime'
        with self.assertRaisesRegex(OnboardingError,'own kind'): self.apply(value)
        self.assertFalse(runtime_campaign_root().exists())

    def test_revision_drift_unknown_claim_and_credentials_rejected(self):
        for edit in ('revision','claim','url','field','future'):
            value=brief_input()
            if edit=='revision': value['evidence'][0]['revision']='b'*40
            if edit=='claim': value['angles'][0]['paragraphs']['x'][1]['claim']='unknown'
            if edit=='url': value['evidence'][0]['url']='https://github.com/x?token=secret'
            if edit=='field': value['token']='not-allowed'
            if edit=='future': value['evidence'][0]['observed_at']=(NOW+timedelta(hours=1)).isoformat()
            with self.subTest(edit=edit),self.assertRaises(OnboardingError): self.apply(value)

    def test_reordered_copy_is_rejected_even_with_different_purpose(self):
        value=brief_input(); value['angles'][1]['paragraphs']['x']=list(reversed(value['angles'][0]['paragraphs']['x']))
        with self.assertRaisesRegex(OnboardingError,'reorders'): self.apply(value)
        self.assertFalse(runtime_campaign_root().exists())

    def test_existing_campaign_reuse_is_rejected(self):
        ordinary=campaign_input(); ordinary['texts']['x']='What happened after you pressed publish?\n\nPublishing records preserve the provider outcome.'
        path=self.write(ordinary); preview=import_campaign(path)
        import_campaign(path,apply=True,expected_sha256=preview['input_sha256'])
        with self.assertRaisesRegex(OnboardingError,'reorders'): self.apply()

    def test_overlap_is_visible_without_claiming_semantic_verification(self):
        value=brief_input(); value['angles'][1]['paragraphs']['x']=[{'text':'What happened after you pressed publish today?'},{'claim':'receipts'}]
        result=import_brief(self.write(value),now=NOW)
        self.assertTrue(result['copy_review']['similarity_warnings'])
        self.assertFalse(result['copy_review']['semantic_quality_verified'])

    def test_all_conflicts_checked_before_writing_and_partial_batch_resumes(self):
        value=brief_input(); value['angles'][1]['campaign']='OCPF-001'
        with self.assertRaises(OnboardingError): self.apply(value)
        self.assertFalse(runtime_campaign_root().exists())
        from ocpf_post.onboarding import _save_campaign
        calls=[]
        def interrupted(manifest,texts):
            calls.append(manifest['campaign'])
            if len(calls)==2: raise OSError('interruption')
            _save_campaign(manifest,texts)
        with patch('ocpf_post.onboarding._save_campaign',side_effect=interrupted),self.assertRaises(OSError): self.apply()
        self.assertTrue((runtime_campaign_root()/'RTEST-BRIEF-001').exists())
        self.assertEqual(self.apply()['result'],'imported')
        self.assertEqual(self.apply()['result'],'already_present')

    def test_long_x_brief_is_preserved_and_threaded_not_silently_shortened(self):
        value=brief_input(); value['angles'][0]['paragraphs']['x'].insert(0,{'text':'Word '*70+'end'})
        result = self.apply(value)
        cid = result['campaigns'][0]['manifest']['campaign']
        text = builtin_text(cid, 'x')
        self.assertIsNotNone(text)
        publication = build_publication('x', text or '')
        self.assertEqual(publication['publication_type'], 'thread')
        self.assertGreater(publication['part_count'], 1)
        self.assertTrue(all(x_weighted_length(row['text']) <= 275 for row in publication['parts']))
        self.assertIn('Word Word Word', text or '')
        self.assertNotIn('…', text or '')

    def test_example_has_distinct_copy_and_source_scoped_claims(self):
        value=json.loads(Path('examples/post-once-evidence-brief.json').read_text())
        # Bind the example to the isolated test project; its actual references and
        # copy are preserved, and do not become live evidence through this test.
        value['project']='runtime-test'
        for i,angle in enumerate(value['angles']):
            angle['campaign']=f'RTEST-EXAMPLE-00{i}'
            angle['destinations']={'x':'x-owner'}
        result=import_brief(self.write(value),now=NOW)
        self.assertEqual(len(result['campaigns']),3)
        self.assertFalse(result['copy_review']['similarity_warnings'])
        self.assertTrue(all(c['kind']=='source' for c in value['claims']))
