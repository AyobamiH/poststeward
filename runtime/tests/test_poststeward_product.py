from __future__ import annotations

from contextlib import contextmanager
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from ocpf_post import automation_authority
from ocpf_post.poststeward_product import (
    ProductLifecycleError,
    doctor,
    runtime_status,
    update,
)


ROOT = Path(__file__).resolve().parents[1]


@contextmanager
def product_env(root: Path):
    values = {
        "POSTSTEWARD_RUNTIME_ROOT": str(ROOT),
        "POSTSTEWARD_RUNTIME_CONFIG_DIR": str(root / "config" / "runtime"),
        "POSTSTEWARD_RUNTIME_STATE_DIR": str(root / "state" / "runtime"),
        "POSTSTEWARD_SETUP_STATE_DIR": str(root / "state" / "setup"),
        "POSTSTEWARD_RELEASES_DIR": str(root / "data" / "releases"),
        "XDG_CONFIG_HOME": str(root / "config"),
        "XDG_STATE_HOME": str(root / "state"),
        "XDG_DATA_HOME": str(root / "data"),
        "POSTSTEWARD_RUNTIME_LINEAGE": "poststeward-local-runtime-v1",
        "POST_ONCE_PRODUCT_LINEAGE": "poststeward-local-runtime-v1",
        "POSTSTEWARD_REQUIRE_CLOUD_FENCE": "1",
    }
    before = {key: os.environ.get(key) for key in values}
    os.environ.update(values)
    try:
        yield
    finally:
        for key, value in before.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


class PostStewardProductLifecycleTests(unittest.TestCase):
    def test_status_and_doctor_are_read_only_without_pairing(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            with product_env(root):
                value = runtime_status()
                self.assertEqual(value["product"], "poststeward")
                self.assertEqual(
                    value["automation_authority"]["status"],
                    "inactive",
                )
                self.assertEqual(
                    value["cloud"]["code"],
                    "RUNTIME_PAIRING_REQUIRED",
                )
                result = doctor()
                self.assertEqual(result["status"], "ATTENTION")
                self.assertTrue(
                    any(
                        row["code"] == "cloud.unavailable"
                        for row in result["findings"]
                    )
                )
                self.assertFalse((root / "state" / "runtime" / "publish-receipts.jsonl").exists())

    def test_update_refuses_active_local_authority_before_network(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            with product_env(root):
                state = root / "state" / "runtime"
                automation_authority.activate(
                    root=state,
                    operation_id="11111111-1111-4111-8111-111111111111",
                    installation_id="22222222-2222-4222-8222-222222222222",
                    authority_generation=1,
                    review_sha256="a" * 64,
                    runtime_revision="b" * 40,
                )
                with self.assertRaises(ProductLifecycleError) as caught:
                    update("stable", dry_run=True)
                self.assertEqual(caught.exception.code, "UPDATE_ACTIVE_RUNTIME")

    def diagnosis(self, *, cloud_change=None, executor_change=None, active=True, release="b" * 40, services=None):
        installation = "22222222-2222-4222-8222-222222222222"
        cloud = {
            "schemaVersion": 1, "release": "c" * 40, "installationId": installation,
            "accounts": [{"alias": "fixture"}],
            "executor": {"executorMode": "local", "executorStatus": "active",
                         "activeInstallationId": installation, "authorityGeneration": 1,
                         "leaseExpiresAt": 2000000000000},
        }
        cloud.update(cloud_change or {})
        cloud["executor"].update(executor_change or {})
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            with product_env(root), patch.dict(os.environ, {"POSTSTEWARD_RUNTIME_RELEASE_SHA": release}):
                state = root / "state" / "runtime"
                if active:
                    automation_authority.activate(root=state,
                        operation_id="11111111-1111-4111-8111-111111111111",
                        installation_id=installation, authority_generation=1,
                        review_sha256="a" * 64, runtime_revision="b" * 40)
                marker = automation_authority.path(state)
                before = marker.read_bytes() if marker.exists() else None
                with patch("ocpf_post.poststeward_product.bindings", return_value=cloud), \
                     patch("ocpf_post.poststeward_product._setup_status", return_value=None), \
                     patch("ocpf_post.poststeward_product._services", return_value=services if services is not None else {"status": "observed", "all_enabled": True, "all_active": True}), \
                     patch("ocpf_post.poststeward_product.time.time", return_value=1800000000):
                    result = doctor()
                self.assertEqual(marker.read_bytes() if marker.exists() else None, before)
                return result

    def test_live_matching_generation_is_ready_despite_different_cloud_release(self):
        result = self.diagnosis()
        self.assertEqual(result["status"], "READY")
        compatibility = result["runtime"]["compatibility"]
        self.assertEqual(compatibility["status"], "compatible")
        self.assertEqual(compatibility["local_release_sha"], "b" * 40)
        self.assertEqual(compatibility["cloud_release_sha"], "c" * 40)
        self.assertTrue(compatibility["execution_authority"]["lease_live"])

    def test_stale_missing_or_boolean_generation_cannot_report_ready(self):
        for generation in (2, None, True, 0):
            with self.subTest(generation=generation):
                result = self.diagnosis(executor_change={"authorityGeneration": generation})
                self.assertEqual(result["status"], "BLOCKED")
                self.assertIn("authority.generation_mismatch", [r["code"] for r in result["findings"]])

    def test_expired_missing_or_malformed_lease_cannot_report_ready(self):
        for lease in (1799999999999, 1800000000000, None, True, "2000000000000"):
            with self.subTest(lease=lease):
                result = self.diagnosis(executor_change={"leaseExpiresAt": lease})
                self.assertEqual(result["status"], "BLOCKED")
                self.assertIn("authority.lease_not_live", [r["code"] for r in result["findings"]])

    def test_changed_installation_or_executor_cannot_report_ready(self):
        for change in ({"activeInstallationId": "another-machine"}, {"executorMode": "hosted"}, {"executorStatus": "inactive"}):
            with self.subTest(change=change):
                result = self.diagnosis(executor_change=change)
                self.assertEqual(result["status"], "BLOCKED")
                self.assertIn("authority.cloud_local_mismatch", [r["code"] for r in result["findings"]])
        result = self.diagnosis(cloud_change={"installationId": "another-machine"})
        self.assertEqual(result["status"], "BLOCKED")

    def test_inactive_other_machine_does_not_request_local_activation(self):
        result = self.diagnosis(active=False, executor_change={"activeInstallationId": "another-machine"})
        self.assertNotIn("authority.cloud_active_local_marker_inactive", [r["code"] for r in result["findings"]])
        target = self.diagnosis(active=False)
        self.assertEqual(target["status"], "ATTENTION")
        self.assertIn("authority.cloud_active_local_marker_inactive", [r["code"] for r in target["findings"]])

    def test_unsupported_or_missing_binding_format_is_visible(self):
        for schema in (2, None, True):
            with self.subTest(schema=schema):
                result = self.diagnosis(cloud_change={"schemaVersion": schema})
                self.assertEqual(result["status"], "BLOCKED")
                self.assertIn("runtime.bindings_schema_unsupported", [r["code"] for r in result["findings"]])

    def test_different_local_release_requires_fresh_review(self):
        result = self.diagnosis(release="d" * 40)
        self.assertEqual(result["status"], "BLOCKED")
        self.assertIn("runtime.activation_release_mismatch", [r["code"] for r in result["findings"]])

    def test_unobservable_active_services_do_not_report_ready(self):
        result = self.diagnosis(services={"status": "unavailable"})
        self.assertEqual(result["status"], "ATTENTION")
        self.assertIn("automation.services_unverified", [r["code"] for r in result["findings"]])


if __name__ == "__main__":
    unittest.main()
