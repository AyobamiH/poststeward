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

    def test_ack_lost_after_cloud_consumes_pairing_is_recovered_from_private_candidate(self):
        offered = self.started()
        with patch.object(cloud, "_request", return_value=(201, offered)):
            cloud.onboard(no_open=True, wait=False)
        installation = cloud._read()["installation_id"]
        attempted = []
        expected_candidate = None

        def interrupted_claim(method, path, **kwargs):
            nonlocal expected_candidate
            self.assertEqual(method, "GET")
            candidate = kwargs.get("runtime_candidate")
            self.assertIsInstance(candidate, str)
            self.assertGreaterEqual(len(candidate), 64)
            attempted.append(candidate)
            if expected_candidate is None:
                expected_candidate = candidate
                raise cloud.CloudError("NETWORK_ERROR", "Response lost after Cloud committed")
            self.assertEqual(candidate, expected_candidate, "resume must retain exact token")
            return 200, {
                "status": "paired",
                "candidateAccepted": True,
                "installationId": installation,
                "workspace": "owner-workspace",
                "tokenExpiresAt": int(time.time() * 1000) + 900_000,
                "executor": {"executorMode": "hosted", "authorityGeneration": 1},
            }

        with patch.object(cloud, "_request", side_effect=interrupted_claim), patch.object(
            cloud.time, "sleep"
        ), patch.object(cloud, "bindings", return_value={"workspace": "owner-workspace"}):
            result = cloud.onboard(no_open=True, wait=True)
        self.assertEqual(result["workspace"], "owner-workspace")
        self.assertEqual(len(attempted), 2)
        self.assertEqual(cloud._read()["runtime_token"], expected_candidate)
        self.assertNotIn("pending_pairing", cloud._read())

    def test_claimed_pairing_recovers_after_owner_code_expired_without_new_post(self):
        offered = self.started()
        with patch.object(cloud, "_request", return_value=(201, offered)):
            cloud.onboard(no_open=True, wait=False)
        saved = cloud._read()
        identity = saved["installation_id"]
        original_candidate = saved["pending_pairing"]["claim_token"]
        saved["pending_pairing"]["started"]["expiresAt"] = int(time.time() * 1000) - 2_000
        cloud._write(saved)

        def recovered(method, path, **kwargs):
            self.assertEqual(method, "GET", "must not create a new approval")
            self.assertEqual(kwargs["runtime_candidate"], original_candidate)
            return 200, {
                "status": "paired",
                "candidateAccepted": True,
                "installationId": identity,
                "workspace": "owner-workspace",
                "tokenExpiresAt": int(time.time() * 1000) + 600_000,
            }
        with patch.object(cloud, "_request", side_effect=recovered) as request, patch.object(
            cloud, "bindings", return_value={"workspace": "owner-workspace"}
        ), patch.object(cloud.webbrowser, "open") as browser:
            cloud.onboard(no_open=False, wait=True)
        self.assertEqual(request.call_count, 1)
        browser.assert_not_called()
        self.assertEqual(cloud._read()["runtime_token"], original_candidate)

    def test_offline_claim_recovery_retains_original_provenance_without_new_post(self):
        offered = self.started()
        with patch.object(cloud, "_request", return_value=(201, offered)):
            cloud.onboard(no_open=True, wait=False)
        saved = cloud._read()
        saved["pending_pairing"]["started"]["expiresAt"] = int(time.time() * 1000) - 1_000
        cloud._write(saved)
        with patch.object(
            cloud, "_request", side_effect=cloud.CloudError("NETWORK_ERROR", "offline")
        ) as request, patch.object(cloud.time, "sleep") as sleep:
            with self.assertRaises(cloud.CloudError) as caught:
                cloud.onboard(no_open=True, wait=True)
        self.assertEqual(caught.exception.code, "RUNTIME_PAIRING_RECOVERY_UNVERIFIED")
        self.assertEqual(request.call_count, 1)
        self.assertEqual(request.call_args.args[0], "GET")
        sleep.assert_not_called()
        self.assertEqual(cloud._read()["pending_pairing"], saved["pending_pairing"])

    def test_claim_race_must_retry_original_candidate_without_new_owner_request(self):
        offered = self.started()
        with patch.object(cloud, "_request", return_value=(201, offered)):
            cloud.onboard(no_open=True, wait=False)
        original = cloud._read()["pending_pairing"]["claim_token"]
        installation = cloud._read()["installation_id"]
        requests = []

        def contested_get(method, path, **kwargs):
            self.assertEqual(method, "GET", "No new owner pairing may be issued")
            self.assertEqual(kwargs.get("runtime_candidate"), original)
            requests.append(method)
            if len(requests) == 1:
                raise cloud.CloudError(
                    "RUNTIME_PAIRING_RACE", "Concurrent claim committed remotely", 409
                )
            return 200, {
                "status": "paired",
                "candidateAccepted": True,
                "installationId": installation,
                "workspace": "owner-workspace",
                "tokenExpiresAt": int(time.time() * 1000) + 900_000,
            }

        with patch.object(cloud, "_request", side_effect=contested_get), patch.object(
            cloud.time, "sleep"
        ) as sleep, patch.object(cloud, "bindings", return_value={"workspace": "owner-workspace"}):
            result = cloud.onboard(no_open=True, wait=True)
        self.assertEqual(result["workspace"], "owner-workspace")
        self.assertEqual(requests, ["GET", "GET"])
        self.assertEqual(sleep.call_count, 1)
        self.assertEqual(cloud._read()["runtime_token"], original)
        self.assertNotIn("pending_pairing", cloud._read())

    def test_expired_claim_race_keeps_private_candidate_for_read_only_recovery(self):
        offered = self.started()
        with patch.object(cloud, "_request", return_value=(201, offered)):
            cloud.onboard(no_open=True, wait=False)
        original = cloud._read()
        candidate = original["pending_pairing"]["claim_token"]
        original["pending_pairing"]["started"]["expiresAt"] = int(time.time() * 1000) - 1_000
        cloud._write(original)

        with patch.object(cloud, "_request", side_effect=cloud.CloudError(
            "RUNTIME_PAIRING_RACE", "Competing claim in-flight", 409
        )) as request:
            with self.assertRaises(cloud.CloudError) as failure:
                cloud.onboard(no_open=True, wait=True)
        self.assertEqual(failure.exception.code, "RUNTIME_PAIRING_RECOVERY_UNVERIFIED")
        request.assert_called_once()
        self.assertEqual(request.call_args.args[0], "GET")
        self.assertEqual(cloud._read()["pending_pairing"]["claim_token"], candidate)
        self.assertNotIn("runtime_token", cloud._read())

    def test_parallel_claim_race_cannot_consume_private_recovery_before_ack(self):
        import threading
        offered = self.started()
        with patch.object(cloud, "_request", return_value=(201, offered)):
            cloud.onboard(no_open=True, wait=False)
        identity = cloud._read()["installation_id"]
        original = cloud._read()["pending_pairing"]["claim_token"]
        first_remote_committed = threading.Event()
        unblock_first_ack = threading.Event()
        poll_lock = threading.Lock()
        calls = []
        outcomes = {}

        def contested_get(method, path, **kwargs):
            self.assertEqual(method, "GET")
            self.assertEqual(kwargs.get("runtime_candidate"), original)
            with poll_lock:
                calls.append(method)
                index = len(calls)
            if index == 1:
                first_remote_committed.set()
                self.assertTrue(unblock_first_ack.wait(8), "First acknowledgement blocked")
            elif index == 2:
                raise cloud.CloudError(
                    "RUNTIME_PAIRING_RACE", "Other waiter committed remotely", 409
                )
            return 200, {
                "status": "paired",
                "candidateAccepted": True,
                "installationId": identity,
                "workspace": "owner-workspace",
                "tokenExpiresAt": int(time.time() * 1000) + 900_000,
            }

        def waiter(name):
            try:
                cloud.onboard(no_open=True, wait=True)
                outcomes[name] = "paired"
            except cloud.CloudError as exc:
                outcomes[name] = exc.code

        with patch.object(cloud, "_request", side_effect=contested_get), patch.object(
            cloud.time, "sleep"
        ), patch.object(cloud, "bindings", return_value={"workspace": "owner-workspace"}):
            first = threading.Thread(target=waiter, args=("first",), daemon=True)
            second = threading.Thread(target=waiter, args=("second",), daemon=True)
            first.start()
            self.assertTrue(first_remote_committed.wait(8))
            second.start()
            second.join(8)
            self.assertFalse(second.is_alive(), "Second waiter must finish promptly")
            unblock_first_ack.set()
            first.join(8)
            self.assertFalse(first.is_alive(), "First waiter must finish promptly")
        self.assertEqual(outcomes["second"], "paired", outcomes)
        self.assertIn(outcomes["first"], ("paired", "RUNTIME_PAIRING_CONTEXT_CHANGED"))
        self.assertEqual(cloud._read()["runtime_token"], original)
        self.assertNotIn("pending_pairing", cloud._read())

    def test_bounded_claim_race_retry_preserves_exact_provenance(self):
        offered = self.started()
        with patch.object(cloud, "_request", return_value=(201, offered)):
            cloud.onboard(no_open=True, wait=False)
        candidate = cloud._read()["pending_pairing"]["claim_token"]
        with patch.object(cloud, "_request", side_effect=cloud.CloudError(
            "RUNTIME_PAIRING_RACE", "Concurrent D1 claim raced", 409
        )) as requests, patch.object(cloud.time, "sleep") as sleep:
            with self.assertRaises(cloud.CloudError) as failure:
                cloud.onboard(no_open=True, wait=True)
        self.assertEqual(failure.exception.code, "RUNTIME_PAIRING_RECOVERY_UNVERIFIED")
        self.assertEqual(requests.call_count, 3, "Transient claim retries must be bounded")
        self.assertEqual(sleep.call_count, 2)
        self.assertEqual(cloud._read()["pending_pairing"]["claim_token"], candidate)
        self.assertNotIn("runtime_token", cloud._read())

    def test_proven_consumed_pairing_still_retires_invalid_candidate(self):
        offered = self.started()
        with patch.object(cloud, "_request", return_value=(201, offered)):
            cloud.onboard(no_open=True, wait=False)
        with patch.object(cloud, "_request", side_effect=cloud.CloudError(
            "RUNTIME_PAIRING_ALREADY_CONSUMED", "Claim no longer matches active installation", 409
        )):
            with self.assertRaises(cloud.CloudError) as failure:
                cloud.onboard(no_open=True, wait=True)
        self.assertEqual(failure.exception.code, "RUNTIME_PAIRING_ALREADY_CONSUMED")
        self.assertNotIn("pending_pairing", cloud._read())
        self.assertNotIn("runtime_token", cloud._read())

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
