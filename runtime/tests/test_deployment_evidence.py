import base64
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from ocpf_post import deployment_evidence as deploy, application_evidence as app


class DeploymentEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.sha = 'a' * 40
        raw = b'reviewed fixture workflow'
        blob = hashlib.sha1(b'blob ' + str(len(raw)).encode() + b'\0' + raw).hexdigest()
        p = patch.object(deploy, 'REVIEWED_WORKFLOW_BLOB', blob); p.start(); self.addCleanup(p.stop)
        self.definition = {'path': deploy.WORKFLOW, 'encoding': 'base64', 'sha': blob,
                           'content': base64.b64encode(raw).decode()}
        self.run = {'id': 10, 'head_sha': self.sha, 'path': deploy.WORKFLOW, 'head_branch': 'main',
                    'event': 'push', 'run_attempt': 2, 'status': 'completed', 'conclusion': 'success', 'updated_at': 'now'}
        self.runs = [self.run]
        self.job = {'id': 20, 'run_id': 10, 'run_attempt': 2, 'head_sha': self.sha,
                    'name': 'deploy', 'status': 'completed', 'conclusion': 'success',
                    'steps': [{'name': name, 'number': i, 'status': 'completed', 'conclusion': 'success'}
                              for i, name in enumerate(deploy.REQUIRED_STEPS, 1)]}
        self.gets = 0

    def fetch(self, url, **kwargs):
        if '/contents/' in url: return deepcopy(self.definition)
        if '/actions/runs?' in url: return {'workflow_runs': deepcopy(self.runs)}
        if '/jobs?' in url: return {'total_count': 1, 'jobs': [deepcopy(self.job)]}
        self.gets += 1
        return deepcopy(self.run)

    def observe(self):
        return deploy.observe_opstruth_deployment(self.sha, fetch=self.fetch, token='test')

    def test_exact_reviewed_workflow_and_latest_attempt_match(self):
        result = self.observe()
        self.assertEqual(result['status'], 'observed')
        self.assertEqual(result['run_attempt'], 2)
        self.assertEqual(result['job_id'], 20)
        self.assertEqual(self.gets, 2)

    def test_ordinary_ci_or_a_new_failed_deployment_is_not_success(self):
        self.runs = [{**self.run, 'path': '.github/workflows/ci.yml'}]
        self.assertEqual(self.observe()['status'], 'unconfirmed')
        self.runs = [deepcopy(self.run), {**self.run, 'id': 11, 'conclusion': 'failure'}]
        self.run = self.runs[-1]
        self.assertEqual(self.observe()['status'], 'unconfirmed')

    def test_wrong_definition_attempt_revision_and_steps_are_rejected(self):
        for field, value in [('run_attempt', 1), ('head_sha', 'b'*40), ('run_id', 99), ('conclusion', 'failure')]:
            with self.subTest(field=field):
                old = self.job[field]; self.job[field] = value
                self.assertEqual(self.observe()['status'], 'unavailable'); self.job[field] = old
        self.job['steps'][1]['conclusion'] = 'skipped'
        self.assertEqual(self.observe()['status'], 'unavailable')
        self.job['steps'][1]['conclusion'] = 'success'
        self.definition['content'] = base64.b64encode(b'changed workflow').decode()
        self.assertEqual(self.observe()['status'], 'unavailable')

    def test_attempt_changed_during_read_cannot_close_evidence(self):
        def fetch(url, **kwargs):
            result = self.fetch(url, **kwargs)
            if self.gets == 2: result['run_attempt'] = 3
            return result
        self.assertEqual(deploy.observe_opstruth_deployment(self.sha, fetch=fetch)['status'], 'unavailable')

    def test_workflow_match_still_requires_current_runtime_and_identity(self):
        with tempfile.TemporaryDirectory() as temporary, patch.dict(os.environ, {'OCPF_POST_STATE_DIR': temporary}):
            path = Path(temporary) / 'check.json'
            config = json.loads((Path(__file__).resolve().parents[1] / 'examples/opstruth-application-check.json').read_text())
            path.write_text(json.dumps(config))
            preview = app.check(path, sha=self.sha)
            source = {'source': {'sha': self.sha}, 'deployments': {'records': []}}
            health = {**config['assertions'], 'commit': self.sha}
            kwargs = dict(sha=self.sha, apply=True, expected_sha256=preview['input_sha256'],
                          source_reader=lambda *a, **kw: source, deployment_reader=lambda _: self.observe(), reader=lambda _: health)
            with patch.object(app, 'verifier_identity', return_value={'passed': True}):
                self.assertEqual(app.check(path, **kwargs)['status'], 'application_observed_workflow_deployment_matched')
                health['commit'] = 'b'*40
                self.assertEqual(app.check(path, **kwargs)['status'], 'failed')
                health['commit'] = self.sha
            with patch.object(app, 'verifier_identity', return_value={'passed': False}):
                self.assertEqual(app.check(path, **kwargs)['status'], 'failed')

    def test_adapter_cannot_be_reused_for_another_environment(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / 'check.json'
            config = json.loads((Path(__file__).resolve().parents[1] / 'examples/opstruth-application-check.json').read_text())
            config['environment'] = 'staging'; path.write_text(json.dumps(config))
            with self.assertRaises(ValueError): app.check(path, sha=self.sha)


if __name__ == '__main__':
    unittest.main()
