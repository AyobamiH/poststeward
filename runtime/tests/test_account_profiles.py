from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from zoneinfo import ZoneInfo

from ocpf_post import account_profiles as a, local_store, portfolio as base, portfolio_queue as queue
from ocpf_post.campaigns import destination_binding
from ocpf_post.onboarding import import_campaign
from ocpf_post.providers import for_account
from ocpf_post.providers.x import XProvider
from ocpf_post.providers.threads import ThreadsProvider
from ocpf_post.registry import load_registry
from ocpf_post.scheduler import create_schedule, execute_schedule, schedule_records, ScheduleError
from ocpf_post.state import provider_token_file, read_json, iter_receipts, write_private_json, append_receipt
from ocpf_post.portfolio_cross_platform import plan_refill
from test_portfolio_queue import fixture, candidate

UTC = timezone.utc
NOW = datetime(2026, 9, 10, 5, tzinfo=UTC)


def profile(provider='x', account_id='111'):
    return {'schema_version': 1, 'provider': provider, 'account_id': account_id, 'label': 'Brand ' + account_id,
            'bindings': [{'project': 'oneclickpostfactory', 'alias': provider + '-brand-' + account_id}],
            'policy': {'daily_target': 2, 'window_start': '07:00', 'window_end': '09:00',
                       'development_max': 1, 'commercial_min': 0, 'minimum_spacing_minutes': 30}}


class AccountTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(); self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        env = patch.dict(os.environ, {'OCPF_POST_CONFIG_DIR': str(self.root/'config'), 'OCPF_POST_STATE_DIR': str(self.root/'state')})
        env.start(); self.addCleanup(env.stop)
        self.posts = []
        self.texts = {}
        self.wrong = False
        self.requests = []
        for provider, target in [('x', 'ocpf_post.providers.x._request_json'), ('threads', 'ocpf_post.providers.threads.request_json')]:
            p = patch(target, side_effect=lambda url, _p=provider, **kw: self.transport(_p, url, **kw))
            p.start(); self.addCleanup(p.stop)
        p = patch('urllib.request.urlopen', side_effect=AssertionError('Unmocked network'))
        p.start(); self.addCleanup(p.stop)

    def transport(self, provider, url, **kw):
        self.requests.append((provider, url, kw))
        token = kw.get('headers', {}).get('Authorization', '').removeprefix('Bearer ')
        account_id = token.split('-')[-1] if token else ''
        if self.wrong:
            account_id = '99999'
        if 'refresh_access_token' in url:
            payload = {'access_token': kw['query']['access_token'], 'expires_in': 5000000}
        elif url.endswith('/oauth2/token'):
            payload = {'access_token': 'token-' + kw['form']['refresh_token'].split('-')[-1], 'refresh_token': kw['form']['refresh_token'], 'expires_in': 7200}
        elif url.endswith('/me'):
            payload = {'id': account_id, 'username': 'brand' + account_id}
        elif kw.get('method') == 'POST':
            self.posts.append((provider, account_id, kw))
            post_id = str(8000 + len(self.posts))
            self.texts[post_id] = kw.get('json_body', kw.get('query'))['text']
            payload = {'id': post_id}
        else:
            post_id = url.rsplit('/', 1)[-1]
            payload = {'id': post_id, 'text': self.texts.get(post_id), 'permalink': 'https://www.threads.net/@brand/post/' + post_id}
        return (200, {'data': payload}) if provider == 'x' and '/oauth2/token' not in url else (200, payload) if provider == 'x' else (200, {}, payload)

    def register(self, provider='x', account_id='111'):
        value = profile(provider, account_id)
        preview = a.register(value)
        self.assertFalse(preview['account']['enabled'])
        return a.register(value, apply=True, expected_sha256=preview['input_sha256'])

    def connect(self, provider='x', account_id='111', enable=True):
        self.register(provider, account_id)
        secret = self.root / (provider + account_id + '.json')
        write_private_json(secret, {'access_token': 'token-' + account_id, 'refresh_token': 'refresh-' + account_id,
                                    'client_id': 'client-' + account_id, 'expires_at': 4102444800})
        a.connect(provider, account_id, credential_file=secret)
        if enable:
            preview = a.activation(provider, account_id)
            a.activation(provider, account_id, apply=True, expected_sha256=preview['review_sha256'])
        return secret

    def campaign(self, provider, account_id, n=1):
        name = f'OCPF-BRAND-{provider.upper()}-{account_id}-{n}'
        value = {'schema_version': 1, 'campaign': name, 'project': 'oneclickpostfactory', 'title': 'Brand approved copy',
                 'status': 'COPY-READY', 'destinations': {provider: provider+'-brand-'+account_id},
                 'texts': {provider: f'Approved brand {account_id} copy {n}.'},
                 'source': {'type': 'owner_approved', 'source_id': name},
                 'allocation': {'lane': 'evergreen', 'priority': 72, 'prepared_at': '2026-09-09T00:00:00Z', 'expires_at': '2026-09-12T00:00:00Z'}}
        path = self.root / 'campaign.json'; path.write_text(json.dumps(value))
        preview = import_campaign(path, allocate=True, now=NOW)
        import_campaign(path, allocate=True, apply=True, expected_sha256=preview['input_sha256'], now=NOW)
        return name

    def test_provider_write_circuit_is_an_account_availability_reason(self):
        with patch(
            "ocpf_post.scheduler.provider_write_circuit",
            return_value={
                "open": True,
                "retry_at": "2026-09-10T06:30:00Z",
                "failure_class": "provider_usage_limit",
            },
        ):
            reason = a.unavailable("x", "1480506376447315969", now=NOW)
        self.assertIn("write circuit open", reason)
        self.assertIn("provider_usage_limit", reason)
        self.assertIn("2026-09-10T06:30:00Z", reason)

    def test_add_only_binding_keeps_defaults_and_requires_review(self):
        before = load_registry()['projects']['oneclickpostfactory']
        value = profile()
        with self.assertRaises(ValueError):
            a.register(value, apply=True, expected_sha256='wrong')
        self.assertFalse(a.path().exists())
        self.register()
        after = load_registry()['projects']['oneclickpostfactory']
        self.assertEqual(before['default_accounts'], after['default_accounts'])
        for alias, account in before['accounts'].items():
            self.assertEqual(account, after['accounts'][alias])
        changed = deepcopy(value); changed['policy']['daily_target'] = 9
        with self.assertRaises(ValueError):
            a.register(changed)
        for invalid in ('../111', '', '0'):
            with self.assertRaises(ValueError):
                a.register({**value, 'account_id': invalid})
        with self.assertRaises(ValueError):
            a.register({**value, 'access_token': 'secret'})

    def test_connections_are_staged_identity_checked_and_never_overwrite_founder(self):
        for provider in ('x', 'threads'):
            write_private_json(provider_token_file(provider), {'access_token': 'founder-' + provider})
            secret = self.connect(provider)
            old = read_json(a.directory(provider, '111')/'token.json')
            a.activation(provider, '111', disable=True)
            self.wrong = True
            with self.assertRaises(ValueError):
                a.connect(provider, '111', credential_file=secret)
            self.wrong = False
            self.assertEqual(read_json(a.directory(provider, '111')/'token.json'), old)
            self.assertEqual(read_json(provider_token_file(provider)), {'access_token': 'founder-' + provider})
            self.assertEqual((a.directory(provider, '111')/'token.json').stat().st_mode & 0o777, 0o600)
        self.assertFalse(self.posts)

    def test_scoped_x_and_threads_ignore_env_and_refresh_independently(self):
        for provider in ('x', 'threads'):
            for account_id in ('111', '222'):
                self.connect(provider, account_id)
        with patch.dict(os.environ, {'X_USER_ACCESS_TOKEN': 'founder-env', 'X_REFRESH_TOKEN': 'founder-refresh',
                                     'X_CLIENT_ID': 'founder-client', 'THREADS_ACCESS_TOKEN': 'founder-env'}):
            for provider in ('x', 'threads'):
                for account_id in ('111', '222'):
                    client = for_account(provider, account_id)
                    self.assertEqual(client.account().account_id, account_id)
                    client.refresh(quiet=True)
                    self.assertEqual(client.account().account_id, account_id)
                    if provider == 'x': self.assertEqual(client.client_id, 'client-' + account_id)
            a.directory('x', '111').joinpath('token.json').unlink()
            with self.assertRaises(ValueError): for_account('x', '111')
        self.assertFalse(self.posts)

    def test_pending_brand_excluded_disabled_schedule_cannot_publish(self):
        self.connect('x', enable=False)
        name = self.campaign('x', '111')
        self.assertNotIn(name, [c['campaign'] for c in base.delivery_candidates(now=NOW)])
        with self.assertRaises(ScheduleError):
            create_schedule(campaign=name, provider='x', at='2026-09-10T06:00:00Z', now=NOW)
        preview = a.activation('x', '111')
        a.activation('x', '111', apply=True, expected_sha256=preview['review_sha256'])
        schedule = create_schedule(campaign=name, provider='x', at='2026-09-10T06:00:00Z', now=NOW)
        a.activation('x', '111', disable=True)
        result = execute_schedule(schedule['schedule_id'])
        self.assertEqual(result['status'], 'failed')
        self.assertFalse(self.posts)
        self.assertEqual(list(iter_receipts()), [])

    def test_four_connections_to_independent_reservations_and_real_adapter_receipts(self):
        names = []
        for provider in ('x', 'threads'):
            for account_id in ('111', '222'):
                self.connect(provider, account_id)
                names.append(self.campaign(provider, account_id))
        policy = {**deepcopy(base.DEFAULT_POLICY), 'selection': 'fair'}
        with patch.object(base, 'plan_refill', plan_refill):
            result = base.apply_refill(now=NOW, horizon_minutes=75, policy=policy)
        brand = [s for s in result['scheduled'] if s['campaign'] in names]
        self.assertEqual(len(brand), 4)
        self.assertFalse(self.posts)
        for schedule in brand:
            result = execute_schedule(schedule['schedule_id'])
            self.assertEqual(result['status'], 'published_verified')
            execute_schedule(schedule['schedule_id'])
        self.assertEqual(len(self.posts), 4)
        self.assertEqual({(p, i) for p, i, _ in self.posts}, {(p,i) for p in ('x','threads') for i in ('111','222')})
        receipts = [r for r in iter_receipts() if r['status'] == 'published_verified']
        self.assertEqual(len(receipts), 4)
        self.assertEqual({r['schedule_id'] for r in receipts}, {s['schedule_id'] for s in brand})

    def test_same_provider_budgets_history_and_offline_replay_are_isolated(self):
        for account_id in ('111', '222'):
            self.connect('x', account_id)
            for n in (1,2,3): self.campaign('x', account_id, n)
        append_receipt({'campaign': 'OCPF-OLD', 'provider': 'x', 'account_id': '111', 'recorded_at': '2026-09-10T04:00:00Z', 'status': 'published_verified'})
        policy = {**deepcopy(base.DEFAULT_POLICY), 'selection': 'fair'}
        inputs = queue.capture_inputs(NOW, 600, policy)
        self.assertEqual(inputs['day_counts']['x']['2026-09-10']['total'], 0)
        p1 = inputs['account_partitions']['x:111']['inputs']
        p2 = inputs['account_partitions']['x:222']['inputs']
        self.assertEqual(p1['day_counts']['x']['2026-09-10']['total'], 1)
        self.assertEqual(p2['day_counts']['x']['2026-09-10']['total'], 0)
        self.assertEqual(len(p1['histories']['x']), 1)
        self.assertEqual(p2['histories']['x'], [])
        with patch.object(base, 'load_policy', return_value=policy):
            snapshot = queue.snapshot(now=NOW, horizon_minutes=600)
        with patch.object(a, 'profiles', side_effect=AssertionError('Live account read during replay')), patch.object(base, 'delivery_candidates', side_effect=AssertionError('Live queue read')):
            replay = queue.replay(snapshot)
        self.assertTrue(replay['baseline_reproduced'])
        plan = replay['fair']
        # The accounts remain isolated, but their legacy daily_target is no longer
        # a throughput cap once provider admission flow is active.
        self.assertEqual(sum(c['account_id']=='111' for c in plan['plan']), 3)
        self.assertEqual(sum(c['account_id']=='222' for c in plan['plan']), 3)
        self.assertEqual(plan['account_capacity']['x:111']['daily_budget_used'], 1)
        self.assertEqual(plan['account_capacity']['x:111']['flow_mode'], 'admission')
        self.assertEqual(plan['account_capacity']['x:111']['daily_ceiling'], 100)
        self.assertEqual(plan['account_capacity']['x:222']['flow_mode'], 'admission')
        self.assertEqual(plan['account_capacity']['x:222']['daily_ceiling'], 100)

    def test_known_linkedin_page_manifests_are_inactive_add_only_bindings(self):
        examples = Path(__file__).resolve().parents[1] / 'examples' / 'linkedin-pages'
        expected = {
            'proof-and-state.json': ('proof-and-state', 'urn:li:organization:146195259'),
            'tail-wagging.json': ('tail-wagging-websites', 'urn:li:organization:108906918'),
        }
        before = load_registry()
        for filename, (project, account_id) in expected.items():
            value = json.loads((examples / filename).read_text())
            preview = a.register(value)
            self.assertEqual(preview['account']['provider'], 'linkedin')
            self.assertEqual(preview['account']['account_id'], account_id)
            self.assertFalse(preview['account']['enabled'])
            self.assertEqual(preview['account']['bindings'][0]['project'], project)
            self.assertEqual(preview['account']['policy']['daily_target'], 6)
            self.assertEqual(preview['account']['policy']['minimum_spacing_minutes'], 120)
        # Previewing the public page manifests must not mutate founder defaults.
        self.assertEqual(load_registry(), before)

    def test_additional_known_linkedin_page_manifests_are_inactive_and_do_not_change_source_routing(self):
        examples = Path(__file__).resolve().parents[1] / 'examples' / 'linkedin-pages'
        expected = {
            'oneclickpostfactory.json': ('oneclickpostfactory', 'urn:li:organization:146606475'),
            'opstruth.json': ('opstruth', 'urn:li:organization:146607534'),
        }
        from ocpf_post.replenisher import source_profiles
        before = load_registry()
        sources = source_profiles()['projects']
        self.assertIn('linkedin', sources['oneclickpostfactory']['providers'])
        self.assertEqual(sources['oneclickpostfactory']['destinations']['linkedin'], 'linkedin-founder')
        self.assertEqual(sources['opstruth']['providers'], ['x'])
        self.assertEqual(sources['opstruth']['destinations'], {'x': 'x-founder'})
        for filename, (project, account_id) in expected.items():
            value = json.loads((examples / filename).read_text())
            preview = a.register(value)
            self.assertEqual(preview['account']['provider'], 'linkedin')
            self.assertEqual(preview['account']['account_id'], account_id)
            self.assertFalse(preview['account']['enabled'])
            self.assertEqual(preview['account']['bindings'][0]['project'], project)
            self.assertEqual(preview['account']['policy']['daily_target'], 6)
            self.assertEqual(preview['account']['policy']['minimum_spacing_minutes'], 120)
        self.assertEqual(load_registry(), before)
        after_sources = source_profiles()['projects']
        self.assertEqual(after_sources['oneclickpostfactory']['destinations']['linkedin'], 'linkedin-founder')
        self.assertEqual(after_sources['opstruth']['providers'], ['x'])

    def test_existing_capacity_trial_scope_accepts_only_isolated_profiles(self):
        from ocpf_post.capacity_experiment import owner_scope
        policy = {**deepcopy(base.DEFAULT_POLICY), 'selection': 'fair'}
        self.assertIsNone(owner_scope(policy))
        self.register('x')
        self.register('threads')
        self.assertIsNone(owner_scope(policy))

    def test_brand_metrics_use_receipt_account_connection(self):
        from ocpf_post.performance import capture
        self.connect('x'); name = self.campaign('x', '111')
        append_receipt({'campaign': name, 'provider': 'x', 'account_id': '111', 'status': 'published_verified', 'post_id': '777'})
        capture(name, 'x')
        metrics_requests = [kw for p, url, kw in self.requests if '/tweets/' in url]
        self.assertTrue(metrics_requests)
        self.assertEqual(metrics_requests[-1]['headers']['Authorization'], 'Bearer token-111')

    def test_reply_collection_cursors_and_send_stay_on_exact_account(self):
        from ocpf_post import engagement as e
        from ocpf_post.model import AccountIdentity
        from test_engagement import FakeClient
        for provider in ('x', 'threads'):
            for account_id in ('111','222'): self.connect(provider, account_id)
        clients = {}
        for provider in ('x', 'threads'):
            for account_id in ('111','222'):
                client = FakeClient()
                client.name = provider
                client.identity = AccountIdentity(provider=provider, account_id=account_id, username='brand'+account_id)
                clients[(provider, account_id)] = client
        def routed(name, **kwargs):
            return clients[(name, kwargs['credential_dir'].name)]
        def page(client, account, previous, roots, now):
            expected = account.account_id
            self.assertIn(previous.get('cursor'), (None, expected))
            raw = {'id': '201', 'author_id': '999', 'text': 'How do receipts work?',
                   'referenced_tweets': [{'type':'replied_to','id':'101'}]}
            if account.provider == 'threads':
                raw = {'id':'201', 'username':'visitor', 'text':'How do receipts work?', 'replied_to': {'id':'101'}}
            return [raw], {'cursor': expected}, False
        with patch('ocpf_post.providers.XProvider', side_effect=lambda **kw:routed('x', **kw)), \
             patch('ocpf_post.providers.ThreadsProvider', side_effect=lambda **kw:routed('threads', **kw)), \
             patch.object(e, 'own_publications', side_effect=lambda p,a,n:{'101':{'campaign':'BRAND','published_at':e.stamp(n)}} if a in ('111','222') else {}), \
             patch.object(e, 'fetch_page', side_effect=page):
            e.sync(apply=True, now=NOW)
            e.sync(apply=True, now=NOW+timedelta(minutes=16))
            for provider in ('x','threads'):
                for account_id in ('111','222'):
                    self.assertEqual(e.read()['polls'][provider+':'+account_id]['cursor'], account_id)
            # X reply uses the bound brand token, exact parent and its own terminal receipt.
            row = next(r for r in e.read()['inbox'].values() if r['provider']=='x' and r['account_id']=='222')
            draft = e.draft(row['id'], 'The receipt records the publishing outcome.', now=NOW+timedelta(minutes=17))
            result = e.send(row['id'], expected_sha256=draft['review_sha256'], live=True, now=NOW+timedelta(minutes=18))
            self.assertEqual(result['result'], 'published_verified')
            self.assertEqual(clients[('x','222')].sent[0][1], '201')
            self.assertFalse(clients[('x','111')].sent)
