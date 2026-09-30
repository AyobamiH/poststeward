#!/usr/bin/env python3
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import tempfile

from ocpf_post.setup_engine import SetupEngine

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


def main() -> None:
    with tempfile.TemporaryDirectory(prefix="post-once-g-acceptance-") as temp:
        root = Path(temp)
        production_state = root / "production-state"
        production_config = root / "production-config"
        production_state.mkdir()
        production_config.mkdir()
        state_marker = production_state / "DO_NOT_TOUCH"
        config_marker = production_config / "DO_NOT_TOUCH"
        state_marker.write_text("unchanged\n", encoding="utf-8")
        config_marker.write_text("unchanged\n", encoding="utf-8")
        before_state = state_marker.read_bytes()
        before_config = config_marker.read_bytes()

        source_state = root / "source-state"
        source_config = root / "source-config"
        source_state.mkdir()
        source_config.mkdir()
        future = (datetime.now(timezone.utc) + timedelta(days=2)).replace(microsecond=0)
        write_jsonl(
            source_state / "schedule-events.jsonl",
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
            source_state / "publish-receipts.jsonl",
            [{
                "campaign": "OLD-1",
                "provider": "x",
                "status": "published_verified",
                "recorded_at": "2026-09-25T12:00:00Z",
            }],
        )
        (source_config / "portfolio-policy.json").write_text(
            '{"schema_version":1,"daily_target":5}\n',
            encoding="utf-8",
        )
        (source_config / "account-profiles.json").write_text(
            '{"schema_version":1,"accounts":{"x-main":{"provider":"x","account_id":"123456"}}}\n',
            encoding="utf-8",
        )
        (source_config / "x-token.json").write_text(
            '{"access_token":"RECOVERY_SECRET_MUST_NOT_TRANSFER"}\n',
            encoding="utf-8",
        )

        old_state = os.environ.get("OCPF_POST_STATE_DIR")
        old_config = os.environ.get("OCPF_POST_CONFIG_DIR")
        try:
            os.environ["OCPF_POST_STATE_DIR"] = str(production_state)
            os.environ["OCPF_POST_CONFIG_DIR"] = str(production_config)

            source = SetupEngine(root / "source-bootstrap")
            started = source.start("migrate", operator_label="CI Example", machine_label="old-host")
            preview = source.recovery_point(
                state_root=source_state,
                config_root=source_config,
                output=root / "recovery.tar.gz",
                source_post_once_version="0.28.29",
                source_revision=REVISION,
                unit_observation=QUIESCENT,
            )
            sealed = source.recovery_point(
                state_root=source_state,
                config_root=source_config,
                output=root / "recovery.tar.gz",
                source_post_once_version="0.28.29",
                source_revision=REVISION,
                apply=True,
                expected_sha256=preview["recovery_point_export"]["review_sha256"],
                unit_observation=QUIESCENT,
            )
            source_after = source.status(started["session"]["session_id"])

            inspector = SetupEngine(root / "inspect-bootstrap")
            inspected = inspector.inspect_bundle(
                root / "recovery.tar.gz",
                expected_bundle_sha256=sealed["recovery_point_export"]["bundle_sha256"],
                recovery=True,
            )
            captured = datetime.fromisoformat(inspected["sealed_at"].replace("Z", "+00:00"))
            source_lost_at = (captured + timedelta(minutes=3)).strftime("%Y-%m-%dT%H:%M:%SZ")

            target = SetupEngine(root / "target-bootstrap")
            restored = target.restore_recovery(
                root / "recovery.tar.gz",
                source_lost_at=source_lost_at,
                max_data_loss_minutes=5,
                expected_bundle_sha256=sealed["recovery_point_export"]["bundle_sha256"],
                machine_label="replacement-host",
            )
            verified = target.verify_recovery([{
                "provider": "x",
                "expected_identity": "123456",
                "observed_identity": "123456",
                "identity_match": "match",
                "ready_for_write_configuration": True,
                "blocking_reasons": [],
                "recovery_fencing_strength": "assisted",
                "optional_gaps": ["provider.stale_authority.unresolved"],
            }])
        finally:
            if old_state is None:
                os.environ.pop("OCPF_POST_STATE_DIR", None)
            else:
                os.environ["OCPF_POST_STATE_DIR"] = old_state
            if old_config is None:
                os.environ.pop("OCPF_POST_CONFIG_DIR", None)
            else:
                os.environ["OCPF_POST_CONFIG_DIR"] = old_config

        raw = (root / "recovery.tar.gz").read_bytes()
        review = json.loads(Path(verified["recovery"]["review"]).read_text(encoding="utf-8"))
        codes = {row["code"] for row in review["checks"]}
        result = {
            "schema_version": 1,
            "status": "pass",
            "source_remained_active": (
                source_after["operation"]["status"] == "active"
                and source_after["installation"]["status"] == "active"
            ),
            "recovery_point_bundle_purpose": inspected["bundle_purpose"],
            "target_restore_stage": restored["session"]["stage"],
            "target_review_stage": verified["session"]["stage"],
            "target_operation_status": verified["operation"]["status"],
            "target_installation_status": verified["installation"]["status"],
            "same_operation": started["operation"]["operation_id"] == verified["operation"]["operation_id"],
            "new_installation": started["installation"]["installation_id"] != verified["installation"]["installation_id"],
            "recovery_status": verified["recovery"]["readiness_status"],
            "source_unknown_present": "authority.source_host.unknown" in codes,
            "recovery_gap_present": "authority.recovery_gap.unreconciled" in codes,
            "stale_authority_present": "provider.stale_authority.x_unresolved" in codes,
            "digest_pin_present": "bundle.authenticity.digest_pinned" in codes,
            "publishing_authority": verified["publishing_authority"],
            "automation_enabled": verified["automation_enabled"],
            "production_state_unchanged": state_marker.read_bytes() == before_state,
            "production_config_unchanged": config_marker.read_bytes() == before_config,
            "credential_bytes_absent_from_bundle": b"RECOVERY_SECRET_MUST_NOT_TRANSFER" not in raw,
            "activation_requires_recovery_resolution": "activation.recovery_resolution.required" in verified["blockers"],
        }
        print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()
