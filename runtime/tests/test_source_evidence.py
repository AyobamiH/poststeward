from copy import deepcopy
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from ocpf_post.replenisher import ReplenisherError
from ocpf_post.runtime_sources import SourceError
from ocpf_post.source_evidence import inspect_source

SHA = 'a' * 40
ROOT = 'https://api.github.com/repos/owner/source'


class EvidenceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.env = patch.dict(os.environ, {'OCPF_POST_CONFIG_DIR': self.tmp.name + '/config', 'OCPF_POST_STATE_DIR': self.tmp.name + '/state'})
        self.env.start(); self.addCleanup(self.env.stop)
        self.profile = {'repository': 'owner/source'}
        self.configuration = patch('ocpf_post.source_evidence._configured', return_value=(self.profile, 42))
        self.configuration.start(); self.addCleanup(self.configuration.stop)
        self.responses = {
            '': {'id': 42, 'full_name': 'owner/source', 'private': True, 'default_branch': 'main'},
            '/commits/main?per_page=100': {'sha': SHA, 'commit': {'message': 'private implementation text'}},
            f'/commits/{SHA}?per_page=100': {'sha': SHA},
            f'/actions/runs?head_sha={SHA}&per_page=100': {'total_count': 1, 'workflow_runs': [
                {'id': 7, 'head_sha': SHA, 'workflow_id': 3, 'event': 'push', 'status': 'completed', 'conclusion': 'success'}]},
            f'/deployments?sha={SHA}&per_page=10': [{'id': 9, 'sha': SHA, 'environment': 'production', 'production_environment': True}],
            '/deployments/9/statuses?per_page=1': [{'id': 10, 'state': 'success', 'description': 'private operational detail'}],
        }
        self.calls = []

    def fetch(self, url, *, token):
        self.assertEqual(token, 'test-token')
        self.assertTrue(url.startswith(ROOT))
        self.calls.append(url)
        value = self.responses[url[len(ROOT):]]
        if isinstance(value, Exception):
            raise value
        return deepcopy(value)

    def inspect(self, **kwargs):
        return inspect_source('test', fetch=self.fetch, token='test-token', **kwargs)

    def test_exact_revision_success_is_not_runtime_or_claim_authority(self):
        result = self.inspect()
        self.assertEqual(result['source']['sha'], SHA)
        self.assertEqual(result['workflow_runs']['records'][0]['conclusion'], 'success')
        self.assertEqual(result['deployments']['records'][0]['latest_status']['state'], 'success')
        self.assertEqual(result['runtime_verification']['status'], 'not_observed')
        self.assertEqual(result['business_outcomes']['status'], 'not_observed')
        self.assertFalse(result['publishing_authority_changed'])
        self.assertNotIn('private implementation text', json.dumps(result))
        self.assertNotIn('private operational detail', json.dumps(result))
        self.assertNotIn('test-token', json.dumps(result))
        self.assertEqual(list(Path(self.tmp.name).iterdir()), [])

    def test_explicit_commit_does_not_inspect_default_head(self):
        self.inspect(sha=SHA)
        self.assertIn(ROOT + f'/commits/{SHA}?per_page=100', self.calls)
        self.assertNotIn(ROOT + '/commits/main?per_page=100', self.calls)

    def test_wrong_revision_never_becomes_positive_evidence(self):
        self.responses[f'/actions/runs?head_sha={SHA}&per_page=100']['workflow_runs'][0]['head_sha'] = 'b' * 40
        self.responses[f'/deployments?sha={SHA}&per_page=10'][0]['sha'] = 'b' * 40
        result = self.inspect()
        self.assertEqual(result['workflow_runs']['status'], 'unavailable')
        self.assertEqual(result['deployments']['status'], 'unavailable')

    def test_missing_is_distinct_from_unavailable(self):
        self.responses[f'/actions/runs?head_sha={SHA}&per_page=100'] = {'total_count': 0, 'workflow_runs': []}
        self.responses[f'/deployments?sha={SHA}&per_page=10'] = ReplenisherError('HTTP 403')
        result = self.inspect()
        self.assertEqual(result['workflow_runs']['status'], 'none_observed')
        self.assertEqual(result['deployments']['status'], 'unavailable')

    def test_deployment_latest_failure_or_unavailable_not_old_success(self):
        self.responses['/deployments/9/statuses?per_page=1'] = [{'state': 'failure'}]
        self.assertEqual(self.inspect()['deployments']['records'][0]['latest_status']['state'], 'failure')
        self.responses['/deployments/9/statuses?per_page=1'] = ReplenisherError('private error details')
        row = self.inspect()['deployments']['records'][0]
        self.assertIsNone(row['latest_status'])
        self.assertEqual(row['status_observation'], 'unavailable')

    def test_pagination_and_pending_results_do_not_become_all_passed(self):
        runs = self.responses[f'/actions/runs?head_sha={SHA}&per_page=100']
        runs['total_count'] = 101
        runs['workflow_runs'].append({'id': 8, 'head_sha': SHA, 'status': 'in_progress', 'conclusion': None})
        result = self.inspect()
        self.assertTrue(result['workflow_runs']['possibly_truncated'])
        self.assertIsNone(result['workflow_runs']['records'][-1]['conclusion'])
        self.assertNotIn('passed', result)

    def test_repository_replacement_or_bad_input_stops_before_secondary_reads(self):
        self.responses['']['id'] = 99
        with self.assertRaises(SourceError):
            self.inspect()
        self.assertEqual(self.calls, [ROOT])
        for sha in ('main', '../secrets', 'a' * 7, 'x' * 40):
            with self.assertRaises(SourceError):
                self.inspect(sha=sha)

    def test_wrong_source_commit_is_rejected(self):
        self.responses[f'/commits/{SHA}?per_page=100']['sha'] = 'b' * 40
        with self.assertRaises(SourceError):
            self.inspect(sha=SHA)

    def test_unsafe_configured_url_is_not_fetched(self):
        self.profile['repository'] = 'owner/source?token=private'
        with self.assertRaises(SourceError):
            self.inspect()
        self.assertEqual(self.calls, [])

    def test_registered_inactive_source_uses_real_configuration_without_activation(self):
        from ocpf_post.onboarding import import_project
        from ocpf_post.runtime_sources import import_source, list_sources
        from test_onboarding import project_input
        from test_runtime_sources import policy
        self.configuration.stop()
        path = Path(self.tmp.name) / 'input.json'
        for importer, value in ((import_project, project_input()), (import_source, policy())):
            path.write_text(json.dumps(value))
            preview = importer(path)
            importer(path, apply=True, expected_sha256=preview['input_sha256'])
        before = {str(p): p.read_bytes() for p in Path(self.tmp.name).rglob('*') if p.is_file()}
        result = inspect_source('runtime-test', fetch=self.fetch, token='test-token')
        after = {str(p): p.read_bytes() for p in Path(self.tmp.name).rglob('*') if p.is_file()}
        self.assertEqual(result['repository']['id'], 42)
        self.assertEqual(before, after)
        self.assertFalse((Path(self.tmp.name) / 'state').exists())
