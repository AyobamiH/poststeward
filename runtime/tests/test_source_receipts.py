from copy import deepcopy
from datetime import timedelta
import hashlib
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from ocpf_post.campaigns import builtin_manifest, builtin_text, runtime_campaign_root
from ocpf_post.onboarding import import_project
from ocpf_post.replenisher import _static_campaigns_for_profile
from ocpf_post.scheduler import create_schedule, run_due, schedule_records
from ocpf_post.source_receipts import source_receipts
from ocpf_post.state import append_receipt, iter_receipts
from test_onboarding import NOW, project_input
from test_runtime_sources import policy
from test_scheduler import FakeProvider


class SourceReceiptTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)
        env=patch.dict(os.environ,{'OCPF_POST_CONFIG_DIR':str(self.root/'config'),'OCPF_POST_STATE_DIR':str(self.root/'state')})
        env.start(); self.addCleanup(env.stop)
        path=self.root/'project.json'; path.write_text(json.dumps(project_input()))
        preview=import_project(path); import_project(path,apply=True,expected_sha256=preview['input_sha256'])
        self.profile=policy(); self.profile['runtime_source']={'id':'activation-1','repository_id':123}
        configured=patch('ocpf_post.source_receipts._configured',return_value=(self.profile,123))
        configured.start(); self.addCleanup(configured.stop)
        self.cid=_static_campaigns_for_profile(self.profile,source_sha='a'*40,now=NOW,apply=True)[0]['campaign']

    def publish(self, *, verify=True):
        provider=FakeProvider(account_id='12345',verify=verify)
        schedule=create_schedule(campaign=self.cid,provider='x',at=(NOW+timedelta(minutes=2)).isoformat(),now=NOW,provider_factory=lambda _:provider)
        run_due(now=NOW+timedelta(minutes=3),provider_factory=lambda _:provider)
        return schedule

    def test_pending_does_not_publish_or_create_state(self):
        before={str(p):p.read_bytes() for p in self.root.rglob('*') if p.is_file()}
        with patch('urllib.request.urlopen',side_effect=AssertionError('no network')):
            report=source_receipts('runtime-test')
        self.assertEqual(report['status'],'pending')
        self.assertEqual(report['generated_campaign_count'],3)
        self.assertEqual(before,{str(p):p.read_bytes() for p in self.root.rglob('*') if p.is_file()})

    def test_scheduled_receipt_closes_milestone_across_superseded_revisions(self):
        schedule=self.publish()
        path=runtime_campaign_root()/self.cid/'manifest.json'
        manifest=json.loads(path.read_text()); manifest['allocation']['enabled']=False
        manifest['allocation']['superseded_by']='README:'+'b'*40
        path.write_text(json.dumps(manifest))
        _static_campaigns_for_profile(self.profile,source_sha='b'*40,now=NOW,apply=True)
        report=source_receipts('runtime-test')
        self.assertEqual(report['status'],'observed')
        self.assertEqual(report['readback_verified_count'],1)
        self.assertEqual(len(report['scheduled_publications']),1)  # provisional+verified are one post
        self.assertEqual(report['scheduled_publications'][0]['schedule_id'],schedule['schedule_id'])
        self.assertEqual(report['scheduled_publications'][0]['link'],'explicit_schedule_id')

    def test_direct_receipt_alone_does_not_complete_scheduled_milestone(self):
        text=builtin_text(self.cid,'x')
        append_receipt({'campaign':self.cid,'provider':'x','account_id':'12345','text_sha256':hashlib.sha256(text.encode()).hexdigest(),
                        'post_id':'direct-post','status':'published_verified','readback_verified':True})
        report=source_receipts('runtime-test')
        self.assertEqual(report['status'],'pending')
        self.assertEqual(report['unmatched_publication_receipt_count'],1)

    def test_account_payload_post_and_explicit_schedule_must_match(self):
        self.publish()
        receipts=list(iter_receipts())
        for field in ('account_id','text_sha256','post_id','schedule_id'):
            rows=deepcopy(receipts)
            for row in rows: row[field]='different'
            with self.subTest(field=field),patch('ocpf_post.source_receipts.iter_receipts',return_value=rows):
                self.assertEqual(source_receipts('runtime-test')['status'],'pending')

    def test_unverified_receipt_is_observed_but_not_upgraded(self):
        self.publish(verify=False)
        report=source_receipts('runtime-test')
        self.assertEqual(report['status'],'observed')
        self.assertEqual(report['readback_verified_count'],0)
        self.assertEqual(report['scheduled_publications'][0]['receipt_status'],'published_unverified')

    def test_incomplete_schedule_cannot_be_completed_by_provisional_receipt(self):
        self.publish()
        records=schedule_records()
        records[0]['status']='executing'
        with patch('ocpf_post.source_receipts.schedule_records',return_value=records):
            self.assertEqual(source_receipts('runtime-test')['status'],'pending')

    def test_brief_or_other_repository_does_not_complete_source_milestone(self):
        self.publish()
        for change in ({'runtime_generated':False},{'source':{'repository':'other/repository'}},{'runtime_source':{'repository_id':999}}):
            manifest={**builtin_manifest(self.cid),**change}
            with self.subTest(change=change),patch('ocpf_post.source_receipts.builtin_manifest',return_value=manifest):
                self.assertEqual(source_receipts('runtime-test')['status'],'pending')

    def test_legacy_matched_receipt_is_labelled_as_correlated(self):
        self.publish()
        rows=list(iter_receipts())
        for row in rows: row.pop('schedule_id',None)
        with patch('ocpf_post.source_receipts.iter_receipts',return_value=rows):
            result=source_receipts('runtime-test')
        self.assertEqual(result['status'],'observed')
        self.assertEqual(result['scheduled_publications'][0]['link'],'legacy_schedule_post_identity_hash_match')
