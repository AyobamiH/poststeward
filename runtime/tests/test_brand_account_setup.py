import importlib.util
import contextlib
import io
import json
from pathlib import Path
import unittest
from unittest.mock import patch

from test_account_profiles import AccountTests
from ocpf_post import account_profiles as accounts

spec = importlib.util.spec_from_file_location('brand_setup', Path(__file__).resolve().parents[1] / 'scripts/connect-brand-accounts.py')
helper = importlib.util.module_from_spec(spec)
spec.loader.exec_module(helper)


class BrandSetupTests(unittest.TestCase):
    setUp = AccountTests.setUp
    transport = AccountTests.transport
    def test_wrong_handle_leaves_registry_and_credentials_untouched(self):
        with self.assertRaises(helper.SetupError):
            helper.setup('threads', token='token-111')
        self.assertFalse(accounts.path().exists())
        self.assertFalse(self.posts)

    def test_verified_threads_id_is_discovered_and_remains_inactive(self):
        original = self.transport
        def transport(provider, url, **kwargs):
            result = original(provider, url, **kwargs)
            if url.endswith('/me'):
                result[2]['username'] = 'proofandstate'
            return result
        with patch('ocpf_post.providers.threads.request_json', side_effect=lambda url, **kw: transport('threads', url, **kw)):
            result = helper.setup('threads', token='token-111')
        self.assertEqual(result['account_id'], '111')
        self.assertEqual(result['handle'], '@proofandstate')
        row = accounts.require('threads', '111')
        self.assertFalse(row['enabled'])
        self.assertTrue(accounts.credential_present(row))
        self.assertEqual(row['bindings'], [{'project':'proof-and-state', 'alias':'threads-brand'}])
        self.assertFalse(self.posts)

    def test_refresh_failure_does_not_register_account(self):
        from ocpf_post.providers.base import ProviderRejected
        with patch('ocpf_post.providers.threads.ThreadsProvider.account') as account, patch('ocpf_post.providers.threads.ThreadsProvider.refresh', side_effect=ProviderRejected(400, 'private body')):
            from ocpf_post.model import AccountIdentity
            account.return_value = AccountIdentity(provider='threads', account_id='111', username='proofandstate')
            with self.assertRaises(ProviderRejected):
                helper.setup('threads', token='token-111')
        self.assertFalse(accounts.path().exists())


    def test_new_threads_token_uses_supplied_expiry_without_forced_refresh(self):
        from ocpf_post.model import AccountIdentity
        with patch('ocpf_post.providers.threads.ThreadsProvider.account', return_value=AccountIdentity(provider='threads', account_id='111', username='proofandstate')), patch('ocpf_post.providers.threads.ThreadsProvider.refresh', side_effect=AssertionError('Not refreshable yet')):
            result = helper.setup('threads', token='token-111', expires_at=4102444800)
        self.assertFalse(result['enabled'])

    def test_x_oauth_discovers_brand_and_does_not_enable_publishing(self):
        from ocpf_post.providers.x import XProvider
        from ocpf_post.model import AccountIdentity
        def authorize(client):
            client._save_token({'access_token':'token-222', 'refresh_token':'refresh-222', 'expires_in':7200})
            return client.account()
        with patch.object(XProvider, 'authorize', authorize), patch.object(XProvider, 'account', return_value=AccountIdentity(provider='x', account_id='222', username='oneclickposty')):
            result = helper.setup('x', client_id='brand-app')
        self.assertEqual(result['handle'], '@oneclickposty')
        self.assertFalse(accounts.require('x', '222')['enabled'])
        self.assertFalse(self.posts)

    def test_confidential_x_secret_survives_reconstruction_and_refresh_without_founder_changes(self):
        import base64
        from ocpf_post.providers.x import XProvider
        from ocpf_post.model import AccountIdentity
        from ocpf_post.state import write_private_json, read_json
        protected = [self.root/'config'/'x-token.json', self.root/'config'/'x-settings.json',
                     self.root/'config'/'accounts'/'threads'/'38453495070963373'/'token.json',
                     self.root/'state'/'schedule-events.jsonl']
        for path in protected: write_private_json(path, {'sentinel': path.name})
        before = {p: p.read_bytes() for p in protected}
        secret = 'private-brand-client-secret'
        expected_header = 'Basic ' + base64.b64encode(('brand-app:' + secret).encode()).decode()
        def authorize(client):
            self.assertEqual(client._token_headers()['Authorization'], expected_header)
            client._save_token({'access_token':'token-222', 'refresh_token':'refresh-222', 'expires_in':7200})
            return client.account()
        output = io.StringIO()
        with contextlib.redirect_stdout(output), patch.object(XProvider, 'authorize', authorize), patch.object(XProvider, 'account', return_value=AccountIdentity(provider='x', account_id='222', username='oneclickposty')):
            helper.setup('x', client_id='brand-app', client_secret=secret)
            self.assertEqual(helper.readiness('x')['result'], 'connected_inactive')
            for _ in range(2):
                client = XProvider(credential_dir=accounts.directory('x','222'))
                self.assertEqual(client._token_headers()['Authorization'], expected_header)
                client.refresh(quiet=True)
            result = helper.activate_brand('x')
        self.assertEqual(result['result'], 'enabled')
        token = accounts.directory('x','222')/'token.json'
        self.assertEqual(read_json(token)['client_secret'], secret)
        self.assertEqual(token.stat().st_mode & 0o777, 0o600)
        self.assertNotIn(secret, output.getvalue())
        self.assertEqual(before, {p:p.read_bytes() for p in protected})
        self.assertFalse(self.posts)

    def test_wrong_x_login_is_discarded_and_activation_requires_connection(self):
        from ocpf_post.providers.x import XProvider
        from ocpf_post.model import AccountIdentity
        with patch.object(XProvider, 'authorize', return_value=AccountIdentity(provider='x', account_id='222', username='founder')):
            with self.assertRaises(helper.SetupError): helper.setup('x', client_id='brand-app')
        self.assertFalse(accounts.path().exists())
        with self.assertRaises(helper.SetupError): helper.activate_brand('x')

    def test_status_has_no_network_and_repeated_connection_does_not_prompt_or_activate(self):
        from ocpf_post.providers.x import XProvider
        from ocpf_post.model import AccountIdentity
        def authorize(client):
            client._save_token({'access_token':'token-222','refresh_token':'refresh-222'})
            return AccountIdentity(provider='x', account_id='222', username='oneclickposty')
        with patch.object(XProvider,'authorize',authorize), patch.object(XProvider,'account',return_value=AccountIdentity(provider='x',account_id='222',username='oneclickposty')):
            helper.setup('x',client_id='brand-app')
        before = accounts.path().read_bytes()
        output = io.StringIO()
        with patch.object(helper,'input',side_effect=AssertionError('No prompt'),create=True), patch.object(helper,'get_provider',side_effect=AssertionError('No network')), contextlib.redirect_stdout(output):
            helper.main(['--provider','x','--status'])
            helper.main(['--provider','x'])
        self.assertIn('connected_inactive',output.getvalue())
        self.assertNotIn('token-222',output.getvalue())
        self.assertEqual(accounts.path().read_bytes(),before)

    def test_activation_rejects_live_numeric_identity_drift(self):
        from ocpf_post.providers.x import XProvider
        from ocpf_post.model import AccountIdentity
        def authorize(client):
            client._save_token({'access_token':'token-222','refresh_token':'refresh-222'})
            return AccountIdentity(provider='x',account_id='222',username='oneclickposty')
        with patch.object(XProvider,'authorize',authorize), patch.object(XProvider,'account',return_value=AccountIdentity(provider='x',account_id='222',username='oneclickposty')):
            helper.setup('x',client_id='brand-app')
        with patch.object(XProvider,'account',return_value=AccountIdentity(provider='x',account_id='999',username='oneclickposty')):
            with self.assertRaises(ValueError): helper.activate_brand('x')
        self.assertFalse(accounts.require('x','222')['enabled'])


del AccountTests
