#!/usr/bin/env python3
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import tempfile

from ocpf_post import automation_authority
from ocpf_post.setup_activation import _exclusive_activation_lock
from ocpf_post.setup_engine import SetupEngine, SetupEngineError

ROOT = Path(__file__).resolve().parents[1]
REVISION = "a" * 40
QUIESCENT = {
    "schema_version": 1,
    "status": "quiescent",
    "units": [],
    "active_writer_units": [],
    "mutation_attempted": False,
    "boundary": "clean-room fixture",
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
    future = (datetime.now(timezone.utc) + timedelta(days=2)).replace(microsecond=0)
    write_jsonl(state / "schedule-events.jsonl", [{
        "schedule_id": "sch_future",
        "event": "scheduled",
        "status": "scheduled",
        "recorded_at": "2026-09-26T12:00:00Z",
        "run_at": future.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "provider": "x",
        "account_id": "123456",
        "campaign": "EXAMPLE-1",
    }])
    write_jsonl(state / "publish-receipts.jsonl", [{
        "campaign": "OLD-1",
        "provider": "x",
        "status": "published_verified",
        "recorded_at": "2026-09-25T12:00:00Z",
    }])
    (config / "portfolio-policy.json").write_text('{"schema_version":1,"daily_target":5}\n', encoding="utf-8")
    (config / "account-profiles.json").write_text(
        '{"schema_version":1,"accounts":{"x-main":{"provider":"x","account_id":"123456"}}}\n',
        encoding="utf-8",
    )
    (config / "x-token.json").write_text('{"access_token":"SOURCE_SECRET"}\n', encoding="utf-8")
    return state, config


class FakeServices:
    def __init__(self, state_root: Path) -> None:
        self.state_root = state_root
        self.enabled = False
        self.active = False
        self.marker_inactive_before_disarm = False

    def inspect(self) -> dict:
        return {
            "schema_version": 1,
            "timers": [{"unit": "fixture.timer", "enabled": self.enabled, "active": self.active}],
            "all_enabled": self.enabled,
            "all_active": self.active,
        }

    def stage(self, **_kwargs) -> dict:
        return {"status": "staged", "provider_consequence": False}

    def preflight(self, **_kwargs) -> dict:
        return {"status": "passed", "checks": [], "provider_consequence": False}

    def arm(self) -> dict:
        self.enabled = True
        self.active = True
        return self.inspect()

    def disarm(self) -> dict:
        self.marker_inactive_before_disarm = (
            automation_authority.read(self.state_root).get("status") != "active"
        )
        self.enabled = False
        self.active = False
        return self.inspect()


def readiness() -> list[dict]:
    return [{
        "provider": "x",
        "expected_identity": "123456",
        "observed_identity": "123456",
        "identity_match": "match",
        "ready_for_write_configuration": True,
        "blocking_reasons": [],
    }]


def main() -> None:
    with tempfile.TemporaryDirectory(prefix="post-once-h-acceptance-") as temp:
        root = Path(temp)
        source_state, source_config = source_tree(root)

        source = SetupEngine(root / "source-bootstrap")
        source.start("migrate", operator_label="CI Example", machine_label="old-host")
        preview = source.export_migration(
            state_root=source_state,
            config_root=source_config,
            output=root / "transfer.tar.gz",
            source_post_once_version="0.28.29",
            source_revision=REVISION,
            unit_observation=QUIESCENT,
        )
        sealed = source.export_migration(
            state_root=source_state,
            config_root=source_config,
            output=root / "transfer.tar.gz",
            source_post_once_version="0.28.29",
            source_revision=REVISION,
            apply=True,
            expected_sha256=preview["migration_export"]["review_sha256"],
            unit_observation=QUIESCENT,
        )

        target = SetupEngine(root / "target-bootstrap")
        target.restore_migration(
            root / "transfer.tar.gz",
            expected_bundle_sha256=sealed["migration_export"]["bundle_sha256"],
            machine_label="new-host",
        )
        target.verify_migration(readiness())

        target_state = root / "target-state"
        target_config = root / "target-config"
        target_config.mkdir()
        machine_secret = target_config / "machine-token.json"
        machine_secret.write_text('{"access_token":"TARGET_ONLY"}\n', encoding="utf-8")
        services = FakeServices(target_state)

        activation_preview = target.activation_preview(
            runtime_root=ROOT,
            state_root=target_state,
            config_root=target_config,
            service_controller=services,
        )
        concurrent_activation_blocked = False
        with _exclusive_activation_lock(target.workspace, action="clean-room-holder"):
            try:
                target.activate(
                    runtime_root=ROOT,
                    state_root=target_state,
                    config_root=target_config,
                    expected_sha256=activation_preview["review_sha256"],
                    service_controller=services,
                )
            except SetupEngineError as exc:
                concurrent_activation_blocked = (
                    exc.code == "activation.concurrent_operation"
                )

        active = target.activate(
            runtime_root=ROOT,
            state_root=target_state,
            config_root=target_config,
            expected_sha256=activation_preview["review_sha256"],
            service_controller=services,
        )

        deactivation_preview = target.deactivation_preview(
            runtime_root=ROOT,
            state_root=target_state,
            reason="CI deactivation drill",
            service_controller=services,
        )
        inactive = target.deactivate(
            runtime_root=ROOT,
            state_root=target_state,
            reason="CI deactivation drill",
            expected_sha256=deactivation_preview["review_sha256"],
            service_controller=services,
        )

        reactivation_preview = target.activation_preview(
            runtime_root=ROOT,
            state_root=target_state,
            config_root=target_config,
            service_controller=services,
        )
        reactivated = target.activate(
            runtime_root=ROOT,
            state_root=target_state,
            config_root=target_config,
            expected_sha256=reactivation_preview["review_sha256"],
            service_controller=services,
        )

        # Dead-host activation is a separate clean-room case.  It must
        # remain blocked until every G recovery-review requirement has a
        # structured, code-specific resolution.
        recovery_case = root / "recovery-case"
        recovery_case.mkdir()
        recovery_state, recovery_config = source_tree(recovery_case)
        recovery_source = SetupEngine(root / "recovery-source-bootstrap")
        recovery_source.start("migrate", operator_label="CI Recovery", machine_label="lost-host")
        recovery_preview = recovery_source.recovery_point(
            state_root=recovery_state,
            config_root=recovery_config,
            output=root / "recovery.tar.gz",
            source_post_once_version="0.28.29",
            source_revision=REVISION,
            unit_observation=QUIESCENT,
        )
        recovery_sealed = recovery_source.recovery_point(
            state_root=recovery_state,
            config_root=recovery_config,
            output=root / "recovery.tar.gz",
            source_post_once_version="0.28.29",
            source_revision=REVISION,
            apply=True,
            expected_sha256=recovery_preview["recovery_point_export"]["review_sha256"],
            unit_observation=QUIESCENT,
        )
        recovery_inspected = SetupEngine(root / "recovery-inspect-bootstrap").inspect_bundle(
            root / "recovery.tar.gz",
            expected_bundle_sha256=recovery_sealed["recovery_point_export"]["bundle_sha256"],
            recovery=True,
        )
        captured = datetime.fromisoformat(recovery_inspected["sealed_at"].replace("Z", "+00:00"))
        lost = captured + timedelta(minutes=2)
        recovery_target = SetupEngine(root / "recovery-target-bootstrap")
        recovery_target.restore_recovery(
            root / "recovery.tar.gz",
            source_lost_at=lost.strftime("%Y-%m-%dT%H:%M:%SZ"),
            max_data_loss_minutes=5,
            expected_bundle_sha256=recovery_sealed["recovery_point_export"]["bundle_sha256"],
            machine_label="replacement-host",
        )
        recovery_verified = recovery_target.verify_recovery(readiness())
        recovered_state = root / "recovered-state"
        recovered_config = root / "recovered-config"
        recovery_services = FakeServices(recovered_state)

        recovery_blocked_without_resolution = False
        try:
            recovery_target.activation_preview(
                runtime_root=ROOT,
                state_root=recovered_state,
                config_root=recovered_config,
                service_controller=recovery_services,
            )
        except SetupEngineError as exc:
            recovery_blocked_without_resolution = (
                exc.code == "activation.recovery_resolution.required"
            )

        review = json.loads(
            Path(recovery_verified["recovery"]["review"]).read_text(encoding="utf-8")
        )
        method_by_code = {}
        for code in review["summary"]["required_blocker_codes"]:
            if code == "authority.source_host.unknown":
                method_by_code[code] = "all_provider_authority_fenced"
            elif code == "authority.recovery_gap.unreconciled":
                method_by_code[code] = "provider_effects_reconciled"
            elif code == "bundle.authenticity.not_provided":
                method_by_code[code] = "trusted_bundle_digest_verified"
            elif code.startswith("provider.stale_authority."):
                method_by_code[code] = "provider_credential_revoked"
            elif code.startswith(
                ("schedule.ambiguous_effect.", "schedule.partial_effect.", "schedule.executing.")
            ):
                method_by_code[code] = "provider_effect_reconciled"
            else:
                raise RuntimeError(f"No clean-room recovery resolution for {code}")

        source_installation_id = review["source_installation_id"]
        observed_at = (lost + timedelta(minutes=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
        resolution = {
            "schema_version": 1,
            "operation_id": recovery_verified["operation"]["operation_id"],
            "source_installation_id": source_installation_id,
            "resolutions": [
                {
                    "code": code,
                    "status": "resolved",
                    "method": method_by_code[code],
                    "observed_at": observed_at,
                    "evidence_ref": f"clean-room:{code}",
                }
                for code in review["summary"]["required_blocker_codes"]
            ],
        }
        recovery_activation_preview = recovery_target.activation_preview(
            runtime_root=ROOT,
            state_root=recovered_state,
            config_root=recovered_config,
            recovery_resolution=resolution,
            service_controller=recovery_services,
        )
        recovery_active = recovery_target.activate(
            runtime_root=ROOT,
            state_root=recovered_state,
            config_root=recovered_config,
            recovery_resolution=resolution,
            expected_sha256=recovery_activation_preview["review_sha256"],
            service_controller=recovery_services,
        )

        result = {
            "schema_version": 1,
            "status": "pass",
            "first_activation_stage": active["session"]["stage"],
            "concurrent_activation_blocked": concurrent_activation_blocked,
            "first_generation": active["operation"]["authority_generation"],
            "state_promoted": (target_state / "publish-receipts.jsonl").is_file(),
            "portable_config_promoted": (target_config / "portfolio-policy.json").is_file(),
            "machine_credential_preserved": machine_secret.is_file() and "TARGET_ONLY" in machine_secret.read_text(encoding="utf-8"),
            "marker_active_after_activation": active["authority"]["status"] == "active",
            "marker_first_deactivation": services.marker_inactive_before_disarm,
            "inactive_after_deactivation": inactive["authority"]["status"] == "inactive",
            "receipts_preserved_after_deactivation": (target_state / "publish-receipts.jsonl").is_file(),
            "reactivation_generation_unchanged": reactivated["operation"]["authority_generation"] == active["operation"]["authority_generation"],
            "reactivated": reactivated["authority"]["status"] == "active",
            "publishing_authority": reactivated["publishing_authority"],
            "automation_enabled": reactivated["automation_enabled"],
            "recovery_blocked_without_resolution": recovery_blocked_without_resolution,
            "recovery_activation_stage": recovery_active["session"]["stage"],
            "recovery_generation": recovery_active["operation"]["authority_generation"],
            "recovery_marker_active": recovery_active["authority"]["status"] == "active",
            "recovery_publishing_authority": recovery_active["publishing_authority"],
            "recovery_automation_enabled": recovery_active["automation_enabled"],
        }
        print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()
