import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from ocpf_post.poststeward_bridge import run_once
from ocpf_post.poststeward_cloud import CloudError
from ocpf_post.setup_activation import ActivationManager


class BridgeTests(unittest.TestCase):
    def test_lost_acknowledgement_retries_only_the_result(self):
        with tempfile.TemporaryDirectory() as root:
            command = {"commandId": "command-1", "installationId": "machine-1", "authorityGeneration": 3,
                       "operation": "runtime_inspect", "input": {"view": "status"}, "expiresAt": int(time.time()*1000)+60000}
            completions = []

            def request(method, path, **kwargs):
                if path.endswith("/claim"):
                    return 200, {"command": command}
                if path.endswith("/authorize"):
                    return 200, {"authorized": True}
                completions.append(kwargs["payload"])
                if len(completions) == 1:
                    raise CloudError("NETWORK_ERROR", "lost acknowledgement")
                return 200, {"status": "completed"}

            with patch("ocpf_post.poststeward_bridge.state_dir", return_value=Path(root)), \
                    patch("ocpf_post.poststeward_bridge.heartbeat", return_value={"authorityGeneration":3,"activeInstallationId":"machine-1"}), \
                    patch("ocpf_post.poststeward_bridge.installation_identity", return_value={"installation_id":"machine-1"}), \
                    patch("ocpf_post.poststeward_bridge.runtime_token", return_value="test"), \
                    patch("ocpf_post.poststeward_bridge._request", side_effect=request), \
                    patch("ocpf_post.poststeward_bridge._execute", return_value={"schedules":[]}) as execute:
                with self.assertRaises(CloudError):
                    run_once()
                self.assertEqual(run_once()["status"], "completed")
                execute.assert_called_once()
                self.assertEqual(completions[0], completions[1])
            self.assertEqual((Path(root)/"remote-commands.json").stat().st_mode & 0o777, 0o600)

    def test_interrupted_intent_is_reported_unknown_without_redispatch(self):
        with tempfile.TemporaryDirectory() as root:
            path=Path(root)/"remote-commands.json"
            path.write_text(json.dumps({"schema_version":1,"commands":{"command-1":{"command_id":"command-1","generation":3,"status":"started"}}}))
            with patch("ocpf_post.poststeward_bridge.state_dir",return_value=Path(root)), \
                    patch("ocpf_post.poststeward_bridge.heartbeat",return_value={"authorityGeneration":3,"activeInstallationId":"machine-1"}), \
                    patch("ocpf_post.poststeward_bridge.installation_identity",return_value={"installation_id":"machine-1"}), \
                    patch("ocpf_post.poststeward_bridge.runtime_token",return_value="test"), \
                    patch("ocpf_post.poststeward_bridge._request",return_value=(200,{"status":"failed"})) as request, \
                    patch("ocpf_post.poststeward_bridge._execute") as execute:
                run_once()
                execute.assert_not_called()
                self.assertEqual(request.call_args.kwargs["payload"]["result"]["error"]["code"],"RUNTIME_COMMAND_OUTCOME_UNKNOWN")

    def test_other_installation_is_refused_before_polling(self):
        with patch("ocpf_post.poststeward_bridge.heartbeat",return_value={"authorityGeneration":3,"activeInstallationId":"other"}), \
                patch("ocpf_post.poststeward_bridge.installation_identity",return_value={"installation_id":"machine-1"}), \
                patch("ocpf_post.poststeward_bridge._request") as request:
            with self.assertRaises(CloudError):
                run_once()
            request.assert_not_called()

    def test_recovery_review_binds_verified_files_and_ignores_later_activation(self):
        with tempfile.TemporaryDirectory() as root:
            review=Path(root)/"review.json"
            review.write_text('{"schema_version":1,"reconciled":true}')
            manager=object.__new__(ActivationManager)
            session={"session_id":"session-1","mode":"recover"}
            events=[{"to_stage":"recovery_review_ready","evidence":{"review":str(review)}}]
            with patch.object(manager,"_session_context",return_value=(session,{"operation_id":"op-1"},{},events)):
                first=manager.cloud_recovery_review()
                events.append({"to_stage":"active","evidence":{"generation":9}})
                self.assertEqual(manager.cloud_recovery_review(),first)
                review.write_text('{"schema_version":1,"reconciled":false}')
                self.assertNotEqual(manager.cloud_recovery_review()["recoveryReviewSha256"],first["recoveryReviewSha256"])


if __name__ == "__main__":
    unittest.main()
