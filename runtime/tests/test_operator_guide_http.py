from __future__ import annotations

import json
from pathlib import Path
import threading
import unittest
from urllib.request import Request, urlopen
from urllib.error import HTTPError
from unittest.mock import patch

from ocpf_post import observer_server, operator_guide


class OperatorGuideHttpTests(unittest.TestCase):
    def setUp(self):
        self.server = observer_server.ConsoleServer(('127.0.0.1', 0), observer_server.ConsoleHandler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        self.url = 'http://127.0.0.1:' + str(self.server.server_port)

    def test_command_endpoint_does_not_build_snapshot(self):
        with patch.object(observer_server, 'build_snapshot', side_effect=AssertionError('no new scan')):
            with urlopen(self.url + '/api/guide/commands', timeout=10) as response:
                data = json.load(response)
        self.assertTrue(data['commands'])
        self.assertEqual(data['commands'][0]['help_command'].split()[1], 'help')

    def test_composite_snapshot_reuses_existing_cache(self):
        value = {'revision': 'one', 'observed_at': '2026-09-21T18:00:00Z'}
        with patch.object(observer_server, 'build_snapshot', return_value={}), patch.object(observer_server, 'build_execution_snapshot', return_value=value) as build:
            one = self.server.snapshot(); two = self.server.snapshot()
        self.assertIs(one, two)
        self.assertIn('guide', one)
        self.assertEqual(one['guide']['source_revision'], one['revision'])
        self.assertEqual(build.call_count, 1)

    def test_assets_are_exact_allowlist(self):
        for path, mime in (('/guide.js', 'text/javascript'), ('/guide.css', 'text/css')):
            with self.subTest(path=path), urlopen(self.url + path) as response:
                self.assertTrue(response.headers['Content-Type'].startswith(mime))
                self.assertGreater(len(response.read()), 500)
        with self.assertRaises(HTTPError) as caught:
            urlopen(self.url + '/ui/../../state.json')
        self.assertEqual(caught.exception.code, 404)

    def test_mutating_methods_and_wrong_host_remain_rejected(self):
        for method in ('POST', 'PUT', 'DELETE', 'PATCH'):
            with self.subTest(method=method), self.assertRaises(HTTPError) as caught:
                urlopen(Request(self.url + '/api/guide/commands', data=b'{}', method=method))
            self.assertEqual(caught.exception.code, 405)
        with self.assertRaises(HTTPError) as caught:
            urlopen(Request(self.url + '/api/guide/commands', headers={'Host': 'untrusted.example'}))
        self.assertEqual(caught.exception.code, 421)

    def test_execution_includes_guide_classic_stays_original(self):
        with urlopen(self.url + '/') as response:
            body = response.read()
        self.assertIn(b'/guide.js', body)
        with urlopen(self.url + '/classic') as response:
            classic = response.read()
        self.assertNotIn(b'/guide.js', classic)

    def test_packaged_asset_contract(self):
        root = Path(__file__).resolve().parents[1]
        text = (root / 'pyproject.toml').read_text()
        self.assertIn('"ui/*.js"', text)
        self.assertIn('"ui/*.css"', text)
        self.assertTrue(callable(operator_guide.decorate_page))


if __name__ == '__main__':
    unittest.main()
