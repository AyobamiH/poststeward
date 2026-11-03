from __future__ import annotations

import copy
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from ocpf_post import automation_authority
from ocpf_post.poststeward_cloud import CloudError, heartbeat
from ocpf_post.product_runtime import apply_environment
from ocpf_post.providers.base import ProviderRejected
from ocpf_post.providers.poststeward_relay import PostStewardRelayProvider
from ocpf_post.service_runner import main as run_service


PIN_ENV = "POSTSTEWARD_UNATTENDED_FENCE"
NOW = 1800000000000
ROOT = Path(__file__).resolve().parents[1]


class UnattendedCloudFenceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.state = self.root / "state"
        self.env = patch.dict(os.environ, {
            "POSTSTEWARD_RUNTIME_ROOT": str(ROOT),
            "POSTSTEWARD_RUNTIME_RELEASE_SHA": "b" * 40,
            "POSTSTEWARD_RUNTIME_STATE_DIR": str(self.state),
            "POSTSTEWARD_RUNTIME_CONFIG_DIR": str(self.root / "config"),
            "POSTSTEWARD_SETUP_STATE_DIR": str(self.root / "setup"),
        })
        self.env.start()
        self.addCleanup(self.env.stop)
        apply_environment()
        self.fence = {
            "workspace": "ws_fixture", "installation_id": "33333333-3333-4333-8333-333333333333",
            "authority_generation": 7,
        }
        self.activate()
        self.pin = {"review_sha256": "a" * 64, "runtime_revision": "b" * 40, "cloud_executor": self.fence}
        os.environ[PIN_ENV] = json.dumps(self.pin)
        self.cloud = {
            "schemaVersion": 1, "workspace": self.fence["workspace"], "installationId": self.fence["installation_id"],
            "executor": {"executorMode": "local", "executorStatus": "active", "authorityGeneration": 7,
                         "activeInstallationId": self.fence["installation_id"], "leaseExpiresAt": NOW + 60000},
            "accounts": [{"provider": "x", "active": True, "alias": "fixture", "version": 2,
                          "identity": {"id": "account-1", "username": "fixture"}}],
        }
        self.calls = []
        self.provider_writes = []
        self.renewal_override = {}
        self.after_heartbeat = None
        self.after_account = None
        patches = [
            patch("ocpf_post.poststeward_cloud.bindings", side_effect=lambda: copy.deepcopy(self.cloud)),
            patch("ocpf_post.providers.poststeward_relay.bindings", side_effect=lambda: copy.deepcopy(self.cloud)),
            patch("ocpf_post.poststeward_cloud.runtime_token", return_value="fixture-token"),
            patch("ocpf_post.poststeward_cloud._request", side_effect=self.remote),
            patch("ocpf_post.poststeward_cloud.time.time", return_value=NOW / 1000),
        ]
        for item in patches:
            item.start()
            self.addCleanup(item.stop)

    def activate(self, *, reviewed=True):
        automation_authority.activate(
            root=self.state, operation_id="11111111-1111-4111-8111-111111111111",
            installation_id="22222222-2222-4222-8222-222222222222", authority_generation=1,
            review_sha256="a" * 64, runtime_revision="b" * 40,
            cloud_executor=self.fence if reviewed else None,
        )

    def remote(self, method, path, **kwargs):
        payload = copy.deepcopy(kwargs.get("payload") or {})
        self.calls.append((method, path, payload))
        executor = self.cloud["executor"]
        if path == "/api/runtime/heartbeat":
            if payload.get("authorityGeneration") != executor["authorityGeneration"]:
                raise CloudError("RUNTIME_EXECUTOR_FENCED", "generation changed", 409)
            executor["leaseExpiresAt"] = NOW + 60000
            result = {**copy.deepcopy(executor), **self.renewal_override}
            if self.after_heartbeat:
                self.after_heartbeat()
            return 200, result
        self.assertEqual(path, "/api/runtime/relay")
        if payload["action"] == "account":
            if self.after_account:
                self.after_account()
            return 200, {"account": {"alias": "fixture", "binding": 2,
                                    "identity": {"id": "account-1", "username": "fixture"}}}
        # Model the existing server write fence, immediately before provider I/O.
        if (payload.get("authorityGeneration") != executor["authorityGeneration"]
                or executor["leaseExpiresAt"] <= NOW):
            raise CloudError("RUNTIME_EXECUTOR_FENCED", "generation or lease changed", 409)
        self.provider_writes.append(payload)
        return 200, {"id": "published-1"}

    def test_stale_review_cannot_renew_or_reach_provider(self):
        self.cloud["executor"]["authorityGeneration"] = 8
        with self.assertRaises(ProviderRejected):
            PostStewardRelayProvider("x").publish("reviewed fixture")
        self.assertEqual(self.calls, [])
        self.assertEqual(self.provider_writes, [])

    def test_cloud_identity_or_workspace_drift_cannot_renew(self):
        for change in ({"workspace": "ws_other"}, {"installationId": "other"}):
            with self.subTest(change=change):
                original = dict(self.cloud)
                self.cloud.update(change)
                with self.assertRaises(ProviderRejected):
                    PostStewardRelayProvider("x").publish("reviewed fixture")
                self.assertEqual(self.calls, [])
                self.cloud = original

    def test_old_invocation_cannot_adopt_new_local_activation(self):
        self.cloud["executor"]["authorityGeneration"] = 8
        automation_authority.activate(
            root=self.state, operation_id="11111111-1111-4111-8111-111111111111",
            installation_id="22222222-2222-4222-8222-222222222222", authority_generation=1,
            review_sha256="c" * 64, runtime_revision="b" * 40,
            cloud_executor={**self.fence, "authority_generation": 8},
        )
        with self.assertRaises(ProviderRejected):
            PostStewardRelayProvider("x").publish("reviewed fixture")
        self.assertEqual(self.calls, [])

    def test_same_reviewed_generation_publishes_with_pinned_payload(self):
        result = PostStewardRelayProvider("x").publish("reviewed fixture")
        self.assertEqual(result["id"], "published-1")
        self.assertEqual(self.calls[0][1:], ("/api/runtime/heartbeat", {"authorityGeneration": 7}))
        self.assertEqual(self.provider_writes[0]["authorityGeneration"], 7)

    def test_expired_same_generation_lease_may_renew_before_effect(self):
        self.cloud["executor"]["leaseExpiresAt"] = NOW - 1
        self.assertEqual(PostStewardRelayProvider("x").publish("reviewed fixture")["id"], "published-1")
        self.assertEqual(self.provider_writes[0]["authorityGeneration"], 7)

    def test_invalid_renewed_fence_blocks_provider_io(self):
        for change in ({"leaseExpiresAt": NOW}, {"leaseExpiresAt": None}, {"leaseExpiresAt": True},
                       {"authorityGeneration": 8}, {"authorityGeneration": True},
                       {"activeInstallationId": "other"}, {"executorStatus": "inactive"}):
            with self.subTest(change=change):
                self.calls.clear()
                self.renewal_override = change
                with self.assertRaises(ProviderRejected):
                    PostStewardRelayProvider("x").publish("reviewed fixture")
                self.assertEqual([path for _, path, _ in self.calls], ["/api/runtime/heartbeat"])
                self.assertEqual(self.provider_writes, [])

    def test_handoff_after_renewal_uses_old_pin_and_is_fenced_before_provider_write(self):
        self.after_heartbeat = lambda: self.cloud["executor"].update(authorityGeneration=8)
        with self.assertRaises(ProviderRejected):
            PostStewardRelayProvider("x").publish("reviewed fixture")
        effects = [payload for _, path, payload in self.calls if path.endswith("/relay") and payload["action"] == "publish"]
        self.assertEqual([payload["authorityGeneration"] for payload in effects], [7])
        self.assertEqual(self.provider_writes, [])

    def test_local_deactivation_after_preflight_blocks_relay_write(self):
        self.after_account = lambda: automation_authority.deactivate(
            root=self.state, review_sha256="c" * 64, reason="fixture stop",
        )
        with self.assertRaises(ProviderRejected):
            PostStewardRelayProvider("x").publish("reviewed fixture")
        self.assertFalse(any(payload.get("action") == "publish" for _, _, payload in self.calls))
        self.assertEqual(self.provider_writes, [])

    def test_service_pin_survives_handoff_before_provider_preflight(self):
        def child(args, **kwargs):
            self.assertEqual(json.loads(kwargs["env"][PIN_ENV]), self.pin)
            with patch.dict(os.environ, kwargs["env"]):
                if args[-2:] == ["cloud", "heartbeat"]:
                    heartbeat()
                    self.cloud["executor"]["authorityGeneration"] = 8
                    return subprocess.CompletedProcess(args, 0)
                self.assertEqual(args[-1], "run-due")
                with self.assertRaises(ProviderRejected):
                    PostStewardRelayProvider("x").publish("reviewed fixture")
                return subprocess.CompletedProcess(args, 3)
        with patch("ocpf_post.service_runner.subprocess.run", side_effect=child) as run:
            self.assertEqual(run_service(["run-due"]), 3)
        self.assertEqual(run.call_count, 2)
        self.assertEqual([path for _, path, _ in self.calls], ["/api/runtime/heartbeat"])
        self.assertEqual(self.provider_writes, [])

    def test_unrecorded_local_marker_stops_service_before_child_or_cloud_io(self):
        self.activate(reviewed=False)
        with patch("ocpf_post.service_runner.subprocess.run") as run:
            self.assertEqual(run_service(["run-due"]), 3)
        run.assert_not_called()
        self.assertEqual(self.calls, [])

    def test_changed_or_malformed_pin_is_fenced_before_cloud_io(self):
        for pin in ("", "[]", "not-json", json.dumps({**self.pin, "review_sha256": "c" * 64})):
            with self.subTest(pin=pin), patch.dict(os.environ, {PIN_ENV: pin}):
                with self.assertRaises(ProviderRejected):
                    PostStewardRelayProvider("x").publish("reviewed fixture")
                self.assertEqual(self.calls, [])

    def test_shell_wrapper_carries_real_guard_context_to_heartbeat_and_work(self):
        runtime = self.root / "shell-runtime"
        scripts = runtime / "scripts"
        scripts.mkdir(parents=True)
        for name in ("run-unattended", "runtime-env"):
            (scripts / name).write_bytes((ROOT / "scripts" / name).read_bytes())
        observed = self.root / "shell-invocations.jsonl"
        executable = runtime / "poststeward"
        executable.write_text(
            "#!/usr/bin/env python3\nimport json,os,sys\n"
            + "with open(" + repr(str(observed)) + ", 'a') as handle:\n"
            + "    handle.write(json.dumps({'args':sys.argv[1:],'pin':json.loads(os.environ['" + PIN_ENV
            + "'])})+'\\n')\n",
            encoding="utf-8",
        )
        executable.chmod(0o700)
        env = {**os.environ, "PYTHONPATH": str(ROOT / "src")}
        env.pop(PIN_ENV, None)
        result = subprocess.run(
            ["sh", str(scripts / "run-unattended"), "run-due"], env=env,
            text=True, capture_output=True, timeout=20, check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        rows = [json.loads(line) for line in observed.read_text().splitlines()]
        self.assertEqual([row["args"] for row in rows], [["cloud", "heartbeat"], ["run-due"]])
        self.assertEqual([row["pin"] for row in rows], [self.pin, self.pin])

    def test_manual_account_read_does_not_require_local_activation_or_renew_lease(self):
        os.environ.pop(PIN_ENV)
        automation_authority.deactivate(root=self.state, review_sha256="c" * 64, reason="manual read")
        self.cloud["executor"].update(executorMode="hosted", authorityGeneration=8)
        self.assertEqual(PostStewardRelayProvider("x").account().account_id, "account-1")
        self.assertEqual([path for _, path, _ in self.calls], ["/api/runtime/relay"])
        self.assertEqual(self.provider_writes, [])


if __name__ == "__main__":
    unittest.main()
