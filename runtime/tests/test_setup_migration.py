from __future__ import annotations

import gzip
import json
from pathlib import Path
import tempfile
import unittest

from ocpf_post.setup_contracts import new_installation, new_operation
from ocpf_post.setup_engine import SetupEngine, SetupEngineError
from ocpf_post.setup_migration import MigrationError, source_preview

REVISION = "a" * 40
QUIESCENT = {
    "schema_version": 1,
    "status": "quiescent",
    "units": [],
    "active_writer_units": [],
    "mutation_attempted": False,
    "boundary": "fixture",
}
ACTIVE = {
    "schema_version": 1,
    "status": "active",
    "units": [],
    "active_writer_units": ["ocpf-post-run-due.timer"],
    "mutation_attempted": False,
    "boundary": "fixture",
}


def active_identity():
    operation = new_operation("Example")
    installation = new_installation(operation["operation_id"])
    operation["status"] = "active"
    operation["active_installation_id"] = installation["installation_id"]
    installation["status"] = "active"
    installation["authority_generation"] = operation["authority_generation"]
    return operation, installation


def write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(row, separators=(",", ":")) + "\n" for row in rows), encoding="utf-8")


def source_tree(root: Path, *, schedule_status: str = "scheduled") -> tuple[Path, Path]:
    state = root / "state"
    config = root / "config"
    state.mkdir()
    config.mkdir()
    write_jsonl(
        state / "schedule-events.jsonl",
        [
            {
                "schedule_id": "sch_future",
                "event": "scheduled",
                "status": schedule_status,
                "recorded_at": "2026-09-26T12:00:00Z",
                "run_at": "2030-09-30T12:00:00Z",
                "provider": "x",
                "account_id": "123456",
                "campaign": "EXAMPLE-1",
            }
        ],
    )
    write_jsonl(
        state / "publish-receipts.jsonl",
        [
            {
                "campaign": "OLD-1",
                "provider": "x",
                "status": "published_verified",
                "recorded_at": "2026-09-25T12:00:00Z",
            }
        ],
    )
    (state / "runtime-campaign.txt").write_text("portable content\n", encoding="utf-8")
    (config / "portfolio-policy.json").write_text(
        '{"schema_version":1,"daily_target":5}\n',
        encoding="utf-8",
    )
    (config / "account-profiles.json").write_text(
        '{"schema_version":1,"accounts":{}}\n',
        encoding="utf-8",
    )
    (config / "x-token.json").write_text(
        '{"access_token":"NEVER_TRANSFER_THIS"}\n',
        encoding="utf-8",
    )
    (config / "linkedin-settings.json").write_text(
        '{"client_secret":"ALSO_NEVER_TRANSFER"}\n',
        encoding="utf-8",
    )
    return state, config


class MigrationPreviewTests(unittest.TestCase):
    def test_preview_excludes_credentials_and_requires_quiescent_source(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            state, config = source_tree(root)
            operation, installation = active_identity()
            preview = source_preview(
                operation=operation,
                installation=installation,
                state_root=state,
                config_root=config,
                output=root / "transfer.tar.gz",
                source_post_once_version="0.28.29",
                source_revision=REVISION,
                unit_observation=QUIESCENT,
            )
            paths = {(row["scope"], row["path"]) for row in preview["files"]}
            self.assertIn(("state", "publish-receipts.jsonl"), paths)
            self.assertIn(("config", "portfolio-policy.json"), paths)
            self.assertNotIn(("config", "x-token.json"), paths)
            self.assertNotIn(("config", "linkedin-settings.json"), paths)
            self.assertEqual(preview["expected_identities"], [{"provider": "x", "account_id": "123456"}])
            self.assertFalse(preview["publishing_authority"])

            with self.assertRaises(MigrationError) as caught:
                source_preview(
                    operation=operation,
                    installation=installation,
                    state_root=state,
                    config_root=config,
                    output=root / "transfer2.tar.gz",
                    source_post_once_version="0.28.29",
                    source_revision=REVISION,
                    unit_observation=ACTIVE,
                )
            self.assertEqual(caught.exception.code, "migration.source.automation_active")

    def test_executing_schedule_blocks_healthy_seal(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            state, config = source_tree(root, schedule_status="executing")
            operation, installation = active_identity()
            with self.assertRaises(MigrationError) as caught:
                source_preview(
                    operation=operation,
                    installation=installation,
                    state_root=state,
                    config_root=config,
                    output=root / "transfer.tar.gz",
                    source_post_once_version="0.28.29",
                    source_revision=REVISION,
                    unit_observation=QUIESCENT,
                )
            self.assertEqual(caught.exception.code, "migration.source.schedule_unresolved")


class HealthyMigrationEngineTests(unittest.TestCase):
    def test_source_to_target_handoff_stops_before_activation(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source_state, source_config = source_tree(root)
            source_workspace = root / "source-bootstrap"
            target_workspace = root / "target-bootstrap"
            bundle = root / "healthy-transfer.tar.gz"

            source = SetupEngine(source_workspace)
            started = source.start(
                "migrate",
                operator_label="Example Ltd",
                machine_label="old-host",
            )
            self.assertEqual(started["session"]["stage"], "preflight_ready")
            source_installation_id = started["installation"]["installation_id"]
            operation_id = started["operation"]["operation_id"]

            previewed = source.export_migration(
                state_root=source_state,
                config_root=source_config,
                output=bundle,
                source_post_once_version="0.28.29",
                source_revision=REVISION,
                unit_observation=QUIESCENT,
            )
            review = previewed["migration_export"]["review_sha256"]
            self.assertFalse(bundle.exists())

            sealed = source.export_migration(
                state_root=source_state,
                config_root=source_config,
                output=bundle,
                source_post_once_version="0.28.29",
                source_revision=REVISION,
                apply=True,
                expected_sha256=review,
                unit_observation=QUIESCENT,
            )
            self.assertTrue(bundle.is_file())
            self.assertEqual(sealed["session"]["stage"], "bundle_verified")
            self.assertEqual(sealed["operation"]["status"], "prepared")
            self.assertEqual(sealed["installation"]["status"], "retired")
            bundle_sha = sealed["migration_export"]["bundle_sha256"]

            with gzip.open(bundle, "rb") as handle:
                raw = handle.read()
            self.assertNotIn(b"NEVER_TRANSFER_THIS", raw)
            self.assertNotIn(b"ALSO_NEVER_TRANSFER", raw)

            target = SetupEngine(target_workspace)
            inspected = target.inspect_bundle(bundle, expected_bundle_sha256=bundle_sha)
            self.assertEqual(inspected["operation_id"], operation_id)
            self.assertEqual(inspected["source_installation_id"], source_installation_id)
            self.assertEqual(inspected["source_continuity"], "retired_and_quiescent")

            restored = target.restore_migration(
                bundle,
                expected_bundle_sha256=bundle_sha,
                machine_label="new-host",
            )
            self.assertEqual(restored["session"]["stage"], "restored_quarantined")
            self.assertEqual(restored["operation"]["operation_id"], operation_id)
            self.assertEqual(restored["operation"]["status"], "prepared")
            self.assertEqual(restored["installation"]["status"], "candidate")
            self.assertNotEqual(restored["installation"]["installation_id"], source_installation_id)
            self.assertIn(
                "provider.reauthorization_and_identity_verification.required",
                restored["blockers"],
            )
            self.assertFalse(restored["publishing_authority"])
            self.assertFalse(restored["automation_enabled"])

            verified = target.verify_migration(
                [
                    {
                        "provider": "x",
                        "expected_identity": "123456",
                        "observed_identity": "123456",
                        "identity_match": "match",
                        "ready_for_write_configuration": True,
                        "blocking_reasons": [],
                    }
                ]
            )
            self.assertEqual(verified["session"]["stage"], "verification_ready")
            self.assertIn("preview_activation_gate", verified["next_actions"])
            self.assertNotIn("automation.activation.not_implemented", verified["blockers"])
            self.assertFalse(verified["publishing_authority"])
            self.assertFalse(verified["automation_enabled"])

            reconcile_path = target_workspace / "migrations" / verified["session"]["session_id"] / "schedule-reconciliation.json"
            reconciliation = json.loads(reconcile_path.read_text(encoding="utf-8"))
            self.assertFalse(reconciliation["automatic_rearm"])
            self.assertFalse(reconciliation["automatic_catch_up"])
            self.assertTrue(all(row["actionable_after_restore"] is False for row in reconciliation["rows"]))

    def test_wrong_target_identity_does_not_advance_from_quarantine(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            state, config = source_tree(root)
            source = SetupEngine(root / "source-bootstrap")
            source.start("migrate", operator_label="Example")
            preview = source.export_migration(
                state_root=state,
                config_root=config,
                output=root / "bundle.tar.gz",
                source_post_once_version="0.28.29",
                source_revision=REVISION,
                unit_observation=QUIESCENT,
            )
            source.export_migration(
                state_root=state,
                config_root=config,
                output=root / "bundle.tar.gz",
                source_post_once_version="0.28.29",
                source_revision=REVISION,
                apply=True,
                expected_sha256=preview["migration_export"]["review_sha256"],
                unit_observation=QUIESCENT,
            )
            target = SetupEngine(root / "target-bootstrap")
            restored = target.restore_migration(root / "bundle.tar.gz")
            with self.assertRaises(SetupEngineError) as caught:
                target.verify_migration(
                    [
                        {
                            "provider": "x",
                            "expected_identity": "123456",
                            "observed_identity": "999999",
                            "identity_match": "mismatch",
                            "ready_for_write_configuration": False,
                            "blocking_reasons": ["provider.identity.mismatch"],
                        }
                    ]
                )
            self.assertEqual(caught.exception.code, "setup.migration.provider_authority_blocked")
            current = target.status(restored["session"]["session_id"])
            self.assertEqual(current["session"]["stage"], "restored_quarantined")
            self.assertFalse(current["publishing_authority"])

    def test_unsupported_state_registry_bundle_fails_before_source_retirement(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            state, config = source_tree(root)
            source = SetupEngine(root / "source-bootstrap")
            source.start("migrate", operator_label="Example")
            with self.assertRaises(SetupEngineError) as caught:
                source.export_migration(
                    state_root=state,
                    config_root=config,
                    output=root / "bundle.tar.gz",
                    source_post_once_version="0.28.29",
                    source_revision=REVISION,
                    source_state_registry_version=2,
                    unit_observation=QUIESCENT,
                )
            self.assertEqual(caught.exception.code, "migration.source.state_registry_unsupported")
            current = source.status()
            self.assertEqual(current["operation"]["status"], "active")
            self.assertEqual(current["installation"]["status"], "active")
            self.assertEqual(current["session"]["stage"], "preflight_ready")
            self.assertFalse((root / "bundle.tar.gz").exists())
            self.assertFalse(current["publishing_authority"])

    def test_review_hash_binds_source_revision_before_retirement(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            state, config = source_tree(root)
            source = SetupEngine(root / "source-bootstrap")
            source.start("migrate", operator_label="Example")
            preview = source.export_migration(
                state_root=state,
                config_root=config,
                output=root / "bundle.tar.gz",
                source_post_once_version="0.28.29",
                source_revision=REVISION,
                unit_observation=QUIESCENT,
            )
            with self.assertRaises(SetupEngineError) as caught:
                source.export_migration(
                    state_root=state,
                    config_root=config,
                    output=root / "bundle.tar.gz",
                    source_post_once_version="0.28.29",
                    source_revision="b" * 40,
                    apply=True,
                    expected_sha256=preview["migration_export"]["review_sha256"],
                    unit_observation=QUIESCENT,
                )
            self.assertEqual(caught.exception.code, "setup.migration.review_changed")
            current = source.status()
            self.assertEqual(current["operation"]["status"], "active")
            self.assertEqual(current["installation"]["status"], "active")
            self.assertEqual(current["session"]["stage"], "preflight_ready")
            self.assertFalse((root / "bundle.tar.gz").exists())

    def test_active_source_failure_does_not_retire_authority(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            state, config = source_tree(root)
            engine = SetupEngine(root / "bootstrap")
            started = engine.start("migrate", operator_label="Example")
            with self.assertRaises(SetupEngineError) as caught:
                engine.export_migration(
                    state_root=state,
                    config_root=config,
                    output=root / "bundle.tar.gz",
                    source_post_once_version="0.28.29",
                    source_revision=REVISION,
                    apply=True,
                    expected_sha256="bad",
                    unit_observation=ACTIVE,
                )
            self.assertEqual(caught.exception.code, "migration.source.automation_active")
            current = engine.status(started["session"]["session_id"])
            self.assertEqual(current["operation"]["status"], "active")
            self.assertEqual(current["installation"]["status"], "active")
            self.assertEqual(current["session"]["stage"], "preflight_ready")


if __name__ == "__main__":
    unittest.main()
