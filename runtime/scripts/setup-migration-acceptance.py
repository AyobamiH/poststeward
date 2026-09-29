#!/usr/bin/env python3
from __future__ import annotations

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
    with tempfile.TemporaryDirectory(prefix="post-once-f-acceptance-") as temp:
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
        write_jsonl(
            source_state / "schedule-events.jsonl",
            [{
                "schedule_id": "sch_future",
                "event": "scheduled",
                "status": "scheduled",
                "recorded_at": "2026-09-26T12:00:00Z",
                "run_at": "2030-09-30T12:00:00Z",
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
            '{"schema_version":1,"daily_target":5}\n', encoding="utf-8"
        )
        (source_config / "x-token.json").write_text(
            '{"access_token":"SECRET_MUST_NOT_TRANSFER"}\n', encoding="utf-8"
        )

        old_state = os.environ.get("OCPF_POST_STATE_DIR")
        old_config = os.environ.get("OCPF_POST_CONFIG_DIR")
        try:
            os.environ["OCPF_POST_STATE_DIR"] = str(production_state)
            os.environ["OCPF_POST_CONFIG_DIR"] = str(production_config)

            source = SetupEngine(root / "source-bootstrap")
            started = source.start("migrate", operator_label="CI Example", machine_label="old-host")
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
            restored = target.restore_migration(
                root / "transfer.tar.gz",
                expected_bundle_sha256=sealed["migration_export"]["bundle_sha256"],
                machine_label="new-host",
            )
            verified = target.verify_migration([{
                "provider": "x",
                "expected_identity": "123456",
                "observed_identity": "123456",
                "identity_match": "match",
                "ready_for_write_configuration": True,
                "blocking_reasons": [],
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

        bundle_raw = (root / "transfer.tar.gz").read_bytes()
        result = {
            "schema_version": 1,
            "status": "pass",
            "source_stage": sealed["session"]["stage"],
            "source_operation_status": sealed["operation"]["status"],
            "source_installation_status": sealed["installation"]["status"],
            "target_restore_stage": restored["session"]["stage"],
            "target_verified_stage": verified["session"]["stage"],
            "same_operation": started["operation"]["operation_id"] == verified["operation"]["operation_id"],
            "new_installation": started["installation"]["installation_id"] != verified["installation"]["installation_id"],
            "publishing_authority": verified["publishing_authority"],
            "automation_enabled": verified["automation_enabled"],
            "production_state_unchanged": state_marker.read_bytes() == before_state,
            "production_config_unchanged": config_marker.read_bytes() == before_config,
            "credential_bytes_absent_from_bundle": b"SECRET_MUST_NOT_TRANSFER" not in bundle_raw,
            "activation_preview_available": verified["next_actions"] == ["preview_activation_gate"],
        }
        print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()
