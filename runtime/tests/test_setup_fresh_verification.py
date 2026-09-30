from __future__ import annotations

import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from ocpf_post import product_runtime
from ocpf_post.setup_engine import SetupEngine, SetupEngineError


READY_ID = "123456789"


def readiness(*, observed: str = READY_ID, write_ready: bool = True) -> dict:
    matched = observed == READY_ID
    blockers = []
    if not matched:
        blockers.append("provider.identity.mismatch")
    if not write_ready:
        blockers.append("provider.write_scope.missing")
    return {
        "schema_version": 1,
        "provider": "x",
        "expected_identity": READY_ID,
        "observed_identity": observed,
        "identity_match": "match" if matched else "mismatch",
        "write_scope_state": "granted" if write_ready else "missing",
        "ready_for_write_configuration": matched and write_ready,
        "blocking_reasons": blockers,
    }


class FreshVerificationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.config = self.root / "config"
        self.state = self.root / "state"
        self.workspace = self.root / "setup"
        self.env = patch.dict(
            os.environ,
            {
                "POSTSTEWARD_RUNTIME_CONFIG_DIR": str(self.config),
                "POSTSTEWARD_RUNTIME_STATE_DIR": str(self.state),
                "POSTSTEWARD_SETUP_STATE_DIR": str(self.workspace),
                "POSTSTEWARD_RELEASES_DIR": str(self.root / "releases"),
                "POSTSTEWARD_RUNTIME_LINEAGE": product_runtime.PRODUCT_LINEAGE,
            },
            clear=False,
        )
        self.env.start()
        product_runtime.apply_environment()

    def tearDown(self) -> None:
        self.env.stop()
        self.tmp.cleanup()

    def _fresh(self) -> SetupEngine:
        engine = SetupEngine(self.workspace)
        value = engine.start(
            "fresh",
            operator_label="Example Operator",
            timezone="UTC",
            pace="occasional",
        )
        self.assertEqual(value["session"]["stage"], "configuration_ready")
        return engine

    def _user_content(self) -> None:
        self.config.mkdir(parents=True, exist_ok=True)
        registry = {
            "schema_version": 1,
            "projects": {
                "example": {
                    "schema_version": 1,
                    "project": "example",
                    "label": "Example",
                    "campaign_prefixes": ["EX-"],
                    "accounts": {
                        "primary": {
                            "provider": "x",
                            "account_id": READY_ID,
                            "label": "Example X",
                        }
                    },
                    "default_accounts": {"x": "primary"},
                }
            },
        }
        (self.config / "runtime-projects.json").write_text(
            json.dumps(registry) + "\n",
            encoding="utf-8",
        )
        campaign = self.state / "runtime-campaigns" / "EX-001"
        campaign.mkdir(parents=True, exist_ok=True)
        (campaign / "manifest.json").write_text(
            json.dumps(
                {
                    "campaign": "EX-001",
                    "project": "example",
                    "title": "Example",
                    "status": "COPY-READY",
                    "providers": ["x"],
                    "destinations": {"x": "primary"},
                    "source": {"type": "owner_approved", "source_id": "fixture"},
                    "allocation": {"enabled": False},
                    "runtime_imported": True,
                    "payload_sha256": {},
                }
            )
            + "\n",
            encoding="utf-8",
        )

    def test_exact_provider_identity_and_user_content_advance_fresh_setup(self) -> None:
        engine = self._fresh()
        self._user_content()
        value = engine.verify_fresh(
            [readiness()],
            [{"provider": "x", "account_id": READY_ID}],
        )
        self.assertEqual(value["session"]["stage"], "verification_ready")
        self.assertFalse(value["publishing_authority"])
        self.assertEqual(value["next_actions"], ["preview_activation_gate"])
        event = value["completed_transitions"][-1]
        self.assertEqual(event["to_stage"], "verification_ready")

    def test_wrong_provider_identity_does_not_advance_revision(self) -> None:
        engine = self._fresh()
        self._user_content()
        before = engine.status()
        with self.assertRaises(SetupEngineError) as caught:
            engine.verify_fresh(
                [readiness(observed="999999")],
                [{"provider": "x", "account_id": READY_ID}],
            )
        self.assertEqual(caught.exception.code, "setup.fresh.provider_authority_blocked")
        after = engine.status()
        self.assertEqual(after["session"]["stage"], "configuration_ready")
        self.assertEqual(after["session"]["revision"], before["session"]["revision"])

    def test_missing_write_scope_does_not_advance(self) -> None:
        engine = self._fresh()
        self._user_content()
        with self.assertRaises(SetupEngineError) as caught:
            engine.verify_fresh(
                [readiness(write_ready=False)],
                [{"provider": "x", "account_id": READY_ID}],
            )
        self.assertEqual(caught.exception.code, "setup.fresh.provider_authority_blocked")
        self.assertEqual(engine.status()["session"]["stage"], "configuration_ready")

    def test_owner_packaged_defaults_cannot_satisfy_fresh_content_gate(self) -> None:
        engine = self._fresh()
        with self.assertRaises(SetupEngineError) as caught:
            engine.verify_fresh(
                [readiness()],
                [{"provider": "x", "account_id": READY_ID}],
            )
        self.assertEqual(caught.exception.code, "setup.fresh.project_configuration_required")
        self.assertEqual(engine.status()["session"]["stage"], "configuration_ready")

    def test_project_without_user_content_cannot_advance(self) -> None:
        engine = self._fresh()
        self._user_content()
        campaign_root = self.state / "runtime-campaigns"
        for path in campaign_root.rglob("*"):
            if path.is_file():
                path.unlink()
        for path in sorted(campaign_root.rglob("*"), reverse=True):
            if path.is_dir():
                path.rmdir()
        campaign_root.rmdir()
        with self.assertRaises(SetupEngineError) as caught:
            engine.verify_fresh(
                [readiness()],
                [{"provider": "x", "account_id": READY_ID}],
            )
        self.assertEqual(caught.exception.code, "setup.fresh.content_configuration_required")


if __name__ == "__main__":
    unittest.main()
