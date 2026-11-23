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
from ocpf_post.setup_activation import ActivationError, ActivationManager, _exclusive_activation_lock, _runtime_revision
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
            (ROOT / "schemas" / "setup-recovery" / "v1" / "automation-authority.schema.json").read_text(encoding="utf-8")
        )
        validator = Draft202012Validator(schema, format_checker=FormatChecker())
        for cloud in (None, {
            "workspace": "ws_fixture", "installation_id": str(uuid.uuid4()), "authority_generation": 7,
        }):
            with self.subTest(cloud=cloud), tempfile.TemporaryDirectory() as temp:
                root = Path(temp)
                active = automation_authority.activate(
                    root=root, operation_id=str(uuid.uuid4()), installation_id=str(uuid.uuid4()),
                    authority_generation=1, review_sha256="c" * 64, runtime_revision="d" * 40,
                    cloud_executor=cloud,
                )
                validator.validate({k: v for k, v in active.items() if k != "path"})
                inactive = automation_authority.deactivate(root=root, review_sha256="e" * 64, reason="schema test")
                validator.validate({k: v for k, v in inactive.items() if k != "path"})
                self.assertNotIn("cloud_executor", inactive)
                if cloud is not None:
                    malformed = {k: v for k, v in inactive.items() if k != "path"}
                    malformed["cloud_executor"] = cloud
                    self.assertFalse(validator.is_valid(malformed))
                    with self.assertRaises(automation_authority.AutomationAuthorityError):
                        automation_authority.validate(malformed)

    def test_cloud_fence_rejects_invalid_or_unexpected_fields_before_writing(self) -> None:
        for change in (
            {"installation_id": "not-an-installation"}, {"authority_generation": True},
            {"authority_generation": 0}, {"workspace": " "}, {"runtime_token": "not-allowed"},
        ):
            with self.subTest(change=change), tempfile.TemporaryDirectory() as temp:
                root = Path(temp)
                cloud = {
                    "workspace": "ws_fixture", "installation_id": str(uuid.uuid4()), "authority_generation": 2,
                    **change,
                }
                with self.assertRaises(automation_authority.AutomationAuthorityError):
                    automation_authority.activate(
                        root=root, operation_id=str(uuid.uuid4()), installation_id=str(uuid.uuid4()),
                        authority_generation=1, review_sha256="c" * 64, runtime_revision="d" * 40,
                        cloud_executor=cloud,
                    )
                self.assertFalse(automation_authority.path(root).exists())


class ActivationTests(ActivationFixture):
    def test_activation_retains_reviewed_cloud_fence_separately_from_local_identity(self) -> None:
        from ocpf_post.poststeward_product import _compatibility

        target, before = self.migration_target()
        state_root = self.root / "target-state"
        config_root = self.root / "target-config"
        config_root.mkdir()
        services = FakeServices()
        transition = ActivationManager(
            store=target.store, workspace=target.workspace, service_controller=services,
        ).cloud_recovery_review()
        cloud_fence = {
            "workspace": "ws_fixture",
            "installation_id": "33333333-3333-4333-8333-333333333333",
            "authority_generation": 7,
        }
        proof = {
            "schema_version": 1, **cloud_fence, "executor_mode": "local", "lease_proven_live": True,
            "transition": {"purpose": "migrate", "reviewSha256": transition["recoveryReviewSha256"]},
        }
        with (
            patch.dict(os.environ, {"POSTSTEWARD_REQUIRE_CLOUD_FENCE": "1", "POSTSTEWARD_RUNTIME_RELEASE_SHA": REVISION}),
            patch("ocpf_post.poststeward_cloud.activation_executor_proof", return_value=proof),
        ):
            preview = target.activation_preview(
                runtime_root=ROOT, state_root=state_root, config_root=config_root, service_controller=services,
            )
            self.assertEqual(preview["cloud_executor"], proof)
            # A new cloud generation invalidates the exact owner review before any service mutation.
            proof["authority_generation"] = 8
            with self.assertRaises(SetupEngineError) as caught:
                target.activate(
                    runtime_root=ROOT, state_root=state_root, config_root=config_root,
                    expected_sha256=preview["review_sha256"], service_controller=services,
                )
            self.assertEqual(caught.exception.code, "activation.review.changed")
            self.assertFalse(services.staged)
            self.assertFalse(automation_authority.path(state_root).exists())
            proof["authority_generation"] = 7
            active = target.activate(
                runtime_root=ROOT, state_root=state_root, config_root=config_root,
                expected_sha256=preview["review_sha256"], service_controller=services,
            )
        marker = automation_authority.read(state_root)
        self.assertEqual(marker["installation_id"], before["installation"]["installation_id"])
        self.assertEqual(marker["authority_generation"], 2)
        self.assertNotEqual(marker["installation_id"], cloud_fence["installation_id"])
        self.assertEqual(marker.get("cloud_executor"), cloud_fence)
        self.assertEqual(active["authority"]["cloud_executor"], cloud_fence)
        cloud = {
            "schemaVersion": 1, "workspace": "ws_fixture", "installationId": cloud_fence["installation_id"],
            "executor": {"executorMode": "local", "executorStatus": "active",
                         "activeInstallationId": cloud_fence["installation_id"], "authorityGeneration": 7,
                         "leaseExpiresAt": 2000000000000},
        }
        with patch("ocpf_post.poststeward_product.time.time", return_value=1800000000):
            self.assertEqual(_compatibility(marker, cloud, REVISION)["status"], "compatible")

    def test_deactivation_preserves_history_and_old_parser_compatibility_even_during_outage(self) -> None:
        import importlib.util

        # Verbatim parser from the rollback target 9fd549bea3c094abe38a75b331a3c60967fdad7b.
        import hashlib
        parser_path = Path(__file__).parent / "fixtures" / "automation_authority_9fd549b.py"
        self.assertEqual(hashlib.sha256(parser_path.read_bytes()).hexdigest(),
                         "caaae4fe5fab3a6f9520f2d95b7d855613a5e0f9f3f9271eff42363d120d10b0")
        spec = importlib.util.spec_from_file_location("prior_stable_authority", parser_path)
        assert spec is not None and spec.loader is not None
        prior = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(prior)
        target, _ = self.migration_target()
        state_root, config_root = self.root / "target-state", self.root / "target-config"
        config_root.mkdir()
        services = MarkerAwareServices(state_root)
        transition = ActivationManager(
            store=target.store, workspace=target.workspace, service_controller=services,
        ).cloud_recovery_review()
        reviewed_fences = []
        retained_receipts = None
        for generation, outage in ((7, False), (8, True)):
            with self.subTest(outage=outage):
                fence = {
                    "workspace": "ws_fixture", "installation_id": "33333333-3333-4333-8333-333333333333",
                    "authority_generation": generation,
                }
                proof = {
                    "schema_version": 1, **fence, "executor_mode": "local", "lease_proven_live": True,
                    "transition": {"purpose": "migrate", "reviewSha256": transition["recoveryReviewSha256"]},
                }
                with (
                    patch.dict(os.environ, {"POSTSTEWARD_REQUIRE_CLOUD_FENCE": "1", "POSTSTEWARD_RUNTIME_RELEASE_SHA": REVISION}),
                    patch("ocpf_post.poststeward_cloud.activation_executor_proof", return_value=proof),
                ):
                    preview = target.activation_preview(
                        runtime_root=ROOT, state_root=state_root, config_root=config_root, service_controller=services,
                    )
                    arm = services.arm

                    def arm_after_durable_proof():
                        committed = [event for event in target.store.events(preview["session_id"]) if event["event_type"] in {
                            "activation_stage_committed", "activation_authority_reconfirmed",
                        }]
                        self.assertEqual(committed[-1]["evidence"].get("cloud_executor"), fence)
                        return arm()

                    with patch.object(services, "arm", side_effect=arm_after_durable_proof):
                        active = target.activate(
                            runtime_root=ROOT, state_root=state_root, config_root=config_root,
                            expected_sha256=preview["review_sha256"], service_controller=services,
                        )
                    with self.assertRaises(prior.AutomationAuthorityError):
                        prior.read(state_root)
                    receipts = state_root / "publish-receipts.jsonl"
                    if retained_receipts is not None:
                        self.assertEqual(receipts.read_bytes(), retained_receipts)
                    with receipts.open("a", encoding="utf-8") as handle:
                        handle.write(json.dumps({"provider": "threads", "status": "published_verified", "fixture_generation": generation}) + "\n")
                    receipt_bytes = retained_receipts = receipts.read_bytes()
                    off = target.deactivation_preview(
                        runtime_root=ROOT, state_root=state_root, reason="rollback check", service_controller=services,
                    )
                    with patch(
                        "ocpf_post.poststeward_cloud.deactivate_executor_fence",
                        side_effect=OSError("offline") if outage else None,
                        return_value={"executorMode": "local", "executorStatus": "inactive", "authorityGeneration": generation + 1},
                    ):
                        inactive = target.deactivate(
                            runtime_root=ROOT, state_root=state_root, reason="rollback check",
                            expected_sha256=off["review_sha256"], service_controller=services,
                        )
                self.assertEqual(prior.read(state_root)["status"], "inactive")
                with self.assertRaises(prior.AutomationAuthorityError):
                    prior.require_active(state_root)
                self.assertNotIn("cloud_executor", inactive["authority"])
                self.assertTrue(services.marker_was_inactive_before_disarm)
                self.assertEqual(inactive["cloud_fence"]["status"], "attention" if outage else "inactive")
                self.assertEqual((state_root / "publish-receipts.jsonl").read_bytes(), receipt_bytes)
                reviewed_fences.append(fence)
                events = target.store.events(active["session"]["session_id"])
                for event_type in ("automation_activated", "automation_deactivated"):
                    self.assertEqual(
                        [event["evidence"].get("cloud_executor") for event in events if event["event_type"] == event_type],
                        reviewed_fences,
                    )
                commits = [event for event in events if event["event_type"] in {
                    "activation_stage_committed", "activation_authority_reconfirmed",
                }]
                self.assertEqual(commits[-1]["evidence"].get("cloud_executor"), fence)

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

    def test_unverified_cloud_fence_ack_closes_local_gate_without_false_success(self) -> None:
        target, _before = self.migration_target()
        state_root = self.root / "target-state"
        config_root = self.root / "target-config"
        config_root.mkdir()
        services = MarkerAwareServices(state_root)
        preview = target.activation_preview(
            runtime_root=ROOT, state_root=state_root, config_root=config_root,
            service_controller=services,
        )
        target.activate(
            runtime_root=ROOT, state_root=state_root, config_root=config_root,
            expected_sha256=preview["review_sha256"], service_controller=services,
        )
        installation = "33333333-3333-4333-8333-333333333333"
        off_preview = target.deactivation_preview(
            runtime_root=ROOT, state_root=state_root, reason="network ambiguity",
            service_controller=services,
        )
        with (
            patch.dict(os.environ, {"POSTSTEWARD_REQUIRE_CLOUD_FENCE": "1"}),
            patch("ocpf_post.poststeward_cloud.bindings", return_value={
                "executor": {
                    "executorMode": "local", "executorStatus": "active",
                    "activeInstallationId": installation, "authorityGeneration": 7,
                },
            }),
            patch("ocpf_post.poststeward_cloud.installation_identity",
                  return_value={"installation_id": installation}),
            patch("ocpf_post.poststeward_cloud.runtime_token", return_value="fixture-token"),
            patch("ocpf_post.poststeward_cloud._request", return_value=(200, {
                "executorMode": "local", "executorStatus": "active",
                "activeInstallationId": installation, "authorityGeneration": 7,
            })) as remote,
        ):
            inactive = target.deactivate(
                runtime_root=ROOT, state_root=state_root, reason="network ambiguity",
                expected_sha256=off_preview["review_sha256"],
                service_controller=services,
            )
        remote.assert_called_once()
        self.assertEqual(inactive["status"], "inactive")
        self.assertEqual(inactive["cloud_fence"]["status"], "attention")
        self.assertEqual(inactive["cloud_fence"]["error_code"],
                         "RUNTIME_EXECUTOR_FENCE_UNVERIFIED")
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
        env["POSTSTEWARD_RUNTIME_STATE_DIR"] = str(state)
        env["POSTSTEWARD_RUNTIME_CONFIG_DIR"] = str(config)
        env["POSTSTEWARD_SETUP_STATE_DIR"] = str(self.root / "unattended-setup")
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
