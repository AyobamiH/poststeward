from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from ocpf_post import vault_sync as vault
from ocpf_post import application_evidence as app
from ocpf_post import performance_review as perf
from ocpf_post.google_vault import document_text, read_document
from ocpf_post.onboarding import import_project
from ocpf_post.campaigns import builtin_manifest, builtin_text, runtime_campaign_root
from ocpf_post.scheduler import create_schedule, run_due, schedule_records, RunnerLock
from ocpf_post.source_receipts import source_receipts
from ocpf_post.state import append_receipt, iter_receipts
from test_onboarding import project_input
from test_scheduler import FakeProvider


class OperatingLoopTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(); self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        env = patch.dict(os.environ, {'OCPF_POST_CONFIG_DIR': str(self.root/'config'), 'OCPF_POST_STATE_DIR': str(self.root/'state')})
        env.start(); self.addCleanup(env.stop)
        self.now = datetime.now(timezone.utc)
        path = self.write(project_input()); result = import_project(path)
        import_project(path, apply=True, expected_sha256=result['input_sha256'])
        self.policy = {'schema_version':1, 'id':'test-vault', 'project':'runtime-test', 'document_id':'abcdefghijklm',
                       'destinations':{'x':'x-owner'}, 'max_age_minutes':60}
        path = self.write(self.policy); result = vault.register(path)
        vault.register(path, apply=True, expected_sha256=result['input_sha256'], enable=True)
        self.entry = {'campaign':'RTEST-VAULT-001','provider':'x','title':'A reviewed lesson', 'text':'Evidence supports a scoped claim.',
                      'status':'APPROVED', 'allocation':{'lane':'evergreen','priority':75,
                          'prepared_at':(self.now-timedelta(hours=1)).isoformat(), 'expires_at':(self.now+timedelta(days=1)).isoformat()}}
        self.approve()
        self.provider = FakeProvider(account_id='12345')

    def write(self, value):
        path = self.root/'input.json'; path.write_text(json.dumps(value)); return path

    def approve(self):
        self.entry['approval_sha256'] = vault.digest({k:v for k,v in self.entry.items() if k != 'approval_sha256'})

    def document(self, version='1', entries=None):
        return {'document_id': self.policy['document_id'], 'version':version, 'modified_at':self.now.isoformat(),
                'text': 'Historical prose is not authority.\n'+vault.BEGIN+'\n'+json.dumps({'schema_version':1,'entries':entries if entries is not None else [self.entry]})+'\n'+vault.END}

    def sync(self, version='1', entries=None):
        return vault.sync(apply=True, reader=lambda _:self.document(version, entries), now=self.now)

    def reserve(self, cid):
        return create_schedule(campaign=cid, provider='x', at=(self.now+timedelta(minutes=2)).isoformat(), now=self.now, provider_factory=lambda _:self.provider)

    def test_preview_has_no_campaign_or_receipt_effect(self):
        result = vault.sync(reader=lambda _:self.document(), now=self.now)
        self.assertEqual(result['vaults'][0]['result'],'preview')
        self.assertFalse(runtime_campaign_root().exists()); self.assertEqual(list(iter_receipts()),[])

    def test_pressure_defers_new_vault_revision_but_still_cancels_withdrawn_schedule(self):
        cid = self.sync()['vaults'][0]['campaigns'][0]
        saved = self.reserve(cid)
        self.entry['text'] = 'A newly approved revision.'
        self.approve()
        with patch('ocpf_post.scoped_admission.Budget.admit', return_value={'admitted': False, 'reasons': ['global_resource_ceiling']}):
            result = self.sync(version='2')['vaults'][0]
        self.assertEqual(result['result'], 'synced')
        self.assertEqual(result['campaigns'], [])
        self.assertEqual(len(result['deferred']), 1)
        self.assertEqual(result['cancelled'], [saved['schedule_id']])
        self.assertEqual(schedule_records()[0]['status'], 'cancelled')
        # The approved document remains the catalogue; later admission imports
        # the same revision, without renewing its original expiry.
        resumed = self.sync(version='2')['vaults'][0]
        self.assertEqual(len(resumed['campaigns']), 1)
        self.assertEqual(builtin_manifest(resumed['campaigns'][0])['allocation']['expires_at'], self.entry['allocation']['expires_at'])
        self.assertEqual(list(iter_receipts()), [])

    def test_corrupt_admission_does_not_disable_vault_withdrawal(self):
        from ocpf_post import admission
        cid = self.sync()['vaults'][0]['campaigns'][0]
        saved = self.reserve(cid)
        admission.state_file().write_text('corrupt')
        result = self.sync(version='2', entries=[])['vaults'][0]
        self.assertEqual(result['cancelled'], [saved['schedule_id']])
        self.assertEqual(result['result'], 'synced')

    def test_approved_sync_schedules_and_publishes_through_existing_runner(self):
        result=self.sync(); cid=result['vaults'][0]['campaigns'][0]
        self.assertIsNone(vault.guard(builtin_manifest(cid),'x'))
        scheduled=self.reserve(cid)
        run_due(now=self.now+timedelta(minutes=3),provider_factory=lambda _:self.provider)
        self.assertEqual(schedule_records()[0]['status'],'published_verified')
        self.assertEqual(list(iter_receipts())[-1]['schedule_id'],scheduled['schedule_id'])
        self.assertEqual(source_receipts('runtime-test',reviewed_briefs=True)['status'],'pending')

        from ocpf_post.operations_snapshot import portfolio_report, save_report
        from ocpf_post import local_store
        observed = portfolio_report(now=self.now+timedelta(minutes=4))
        self.assertEqual(observed['status'], 'observed')
        publication = observed['projects']['runtime-test']['items']['vault_publication']
        self.assertEqual(publication['status'], 'observed')
        self.assertEqual(publication['readback_verified_count'], 1)
        self.assertEqual(publication['scheduled_publications'][0]['schedule_id'], scheduled['schedule_id'])
        saved = save_report(observed)
        self.assertEqual(local_store.read(Path(saved['report_file'])), observed)
        self.assertEqual(Path(saved['report_file']).stat().st_mode & 0o777, 0o600)

    def test_vault_receipt_requires_its_own_schedule_and_preserves_unverified(self):
        cid = self.sync()['vaults'][0]['campaigns'][0]
        self.assertEqual(source_receipts('runtime-test', reviewed_vaults=True)['status'], 'pending')
        self.provider.verify = False
        self.reserve(cid)
        run_due(now=self.now+timedelta(minutes=3), provider_factory=lambda _: self.provider)
        observation = source_receipts('runtime-test', reviewed_vaults=True)
        self.assertEqual(observation['status'], 'observed')
        self.assertEqual(observation['readback_verified_count'], 0)
        rows = list(iter_receipts())
        for field in ('account_id', 'text_sha256', 'post_id', 'schedule_id'):
            with self.subTest(field=field), patch('ocpf_post.source_receipts.iter_receipts',
                                                 return_value=[{**r, field: 'wrong'} for r in rows]):
                self.assertEqual(source_receipts('runtime-test', reviewed_vaults=True)['status'], 'pending')

    def test_vault_historical_receipt_survives_withdrawal(self):
        cid = self.sync()['vaults'][0]['campaigns'][0]
        self.reserve(cid)
        run_due(now=self.now+timedelta(minutes=3), provider_factory=lambda _: self.provider)
        self.sync('2', entries=[])
        observation = source_receipts('runtime-test', reviewed_vaults=True)
        self.assertEqual(observation['readback_verified_count'], 1)
        self.assertEqual(observation['scheduled_publications'][0]['campaign'], cid)

    def test_portfolio_observation_is_read_only_and_rejects_races_or_corrupt_logs(self):
        from ocpf_post.operations_snapshot import portfolio_report
        from ocpf_post.source_receipts import publication_inputs
        before = {str(p): p.read_bytes() for p in self.root.rglob('*') if p.is_file()}
        with patch('urllib.request.urlopen', side_effect=AssertionError('No network')):
            self.assertEqual(portfolio_report()['status'], 'observed')
        self.assertEqual(before, {str(p): p.read_bytes() for p in self.root.rglob('*') if p.is_file()})
        with patch('ocpf_post.operations_snapshot._fingerprints', side_effect=[{}, {'changed': 1}] * 3):
            result = portfolio_report()
        self.assertEqual(result['status'], 'snapshot_changed'); self.assertEqual(result['projects'], {})
        (self.root/'state').mkdir(exist_ok=True)
        (self.root/'state'/'publish-receipts.jsonl').write_text('{broken\n')
        result = portfolio_report()
        self.assertEqual(result['status'], 'unavailable'); self.assertEqual(result['projects'], {})
        with self.assertRaises(ValueError): publication_inputs()

    def test_portfolio_component_failure_retains_other_projects_without_raw_errors(self):
        from ocpf_post.operations_snapshot import portfolio_report
        from ocpf_post import operations
        actual = operations.report
        def inspect(project, **kwargs):
            if project == 'runtime-test': raise ValueError('secret-provider-body')
            return actual(project, **kwargs)
        with patch.object(operations, 'report', side_effect=inspect):
            result = portfolio_report()
        self.assertEqual(result['projects']['runtime-test']['status'], 'unavailable')
        self.assertIn('items', result['projects']['oneclickpostfactory'])
        self.assertNotIn('secret-provider-body', json.dumps(result))

    def test_portfolio_retries_a_concurrent_write_with_fresh_publication_inputs(self):
        from ocpf_post import operations, source_receipts as receipts_module
        from ocpf_post.operations_snapshot import portfolio_report
        actual = operations.report
        changed = False
        def inspect(project, **kwargs):
            nonlocal changed
            if not changed:
                changed = True
                append_receipt({'campaign': 'unrelated', 'provider': 'x', 'account_id': 'other', 'status': 'failed'})
            return actual(project, **kwargs)
        with patch.object(operations, 'report', side_effect=inspect), patch.object(
                receipts_module, 'publication_inputs', wraps=receipts_module.publication_inputs) as inputs:
            result = portfolio_report()
        self.assertEqual(result['status'], 'observed')
        self.assertEqual(result['snapshot_attempts'], 2)
        self.assertEqual(inputs.call_count, 2)

    def test_unstable_attempt_is_bounded_and_preserves_last_stable_report(self):
        from ocpf_post.operations_snapshot import portfolio_report, save_report
        from ocpf_post import local_store
        stable = portfolio_report()
        saved = save_report(stable)
        original = Path(saved['report_file']).read_bytes()
        with patch('ocpf_post.operations_snapshot._fingerprints', side_effect=[{}, {'changed': 1}] * 3) as fingerprints:
            result = portfolio_report()
        self.assertEqual(fingerprints.call_count, 6)
        self.assertEqual(result['snapshot_attempts'], 3)
        self.assertEqual(result['projects'], {})
        attempt = save_report(result)
        self.assertTrue(attempt['last_stable_report_preserved'])
        self.assertEqual(Path(saved['report_file']).read_bytes(), original)
        self.assertEqual(local_store.read(Path(attempt['report_file']))['status'], 'snapshot_changed')
        self.assertNotEqual(saved['report_file'], attempt['report_file'])
        # Missing prior reports must never be labelled preserved.
        Path(saved['report_file']).unlink()
        self.assertFalse(save_report(result)['last_stable_report_preserved'])
        local_store.write(Path(saved['report_file']), result)
        self.assertFalse(save_report(result)['last_stable_report_preserved'])

    def test_fair_allocator_reaches_existing_scheduler_and_receipt_boundary(self):
        from ocpf_post import portfolio as allocator
        from ocpf_post.portfolio_cross_platform import plan_refill
        cid = self.sync()['vaults'][0]['campaigns'][0]
        slot = self.now + timedelta(minutes=4)
        policy = {'schema_version': 1, 'selection': 'fair', 'timezone': 'UTC', 'minimum_lead_minutes': 1,
                  'providers': {'x': {'daily_target': 1, 'window_start': slot.strftime('%H:%M'),
                                      'window_end': (slot+timedelta(minutes=30)).strftime('%H:%M'),
                                      'development_max': 0, 'commercial_min': 0}}}
        def reserve(**kwargs):
            return create_schedule(**kwargs, provider_factory=lambda _: self.provider)
        with patch.object(allocator, '_campaign_ids', return_value=[cid]), patch.object(allocator, 'plan_refill', plan_refill):
            result = allocator.apply_refill(now=self.now, horizon_minutes=10, policy=policy, schedule_creator=reserve)
            self.assertEqual(result['errors'], [])
            self.assertEqual(len(result['scheduled']), 1)
            self.assertEqual(list(iter_receipts()), [])
            run_due(now=slot+timedelta(minutes=1), provider_factory=lambda _: self.provider)
            receipts = list(iter_receipts())
            self.assertEqual(receipts[-1]['status'], 'published_verified')
            self.assertEqual(receipts[-1]['schedule_id'], result['scheduled'][0]['schedule_id'])
            self.assertEqual(allocator.apply_refill(now=slot+timedelta(minutes=2), horizon_minutes=10,
                                                    policy=policy, schedule_creator=reserve)['scheduled'], [])
            from ocpf_post.queue_watch import load
            observed = list(load()['records'].values())
            self.assertEqual(len(observed), 1)
            self.assertEqual(observed[0]['state'], 'published_verified')
            self.assertEqual(observed[0]['outcome']['schedule_id'], receipts[-1]['schedule_id'])

    def test_admission_flow_supersedes_trial_and_reaches_guarded_scheduler_receipt(self):
        from ocpf_post import portfolio as allocator, capacity_experiment as trial
        from ocpf_post.portfolio_cross_platform import plan_refill
        cid = self.sync()['vaults'][0]['campaigns'][0]
        baseline = deepcopy(allocator.DEFAULT_POLICY); baseline['selection'] = 'fair'
        allocator.write_private_json(allocator.policy_file(), baseline)
        def reserve(**kwargs):
            return create_schedule(**kwargs, provider_factory=lambda _: self.provider)
        with patch.object(allocator, '_campaign_ids', return_value=[cid]), patch.object(allocator, 'plan_refill', plan_refill):
            trial_state = trial.reconcile(apply=True, now=self.now)
            self.assertIn(trial_state['status'], {'not_started', 'superseded'})
            effective = allocator.load_policy()
            self.assertEqual(effective['providers']['x']['flow_mode'], 'admission')
            self.assertEqual(effective['providers']['x']['hard_daily_ceiling'], 100)
            self.assertEqual(effective['providers']['x']['daily_target'], 20)
            result = allocator.apply_refill(now=self.now, horizon_minutes=1440, schedule_creator=reserve)
            self.assertEqual(result['errors'], [])
            self.assertEqual(len(result['scheduled']), 1)
            slot = datetime.fromisoformat(result['scheduled'][0]['run_at'].replace('Z', '+00:00'))
            run_due(now=slot+timedelta(minutes=1), provider_factory=lambda _:self.provider)
            receipt = list(iter_receipts())[-1]
            self.assertEqual(receipt['status'], 'published_verified')
            self.assertEqual(receipt['schedule_id'], result['scheduled'][0]['schedule_id'])
            self.assertEqual(receipt['campaign'], cid)

    def test_edit_creates_new_revision_and_cancels_old_without_rewriting_payload(self):
        cid=self.sync()['vaults'][0]['campaigns'][0]; self.reserve(cid)
        old=builtin_text(cid,'x'); self.entry['text']='A different reviewed lesson.'; self.approve()
        newer=self.sync('2')['vaults'][0]['campaigns'][0]
        self.assertNotEqual(cid,newer); self.assertEqual(builtin_text(cid,'x'),old)
        self.assertEqual(schedule_records()[0]['status'],'cancelled')
        self.assertIsNotNone(vault.guard(builtin_manifest(cid),'x'))

    def test_unapproved_edit_withdraws_prior_revision(self):
        cid=self.sync()['vaults'][0]['campaigns'][0]; self.reserve(cid)
        self.entry['text']='Unreviewed edit'
        result=self.sync('2')
        self.assertEqual(result['vaults'][0]['skipped'][0]['reason'],'approval_hash_mismatch')
        self.assertEqual(schedule_records()[0]['status'],'cancelled')

    def test_deleted_or_manual_entry_cannot_remain_scheduled(self):
        cid=self.sync()['vaults'][0]['campaigns'][0]; self.reserve(cid)
        self.sync('2',[])
        self.assertEqual(schedule_records()[0]['status'],'cancelled')
        self.entry['status']='MANUAL_ONLY'; self.approve()
        self.assertEqual(self.sync('3')['vaults'][0]['campaigns'],[])

    def test_consumed_original_campaign_blocks_revised_vault_entry(self):
        cid=self.sync()['vaults'][0]['campaigns'][0]
        append_receipt({'campaign':self.entry['campaign'],'provider':'x','account_id':'12345','status':'published_unverified'})
        self.assertIn('consumed',vault.guard(builtin_manifest(cid),'x'))
        self.entry['text']='Changed wording after publication'; self.approve()
        newer=self.sync('2')['vaults'][0]['campaigns'][0]
        self.assertIn('consumed',vault.guard(builtin_manifest(newer),'x'))

    def test_stale_observation_blocks_executor_before_provider_call(self):
        cid=self.sync()['vaults'][0]['campaigns'][0]; self.reserve(cid)
        with patch('ocpf_post.vault_sync.datetime') as clock:
            clock.now.return_value=self.now+timedelta(hours=2)
            run_due(now=self.now+timedelta(hours=2),provider_factory=lambda _:self.provider)
        self.assertEqual(schedule_records()[0]['status'],'drift_blocked')
        self.assertEqual(list(iter_receipts()),[])

    def test_runner_lock_prevents_sync_mutation(self):
        self.sync(); before=vault.observations()
        with RunnerLock():
            result=self.sync('2',[])
        self.assertEqual(result['vaults'][0]['result'],'unavailable')
        self.assertEqual(vault.observations(),before)

    def test_duplicate_block_or_entries_rejected(self):
        with self.assertRaises(ValueError): vault.parse_entries(self.document()['text']*2)
        self.assertEqual(self.sync(entries=[self.entry,self.entry])['vaults'][0]['result'],'unavailable')

    def test_invalid_later_entry_preflights_before_any_import(self):
        invalid=deepcopy(self.entry); invalid['campaign']='RTEST-BAD'; invalid['provider']='linkedin'
        result=self.sync(entries=[self.entry,invalid])
        self.assertEqual(result['vaults'][0]['result'],'unavailable')
        self.assertFalse(runtime_campaign_root().exists())

    def test_same_revision_idempotent_and_version_rollback_refused(self):
        cid=self.sync('2')['vaults'][0]['campaigns'][0]
        self.assertEqual(self.sync('2')['vaults'][0]['campaigns'],[cid])
        self.assertEqual(self.sync('1')['vaults'][0]['result'],'unavailable')

    def test_document_tabs_include_nested_text(self):
        def tab(text): return {'documentTab':{'body':{'content':[{'paragraph':{'elements':[{'textRun':{'content':text}}]}}]}}}
        first=tab('one'); first['childTabs']=[tab('two')]
        self.assertEqual(document_text({'tabs':[first,tab('three')]}),'one\ntwo\nthree')

    def test_google_read_rejects_version_change_and_excludes_suggestions(self):
        meta={'id':'abcdefghijklm','mimeType':'application/vnd.google-apps.document','version':'1','modifiedTime':'now','trashed':False}
        with patch('ocpf_post.google_vault._token',return_value='test'), patch('ocpf_post.google_vault.json_request',side_effect=[meta,{'documentId':'abcdefghijklm','tabs':[]},{**meta,'version':'2'}]) as read:
            with self.assertRaises(ValueError): read_document('abcdefghijklm')
            self.assertIn('PREVIEW_WITHOUT_SUGGESTIONS', read.call_args_list[1].args[0])

    def test_application_check_distinguishes_revision_assertion_and_deployment(self):
        config={'schema_version':1,'project':'runtime-test','repository':'owner/source','environment':'production',
                'url':'https://example.com/health','revision_field':'revision','assertions':{'checks.storage':'ok'},'ttl_minutes':15}
        source={'source':{'sha':'a'*40},'deployments':{'records':[]}}
        path=self.write(config)
        with patch('ocpf_post.application_evidence._configured',return_value=({'repository':'owner/source'},None)):
            preview=app.check(path,sha='a'*40,reader=lambda _:self.fail('preview network'))
            result=app.check(path,sha='a'*40,apply=True,expected_sha256=preview['input_sha256'],source_reader=lambda *a,**k:source,reader=lambda _: {'revision':'a'*40,'checks':{'storage':'ok'}})
            self.assertEqual(result['status'],'application_observed_deployment_unconfirmed')
            source['deployments']['records']=[{'sha':'a'*40,'environment':'production','latest_status':{'state':'success'}}]
            result=app.check(path,sha='a'*40,apply=True,expected_sha256=preview['input_sha256'],source_reader=lambda *a,**k:source,reader=lambda _: {'revision':'b'*40,'checks':{'storage':'ok'}})
            self.assertEqual(result['status'],'failed')

    def test_metrics_require_matching_identity_age_and_available_values(self):
        receipts=[{'campaign':c,'provider':'x','account_id':'12345','post_id':c,'status':'published_verified','recorded_at':(self.now-timedelta(hours=24)).isoformat()} for c in ['A','B']]
        snapshots=[{**r,'captured_at':self.now.isoformat(),'availability':{'status':'available'},'metrics':{'likes':0,'impressions':None}} for r in receipts]
        with patch('ocpf_post.performance_review.iter_receipts',return_value=receipts), patch('ocpf_post.performance_review.iter_snapshots',return_value=snapshots):
            result=perf.review(['A','B'],provider='x',account_id='12345')
            self.assertEqual(result['status'],'comparable'); self.assertEqual(result['common_metrics'],['likes'])
            self.assertIsNone(result['rows'][0]['metrics']['impressions'])
            snapshots[1]['account_id']='other'
            self.assertEqual(perf.review(['A','B'],provider='x',account_id='12345')['status'],'insufficient_evidence')

    def test_metrics_use_first_creation_time_not_later_verification(self):
        base={'campaign':'A','provider':'x','account_id':'12345','post_id':'1','status':'published_unverified','recorded_at':(self.now-timedelta(days=1)).isoformat()}
        with patch('ocpf_post.performance_review.iter_receipts',return_value=[base,{**base,'status':'published_verified','recorded_at':self.now.isoformat()}]):
            self.assertEqual(next(iter(perf.publications().values()))['at'],self.now-timedelta(days=1))

    def test_https_blocks_private_addresses_before_connect(self):
        from ocpf_post.bounded_http import json_request
        with patch('socket.getaddrinfo',return_value=[(2,1,6,'',('127.0.0.1',443))]), patch('socket.create_connection') as connect:
            with self.assertRaises(ValueError): json_request('https://example.com/health')
            connect.assert_not_called()

    def test_verifier_identity_adapter_checks_key_fingerprint_and_rpc_identity(self):
        import base64
        import hashlib
        der=bytes.fromhex('302a300506032b6570032100')+bytes(range(32))
        fingerprint=hashlib.sha256(der).hexdigest()
        identity={'schema':'opstruth.verifier-identity.v1','algorithm':'Ed25519','changedState':False,
                  'signerFingerprint':'sha256:'+fingerprint,'doneStateSignerFingerprint':fingerprint,
                  'publicKeyPem':'-----BEGIN PUBLIC KEY-----\n'+base64.b64encode(der).decode()+'\n-----END PUBLIC KEY-----'}
        reply={'jsonrpc':'2.0','id':'post-once-identity-check','result':{'structuredContent':identity}}
        self.assertTrue(app.verifier_identity(lambda *a,**k:reply)['passed'])
        identity['signerFingerprint']='sha256:'+'0'*64
        self.assertFalse(app.verifier_identity(lambda *a,**k:reply)['passed'])

    def test_google_oauth_pkce_state_and_private_persistence(self):
        import base64
        import hashlib
        import io
        from contextlib import redirect_stdout
        from urllib.parse import urlsplit, parse_qs
        from ocpf_post.google_vault import authorize, credential_path
        client=self.write({'installed':{'client_id':'desktop-client','client_secret':'test-secret'}});client.chmod(0o600)
        urls=[]; responses=[]
        class Server:
            def __init__(self, address, handler):
                self.handler=handler; self.count=0
                self.address=address
            def __enter__(self):return self
            def __exit__(self,*args):pass
            def handle_request(self):
                state=parse_qs(urlsplit(urls[0]).query)['state'][0]
                handler=object.__new__(self.handler)
                handler.path='/callback?state='+('wrong' if self.count==0 else state)+'&code=one-use-code'
                handler.send_response=responses.append;handler.end_headers=lambda:None;handler.wfile=io.BytesIO()
                handler.do_GET();self.count+=1
        with patch('http.server.HTTPServer',Server),patch('webbrowser.open',side_effect=urls.append),patch('ocpf_post.google_vault.json_request',return_value={'refresh_token':'test-refresh'}) as token,redirect_stdout(io.StringIO()):
            result=authorize(client)
        self.assertEqual(responses,[400,200]);self.assertEqual(result['result'],'google_drive_authorized')
        params=parse_qs(urlsplit(urls[0]).query);body=parse_qs(token.call_args.kwargs['data'])
        expected=base64.urlsafe_b64encode(hashlib.sha256(body['code_verifier'][0].encode()).digest()).decode().rstrip('=')
        self.assertEqual(params['code_challenge'],[expected])
        self.assertEqual(params['scope'],['https://www.googleapis.com/auth/drive.readonly'])
        self.assertEqual(credential_path().stat().st_mode & 0o777,0o600)

    def test_brief_receipt_requires_its_own_completed_schedule(self):
        from ocpf_post.evidence_briefs import import_brief
        from test_evidence_briefs import brief_input, NOW
        path=self.write(brief_input()); preview=import_brief(path,now=NOW,allocate=True)
        import_brief(path,apply=True,expected_sha256=preview['input_sha256'],allocate=True,now=NOW)
        self.assertEqual(source_receipts('runtime-test',reviewed_briefs=True)['status'],'pending')
        create_schedule(campaign='RTEST-BRIEF-001',provider='x',at=(NOW+timedelta(minutes=2)).isoformat(),now=NOW,provider_factory=lambda _:self.provider)
        run_due(now=NOW+timedelta(minutes=3),provider_factory=lambda _:self.provider)
        report=source_receipts('runtime-test',reviewed_briefs=True)
        self.assertEqual(report['status'],'observed');self.assertEqual(report['readback_verified_count'],1)
        self.assertEqual(report['milestone'],'reviewed_brief_scheduled_publication_receipt')

    def test_failed_metrics_capture_is_retained_and_not_retried_each_refill(self):
        append_receipt({'campaign':'A','provider':'x','account_id':'12345','post_id':'1','status':'published_verified',
                        'recorded_at':(self.now-timedelta(hours=24)).isoformat()})
        def unavailable(*args):raise ValueError('missing provider access')
        self.assertEqual(perf.capture_due(apply=True,now=self.now,capture_fn=unavailable)['observations'][0]['result'],'unavailable')
        with patch('ocpf_post.performance_review.capture') as provider:
            self.assertEqual(perf.capture_due(apply=True,now=self.now+timedelta(minutes=5),capture_fn=provider)['observations'],[])
            provider.assert_not_called()
