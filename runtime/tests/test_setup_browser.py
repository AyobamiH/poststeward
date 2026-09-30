from __future__ import annotations

from http.client import HTTPConnection
from pathlib import Path
import tempfile
import threading
import unittest
from urllib.parse import urlencode

from ocpf_post.setup_browser import BIND_HOST, MAX_REQUEST_BYTES, build_server


class SetupBrowserSecurityTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.workspace = Path(self.tmp.name) / "bootstrap"
        self.server, self.app = build_server(self.workspace, port=0)
        self.port = self.server.server_port
        self.origin = f"http://{BIND_HOST}:{self.port}"
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=3)
        self.tmp.cleanup()

    def request(
        self,
        method: str,
        path: str,
        *,
        body: str | None = None,
        headers: dict[str, str] | None = None,
    ) -> tuple[int, dict[str, str], str]:
        conn = HTTPConnection(BIND_HOST, self.port, timeout=5)
        payload = body.encode("utf-8") if body is not None else None
        conn.request(method, path, body=payload, headers=headers or {})
        response = conn.getresponse()
        data = response.read().decode("utf-8")
        result_headers = {key: value for key, value in response.getheaders()}
        status = response.status
        conn.close()
        return status, result_headers, data

    def bootstrap(self) -> str:
        token = self.app.bootstrap_token
        self.assertIsNotNone(token)
        assert token is not None
        status, headers, _ = self.request("GET", f"/?session={token}")
        self.assertEqual(status, 303)
        self.assertEqual(headers["Location"], "/")
        self.assertIsNone(self.app.bootstrap_token)
        cookie = headers["Set-Cookie"]
        self.assertIn("HttpOnly", cookie)
        self.assertIn("SameSite=Strict", cookie)
        self.assertNotIn(token, headers["Location"])
        replay_status, _, _ = self.request("GET", f"/?session={token}")
        self.assertEqual(replay_status, 403)
        return cookie.split(";", 1)[0]

    def post(
        self,
        path: str,
        values: dict[str, str],
        *,
        cookie: str,
        origin: str | None = None,
    ) -> tuple[int, dict[str, str], str]:
        body = urlencode(values)
        headers = {
            "Cookie": cookie,
            "Content-Type": "application/x-www-form-urlencoded",
            "Origin": origin or self.origin,
        }
        return self.request("POST", path, body=body, headers=headers)

    def test_server_binds_literal_loopback_on_ephemeral_port(self) -> None:
        self.assertEqual(self.server.server_address[0], "127.0.0.1")
        self.assertGreater(self.port, 0)
        self.assertTrue(self.app.bootstrap_url(self.port).startswith(f"http://127.0.0.1:{self.port}/?session="))

    def test_unauthorised_browser_is_rejected_and_bootstrap_sets_strict_cookie(self) -> None:
        status, _, _ = self.request("GET", "/")
        self.assertEqual(status, 403)

        cookie = self.bootstrap()
        status, headers, body = self.request("GET", "/", headers={"Cookie": cookie})
        self.assertEqual(status, 200)
        self.assertEqual(headers["Cache-Control"], "no-store, max-age=0")
        self.assertIn("default-src 'none'", headers["Content-Security-Policy"])
        self.assertEqual(headers["X-Frame-Options"], "DENY")
        self.assertNotIn("<script", body.lower())
        self.assertIn("No setup session yet", body)

    def test_wrong_host_is_rejected(self) -> None:
        conn = HTTPConnection(BIND_HOST, self.port, timeout=5)
        conn.putrequest("GET", "/", skip_host=True)
        conn.putheader("Host", "attacker.example")
        conn.endheaders()
        response = conn.getresponse()
        response.read()
        self.assertEqual(response.status, 403)
        conn.close()

    def test_cross_origin_and_bad_csrf_fail_before_state_write(self) -> None:
        cookie = self.bootstrap()
        fields = {
            "csrf_token": self.app.csrf_token,
            "mode": "fresh",
            "operator_label": "Example Ltd",
            "timezone": "UTC",
            "pace": "occasional",
        }
        status, _, _ = self.post("/action/start", fields, cookie=cookie, origin="https://attacker.example")
        self.assertEqual(status, 403)
        self.assertIsNone(self.app.status())

        fields["csrf_token"] = "wrong"
        status, _, _ = self.post("/action/start", fields, cookie=cookie)
        self.assertEqual(status, 403)
        self.assertIsNone(self.app.status())

    def test_valid_fresh_setup_uses_same_engine_and_remains_non_publishing(self) -> None:
        cookie = self.bootstrap()
        status, _, body = self.post(
            "/action/start",
            {
                "csrf_token": self.app.csrf_token,
                "mode": "fresh",
                "operator_label": "Example Ltd",
                "machine_label": "browser-test",
                "timezone": "UTC",
                "pace": "occasional",
            },
            cookie=cookie,
        )
        self.assertEqual(status, 200)
        self.assertIn("Setup started", body)
        current = self.app.status()
        self.assertIsNotNone(current)
        assert current is not None
        self.assertEqual(current["session"]["stage"], "configuration_ready")
        self.assertFalse(current["publishing_authority"])
        self.assertFalse(current["automation_enabled"])
        self.assertEqual(current["configuration"]["daily_originals"], 3)

    def test_request_body_limit_fails_closed(self) -> None:
        cookie = self.bootstrap()
        conn = HTTPConnection(BIND_HOST, self.port, timeout=5)
        conn.putrequest("POST", "/action/start")
        conn.putheader("Host", f"{BIND_HOST}:{self.port}")
        conn.putheader("Cookie", cookie)
        conn.putheader("Origin", self.origin)
        conn.putheader("Content-Type", "application/x-www-form-urlencoded")
        conn.putheader("Content-Length", str(MAX_REQUEST_BYTES + 1))
        conn.endheaders()
        response = conn.getresponse()
        response.read()
        self.assertEqual(response.status, 413)
        conn.close()


if __name__ == "__main__":
    unittest.main()
