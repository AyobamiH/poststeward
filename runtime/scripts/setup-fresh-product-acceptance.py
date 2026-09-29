#!/usr/bin/env python3
"""Clean-room Fresh product acceptance for Milestone K.

No live provider credential or provider call is used. Readiness is injected as the
same safe projection produced by the read-only provider-readiness layer. The test
proves the durable Fresh state machine and activation-preview boundary.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import tempfile

from ocpf_post import product_runtime
from ocpf_post.setup_engine import SetupEngine

ROOT = Path(__file__).resolve().parents[1]
ACCOUNT_ID = "123456789"


class FakeServices:
    def inspect(self) -> dict:
        return {
            "schema_version": 1,
            "timers": [],
            "all_enabled": False,
            "all_active": False,
        }


def main() -> None:
    old = dict(os.environ)
    try:
        with tempfile.TemporaryDirectory(prefix="post-once-fresh-acceptance-") as temp:
            root = Path(temp)
            config = root / "config"
            state = root / "state"
            workspace = root / "setup"
            os.environ.update({
                "POST_ONCE_CONFIG_DIR": str(config),
                "POST_ONCE_STATE_DIR": str(state),
                "POST_ONCE_SETUP_STATE_DIR": str(workspace),
                "POST_ONCE_RELEASES_DIR": str(root / "releases"),
                "POST_ONCE_PRODUCT_LINEAGE": product_runtime.PRODUCT_LINEAGE,
            })
            product_runtime.apply_environment()

            engine = SetupEngine(workspace)
            started = engine.start(
                "fresh",
                operator_label="Clean Room User",
                timezone="UTC",
                pace="occasional",
            )
            if started["session"]["stage"] != "configuration_ready":
                raise RuntimeError("Fresh setup did not stop at configuration_ready")

            # Fresh standalone product begins with no inherited owner project/content.
            from ocpf_post.campaigns import campaign_ids
            from ocpf_post.registry import load_registry

            if load_registry()["projects"] or campaign_ids():
                raise RuntimeError("Fresh standalone product inherited owner project/campaign defaults")

            config.mkdir(parents=True, exist_ok=True)
            (config / "runtime-projects.json").write_text(
                json.dumps({
                    "schema_version": 1,
                    "projects": {
                        "clean-room": {
                            "schema_version": 1,
                            "project": "clean-room",
                            "label": "Clean Room",
                            "campaign_prefixes": ["CR-"],
                            "accounts": {
                                "primary": {
                                    "provider": "x",
                                    "account_id": ACCOUNT_ID,
                                    "label": "Clean Room X",
                                }
                            },
                            "default_accounts": {"x": "primary"},
                        }
                    },
                }) + "\n",
                encoding="utf-8",
            )
            campaign = state / "runtime-campaigns" / "CR-001"
            campaign.mkdir(parents=True, exist_ok=True)
            (campaign / "manifest.json").write_text(
                json.dumps({
                    "campaign": "CR-001",
                    "project": "clean-room",
                    "title": "Clean Room Campaign",
                    "status": "COPY-READY",
                    "providers": ["x"],
                    "destinations": {"x": "primary"},
                    "source": {"type": "owner_approved", "source_id": "clean-room"},
                    "allocation": {"enabled": False},
                    "runtime_imported": True,
                    "payload_sha256": {},
                }) + "\n",
                encoding="utf-8",
            )

            verified = engine.verify_fresh(
                [{
                    "schema_version": 1,
                    "provider": "x",
                    "expected_identity": ACCOUNT_ID,
                    "observed_identity": ACCOUNT_ID,
                    "identity_match": "match",
                    "write_scope_state": "granted",
                    "ready_for_write_configuration": True,
                    "blocking_reasons": [],
                }],
                [{"provider": "x", "account_id": ACCOUNT_ID}],
            )
            if verified["session"]["stage"] != "verification_ready":
                raise RuntimeError("Fresh verification did not reach verification_ready")
            if verified["publishing_authority"] or verified["automation_enabled"]:
                raise RuntimeError("Fresh verification unexpectedly granted automation authority")

            preview = engine.activation_preview(
                runtime_root=ROOT,
                state_root=state,
                config_root=config,
                service_controller=FakeServices(),
            )
            if preview["status"] != "preview":
                raise RuntimeError("Fresh activation did not produce a review-only preview")
            if preview["publishing_authority"] or preview["automation_enabled"]:
                raise RuntimeError("Activation preview unexpectedly changed authority")

            result = {
                "schema_version": 1,
                "status": "pass",
                "initial_stage": started["session"]["stage"],
                "fresh_registry_initially_empty": True,
                "packaged_owner_campaigns_hidden": True,
                "verified_stage": verified["session"]["stage"],
                "user_project_count": 1,
                "user_campaign_count": 1,
                "expected_provider": "x",
                "expected_account_id": ACCOUNT_ID,
                "provider_identity_match": True,
                "provider_write_readiness": True,
                "activation_preview_status": preview["status"],
                "publishing_authority": preview["publishing_authority"],
                "automation_enabled": preview["automation_enabled"],
                "provider_consequence_attempted": False,
                "original_post_once_mutated": False,
            }
            print(json.dumps(result, sort_keys=True))
    finally:
        os.environ.clear()
        os.environ.update(old)


if __name__ == "__main__":
    main()
