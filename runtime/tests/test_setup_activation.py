from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch
import uuid

from jsonschema import Draft202012Validator, FormatChecker

from ocpf_post import automation_authority
from ocpf_post.setup_activation import ActivationError, _exclusive_activation_lock, _runtime_revision
from ocpf_post.setup_engine import SetupEngine, SetupEngineError

ROOT = Path(__file__).resolve().parents[1]
REVISION = "a" * 40
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


def source_tree(root: Path) -> tuple[Path, Path]:
    state = root / "source-state"
    config = root / "source-config"
    state.mkdir()
    config.mkdir()
    future = (datetime.now(timezone.utc) + timedelta(days=3)).replace(microsecond=0)
    write_jsonl(
        state / "schedule-events.jsonl",
        [{
            "schedule_id": "sch_future",
            "event": "scheduled",
            "status": "scheduled",
            "recorded_at": "2026-09-26T12:00:00Z",
            "run_at": future.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "provider": "x",
            "account_id": "123456",
            "campaign": "EXAMPLE-1",
        }],
    )
    write_jsonl(
        state / "publish-receipts.jsonl",
        [{
            "campaign": "OLD-1",
            "provider": "x",
            "status": "published_verified",
            "recorded_at": "2026-09-25T12:00:00Z",
        }],
    )
    (state / "runtime-campaign.txt").write_text("portable content\n", encoding="utf-8")
    (config / "portfolio-policy.json").write_text(
        '{"schema_version":1,"daily_target":5}\n', encoding="utf-8"
    )
    (config / "account-profiles.json").write_text(
        '{"schema_version":1,"accounts":{"x-main":{"provider":"x","account_id":"123456"}}}\n',
        encoding="utf-8",
    )
    (config / "x-token.json").write_text(
        '{"access_token":"SECRET_MUST_NOT_TRANSFER"}\n', encoding="utf-8"
    )
    return state, config


def readiness() -> list[dict]:
    return [{
        "provider": "x",
        "expected_identity": "123456",
        "observed_identity": "123456",
        "identity_match": "match",
        "ready_for_write_configuration": True,
        "blocking_reasons": [],
        "recovery_fencing_strength": "assisted",
        "optional_gaps": ["provider.stale_authority.unresolved"],
    }]


class FakeServices:
    def __init__(self, *, fail_preflight: bool = False, fail_post_cutover: bool = False) -> None:
        self.enabled = False
        self.active = False
        self.staged = False
        self.fail_preflight = fail_preflight
        self.fail_post_cutover = fail_post_cutover
        self.inspect_calls = 0

    def inspect(self) -> dict:
        self.inspect_calls += 1
        active = self.active
        # External preview + apply's fresh preview consume calls 1/2. Arm does
        # not call inspect in this fake; call 3 is post-cutover attestation.
        if self.fail_post_cutover and self.inspect_calls >= 3 and self.active:
            active = False
        return {
            "schema_version": 1,
            "timers": [
                {"unit": "fixture.timer", "enabled": self.enabled, "active": active}
            ],
            "all_enabled": self.enabled,
            "all_active": active,
        }

    def stage(self, **_kwargs) -> dict:
        self.staged = True
        return {"status": "staged", "provider_consequence": False}

    def preflight(self, **_kwargs) -> dict:
        if self.fail_preflight:
            raise ActivationError("activation.preflight.failed", "fixture preflight failure")
        return {"status": "passed", "checks": [], "provider_consequence": False}

    def arm(self) -> dict:
        self.enabled = True
        self.active = True
        return {
            "schema_version": 1,
            "timers": [{"unit": "fixture.timer", "enabled": True, "active": True}],
            "all_enabled": True,
            "all_active": True,
        }

    def disarm(self) -> dict:
        self.enabled = False
        self.active = False
        return {
            "schema_version": 1,
            "timers": [{"unit": "fixture.timer", "enabled": False, "active": False}],
            "all_enabled": False,
            "all_active": False,
        }


class MarkerAwareServices(FakeServices):
    def __init__(self, state_root: Path) -> None:
        super().__init__()
        self.state_root = state_root
        self.marker_was_inactive_before_disarm = False

    def disarm(self) -> dict:
        self.marker_was_inactive_before_disarm = (
            automation_authority.read(self.state_root).get("status") != "active"
        )
        return super().disarm()


class ActivationFixture(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def migration_target(self) -> tuple[SetupEngine, dict]:
        state, config = source_tree(self.root)
        source = SetupEngine(self.root / "source-bootstrap")
        source.start("migrate", operator_label="Example Ltd", machine_label="old-host")
        preview = source.export_migration(
            state_root=state,
            config_root=config,
            output=self.root / "transfer.tar.gz",
            source_post_once_version="0.28.29",
            source_revision=REVISION,
            unit_observation=QUIESCENT,
        )
        sealed = source.export_migration(
            state_root=state,
            config_root=config,
            output=self.root / "transfer.tar.gz",
            source_post_once_version="0.28.29",
            source_revision=REVISION,
            apply=True,
            expected_sha256=preview["migration_export"]["review_sha256"],
            unit_observation=QUIESCENT,
        )
        target = SetupEngine(self.root / "target-bootstrap")
        target.restore_migration(
            self.root / "transfer.tar.gz",
            expected_bundle_sha256=sealed["migration_export"]["bundle_sha256"],
            machine_label="new-host",
        )
        verified = target.verify_migration(readiness())
        self.assertEqual(verified["session"]["stage"], "verification_ready")
        return target, verified

    def recovery_target(self) -> tuple[SetupEngine, dict, datetime]:
        state, config = source_tree(self.root)
        source = SetupEngine(self.root / "recovery-source-bootstrap")
        source.start("migrate", operator_label="Example Ltd", machine_label="lost-host")
        preview = source.recovery_point(
            state_root=state,
            config_root=config,
            output=self.root / "recovery.tar.gz",
            source_post_once_version="0.28.29",
            source_revision=REVISION,
            unit_observation=QUIESCENT,
        )
        sealed = source.recovery_point(
            state_root=state,
            config_root=config,
            output=self.root / "recovery.tar.gz",
            source_post_once_version="0.28.29",
            source_revision=REVISION,
            apply=True,
            expected_sha256=preview["recovery_point_export"]["review_sha256"],
            unit_observation=QUIESCENT,
        )
        inspected = SetupEngine(self.root / "inspect-bootstrap").inspect_bundle(
            self.root / "recovery.tar.gz",
            expected_bundle_sha256=sealed["recovery_point_export"]["bundle_sha256"],
            recovery=True,
        )
        captured = datetime.fromisoformat(inspected["sealed_at"].replace("Z", "+00:00"))
        lost = captured + timedelta(minutes=2)
        target = SetupEngine(self.root / "recovery-target-bootstrap")
        target.restore_recovery(
            self.root / "recovery.tar.gz",
            source_lost_at=lost.strftime("%Y-%m-%dT%H:%M:%SZ"),
            max_data_loss_minutes=5,
            expected_bundle_sha256=sealed["recovery_point_export"]["bundle_sha256"],
            machine_label="replacement-host",
        )
        verified = target.verify_recovery(readiness())
        self.assertEqual(verified["session"]["stage"], "recovery_review_ready")
        return target, verified, lost

    def recovery_resolution(self, verified: dict, lost: datetime) -> dict:
        review = json.loads(Path(verified["recovery"]["review"]).read_text(encoding="utf-8"))
        codes = review["summary"]["required_blocker_codes"]
        methods = {}
        for code in codes:
            if code == "authority.source_host.unknown":
                methods[code] = "all_provider_authority_fenced"
            elif code == "authority.recovery_gap.unreconciled":
                methods[code] = "provider_effects_reconciled"
            elif code == "bundle.authenticity.not_provided":
                methods[code] = "trusted_bundle_digest_verified"
            elif code.startswith("provider.stale_authority."):
                methods[code] = "provider_credential_revoked"
            elif code.startswith(("schedule.ambiguous_effect.", "schedule.partial_effect.", "schedule.executing.")):
                methods[code] = "provider_effect_reconciled"
            else:
                self.fail(f"No fixture resolution method for {code}")
        source_id = review["source_installation_id"]
        observed = (lost + timedelta(minutes=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
        return {
            "schema_version": 1,
            "operation_id": verified["operation"]["operation_id"],
            "source_installation_id": source_id,
            "resolutions": [
                {
                    "code": code,
                    "status": "resolved",
                    "method": methods[code],
                    "observed_at": observed,
                    "evidence_ref": f"fixture:{code}",
                }
                for code in codes
            ],
        }


class RuntimeIdentityTests(unittest.TestCase):
    def test_activation_runtime_requires_clean_exact_checkout(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            subprocess.run(["git", "init", str(root)], check=True, capture_output=True, text=True)
            subprocess.run(["git", "-C", str(root), "config", "user.name", "CI"], check=True)
            subprocess.run(["git", "-C", str(root), "config", "user.email", "ci@example.test"], check=True)
            tracked = root / "runtime.txt"
            tracked.write_text("reviewed\n", encoding="utf-8")
            subprocess.run(["git", "-C", str(root), "add", "runtime.txt"], check=True)
            subprocess.run(["git", "-C", str(root), "commit", "-m", "baseline"], check=True, capture_output=True, text=True)
            revision = _runtime_revision(root)
            self.assertRegex(revision, r"^[0-9a-f]{40}$")

            tracked.write_text("dirty\n", encoding="utf-8")
            with self.assertRaises(ActivationError) as caught:
                _runtime_revision(root)
            self.assertEqual(caught.exception.code, "activation.runtime.dirty")


class AutomationAuthorityTests(unittest.TestCase):
    def test_marker_rejects_unknown_fields_and_unsafe_permissions(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            operation_id = str(uuid.uuid4())
            installation_id = str(uuid.uuid4())
            value = automation_authority.activate(
                root=root,
                operation_id=operation_id,
                installation_id=installation_id,
                authority_generation=2,
                review_sha256="a" * 64,
                runtime_revision="b" * 40,
            )
            marker = Path(value["path"])
            self.assertEqual(marker.stat().st_mode & 0o777, 0o600)

            stored = json.loads(marker.read_text(encoding="utf-8"))
            stored["unexpected"] = "not-allowed"
            marker.write_text(json.dumps(stored) + "\n", encoding="utf-8")
            marker.chmod(0o600)
            with self.assertRaises(automation_authority.AutomationAuthorityError):
                automation_authority.read(root)

            stored.pop("unexpected")
            marker.write_text(json.dumps(stored) + "\n", encoding="utf-8")
            marker.chmod(0o644)
            with self.assertRaises(automation_authority.AutomationAuthorityError):
                automation_authority.read(root)

    def test_stored_markers_match_json_schema(self) -> None:
        schema = json.loads(
            (
                ROOT
                / "schemas"
                / "setup-recovery"
                / "v1"
                / "automation-authority.schema.json"
            ).read_text(encoding="utf-8")
        )
        validator = Draft202012Validator(schema, format_checker=FormatChecker())
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            active = automation_authority.activate(
                root=root,
                operation_id=str(uuid.uuid4()),
                installation_id=str(uuid.uuid4()),
                authority_generation=1,
                review_sha256="c" * 64,
                runtime_revision="d" * 40,
            )
            validator.validate({k: v for k, v in active.items() if k != "path"})
            inactive = automation_authority.deactivate(
                root=root,
                review_sha256="e" * 64,
                reason="schema test",
            )
            validator.validate({k: v for k, v in inactive.items() if k != "path"})


class ActivationTests(ActivationFixture):
    def test_activation_apply_serializes_local_writer_before_mutation(self) -> None:
        target, _ = self.migration_target()
        state_root = self.root / "target-state"
        config_root = self.root / "target-config"
        services = FakeServices()
        preview = target.activation_preview(
            runtime_root=ROOT,
            state_root=state_root,
            config_root=config_root,
            service_controller=services,
        )

        with _exclusive_activation_lock(target.workspace, action="test-holder"):
            with self.assertRaises(SetupEngineError) as caught:
                target.activate(
                    runtime_root=ROOT,
                    state_root=state_root,
                    config_root=config_root,
                    expected_sha256=preview["review_sha256"],
                    service_controller=services,
                )

        self.assertEqual(caught.exception.code, "activation.concurrent_operation")
        self.assertFalse(services.staged)
        self.assertFalse((state_root / "publish-receipts.jsonl").exists())
        self.assertEqual(automation_authority.read(state_root)["status"], "inactive")

    def test_migration_activation_promotes_state_then_deactivation_is_marker_first(self) -> None:
        target, before = self.migration_target()
        state_root = self.root / "target-state"
        config_root = self.root / "target-config"
        config_root.mkdir()
        # Machine authority survives portable-config promotion because credentials
        # were never in the transfer bundle.
        (config_root / "machine-token.json").write_text(
            '{"access_token":"TARGET_ONLY"}\n', encoding="utf-8"
        )

        services = MarkerAwareServices(state_root)
        preview = target.activation_preview(
            runtime_root=ROOT,
            state_root=state_root,
            config_root=config_root,
            service_controller=services,
        )
        self.assertEqual(preview["status"], "preview")
        self.assertEqual(preview["target_authority_generation"], 2)
        self.assertFalse(preview["publishing_authority"])

        active = target.activate(
            runtime_root=ROOT,
            state_root=state_root,
            config_root=config_root,
            expected_sha256=preview["review_sha256"],
            service_controller=services,
        )
        self.assertEqual(active["status"], "active")
        self.assertEqual(active["session"]["stage"], "active")
        self.assertEqual(active["operation"]["authority_generation"], 2)
        self.assertEqual(active["installation"]["authority_generation"], 2)
        self.assertEqual(automation_authority.read(state_root)["status"], "active")
        self.assertTrue((state_root / "publish-receipts.jsonl").is_file())
        self.assertTrue((config_root / "portfolio-policy.json").is_file())
        self.assertTrue((config_root / "machine-token.json").is_file())
        self.assertEqual(
            (config_root / "machine-token.json").read_text(encoding="utf-8"),
            '{"access_token":"TARGET_ONLY"}\n',
        )
        self.assertEqual(before["installation"]["installation_id"], active["operation"].get("active_installation_id"))

        off_preview = target.deactivation_preview(
            runtime_root=ROOT,
            state_root=state_root,
            reason="maintenance",
            service_controller=services,
        )
        inactive = target.deactivate(
            runtime_root=ROOT,
            state_root=state_root,
            reason="maintenance",
            expected_sha256=off_preview["review_sha256"],
            service_controller=services,
        )
        self.assertEqual(inactive["status"], "inactive")
        self.assertTrue(services.marker_was_inactive_before_disarm)
        self.assertEqual(automation_authority.read(state_root)["status"], "inactive")
        self.assertTrue((state_root / "publish-receipts.jsonl").is_file())

        # Same authority holder can be reviewed and reactivated without inventing
        # a new generation or rebuilding history.
        re_preview = target.activation_preview(
            runtime_root=ROOT,
            state_root=state_root,
            config_root=config_root,
            service_controller=services,
        )
        self.assertEqual(re_preview["target_authority_generation"], 2)
        reactivated = target.activate(
            runtime_root=ROOT,
            state_root=state_root,
            config_root=config_root,
            expected_sha256=re_preview["review_sha256"],
            service_controller=services,
        )
        self.assertEqual(reactivated["operation"]["authority_generation"], 2)
        self.assertEqual(automation_authority.read(state_root)["status"], "active")

    def test_deactivation_cloud_self_fence_precedes_local_marker_cutoff(self) -> None:
        target, _before = self.migration_target()
        state_root = self.root / "target-state"
        config_root = self.root / "target-config"
        config_root.mkdir()
        services = MarkerAwareServices(state_root)
        preview = target.activation_preview(
            runtime_root=ROOT,
            state_root=state_root,
            config_root=config_root,
            service_controller=services,
        )
        target.activate(
            runtime_root=ROOT,
            state_root=state_root,
            config_root=config_root,
            expected_sha256=preview["review_sha256"],
            service_controller=services,
        )
        observed: list[str] = []

        def cloud_fence(reason: str):
            self.assertEqual(reason, "maintenance")
            self.assertEqual(automation_authority.read(state_root)["status"], "active")
            observed.append("cloud_fenced")
            return {
                "executorMode": "local",
                "executorStatus": "inactive",
                "authorityGeneration": 3,
            }

        off_preview = target.deactivation_preview(
            runtime_root=ROOT,
            state_root=state_root,
            reason="maintenance",
            service_controller=services,
        )
        with (
            patch.dict(os.environ, {"POSTSTEWARD_REQUIRE_CLOUD_FENCE": "1"}),
            patch(
                "ocpf_post.poststeward_cloud.deactivate_executor_fence",
                side_effect=cloud_fence,
            ),
        ):
            inactive = target.deactivate(
                runtime_root=ROOT,
                state_root=state_root,
                reason="maintenance",
                expected_sha256=off_preview["review_sha256"],
                service_controller=services,
            )

        self.assertEqual(observed, ["cloud_fenced"])
        self.assertEqual(inactive["cloud_fence"]["status"], "inactive")
        self.assertTrue(inactive["cloud_fence"]["attempted"])
        self.assertTrue(services.marker_was_inactive_before_disarm)
        self.assertEqual(automation_authority.read(state_root)["status"], "inactive")

    def test_deactivation_closes_local_authority_when_cloud_fence_is_unreachable(self) -> None:
        target, _before = self.migration_target()
        state_root = self.root / "target-state"
        config_root = self.root / "target-config"
        config_root.mkdir()
        services = MarkerAwareServices(state_root)
        preview = target.activation_preview(
            runtime_root=ROOT,
            state_root=state_root,
            config_root=config_root,
            service_controller=services,
        )
        target.activate(
            runtime_root=ROOT,
            state_root=state_root,
            config_root=config_root,
            expected_sha256=preview["review_sha256"],
            service_controller=services,
        )
        off_preview = target.deactivation_preview(
            runtime_root=ROOT,
            state_root=state_root,
            reason="network outage",
            service_controller=services,
        )
        with (
            patch.dict(os.environ, {"POSTSTEWARD_REQUIRE_CLOUD_FENCE": "1"}),
            patch(
                "ocpf_post.poststeward_cloud.deactivate_executor_fence",
                side_effect=OSError("offline"),
            ),
        ):
            inactive = target.deactivate(
                runtime_root=ROOT,
                state_root=state_root,
                reason="network outage",
                expected_sha256=off_preview["review_sha256"],
                service_controller=services,
            )

        self.assertEqual(inactive["cloud_fence"]["status"], "attention")
        self.assertTrue(inactive["cloud_fence"]["attempted"])
        self.assertTrue(services.marker_was_inactive_before_disarm)
        self.assertEqual(automation_authority.read(state_root)["status"], "inactive")

    def test_activation_review_detects_target_drift(self) -> None:
        target, _ = self.migration_target()
        state_root = self.root / "target-state"
        config_root = self.root / "target-config"
        services = FakeServices()
        preview = target.activation_preview(
            runtime_root=ROOT,
            state_root=state_root,
            config_root=config_root,
            service_controller=services,
        )
        config_root.mkdir()
        (config_root / "unexpected.json").write_text('{"changed":true}\n', encoding="utf-8")
        with self.assertRaises(SetupEngineError) as caught:
            target.activate(
                runtime_root=ROOT,
                state_root=state_root,
                config_root=config_root,
                expected_sha256=preview["review_sha256"],
                service_controller=services,
            )
        self.assertEqual(caught.exception.code, "activation.review.changed")
        self.assertEqual(automation_authority.read(state_root)["status"], "inactive")

    def test_pre_cutover_failure_restores_original_target_roots(self) -> None:
        target, _ = self.migration_target()
        state_root = self.root / "target-state"
        config_root = self.root / "target-config"
        state_root.mkdir()
        config_root.mkdir()
        (state_root / "local-marker.txt").write_text("before\n", encoding="utf-8")
        services = FakeServices(fail_preflight=True)
        preview = target.activation_preview(
            runtime_root=ROOT,
            state_root=state_root,
            config_root=config_root,
            service_controller=services,
        )
        with self.assertRaises(SetupEngineError) as caught:
            target.activate(
                runtime_root=ROOT,
                state_root=state_root,
                config_root=config_root,
                expected_sha256=preview["review_sha256"],
                service_controller=services,
            )
        self.assertEqual(caught.exception.code, "activation.preflight.failed")
        self.assertEqual((state_root / "local-marker.txt").read_text(encoding="utf-8"), "before\n")
        self.assertFalse((state_root / "publish-receipts.jsonl").exists())
        self.assertEqual(automation_authority.read(state_root)["status"], "inactive")

    def test_post_cutover_failure_closes_gate_without_rolling_history_back(self) -> None:
        target, _ = self.migration_target()
        state_root = self.root / "target-state"
        config_root = self.root / "target-config"
        services = FakeServices(fail_post_cutover=True)
        preview = target.activation_preview(
            runtime_root=ROOT,
            state_root=state_root,
            config_root=config_root,
            service_controller=services,
        )
        with self.assertRaises(SetupEngineError) as caught:
            target.activate(
                runtime_root=ROOT,
                state_root=state_root,
                config_root=config_root,
                expected_sha256=preview["review_sha256"],
                service_controller=services,
            )
        self.assertEqual(caught.exception.code, "activation.post_cutover.attestation_failed")
        self.assertEqual(automation_authority.read(state_root)["status"], "inactive")
        self.assertTrue((state_root / "publish-receipts.jsonl").is_file())
        current = target.status()
        self.assertEqual(current["session"]["stage"], "active")
        self.assertEqual(current["operation"]["status"], "active")

    def test_recovery_activation_requires_exact_structured_resolution(self) -> None:
        target, verified, lost = self.recovery_target()
        state_root = self.root / "recovered-state"
        config_root = self.root / "recovered-config"
        services = FakeServices()
        with self.assertRaises(SetupEngineError) as caught:
            target.activation_preview(
                runtime_root=ROOT,
                state_root=state_root,
                config_root=config_root,
                service_controller=services,
            )
        self.assertEqual(caught.exception.code, "activation.recovery_resolution.required")

        resolution = self.recovery_resolution(verified, lost)
        preview = target.activation_preview(
            runtime_root=ROOT,
            state_root=state_root,
            config_root=config_root,
            recovery_resolution=resolution,
            service_controller=services,
        )
        self.assertEqual(preview["target_authority_generation"], 2)
        active = target.activate(
            runtime_root=ROOT,
            state_root=state_root,
            config_root=config_root,
            recovery_resolution=resolution,
            expected_sha256=preview["review_sha256"],
            service_controller=services,
        )
        self.assertEqual(active["status"], "active")
        self.assertEqual(active["operation"]["authority_generation"], 2)
        self.assertEqual(automation_authority.read(state_root)["status"], "active")

    def test_recovery_resolution_does_not_accept_acknowledgement(self) -> None:
        target, verified, lost = self.recovery_target()
        resolution = self.recovery_resolution(verified, lost)
        resolution["resolutions"][0]["method"] = "acknowledged"
        with self.assertRaises(SetupEngineError) as caught:
            target.activation_preview(
                runtime_root=ROOT,
                state_root=self.root / "state",
                config_root=self.root / "config",
                recovery_resolution=resolution,
                service_controller=FakeServices(),
            )
        self.assertEqual(caught.exception.code, "activation.recovery_resolution.method_invalid")

    def test_unattended_wrapper_fails_closed_without_active_marker(self) -> None:
        state = self.root / "unattended-state"
        config = self.root / "unattended-config"
        env = dict(os.environ)
        env["OCPF_POST_STATE_DIR"] = str(state)
        env["OCPF_POST_CONFIG_DIR"] = str(config)
        result = subprocess.run(
            ["sh", str(ROOT / "scripts" / "run-unattended"), "run-due"],
            cwd=ROOT,
            env=env,
            text=True,
            capture_output=True,
            timeout=30,
            check=False,
        )
        self.assertEqual(result.returncode, 3)
        self.assertFalse(state.exists() and (state / "publish-receipts.jsonl").exists())


if __name__ == "__main__":
    unittest.main()
