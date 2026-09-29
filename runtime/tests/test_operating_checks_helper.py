import contextlib
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch


class OperatingChecksHelperTests(unittest.TestCase):
    def helper(self):
        path = Path(__file__).resolve().parents[1] / 'scripts/complete-operating-checks.py'
        spec = importlib.util.spec_from_file_location('operating_checks_helper', path)
        module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
        return module

    def test_local_default_preserves_established_milestones_and_continues_after_failure(self):
        helper = self.helper(); calls = []
        def run(args, **kwargs):
            calls.append(args)
            self.assertGreater(kwargs['timeout'], 0)
            if 'queue' in args:
                raise subprocess.TimeoutExpired(args, kwargs['timeout'])
            if 'health' in args:
                return subprocess.CompletedProcess(args, 3, '{"status":"attention"}', '')
            return subprocess.CompletedProcess(args, 0, '{"status":"observed"}', '')
        with tempfile.TemporaryDirectory() as temp, patch.dict(os.environ, OCPF_POST_STATE_DIR=temp), \
             patch.object(helper.subprocess, 'run', side_effect=run), contextlib.redirect_stdout(io.StringIO()):
            helper.main(['--observe-only'])
            report = json.loads((Path(temp) / 'operating-checks.json').read_text())
            self.assertTrue(report['steps'][0]['execution_ok'])
            self.assertEqual(report['steps'][1]['status'], 'unavailable')
            self.assertTrue(report['steps'][2]['execution_ok'])
            self.assertEqual((Path(temp) / 'operating-checks.json').stat().st_mode & 0o777, 0o600)
        for args in calls:
            self.assertFalse(set(args) & {'--apply', '--live', 'run-due', 'replenish', 'connect-proof-and-state-feed.py', 'evidence'})
        self.assertEqual(len(calls), 3)

    def test_refresh_and_readback_use_only_existing_authority(self):
        helper = self.helper(); calls = []
        def run(args, **kwargs):
            calls.append(args)
            return subprocess.CompletedProcess(args, 0, '{"status":"observed"}', '')
        with tempfile.TemporaryDirectory() as temp, patch.dict(os.environ, OCPF_POST_STATE_DIR=temp), \
             patch.object(helper.subprocess, 'run', side_effect=run), contextlib.redirect_stdout(io.StringIO()):
            helper.main(['--refresh', '--readback'])
        self.assertTrue(any('reconcile' in args for args in calls))
        self.assertTrue(any('capture-due' in args for args in calls))
        for args in calls:
            self.assertFalse(set(args) & {'--live', 'run-due', 'process', 'enable', 'connect', 'evidence', 'replenish'})

    def test_show_saved_preserves_report_and_prints_actionable_diagnostics_without_calls(self):
        helper=self.helper()
        original=json.dumps({'schema_version':1,'observed_at':'2026-09-13T07:05:22Z','steps':[
            {'step':'timer-and-publishing-health','execution_ok':True,'status':'attention',
             'result':{'findings':[{'code':'service_failed','unit':'ocpf-post-run-due.service'}],
                       'units':{'ocpf-post-run-due.service':{'ExecMainStatus':'3'}}}}]})
        output=io.StringIO()
        with tempfile.TemporaryDirectory() as temp, patch.dict(os.environ,OCPF_POST_STATE_DIR=temp), \
             patch.object(helper.subprocess,'run',side_effect=AssertionError('Saved means no execution')), \
             contextlib.redirect_stdout(output):
            path=Path(temp)/'operating-checks.json';path.write_text(original)
            helper.main(['--show-saved'])
            self.assertEqual(path.read_text(),original)
        result=json.loads(output.getvalue())
        self.assertEqual(result['observed_at'],'2026-09-13T07:05:22Z')
        self.assertEqual(result['diagnostics']['health_findings'][0]['code'],'service_failed')
