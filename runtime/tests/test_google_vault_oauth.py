from __future__ import annotations

import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

from ocpf_post.google_vault import authorize
from ocpf_post.onboarding import OnboardingError


class _Lock:
    def __enter__(self):
        return None

    def __exit__(self, *args):
        return False


class GoogleVaultOAuthDiagnosticsTests(unittest.TestCase):
    def _client_file(self):
        fd, name = tempfile.mkstemp(suffix=".json")
        os.close(fd)
        path = Path(name)
        path.write_text(json.dumps({
            "installed": {
                "client_id": "client-id",
                "client_secret": "client-secret",
            }
        }), encoding="utf-8")
        os.chmod(path, 0o600)
        self.addCleanup(lambda: path.unlink(missing_ok=True))
        return str(path)

    def test_callback_timeout_is_reported_without_generic_import_error(self):
        class Server:
            def __init__(self, *_args):
                self.timeout = None

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def handle_request(self):
                return None

        with patch("http.server.HTTPServer", Server), \
             patch("webbrowser.open", return_value=True), \
             patch("ocpf_post.google_vault._import_lock", return_value=_Lock()), \
             patch("time.monotonic", side_effect=[0.0, 181.0]):
            with self.assertRaisesRegex(OnboardingError, "callback timed out"):
                authorize(self._client_file(), port=8766)

    def test_token_exchange_failure_reports_post_callback_stage(self):
        opened = []

        class Server:
            def __init__(self, _address, handler):
                self.handler = handler
                self.timeout = None
                self.used = False

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def handle_request(self):
                if self.used:
                    return
                self.used = True
                query = parse_qs(urlsplit(opened[-1]).query)
                state = query["state"][0]
                handler = self.handler.__new__(self.handler)
                handler.path = "/callback?state=" + state + "&code=short-lived-code"
                handler.send_response = lambda *_args, **_kwargs: None
                handler.end_headers = lambda: None
                handler.wfile = io.BytesIO()
                self.handler.do_GET(handler)

        def open_browser(url):
            opened.append(url)
            return True

        with patch("http.server.HTTPServer", Server), \
             patch("webbrowser.open", side_effect=open_browser), \
             patch("ocpf_post.google_vault._import_lock", return_value=_Lock()), \
             patch("ocpf_post.google_vault.json_request",
                   side_effect=ValueError("HTTPS request returned status 400; response body omitted")):
            with self.assertRaisesRegex(OnboardingError, "callback arrived, but token exchange failed"):
                authorize(self._client_file(), port=8766)

    def test_google_consent_error_is_reported_without_token_exchange(self):
        opened = []

        class Server:
            def __init__(self, _address, handler):
                self.handler = handler
                self.timeout = None
                self.used = False

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def handle_request(self):
                if self.used:
                    return
                self.used = True
                query = parse_qs(urlsplit(opened[-1]).query)
                state = query["state"][0]
                handler = self.handler.__new__(self.handler)
                handler.path = "/callback?state=" + state + "&error=access_denied"
                handler.send_response = lambda *_args, **_kwargs: None
                handler.end_headers = lambda: None
                handler.wfile = io.BytesIO()
                self.handler.do_GET(handler)

        with patch("http.server.HTTPServer", Server), \
             patch("webbrowser.open", side_effect=lambda url: opened.append(url) or True), \
             patch("ocpf_post.google_vault._import_lock", return_value=_Lock()), \
             patch("ocpf_post.google_vault.json_request") as request:
            with self.assertRaisesRegex(OnboardingError, "error=access_denied"):
                authorize(self._client_file(), port=8766)
            request.assert_not_called()


if __name__ == "__main__":
    unittest.main()
