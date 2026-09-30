from contextlib import contextmanager
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from ocpf_post import vault_sync as vault
from ocpf_post.campaigns import builtin_manifest, destination_binding
from ocpf_post.model import AccountIdentity
from ocpf_post.onboarding import import_project
from ocpf_post.scheduler import create_schedule, run_due, schedule_records
from ocpf_post.source_receipts import source_receipts
from ocpf_post.state import iter_receipts
from test_onboarding import project_input
from test_scheduler import FakeProvider


class VaultExtensionTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(); self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        env = patch.dict(os.environ, {'OCPF_POST_CONFIG_DIR': str(self.root / 'config'),
                                     'OCPF_POST_STATE_DIR': str(self.root / 'state')})
        env.start(); self.addCleanup(env.stop)
        self.now = datetime.now(timezone.utc)
        project = project_input()
        project['accounts']['threads-brand'] = {'provider': 'threads', 'account_id': '54321'}
        project['accounts']['x-other'] = {'provider': 'x', 'account_id': '99999'}
        path = self.root / 'project.json'; path.write_text(json.dumps(project))
        result = import_project(path); import_project(path, apply=True, expected_sha256=result['input_sha256'])
        self.policy = {'schema_version': 1, 'id': 'test-vault', 'project': 'runtime-test',
                       'document_id': 'abcdefghijklm', 'destinations': {'x': 'x-owner'}, 'max_age_minutes': 60}
        path = self.root / 'vault.json'; path.write_text(json.dumps(self.policy))
        result = vault.register(path, enable=True)
        vault.register(path, apply=True, expected_sha256=result['input_sha256'], enable=True)
        self.base = self.entry('x', 'RTEST-VAULT-001', 'Founder copy stays unchanged.')
        self.brand = self.entry('threads', 'RTEST-VAULT-BRAND-001', 'A distinct brand question.')
        self.version = '1'

    def entry(self, provider, campaign, text):
        value = {'campaign': campaign, 'provider': provider, 'title': 'Reviewed copy', 'text': text,
                 'status': 'APPROVED', 'allocation': {'lane': 'evergreen', 'priority': 72,
                 'prepared_at': (self.now - timedelta(hours=1)).isoformat(),
                 'expires_at': (self.now + timedelta(days=1)).isoformat()}}
        return {**value, 'approval_sha256': vault.digest(value)}

    def section(self, entries, provider=None):
        prefix = 'POST-ONCE ' + (provider.upper() + ' ' if provider else '') + 'APPROVED ENTRIES '
        return prefix + 'BEGIN\n' + json.dumps({'schema_version': 1, 'entries': entries}) + '\n' + prefix + 'END\n'

    def document(self, _=None):
        return {'document_id': self.policy['document_id'], 'version': self.version,
                'text': self.section([self.base]) + self.section([self.brand], 'threads')}

    def extend(self, **kwargs):
        return vault.extend('test-vault', 'threads', 'threads-brand', reader=self.document, now=self.now, **kwargs)

    def activate(self):
        preview = self.extend()
        return self.extend(apply=True, expected_sha256=preview['review_sha256'])

    def sync(self):
        return vault.sync(apply=True, reader=self.document, now=self.now)['vaults'][0]

    def test_staged_provider_section_does_not_change_legacy_feed(self):
        doc = self.document()
        self.assertEqual(vault.parse_entries(doc['text']), [self.base])
        self.assertEqual(len(self.sync()['campaigns']), 1)
        # An unauthorised malformed section cannot disrupt the founder feed.
        doc['text'] = self.section([self.base]) + 'POST-ONCE THREADS APPROVED ENTRIES BEGIN\nbroken'
        self.assertEqual(len(vault.prepare(vault.policies()['test-vault'], doc, self.now)[0]), 1)
        self.activate()
        with self.assertRaises(ValueError):
            vault.prepare(vault.policies()['test-vault'], doc, self.now)

    def test_extension_is_reviewed_add_only_and_idempotent(self):
        before = deepcopy(vault.policies()); observed = deepcopy(vault.observations())
        preview = self.extend()
        self.assertEqual(vault.policies(), before)
        self.assertEqual(len(preview['review']['copy']), 2)
        with self.assertRaisesRegex(ValueError, 'review changed'):
            self.extend(apply=True, expected_sha256='wrong')
        self.assertEqual(self.activate()['result'], 'extended')
        self.assertEqual(vault.policies()['test-vault']['destinations'], {'x': 'x-owner', 'threads': 'threads-brand'})
        self.assertEqual(self.activate()['result'], 'already_present')
        self.assertEqual(vault.observations(), observed)
        self.assertEqual(list(iter_receipts()), [])
        with self.assertRaisesRegex(ValueError, 'cannot be replaced'):
            vault.extend('test-vault', 'x', 'x-other', reader=self.document)

    def test_changed_document_or_account_invalidates_review(self):
        preview = self.extend(); self.version = '2'
        with self.assertRaisesRegex(ValueError, 'review changed'):
            self.extend(apply=True, expected_sha256=preview['review_sha256'])
        self.version = '1'
        from ocpf_post.registry import resolve_account
        def changed(project, alias, **kw):
            result = resolve_account(project, alias, **kw)
            return {**result, 'account_id': '77777'} if alias == 'threads-brand' else result
        with patch('ocpf_post.registry.resolve_account', side_effect=changed):
            with self.assertRaisesRegex(ValueError, 'review changed'):
                self.extend(apply=True, expected_sha256=preview['review_sha256'])
        self.assertNotIn('threads', vault.policies()['test-vault']['destinations'])

    def test_inactive_brand_cannot_extend(self):
        with patch('ocpf_post.account_profiles.unavailable', return_value='inactive'):
            with self.assertRaisesRegex(ValueError, 'enabled first'):
                self.extend()

    def test_provider_sections_enforce_schema_duplicates_and_aggregate_bound(self):
        self.activate(); policy = vault.policies()['test-vault']
        bad = [self.section([self.base]) + self.section([self.base], 'threads'),
               self.section([self.base, self.brand]) + self.section([self.brand], 'threads'),
               self.section([self.base]) + self.section([self.brand], 'threads') * 2]
        for text in bad:
            with self.subTest(text=text), self.assertRaises(ValueError):
                vault.prepare(policy, {'text': text}, self.now)
        with self.assertRaisesRegex(ValueError, 'Maximum 100'):
            vault.parse_entries(self.section([self.base] * 100) + self.section([self.brand], 'threads'), providers=['threads'])

    def test_sync_retains_founder_schedule_and_reconciles_brand_revision(self):
        founder = self.sync()['campaigns'][0]
        fake = FakeProvider(account_id='12345')
        saved = create_schedule(campaign=founder, provider='x', at=(self.now + timedelta(minutes=2)).isoformat(),
                                now=self.now, provider_factory=lambda _: fake)
        self.activate(); result = self.sync()
        self.assertIn(founder, result['campaigns']); self.assertFalse(result['cancelled'])
        brand = next(c for c in result['campaigns'] if c.endswith('-THREADS'))
        self.assertEqual(destination_binding(brand, 'threads')['account_id'], '54321')
        fake.identity = AccountIdentity(provider='threads', account_id='54321', username='brand')
        brand_schedule = create_schedule(campaign=brand, provider='threads', at=(self.now + timedelta(minutes=3)).isoformat(),
                                         now=self.now, provider_factory=lambda _: fake)
        self.brand = self.entry('threads', self.brand['campaign'], 'Newly reviewed brand wording.'); self.version = '2'
        result = self.sync()
        self.assertEqual(result['cancelled'], [brand_schedule['schedule_id']])
        self.assertEqual(next(s for s in schedule_records() if s['schedule_id'] == saved['schedule_id'])['status'], 'scheduled')

    def test_brand_receipt_survives_revision_and_blocks_republication(self):
        self.activate(); brand = next(c for c in self.sync()['campaigns'] if c.endswith('-THREADS'))
        fake = FakeProvider(account_id='54321')
        fake.identity = AccountIdentity(provider='threads', account_id='54321', username='brand')
        schedule = create_schedule(campaign=brand, provider='threads', at=(self.now + timedelta(minutes=2)).isoformat(),
                                   now=self.now, provider_factory=lambda _: fake)
        run_due(now=self.now + timedelta(minutes=3), provider_factory=lambda _: fake)
        receipt = list(iter_receipts())[-1]
        self.assertEqual(receipt['schedule_id'], schedule['schedule_id'])
        self.assertEqual(receipt['account_id'], '54321')
        self.assertEqual(receipt['status'], 'published_verified')
        self.brand = self.entry('threads', self.brand['campaign'], 'A later revision must not republish.'); self.version = '2'
        revised = next(c for c in self.sync()['campaigns'] if c.endswith('-THREADS'))
        self.assertIn('consumed', vault.guard(builtin_manifest(revised), 'threads', now=self.now))
        self.assertEqual(source_receipts('runtime-test', reviewed_vaults=True)['readback_verified_count'], 1)

    def test_deferred_package_is_not_reported_as_active_authority(self):
        class BlockedBudget:
            def admit(self, project, provider, account, *, expires_at=None):
                return {
                    "admitted": False,
                    "project": project,
                    "scope": provider + ":" + account,
                    "reasons": ["admission_writer_busy"],
                    "error_type": "BlockingIOError",
                }

        @contextmanager
        def blocked_budget(_now):
            yield BlockedBudget()

        with patch("ocpf_post.scoped_admission.vault_budget", side_effect=blocked_budget):
            result = self.sync()

        self.assertEqual(result["campaigns"], [])
        self.assertEqual(len(result["deferred"]), 1)
        self.assertEqual(result["deferred"][0]["reasons"], ["admission_writer_busy"])
        self.assertEqual(result["deferred"][0]["error_type"], "BlockingIOError")
        observed = vault.observations()["test-vault"]
        self.assertEqual(observed["active"], {})
        self.assertEqual(observed["history"], {})

    def test_overfull_portfolio_admits_bounded_brand_stock_through_scheduled_receipt(self):
        from ocpf_post import portfolio
        self.activate()
        self.base['status'] = 'PUBLISHED'
        entries = [self.entry('threads', f'RTEST-RESERVE-{i}', f'Distinct reviewed topic {i}.') for i in range(4)]
        document = {'document_id': self.policy['document_id'], 'version': '2',
                    'text': self.section([self.base]) + self.section(entries, 'threads')}
        overloaded = [{'campaign': f'LEGACY-{i}', 'provider': 'linkedin',
                       'account_id': 'other-account', 'project': 'busy'} for i in range(431)]
        original = portfolio.delivery_candidates
        with patch.object(portfolio, 'delivery_candidates', side_effect=lambda **kw: overloaded + original(**kw)):
            result = vault.sync(apply=True, reader=lambda _: document, now=self.now)['vaults'][0]
            self.assertEqual(len(result['campaigns']), 3)
            self.assertEqual(len(result['deferred']), 1)
            self.assertIn('global_resource_ceiling', result['deferred'][0]['reasons'])
            repeated = vault.sync(apply=True, reader=lambda _: document, now=self.now)['vaults'][0]
            self.assertEqual(repeated['campaigns'], result['campaigns'])
            fake = FakeProvider(account_id='54321')
            fake.identity = AccountIdentity(provider='threads', account_id='54321', username='brand')
            scheduled = create_schedule(campaign=result['campaigns'][0], provider='threads',
                                        at=(self.now + timedelta(minutes=2)).isoformat(), now=self.now,
                                        provider_factory=lambda _: fake)
            still_full = vault.sync(apply=True, reader=lambda _: document, now=self.now)['vaults'][0]
            self.assertEqual(len(still_full['deferred']), 1)
            run_due(now=self.now + timedelta(minutes=3), provider_factory=lambda _: fake)
            receipts = list(iter_receipts())
            self.assertEqual(receipts[-1]['schedule_id'], scheduled['schedule_id'])
            self.assertEqual(receipts[-1]['status'], 'published_verified')
            self.assertEqual(receipts[-1]['account_id'], '54321')
            replenished = vault.sync(apply=True, reader=lambda _: document,
                                      now=self.now + timedelta(minutes=4))['vaults'][0]
            self.assertEqual(len(replenished['campaigns']), 4)
            self.assertEqual(replenished['deferred'], [])
            self.assertEqual(list(iter_receipts()), receipts)
            self.assertEqual(len(fake.published), 1)

    def test_owner_helper_uses_separate_credentials_and_scheduled_receipt(self):
        import importlib.util
        import test_account_profiles as account_cases
        from ocpf_post import account_profiles as accounts
        from ocpf_post.state import write_private_json
        transport = account_cases.AccountTests()
        transport.setUp(); self.addCleanup(transport.doCleanups)
        helper_path = Path(__file__).resolve().parents[1] / 'scripts/connect-proof-and-state-feed.py'
        spec = importlib.util.spec_from_file_location('brand_feed_helper', helper_path)
        helper = importlib.util.module_from_spec(spec); spec.loader.exec_module(helper)
        profile = account_cases.profile('threads', helper.ACCOUNT)
        profile['bindings'] = [{'project': 'proof-and-state', 'alias': 'threads-brand'}]
        preview = accounts.register(profile)
        accounts.register(profile, apply=True, expected_sha256=preview['input_sha256'])
        secret = transport.root / 'credentials.json'
        write_private_json(secret, {'access_token': 'token-' + helper.ACCOUNT, 'expires_at': 4102444800})
        accounts.connect('threads', helper.ACCOUNT, credential_file=secret)
        activation = accounts.activation('threads', helper.ACCOUNT)
        accounts.activation('threads', helper.ACCOUNT, apply=True, expected_sha256=activation['review_sha256'])
        policy = {**self.policy, 'id': helper.VAULT, 'project': 'proof-and-state',
                  'document_id': helper.DOCUMENT, 'destinations': {'x': 'x-founder'}}
        path = transport.root / 'policy.json'; path.write_text(json.dumps(policy))
        preview = vault.register(path, enable=True)
        vault.register(path, enable=True, apply=True, expected_sha256=preview['input_sha256'])
        entry = self.entry('threads', 'PAS-THVAULT-TEST-001', 'A brand-bound vault campaign.')
        document = {'document_id': helper.DOCUMENT, 'version': '1',
                    'text': self.section([]) + self.section([entry], 'threads')}
        import contextlib
        import io
        with contextlib.redirect_stdout(io.StringIO()):
            result = helper.connect(reader=lambda _: document, now=self.now)
            repeated = helper.connect(reader=lambda _: document, now=self.now)
        self.assertEqual(result['approved_threads_campaigns'], 1)
        self.assertEqual(repeated['authority_result'], 'already_present')
        campaign = result['sync']['vaults'][0]['campaigns'][0]
        # The actual adapters use the scoped bundle; a founder environment token cannot override it.
        with patch.dict(os.environ, {'THREADS_ACCESS_TOKEN': 'founder-token'}):
            schedule = create_schedule(campaign=campaign, provider='threads',
                                       at=(self.now + timedelta(minutes=2)).isoformat(), now=self.now)
            run_due(now=self.now + timedelta(minutes=3))
        self.assertEqual(transport.posts[0][1], helper.ACCOUNT)
        receipt = list(iter_receipts())[-1]
        self.assertEqual(receipt['account_id'], helper.ACCOUNT)
        self.assertEqual(receipt['schedule_id'], schedule['schedule_id'])
        self.assertEqual(receipt['status'], 'published_verified')


if __name__ == '__main__':
    unittest.main()
