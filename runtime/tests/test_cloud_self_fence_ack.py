"""Cloud self-fence acknowledgement must be verified before reporting success."""
from __future__ import annotations

import copy
import unittest
from unittest.mock import patch

from ocpf_post.poststeward_cloud import CloudError, deactivate_executor_fence


INSTALLATION = "33333333-3333-4333-8333-333333333333"


class CloudSelfFenceAcknowledgementTests(unittest.TestCase):
    def setUp(self) -> None:
        self.before = {
            "executor": {
                "executorMode": "local",
                "executorStatus": "active",
                "activeInstallationId": INSTALLATION,
                "authorityGeneration": 4,
            }
        }
        self.confirmed = {
            "executorMode": "local",
            "executorStatus": "inactive",
            "activeInstallationId": INSTALLATION,
            "authorityGeneration": 5,
        }
        self.bindings = patch(
            "ocpf_post.poststeward_cloud.bindings", return_value=self.before
        )
        self.identity = patch(
            "ocpf_post.poststeward_cloud.installation_identity",
            return_value={"installation_id": INSTALLATION},
        )
        self.token = patch(
            "ocpf_post.poststeward_cloud.runtime_token",
            return_value="fixture-token",
        )
        for handle in (self.bindings, self.identity, self.token):
            handle.start()
            self.addCleanup(handle.stop)

    def test_verified_inactive_successor_generation_is_accepted(self) -> None:
        with patch(
            "ocpf_post.poststeward_cloud._request", return_value=(200, self.confirmed)
        ) as request:
            result = deactivate_executor_fence("owner approved stop")
        self.assertEqual(result, self.confirmed)
        request.assert_called_once_with(
            "POST", "/api/runtime/executor/fence", token="fixture-token",
            payload={"authorityGeneration": 4, "reason": "owner approved stop"},
        )

    def test_unverified_cloud_response_never_counts_as_success(self) -> None:
        for name, changes, response_code in (
            ("still_active", {"executorStatus": "active"}, 200),
            ("hosted", {"executorMode": "hosted"}, 200),
            ("other_machine", {"activeInstallationId": "elsewhere"}, 200),
            ("same_generation", {"authorityGeneration": 4}, 200),
            ("earlier_generation", {"authorityGeneration": 3}, 200),
            ("boolean_generation", {"authorityGeneration": True}, 200),
            ("missing_state", {"executorStatus": None}, 200),
            ("accepted_not_done", {}, 202),
        ):
            with self.subTest(name=name):
                reported = {**copy.deepcopy(self.confirmed), **changes}
                with patch(
                    "ocpf_post.poststeward_cloud._request",
                    return_value=(response_code, reported),
                ) as request:
                    with self.assertRaises(CloudError) as caught:
                        deactivate_executor_fence("fixture stop")
                    self.assertEqual(
                        caught.exception.code, "RUNTIME_EXECUTOR_FENCE_UNVERIFIED"
                    )
                    request.assert_called_once()

    def test_remote_failure_is_not_retried(self) -> None:
        with patch(
            "ocpf_post.poststeward_cloud._request",
            side_effect=CloudError("NETWORK_ERROR", "offline"),
        ) as request:
            with self.assertRaises(CloudError) as caught:
                deactivate_executor_fence("fixture stop")
        self.assertEqual(caught.exception.code, "NETWORK_ERROR")
        request.assert_called_once()


if __name__ == "__main__":
    unittest.main()
