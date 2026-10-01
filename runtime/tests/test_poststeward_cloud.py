from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ocpf_post.poststeward_cloud import (
    CloudError,
    activation_executor_proof,
)


class CloudExecutorProofTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.env = patch.dict(
            os.environ,
            {
                "XDG_CONFIG_HOME": str(self.root / "config"),
                "POSTSTEWARD_ORIGIN": "https://poststeward.example",
            },
            clear=False,
        )
        self.env.start()
        self.addCleanup(self.env.stop)

    def test_expired_pairing_can_start_fresh_owner_review_without_reusing_token(self) -> None:
        from ocpf_post.poststeward_cloud import onboard
        started={"pairingId":"new-pair","pollToken":"test-poll-token","verificationUrl":"https://poststeward.example/app", "userCode":"TEST-CODE","expiresAt":9999999999999}
        with patch("ocpf_post.poststeward_cloud.installation_identity",return_value={"runtime_token":"expired-token","installation_id":"installation","label":"test","platform":"test"}), patch("ocpf_post.poststeward_cloud.bindings",side_effect=CloudError("RUNTIME_PAIRING_EXPIRED","expired",3)), patch("ocpf_post.poststeward_cloud._request",return_value=(200,started)) as request:
            self.assertEqual(onboard(no_open=True,wait=False),started)
            self.assertNotIn("token",request.call_args.kwargs)

    def test_activation_proof_requires_exact_paired_installation_and_live_generation(self) -> None:
        with patch(
            "ocpf_post.poststeward_cloud.installation_identity",
            return_value={"installation_id": "11111111-1111-4111-8111-111111111111"},
        ), patch(
            "ocpf_post.poststeward_cloud.bindings",
            return_value={
                "workspace": "workspace-1",
                "installationId": "11111111-1111-4111-8111-111111111111",
                "executor": {
                    "executorMode": "local",
                    "activeInstallationId": "11111111-1111-4111-8111-111111111111",
                    "authorityGeneration": 7,
                },
            },
        ), patch(
            "ocpf_post.poststeward_cloud.heartbeat",
            return_value={"authorityGeneration": 7, "leaseExpiresAt": 9999999999999},
        ):
            value = activation_executor_proof()
        self.assertEqual(value["workspace"], "workspace-1")
        self.assertEqual(value["authority_generation"], 7)
        self.assertEqual(value["executor_mode"], "local")
        self.assertTrue(value["lease_proven_live"])
        self.assertNotIn("lease_expires_at", value)

    def test_hosted_executor_cannot_activate_local_runtime(self) -> None:
        with patch(
            "ocpf_post.poststeward_cloud.installation_identity",
            return_value={"installation_id": "11111111-1111-4111-8111-111111111111"},
        ), patch(
            "ocpf_post.poststeward_cloud.bindings",
            return_value={
                "workspace": "workspace-1",
                "executor": {
                    "executorMode": "hosted",
                    "activeInstallationId": None,
                    "authorityGeneration": 8,
                },
            },
        ):
            with self.assertRaises(CloudError) as caught:
                activation_executor_proof()
        self.assertEqual(caught.exception.code, "RUNTIME_EXECUTOR_NOT_LOCAL")

    def test_different_installation_is_fenced_before_heartbeat(self) -> None:
        with patch(
            "ocpf_post.poststeward_cloud.installation_identity",
            return_value={"installation_id": "11111111-1111-4111-8111-111111111111"},
        ), patch(
            "ocpf_post.poststeward_cloud.bindings",
            return_value={
                "workspace": "workspace-1",
                "executor": {
                    "executorMode": "local",
                    "activeInstallationId": "22222222-2222-4222-8222-222222222222",
                    "authorityGeneration": 9,
                },
            },
        ), patch("ocpf_post.poststeward_cloud.heartbeat") as heartbeat:
            with self.assertRaises(CloudError) as caught:
                activation_executor_proof()
        self.assertEqual(caught.exception.code, "RUNTIME_EXECUTOR_FENCED")
        heartbeat.assert_not_called()


if __name__ == "__main__":
    unittest.main()
