"""Owner-approval pairing must survive a CLI exit without replaying a request.

All network calls are intercepted. No customer account, Cloud authority, or
provider effect is created by this test module.
"""
from __future__ import annotations

import contextlib
import io
import os
import subprocess
import sys
from pathlib import Path
import stat
import tempfile
import time
import unittest
from unittest.mock import patch

from ocpf_post import poststeward_cloud as cloud


class PairingResumeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="poststeward-pairing-resume-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.env = patch.dict(os.environ, {
            "XDG_CONFIG_HOME": str(self.root / "config"),
            "POSTSTEWARD_ORIGIN": "https://poststeward.example",
        })
        self.env.start()
        self.addCleanup(self.env.stop)

    @staticmethod
    def started(pair="pair-one", code="ABC-123"):
        return {
            "pairingId": pair,
            "pollToken": "secret-" + pair,
            "verificationUrl": "https://poststeward.example/owner/pair",
            "userCode": code,
            "expiresAt": int(time.time() * 1000) + 120_000,
            "pollAfterMs": 1000,
        }

    @staticmethod
    def approved(requested):
        return {
            "status": "paired",
            "installationId": requested["installation_id"],
            "workspace": "owner-workspace",
            "runtimeToken": "owner-approved-runtime-token",
            "tokenExpiresAt": int(time.time() * 1000) + 900_000,
            "executor": {"authorityGeneration": 2, "executorMode": "hosted"},
        }

    def test_no_wait_restart_reuses_exact_pending_request_without_new_post(self):
        requests = []
        offered = self.started()
        with patch.object(cloud, "_request", side_effect=lambda method, path, **kwargs: (
            requests.append((method, path, kwargs)) or (200, offered)
        )), contextlib.redirect_stdout(io.StringIO()) as captured:
            self.assertEqual(cloud.onboard(no_open=True, wait=False), offered)
            self.assertEqual(cloud.onboard(no_open=True, wait=False), offered)
        self.assertEqual([x[0] for x in requests], ["POST"])
        self.assertNotIn(offered["pollToken"], captured.getvalue())
        persisted = cloud._read()["pending_pairing"]
        self.assertEqual(persisted["started"]["pairingId"], offered["pairingId"])
        self.assertEqual(persisted["installation_id"], cloud._read()["installation_id"])
        self.assertEqual(persisted["origin"], "https://poststeward.example")
        self.assertEqual(stat.S_IMODE(cloud.client_path().stat().st_mode), 0o600)

    def test_restart_can_obtain_explicit_approval_using_original_poll_token(self):
        offered = self.started()
        saved = []
        def first_request(method, path, **kwargs):
            self.assertEqual(method, "POST")
            return 200, offered
        with patch.object(cloud, "_request", side_effect=first_request):
            cloud.onboard(no_open=True, wait=False)
        original_installation = cloud._read()["installation_id"]
        def resumed_request(method, path, **kwargs):
            saved.append((method, path, kwargs))
            self.assertEqual(method, "GET", "resumption must not request another pairing")
            self.assertIn(offered["pairingId"], path)
            self.assertEqual(kwargs.get("token"), offered["pollToken"])
            return 200, self.approved({"installation_id": original_installation})
        with patch.object(cloud, "_request", side_effect=resumed_request), patch.object(
            cloud, "bindings", return_value={"workspace": "owner-workspace", "executor": {"executorMode": "hosted"}}
        ):
            accepted = cloud.onboard(no_open=True, wait=True)
        self.assertEqual(accepted["workspace"], "owner-workspace")
        self.assertEqual(len(saved), 1)
        persisted = cloud._read()
        self.assertEqual(persisted["runtime_token"], "owner-approved-runtime-token")
        self.assertNotIn("pending_pairing", persisted)
        self.assertEqual(persisted["installation_id"], original_installation)

    def test_expired_pending_approval_requires_explicit_fresh_request(self):
        offered = self.started()
        with patch.object(cloud, "_request", return_value=(200, offered)):
            cloud.onboard(no_open=True, wait=False)
        old = cloud._read()
        old["pending_pairing"]["started"]["expiresAt"] = 1
        cloud._write(old)
        next_started = self.started(pair="fresh-request", code="NEW-555")
        with patch.object(cloud, "_request", return_value=(200, next_started)) as request:
            self.assertEqual(cloud.onboard(no_open=True, wait=False), next_started)
            request.assert_called_once()
            self.assertEqual(request.call_args.args[0], "POST")
        self.assertEqual(cloud._read()["pending_pairing"]["started"]["pairingId"], "fresh-request")

    def test_different_installation_cannot_reuse_an_older_poll_token(self):
        offered = self.started()
        with patch.object(cloud, "_request", return_value=(200, offered)):
            cloud.onboard(no_open=True, wait=False)
        altered = cloud._read()
        altered["installation_id"] = "second-installation"
        cloud._write(altered)
        next_started = self.started(pair="new-installation")
        with patch.object(cloud, "_request", return_value=(200, next_started)) as request:
            cloud.onboard(no_open=True, wait=False)
            request.assert_called_once()
        self.assertEqual(cloud._read()["pending_pairing"]["installation_id"], "second-installation")

    def test_revoked_pending_approval_blocks_without_automatic_reissue(self):
        offered = self.started()
        with patch.object(cloud, "_request", return_value=(200, offered)):
            cloud.onboard(no_open=True, wait=False)
        with patch.object(cloud, "_request", side_effect=cloud.CloudError(
            "RUNTIME_PAIRING_REVOKED", "no longer valid", 410
        )) as request:
            with self.assertRaises(cloud.CloudError) as error:
                cloud.onboard(no_open=True, wait=True)
            self.assertEqual(error.exception.status, 410)
            self.assertEqual(request.call_count, 1)
            self.assertEqual(request.call_args.args[0], "GET")
        self.assertNotIn("pending_pairing", cloud._read())

    def test_logout_abandons_pending_approval(self):
        with patch.object(cloud, "_request", return_value=(200, self.started())):
            cloud.onboard(no_open=True, wait=False)
        cloud.logout()
        self.assertNotIn("pending_pairing", cloud._read())


    def test_real_subprocess_exit_preserves_one_owner_request_for_resume(self):
        child = """
import time
from unittest.mock import patch
from ocpf_post import poststeward_cloud as cloud
offered = {
    'pairingId': 'process-pair',
    'pollToken': 'process-poll-secret',
    'verificationUrl': 'https://poststeward.example/app',
    'userCode': 'PROCESS-23',
    'expiresAt': int(time.time()*1000)+120000,
}
with patch.object(cloud, '_request', return_value=(200, offered)):
    cloud.onboard(no_open=True, wait=False)
"""
        env = os.environ.copy()
        env["PYTHONPATH"] = str(Path(__file__).resolve().parents[1] / "src") + os.pathsep + env.get("PYTHONPATH", "")
        run = subprocess.run([sys.executable, "-c", child], env=env, capture_output=True, text=True, timeout=15)
        self.assertEqual(run.returncode, 0, run.stderr)
        persisted = cloud._read()
        self.assertEqual(persisted["pending_pairing"]["started"]["pairingId"], "process-pair")
        identity = persisted["installation_id"]

        def resumed(method, path, **kwargs):
            self.assertEqual(method, "GET")
            self.assertEqual(kwargs.get("token"), "process-poll-secret")
            self.assertIn("process-pair", path)
            return 200, self.approved({"installation_id": identity})
        with patch.object(cloud, "_request", side_effect=resumed) as request, patch.object(
            cloud, "bindings", return_value={"workspace": "owner-workspace"}
        ):
            cloud.onboard(no_open=True, wait=True)
            request.assert_called_once()
        self.assertEqual(cloud._read()["runtime_token"], "owner-approved-runtime-token")
        self.assertNotIn("pending_pairing", cloud._read())

    def test_mismatched_owner_approval_does_not_install_runtime_token(self):
        with patch.object(cloud, "_request", return_value=(200, self.started())):
            cloud.onboard(no_open=True, wait=False)
        wrong = self.approved({"installation_id": "a-different-installation"})
        with patch.object(cloud, "_request", return_value=(200, wrong)):
            with self.assertRaises(cloud.CloudError) as error:
                cloud.onboard(no_open=True, wait=True)
        self.assertEqual(error.exception.code, "RUNTIME_PAIRING_IDENTITY_MISMATCH")
        self.assertNotIn("runtime_token", cloud._read())
        self.assertNotIn("pending_pairing", cloud._read())

    def test_untrusted_approval_origin_is_rejected_before_storage_or_browser(self):
        offered = self.started()
        offered["verificationUrl"] = "https://impostor.example/pair"
        with patch.object(cloud, "_request", return_value=(200, offered)), patch.object(
            cloud.webbrowser, "open"
        ) as browser:
            with self.assertRaises(cloud.CloudError) as error:
                cloud.onboard(wait=False)
        self.assertEqual(error.exception.code, "RUNTIME_PAIRING_INVALID_RESPONSE")
        browser.assert_not_called()
        self.assertNotIn("pending_pairing", cloud._read())

    def test_untrusted_poll_delay_is_bounded_without_losing_owner_approval(self):
        offered = self.started()
        offered["pollAfterMs"] = 86_400_000
        with patch.object(cloud, "_request", return_value=(200, offered)):
            cloud.onboard(no_open=True, wait=False)
        identity = cloud._read()["installation_id"]
        with patch.object(cloud, "_request", side_effect=[
            (202, {"status": "pending"}),
            (200, self.approved({"installation_id": identity})),
        ]) as request, patch.object(cloud.time, "sleep") as sleep, patch.object(
            cloud, "bindings", return_value={"workspace": "owner-workspace"}
        ):
            cloud.onboard(no_open=True, wait=True)
        self.assertEqual(request.call_count, 2)
        sleep.assert_called_once_with(10)


    def test_simultaneous_cli_starts_claim_one_pairing_request(self):
        """Cross-process start is serialised; the second reuses durable state."""
        child = """
import os,time
from unittest.mock import patch
from ocpf_post import poststeward_cloud as cloud
offered = {
    'pairingId': 'concurrent-pair',
    'pollToken': 'concurrent-secret',
    'verificationUrl': 'https://poststeward.example/app',
    'userCode': 'ONE-CODE',
    'expiresAt': int(time.time()*1000)+120000,
}
def request(method, path, **kwargs):
    if method != 'POST':
        raise AssertionError('unexpected effect')
    with open(os.environ['TEST_POSTS_LOG'], 'a', encoding='utf-8') as handle:
        handle.write('POST' + chr(10))
    time.sleep(0.35)
    return 200, offered
with patch.object(cloud, '_request', side_effect=request):
    cloud.onboard(no_open=True, wait=False)
"""
        log = self.root / "posts.log"
        env = os.environ.copy()
        env["TEST_POSTS_LOG"] = str(log)
        env["PYTHONPATH"] = str(Path(__file__).resolve().parents[1] / "src") + os.pathsep + env.get("PYTHONPATH", "")
        processes = [
            subprocess.Popen([sys.executable, "-c", child], env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            for _ in range(2)
        ]
        for process in processes:
            stdout, stderr = process.communicate(timeout=25)
            self.assertEqual(process.returncode, 0, stderr + stdout)
        self.assertEqual(log.read_text(encoding="utf-8").splitlines(), ["POST"])
        self.assertEqual(cloud._read()["pending_pairing"]["started"]["pairingId"], "concurrent-pair")
        self.assertNotIn("runtime_token", cloud._read())


    def test_logout_during_remote_approval_cannot_resurrect_runtime_token(self):
        with patch.object(cloud, "_request", return_value=(200, self.started())):
            cloud.onboard(no_open=True, wait=False)
        installation = cloud._read()["installation_id"]

        def revoked_during_poll(method, path, **kwargs):
            self.assertEqual(method, "GET")
            cloud.logout()  # Simulate owner revoking the local pending request.
            return 200, self.approved({"installation_id": installation})

        with patch.object(cloud, "_request", side_effect=revoked_during_poll):
            with self.assertRaises(cloud.CloudError) as error:
                cloud.onboard(no_open=True, wait=True)
        self.assertEqual(error.exception.code, "RUNTIME_PAIRING_CONTEXT_CHANGED")
        self.assertNotIn("runtime_token", cloud._read())
        self.assertNotIn("pending_pairing", cloud._read())

    def test_old_poll_failure_does_not_delete_newer_approval_request(self):
        offered = self.started()
        with patch.object(cloud, "_request", return_value=(200, offered)):
            cloud.onboard(no_open=True, wait=False)
        replacement = self.started(pair="new-pair-after-revocation")

        def changed_during_poll(method, path, **kwargs):
            self.assertEqual(method, "GET")
            current = cloud._read()
            current["pending_pairing"]["started"] = replacement
            cloud._write(current)
            raise cloud.CloudError("RUNTIME_PAIRING_REVOKED", "old approval revoked", 410)

        with patch.object(cloud, "_request", side_effect=changed_during_poll):
            with self.assertRaises(cloud.CloudError):
                cloud.onboard(no_open=True, wait=True)
        self.assertEqual(cloud._read()["pending_pairing"]["started"]["pairingId"], replacement["pairingId"])
        self.assertNotIn("runtime_token", cloud._read())


if __name__ == "__main__":
    unittest.main()
