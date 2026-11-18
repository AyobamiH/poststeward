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
                "provider": payload["provider"],
                "capabilities": {"oauth": True, "refresh": True, "readback": True},
                "account": {
                    "identity": {"id": account_id, "username": "observed"},
                    "alias": payload["accountAlias"],
                    "binding": payload["bindingVersion"],
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
                "schemaVersion": 1, "provider": payload["provider"],
                "capabilities": {"oauth": True},
                "account": {"identity": {"id": payload["accountId"]}, "binding": payload["bindingVersion"], "alias": payload["accountAlias"]}
            },
        ):
            with self.assertRaisesRegex(Exception, "review changed"):
                apply(expected_sha256=preview["review_sha256"], **self.kwargs())

    def test_reviewed_duplicate_identity_routes_runtime_reads_to_one_binding(self):
        from ocpf_post.providers.poststeward_relay import PostStewardRelayProvider
        from ocpf_post.providers.base import ProviderRejected

        rows = cloud_accounts()
        threads = rows[1]
        duplicate = dict(threads, alias="other_threads")
        bindings = {"workspace":"workspace-1", "installationId":"id", "accounts":[threads, duplicate]}
        with patch("ocpf_post.poststeward_onboarding.bindings", return_value=bindings), patch(
            "ocpf_post.poststeward_onboarding.relay",
            return_value={"schemaVersion":1, "provider":"threads", "capabilities":{"oauth":True}, "account":{"identity":threads["identity"], "alias":threads["alias"], "binding":threads["version"]}},
        ):
            args = dict(self.kwargs(), threads_alias=threads["alias"])
            preview = plan(**args)
            apply(expected_sha256=preview["review_sha256"], **args)
        adapter = PostStewardRelayProvider("threads", account_id="202")
        with patch("ocpf_post.providers.poststeward_relay.bindings", return_value=bindings), patch(
            "ocpf_post.providers.poststeward_relay.relay",
            return_value={"account":{"identity":threads["identity"], "alias":threads["alias"], "binding":2}},
        ) as relay:
            self.assertEqual(adapter.account().account_id, "202")
            self.assertEqual(relay.call_args.args[0]["accountAlias"], threads["alias"])
            self.assertEqual(relay.call_args.args[0]["bindingVersion"], 2)
            threads["version"] = 3
            with self.assertRaises(ProviderRejected):
                PostStewardRelayProvider("threads", account_id="202").account()
            self.assertEqual(relay.call_count, 1)

    def test_relay_cannot_substitute_alias_or_binding(self):
        rows = cloud_accounts()
        with patch("ocpf_post.poststeward_onboarding.bindings", return_value={"accounts":rows, "workspace":"workspace-1","installationId":"id"}), patch(
            "ocpf_post.poststeward_onboarding.relay",
            side_effect=lambda payload: {"schemaVersion":1, "provider":payload["provider"], "capabilities":{"oauth":True}, "account":{"identity":{"id":payload["accountId"]}, "alias":"different", "binding":payload["bindingVersion"]}},
        ):
            with self.assertRaisesRegex(Exception, "provider.binding.mismatch"):
                plan(**self.kwargs())

    def test_plan_refuses_hosted_oauth_revoked_after_bindings_snapshot(self):
        one, _ = self.mocks()

        def stale_capability(payload):
            return {
                "schemaVersion": 1,
                "provider": payload["provider"],
                "capabilities": {"oauth": False},
                "account": {
                    "identity": {"id": payload["accountId"]},
                    "alias": payload["accountAlias"],
                    "binding": payload["bindingVersion"],
                },
            }

        with one, patch(
            "ocpf_post.poststeward_onboarding.relay",
            side_effect=stale_capability,
        ):
            with self.assertRaisesRegex(
                Exception, "provider.oauth.hosted_connection_required"
            ):
                plan(**self.kwargs())
        self.assertFalse((self.root / "config" / "runtime-projects.json").exists())

    def test_plan_refuses_cross_provider_identity_substitution(self):
        one, _ = self.mocks()

        def wrong_provider(payload):
            return {
                "schemaVersion": 1,
                "provider": "x" if payload["provider"] == "threads" else "threads",
                "capabilities": {"oauth": True},
                "account": {
                    "identity": {"id": payload["accountId"]},
                    "alias": payload["accountAlias"],
                    "binding": payload["bindingVersion"],
                },
            }

        with one, patch(
            "ocpf_post.poststeward_onboarding.relay",
            side_effect=wrong_provider,
        ):
            with self.assertRaisesRegex(
                Exception, "provider.binding.provider_mismatch"
            ):
                plan(**self.kwargs())
        self.assertFalse((self.root / "config" / "runtime-projects.json").exists())

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
