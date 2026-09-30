from __future__ import annotations

from contextlib import contextmanager
import os
from pathlib import Path
import tempfile
import unittest

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


if __name__ == "__main__":
    unittest.main()
