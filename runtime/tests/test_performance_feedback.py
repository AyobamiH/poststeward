from datetime import datetime, timedelta, timezone
from copy import deepcopy
import os
from pathlib import Path
import tempfile
from unittest.mock import patch
import unittest
from ocpf_post import performance_feedback as f, performance_review as review, local_store
from ocpf_post.portfolio_queue import _fair_choice
from ocpf_post.portfolio_diversity import DEFAULT_DIVERSITY


class PerformanceFeedbackTests(unittest.TestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup)
        env=patch.dict(os.environ,{'OCPF_POST_STATE_DIR':temp.name, 'OCPF_POST_CONFIG_DIR':temp.name+'/config'});env.start();self.addCleanup(env.stop)
        self.now=datetime(2026,9,12,12,tzinfo=timezone.utc)
        self.pub={};self.rows=[]
        for variant,likes in [('insight',10),('question',30)]:
            for i in range(5):
                campaign=f'TEST-{variant.upper()}-{i}';pid=str(100+len(self.rows))
                receipt={'campaign':campaign,'provider':'threads','account_id':'123','post_id':pid,
                         'status':'published_verified','text_sha256':pid,'recorded_at':f.stamp(self.now-timedelta(hours=24))}
                self.pub[(campaign,'threads','123',pid)]={'receipt':receipt,'at':self.now-timedelta(hours=24)}
                self.rows.append({**receipt,'captured_at':f.stamp(self.now),'availability':{'status':'available'},
                                  'metrics':{'views':1000,'likes':likes,'reposts':1,'quotes':0,'replies':999},
                                  'editorial':{'project':'sample','lane':'evergreen','variant':variant,
                                               'revision':'abc','topic':str(i),'text_sha256':pid,
                                               'template_version':f.TEMPLATE_VERSION}})

    def build(self,apply=False):
        with patch('ocpf_post.performance_review.publications',return_value=self.pub),patch('ocpf_post.performance.iter_snapshots',return_value=self.rows):
            return f.build(apply=apply,now=self.now)

    def test_comparable_signal_changes_selection_with_exploration_and_expiry(self):
        report=self.build(True); self.assertEqual(report['status'],'signals_available')
        signal=next(iter(report['signals'].values()));self.assertEqual(signal['variant'],'question')
        manifest={'project':'sample','payload_sha256':{'threads':'payload'},'allocation':{'lane':'evergreen'},
                  'source':{'type':'repository_product_truth','source_sha':'abc','source_id':'sample-topic-question'}}
        candidate={'provider':'threads','account_id':'123','text_sha256':'payload'}
        self.assertEqual(f.candidate_boost(candidate,manifest,f.snapshot(self.now),self.now),3)
        self.assertEqual(f.snapshot(self.now+timedelta(days=2)),{})
        common={'project':'sample','lane':'evergreen','priority':70,'family':'sample','provider':'threads',
                'prepared_at':f.stamp(self.now),'expires_at':None,'topic_key':'topic'}
        preferred={**common,'campaign':'A','performance_boost':3,'performance_expires_at':f.stamp(self.now+timedelta(hours=24))}
        other={**common,'campaign':'Z','performance_boost':0}
        self.assertEqual(_fair_choice([preferred,other],[],self.now,DEFAULT_DIVERSITY)['campaign'],'A')
        history=[{'at':self.now-timedelta(hours=7),'project':'sample','campaign':'OLDER','topic_key':'other','family':'sample'}]
        self.assertEqual(_fair_choice([preferred,other],history,self.now,DEFAULT_DIVERSITY)['campaign'],'Z')
        self.assertEqual(_fair_choice([preferred,other],[],self.now+timedelta(hours=25),DEFAULT_DIVERSITY)['campaign'],'Z')

    def test_duplicate_snapshots_do_not_manufacture_sample_size(self):
        self.rows=self.rows[:1]*10+self.rows[5:6]*10
        self.assertEqual(self.build()['signals'],{})

    def test_unknown_exposure_wrong_payload_future_and_wrong_account_excluded(self):
        for mutation in ('exposure','payload','future','account'):
            original=deepcopy(self.rows)
            for row in self.rows:
                if mutation=='exposure':row['metrics']['views']=None
                elif mutation=='payload':row['editorial']['text_sha256']='changed'
                elif mutation=='future':row['captured_at']=f.stamp(self.now+timedelta(days=1))
                else:row['account_id']='other'
            self.assertEqual(self.build()['signals'],{},mutation)
            self.rows=original

    def test_multi_part_root_metrics_never_train_copy_preference(self):
        for value in self.pub.values():
            value['receipt']['publication_type'] = 'thread'
            value['receipt']['part_count'] = 2
        for row in self.rows:
            row['publication_type'] = 'thread'
            row['part_count'] = 2
            row['metric_scope'] = 'root_post_only'
        report = self.build()
        self.assertEqual(report['signals'], {})
        self.assertEqual(report['excluded_counts'].get('multi_part_metrics_not_comparable'), len(self.rows))

    def test_own_reply_count_does_not_determine_preference(self):
        for row in self.rows:
            row['metrics']['likes']=1;row['metrics']['replies']=10000 if row['editorial']['variant']=='insight' else 0
        self.assertEqual(self.build()['signals'],{})

    def test_source_revision_and_low_topic_diversity_cannot_be_pooled(self):
        for row in self.rows:
            row['editorial']['revision']=row['editorial']['variant']
        self.assertEqual(self.build()['signals'],{})
        for row in self.rows:
            row['editorial'].update(revision='abc',topic='only-one')
        self.assertEqual(self.build()['signals'],{})

    def test_unmatched_project_or_revision_cannot_create_a_preference(self):
        for row in self.rows:
            row['editorial']['project'] = row['editorial']['variant']
        report = self.build()
        self.assertEqual(report['signals'], {})
        self.assertTrue(all(c['matched_topics'] == 0 for c in report['cohorts'][0]['comparisons']))

    def test_unknown_template_and_different_lanes_do_not_share_feedback(self):
        for row in self.rows:
            row['editorial']['template_version'] = 'unrecognised'
        self.assertEqual(self.build()['signals'], {})
        for row in self.rows:
            row['editorial']['template_version'] = f.TEMPLATE_VERSION
            row['editorial']['lane'] = row['editorial']['variant']
        self.assertEqual(self.build()['signals'], {})

    def test_paired_topic_rates_prevent_project_mix_from_creating_a_winner(self):
        # Each matched topic favours insight even though the unmatched high-rate
        # question posts make pooled question averages look stronger.
        for row in self.rows:
            row['metrics']['likes'] = 10 if row['editorial']['variant'] == 'insight' else 5
        for i, row in enumerate(deepcopy(self.rows[5:])):
            pid = str(900 + i)
            row.update(campaign='EXTRA-'+str(i), post_id=pid)
            row['editorial'].update(project='different-project', text_sha256=pid)
            row['metrics']['likes'] = 900
            receipt = {**row, 'status':'published_verified', 'text_sha256':pid}
            self.pub[(row['campaign'],'threads','123',pid)] = {'receipt':receipt,'at':self.now-timedelta(hours=24)}
            self.rows.append(row)
        self.assertEqual(next(iter(self.build()['signals'].values()))['variant'], 'insight')

    def test_actual_catalogue_produces_a_reachable_matched_template_preference(self):
        from ocpf_post import replenisher as r
        from ocpf_post.campaigns import builtin_manifest
        self.pub = {}; self.rows = []; generated = []
        profiles = r.source_profiles()['projects']
        for project in ('oneclickpostfactory', 'proof-and-state', 'parcelbasis'):
            profile = {**profiles[project], 'project':project}
            generated += r._static_campaigns_for_profile(profile, source_sha='a'*40,
                now=self.now-timedelta(hours=24), apply=True)
        for item in generated:
            manifest = builtin_manifest(item['campaign'])
            if item['lane'] != 'commercial' or 'x' not in item['providers']:
                continue
            pid = str(1000+len(self.rows)); sha = manifest['payload_sha256']['x']
            receipt = {'campaign':item['campaign'], 'provider':'x', 'account_id':'123',
                'post_id':pid, 'status':'published_verified', 'text_sha256':sha}
            meta = f.metadata(manifest, receipt)
            self.assertIsNotNone(meta)
            self.pub[(item['campaign'],'x','123',pid)] = {'receipt':receipt,'at':self.now-timedelta(hours=24)}
            self.rows.append({**receipt, 'captured_at':f.stamp(self.now), 'editorial':meta,
                'availability':{'status':'available'}, 'metrics':{'impressions':1000,
                    'likes':30 if meta['variant']=='question' else 10, 'reposts':1, 'quotes':0}})
        report = self.build(True)
        signal = next(iter(report['signals'].values()))
        self.assertEqual(signal['variant'], 'question')
        self.assertTrue(all(c['matched_topics']==6 for c in report['cohorts'][0]['comparisons']))
        pool = []
        for item in generated:
            if not item['campaign'].startswith('OCPF-AUTO-01'):
                continue
            manifest = builtin_manifest(item['campaign'])
            candidate = {'campaign':item['campaign'], 'provider':'x', 'account_id':'123',
                'text_sha256':manifest['payload_sha256']['x'], 'project':'oneclickpostfactory',
                'priority':manifest['allocation']['priority'], 'lane':'commercial',
                'prepared_at':f.stamp(self.now), 'expires_at':None, 'family':'sample', 'topic_key':'topic'}
            candidate.update(f.candidate_preference(candidate,manifest,report['signals'],self.now))
            pool.append(candidate)
        baseline = _fair_choice([{**c,'performance_boost':0} for c in pool], [], self.now, DEFAULT_DIVERSITY)
        chosen = _fair_choice(pool, [], self.now, DEFAULT_DIVERSITY)
        self.assertIn('01I-', baseline['campaign'])
        self.assertIn('01Q-', chosen['campaign'])
        history = [{'at':self.now-timedelta(hours=7),'project':'oneclickpostfactory',
            'campaign':'OLDER','topic_key':'other','family':'sample'}]
        self.assertIn('01I-', _fair_choice(pool, history, self.now, DEFAULT_DIVERSITY)['campaign'])

    def test_provisional_receipt_does_not_downgrade_verified_or_move_first_time(self):
        receipt=next(iter(self.pub.values()))['receipt']
        provisional={**receipt,'status':'published_unverified','recorded_at':f.stamp(self.now-timedelta(hours=25))}
        for rows in ([receipt,provisional],[provisional,receipt]):
            with patch.object(review,'iter_receipts',return_value=rows):
                row=next(iter(review.publications().values()))
                self.assertEqual(row['receipt']['status'],'published_verified')
                self.assertEqual(row['at'],self.now-timedelta(hours=25))

    def test_corrupt_feedback_is_ignored_without_state_repair(self):
        f.path().write_text('{broken')
        self.assertEqual(f.snapshot(self.now),{})
        self.assertEqual(f.path().read_text(),'{broken')

    def test_feedback_can_be_disabled_without_rewriting_evidence(self):
        self.build(True)
        before=f.path().read_bytes()
        f.configure(False)
        self.assertEqual(f.snapshot(self.now),{})
        self.assertEqual(f.path().read_bytes(),before)

    def test_one_provider_post_cannot_count_as_several_campaigns(self):
        for identity, value in list(self.pub.items()):
            duplicate = ('DUP-' + identity[0], *identity[1:])
            self.pub[duplicate] = value
        self.assertEqual(self.build()['signals'], {})
