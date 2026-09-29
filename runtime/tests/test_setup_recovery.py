from __future__ import annotations

from datetime import datetime, timedelta, timezone
import gzip
import json
from pathlib import Path
import tempfile
import unittest

from jsonschema import Draft202012Validator, FormatChecker

from ocpf_post.setup_engine import SetupEngine, SetupEngineError

REVISION = "a" * 40
ROOT = Path(__file__).resolve().parents[1]
SCHEMA_ROOT = ROOT / "schemas" / "setup-recovery" / "v1"
FORMAT_CHECKER = FormatChecker()


def schema(name: str) -> dict:
    return json.loads((SCHEMA_ROOT / name).read_text(encoding="utf-8"))


QUIESCENT = {
    "schema_version": 1,
    "status": "quiescent",
    "units": [],
    "active_writer_units": [],
    "mutation_attempted": False,
    "boundary": "fixture",
}


def write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(json.dumps(row, separators=(",", ":")) + "\n" for row in rows),
        encoding="utf-8",
    )


def source_tree(root: Path, *, schedule_status: str = "scheduled") -> tuple[Path, Path]:
    state = root / "state"
    config = root / "config"
    state.mkdir()
    config.mkdir()

    run_at = (datetime.now(timezone.utc) + timedelta(days=3)).replace(microsecond=0)
    write_jsonl(
        state / "schedule-events.jsonl",
        [
            {
                "schedule_id": "sch_future",
                "event": "scheduled",
                "status": schedule_status,
                "recorded_at": "2026-09-26T12:00:00Z",
                "run_at": run_at.strftime("%Y-%m-%dT%H:%M:%SZ"),
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
    (state / "emergency-token.json").write_text(
        '{"access_token":"STATE_SECRET_MUST_NOT_TRANSFER"}\n',
        encoding="utf-8",
    )
    (config / "portfolio-policy.json").write_text(
        '{"schema_version":1,"daily_target":5}\n',
        encoding="utf-8",
    )
    (config / "account-profiles.json").write_text(
        '{"schema_version":1,"accounts":{"x-main":{"provider":"x","account_id":"123456"}}}\n',
        encoding="utf-8",
    )
    (config / "x-token.json").write_text(
        '{"access_token":"CONFIG_SECRET_MUST_NOT_TRANSFER"}\n',
        encoding="utf-8",
    )
    return state, config


def readiness(identity: str = "123456") -> list[dict]:
    return [
        {
            "provider": "x",
            "expected_identity": "123456",
            "observed_identity": identity,
            "identity_match": "match" if identity == "123456" else "mismatch",
            "ready_for_write_configuration": identity == "123456",
            "blocking_reasons": [] if identity == "123456" else ["provider.identity.mismatch"],
            "recovery_fencing_strength": "assisted",
            "optional_gaps": ["provider.stale_authority.unresolved"],
        }
    ]


class RecoveryPointTests(unittest.TestCase):
    def test_recovery_point_does_not_retire_source_and_excludes_credentials(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            state, config = source_tree(root)
            engine = SetupEngine(root / "source-bootstrap")
            started = engine.start("migrate", operator_label="Example Ltd", machine_label="old-host")

            preview = engine.recovery_point(
                state_root=state,
                config_root=config,
                output=root / "recovery.tar.gz",
                source_post_once_version="0.28.29",
                source_revision=REVISION,
                unit_observation=QUIESCENT,
            )
            self.assertFalse((root / "recovery.tar.gz").exists())
            review = preview["recovery_point_export"]["review_sha256"]

            sealed = engine.recovery_point(
                state_root=state,
                config_root=config,
                output=root / "recovery.tar.gz",
                source_post_once_version="0.28.29",
                source_revision=REVISION,
                apply=True,
                expected_sha256=review,
                unit_observation=QUIESCENT,
            )
            self.assertTrue((root / "recovery.tar.gz").is_file())
            self.assertTrue(sealed["recovery_point_export"]["source_remains_active"])
            current = engine.status(started["session"]["session_id"])
            self.assertEqual(current["operation"]["status"], "active")
            self.assertEqual(current["installation"]["status"], "active")
            self.assertEqual(current["session"]["stage"], "preflight_ready")
            self.assertFalse(current["publishing_authority"])

            with gzip.open(root / "recovery.tar.gz", "rb") as handle:
                raw = handle.read()
            self.assertNotIn(b"CONFIG_SECRET_MUST_NOT_TRANSFER", raw)
            self.assertNotIn(b"STATE_SECRET_MUST_NOT_TRANSFER", raw)

    def test_recovery_point_requires_exact_review_hash(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            state, config = source_tree(root)
            engine = SetupEngine(root / "source-bootstrap")
            started = engine.start("migrate", operator_label="Example")
            with self.assertRaises(SetupEngineError) as caught:
                engine.recovery_point(
                    state_root=state,
                    config_root=config,
                    output=root / "recovery.tar.gz",
                    source_post_once_version="0.28.29",
                    source_revision=REVISION,
                    apply=True,
                    expected_sha256="wrong",
                    unit_observation=QUIESCENT,
                )
            self.assertEqual(caught.exception.code, "setup.recovery.review_changed")
            current = engine.status(started["session"]["session_id"])
            self.assertEqual(current["operation"]["status"], "active")
            self.assertFalse((root / "recovery.tar.gz").exists())


class DeadHostRecoveryTests(unittest.TestCase):
    def create_point(self, root: Path) -> tuple[Path, str, dict]:
        state, config = source_tree(root)
        source = SetupEngine(root / "source-bootstrap")
        started = source.start("migrate", operator_label="Example Ltd", machine_label="old-host")
        preview = source.recovery_point(
            state_root=state,
            config_root=config,
            output=root / "recovery.tar.gz",
            source_post_once_version="0.28.29",
            source_revision=REVISION,
            unit_observation=QUIESCENT,
        )
        sealed = source.recovery_point(
            state_root=state,
            config_root=config,
            output=root / "recovery.tar.gz",
            source_post_once_version="0.28.29",
            source_revision=REVISION,
            apply=True,
            expected_sha256=preview["recovery_point_export"]["review_sha256"],
            unit_observation=QUIESCENT,
        )
        return root / "recovery.tar.gz", sealed["recovery_point_export"]["bundle_sha256"], started

    def test_dead_host_recovery_reaches_review_gate_not_activation(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            bundle, digest, source_started = self.create_point(root)

            inspector = SetupEngine(root / "inspect-bootstrap")
            inspected = inspector.inspect_bundle(
                bundle,
                expected_bundle_sha256=digest,
                recovery=True,
            )
            self.assertEqual(inspected["bundle_purpose"], "dead_host_recovery")
            self.assertEqual(inspected["authenticity"], "digest_pinned")
            Draft202012Validator(
                schema("recovery-point.schema.json"),
                format_checker=FORMAT_CHECKER,
            ).validate(inspected["recovery_point"])
            captured = datetime.fromisoformat(inspected["sealed_at"].replace("Z", "+00:00"))
            lost_at = (captured + timedelta(minutes=4)).strftime("%Y-%m-%dT%H:%M:%SZ")

            target = SetupEngine(root / "target-bootstrap")
            restored = target.restore_recovery(
                bundle,
                source_lost_at=lost_at,
                max_data_loss_minutes=5,
                expected_bundle_sha256=digest,
                machine_label="replacement-host",
            )
            self.assertEqual(restored["mode"], "recover")
            self.assertEqual(restored["session"]["stage"], "restored_quarantined")
            self.assertEqual(restored["operation"]["operation_id"], source_started["operation"]["operation_id"])
            self.assertEqual(restored["operation"]["status"], "recovery_review")
            self.assertIsNone(restored["operation"]["active_installation_id"])
            self.assertEqual(restored["installation"]["status"], "candidate")
            self.assertNotEqual(
                restored["installation"]["installation_id"],
                source_started["installation"]["installation_id"],
            )
            self.assertEqual(restored["recovery"]["source_status"], "recovery_unknown")
            self.assertFalse(restored["publishing_authority"])
            self.assertFalse(restored["automation_enabled"])

            verified = target.verify_recovery(readiness())
            self.assertEqual(verified["session"]["stage"], "recovery_review_ready")
            self.assertEqual(verified["recovery"]["readiness_status"], "RECOVERY_REVIEW_REQUIRED")
            self.assertIn("recovery.review.required", verified["blockers"])
            self.assertIn("recovery.stale_source_authority.unresolved", verified["blockers"])
            self.assertIn("activation.recovery_resolution.required", verified["blockers"])
            self.assertIn("prepare_recovery_resolution_then_preview_activation", verified["next_actions"])
            self.assertFalse(verified["publishing_authority"])
            self.assertFalse(verified["automation_enabled"])

            review_path = Path(verified["recovery"]["review"])
            review = json.loads(review_path.read_text(encoding="utf-8"))
            Draft202012Validator(
                schema("recovery-review.schema.json"),
                format_checker=FORMAT_CHECKER,
            ).validate(review)
            codes = {row["code"] for row in review["checks"]}
            self.assertIn("authority.source_host.unknown", codes)
            self.assertIn("authority.recovery_gap.unreconciled", codes)
            self.assertIn("provider.stale_authority.x_unresolved", codes)
            self.assertIn("bundle.authenticity.digest_pinned", codes)
            self.assertNotIn("bundle.authenticity.not_provided", codes)
            self.assertEqual(review["summary"]["status"], "RECOVERY_REVIEW_REQUIRED")

            reconciliation = json.loads(
                (
                    target.workspace
                    / "migrations"
                    / verified["session"]["session_id"]
                    / "recovery-schedule-reconciliation.json"
                ).read_text(encoding="utf-8")
            )
            self.assertFalse(reconciliation["automatic_rearm"])
            self.assertFalse(reconciliation["automatic_catch_up"])
            self.assertTrue(all(row["actionable_after_restore"] is False for row in reconciliation["rows"]))

    def test_recovery_without_trusted_digest_remains_authenticity_review_required(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            bundle, _digest, _started = self.create_point(root)
            inspected = SetupEngine(root / "inspect").inspect_bundle(bundle, recovery=True)
            captured = datetime.fromisoformat(inspected["sealed_at"].replace("Z", "+00:00"))
            target = SetupEngine(root / "target")
            target.restore_recovery(
                bundle,
                source_lost_at=(captured + timedelta(minutes=1)).strftime("%Y-%m-%dT%H:%M:%SZ"),
                max_data_loss_minutes=5,
            )
            verified = target.verify_recovery(readiness())
            review = json.loads(Path(verified["recovery"]["review"]).read_text(encoding="utf-8"))
            codes = {row["code"] for row in review["checks"]}
            self.assertIn("bundle.authenticity.not_provided", codes)
            self.assertEqual(review["summary"]["status"], "RECOVERY_REVIEW_REQUIRED")

    def test_rpo_exceeded_blocks_before_setup_control_store_is_created(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            bundle, digest, _started = self.create_point(root)
            inspected = SetupEngine(root / "inspect").inspect_bundle(
                bundle,
                expected_bundle_sha256=digest,
                recovery=True,
            )
            captured = datetime.fromisoformat(inspected["sealed_at"].replace("Z", "+00:00"))
            target = SetupEngine(root / "target")
            with self.assertRaises(SetupEngineError) as caught:
                target.restore_recovery(
                    bundle,
                    source_lost_at=(captured + timedelta(minutes=30)).strftime("%Y-%m-%dT%H:%M:%SZ"),
                    max_data_loss_minutes=5,
                    expected_bundle_sha256=digest,
                )
            self.assertEqual(caught.exception.code, "recovery.point.rpo_exceeded")
            self.assertFalse((target.workspace / "setup-control.sqlite3").exists())

    def test_restore_retry_is_idempotent_and_cannot_change_committed_rpo(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            bundle, digest, _started = self.create_point(root)
            inspected = SetupEngine(root / "inspect").inspect_bundle(
                bundle,
                expected_bundle_sha256=digest,
                recovery=True,
            )
            captured = datetime.fromisoformat(inspected["sealed_at"].replace("Z", "+00:00"))
            lost_at = (captured + timedelta(minutes=2)).strftime("%Y-%m-%dT%H:%M:%SZ")
            target = SetupEngine(root / "target")
            first = target.restore_recovery(
                bundle,
                source_lost_at=lost_at,
                max_data_loss_minutes=5,
                expected_bundle_sha256=digest,
            )
            repeated = target.restore_recovery(
                bundle,
                source_lost_at=lost_at,
                max_data_loss_minutes=5,
                expected_bundle_sha256=digest,
            )
            self.assertEqual(
                repeated["session"]["session_id"],
                first["session"]["session_id"],
            )
            self.assertEqual(repeated["session"]["stage"], "restored_quarantined")

            with self.assertRaises(SetupEngineError) as caught:
                target.restore_recovery(
                    bundle,
                    source_lost_at=(captured + timedelta(minutes=3)).strftime("%Y-%m-%dT%H:%M:%SZ"),
                    max_data_loss_minutes=5,
                    expected_bundle_sha256=digest,
                )
            self.assertEqual(caught.exception.code, "setup.recovery.review_changed")
            current = target.status(first["session"]["session_id"])
            self.assertEqual(current["session"]["stage"], "restored_quarantined")
            self.assertFalse(current["publishing_authority"])

    def test_wrong_provider_identity_keeps_recovery_quarantined(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            bundle, digest, _started = self.create_point(root)
            inspected = SetupEngine(root / "inspect").inspect_bundle(
                bundle,
                expected_bundle_sha256=digest,
                recovery=True,
            )
            captured = datetime.fromisoformat(inspected["sealed_at"].replace("Z", "+00:00"))
            target = SetupEngine(root / "target")
            restored = target.restore_recovery(
                bundle,
                source_lost_at=(captured + timedelta(minutes=1)).strftime("%Y-%m-%dT%H:%M:%SZ"),
                max_data_loss_minutes=5,
                expected_bundle_sha256=digest,
            )
            with self.assertRaises(SetupEngineError) as caught:
                target.verify_recovery(readiness("999999"))
            self.assertEqual(caught.exception.code, "setup.recovery.provider_authority_blocked")
            current = target.status(restored["session"]["session_id"])
            self.assertEqual(current["session"]["stage"], "restored_quarantined")
            self.assertFalse(current["publishing_authority"])


if __name__ == "__main__":
    unittest.main()
