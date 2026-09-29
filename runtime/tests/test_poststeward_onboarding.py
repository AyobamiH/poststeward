from __future__ import annotations

import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from ocpf_post.poststeward_onboarding import apply, plan
from ocpf_post.product_runtime import PRODUCT_LINEAGE
from ocpf_post.setup_engine import SetupEngine


def cloud_accounts():
    return [
        {
            "alias": "brand_x",
            "provider": "x",
            "identity": {"id": "101", "username": "brandx"},
            "version": 1,
            "active": True,
            "capabilities": {"oauth": True, "refresh": True, "readback": True},
        },
        {
            "alias": "brand_threads",
            "provider": "threads",
            "identity": {"id": "202", "username": "brandthreads"},
            "version": 2,
            "active": True,
            "capabilities": {"oauth": True, "refresh": True, "readback": True},
        },
        {
            "alias": "brand_linkedin",
            "provider": "linkedin",
            "identity": {"id": "urn:li:organization:303", "username": "Brand Page"},
            "version": 3,
            "active": True,
            "capabilities": {"oauth": True, "refresh": True, "readback": True},
        },
    ]


class PostStewardOnboardingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.env = patch.dict(
            os.environ,
            {
                "HOME": str(self.root / "home"),
                "XDG_CONFIG_HOME": str(self.root / "config-home"),
                "XDG_STATE_HOME": str(self.root / "state-home"),
                "POSTSTEWARD_RUNTIME_LINEAGE": PRODUCT_LINEAGE,
                "POSTSTEWARD_RUNTIME_CONFIG_DIR": str(self.root / "config"),
                "POSTSTEWARD_RUNTIME_STATE_DIR": str(self.root / "state"),
                "POSTSTEWARD_SETUP_STATE_DIR": str(self.root / "setup"),
            },
            clear=False,
        )
        self.env.start()
        self.addCleanup(self.env.stop)
        from ocpf_post.product_runtime import apply_environment

        apply_environment()

    def mocks(self):
        bindings_patch = patch(
            "ocpf_post.poststeward_onboarding.bindings",
            return_value={
                "workspace": "workspace-1",
                "installationId": "11111111-1111-4111-8111-111111111111",
                "accounts": cloud_accounts(),
            },
        )

        def relay_account(payload):
            account_id = payload["accountId"]
            return {
                "schemaVersion": 1,
                "account": {
                    "identity": {"id": account_id, "username": "observed"},
                    "binding": 1,
                },
            }

        relay_patch = patch("ocpf_post.poststeward_onboarding.relay", side_effect=relay_account)
        return bindings_patch, relay_patch

    def kwargs(self):
        return {
            "project_id": "brand",
            "label": "Brand",
            "campaign_text": "A reviewed PostSteward beta update.",
            "timezone": "Europe/London",
            "pace": "regular",
            "machine_label": "Beta machine",
        }

    def test_preview_binds_all_three_cloud_providers_without_local_credentials(self):
        one, two = self.mocks()
        with one, two:
            value = plan(**self.kwargs())
        self.assertEqual(value["status"], "preview")
        self.assertEqual(
            [row["provider"] for row in value["selected_accounts"]],
            ["x", "threads", "linkedin"],
        )
        self.assertFalse(value["campaign_allocation_enabled"])
        self.assertFalse(value["provider_consequence_attempted"])
        self.assertTrue(all(row["credential_origin"] == "poststeward_cloud" for row in value["provider_readiness"]))
        self.assertFalse((self.root / "config" / "runtime-projects.json").exists())

    def test_apply_imports_manual_campaign_and_reaches_verification_ready(self):
        one, two = self.mocks()
        with one, two:
            preview = plan(**self.kwargs())
            value = apply(expected_sha256=preview["review_sha256"], **self.kwargs())
        self.assertEqual(value["status"], "verification_ready")
        self.assertEqual(value["setup"]["session"]["stage"], "verification_ready")
        self.assertFalse(value["publishing_authority"])
        self.assertFalse(value["automation_enabled"])
        manifest = json.loads(
            (self.root / "state" / "runtime-campaigns" / "BRAND-001" / "manifest.json").read_text()
        )
        self.assertFalse(manifest["allocation"]["enabled"])
        project = json.loads((self.root / "config" / "runtime-projects.json").read_text())
        providers = {
            account["provider"]
            for account in project["projects"]["brand"]["accounts"].values()
        }
        self.assertEqual(providers, {"x", "threads", "linkedin"})

    def test_changed_cloud_binding_changes_review_and_blocks_apply(self):
        one, two = self.mocks()
        with one, two:
            preview = plan(**self.kwargs())
        changed = cloud_accounts()
        changed[0] = {
            **changed[0],
            "identity": {"id": "999", "username": "different"},
            "version": 2,
        }
        with patch(
            "ocpf_post.poststeward_onboarding.bindings",
            return_value={"workspace": "workspace-1", "installationId": "id", "accounts": changed},
        ), patch(
            "ocpf_post.poststeward_onboarding.relay",
            side_effect=lambda payload: {
                "account": {"identity": {"id": payload["accountId"]}, "binding": 2}
            },
        ):
            with self.assertRaisesRegex(Exception, "review changed"):
                apply(expected_sha256=preview["review_sha256"], **self.kwargs())

    def test_multiple_accounts_require_explicit_alias(self):
        rows = cloud_accounts() + [
            {
                "alias": "second_x",
                "provider": "x",
                "identity": {"id": "404", "username": "second"},
                "version": 1,
                "active": True,
                "capabilities": {"oauth": True},
            }
        ]
        with patch(
            "ocpf_post.poststeward_onboarding.bindings",
            return_value={"workspace": "workspace-1", "installationId": "id", "accounts": rows},
        ):
            with self.assertRaisesRegex(Exception, "Multiple x destinations"):
                plan(**self.kwargs())


if __name__ == "__main__":
    unittest.main()
