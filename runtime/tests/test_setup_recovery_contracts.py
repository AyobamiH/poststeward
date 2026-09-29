from __future__ import annotations

import json
import unittest
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path

from jsonschema import Draft202012Validator, FormatChecker

from ocpf_post.setup_contracts import (
    SetupStage,
    aggregate_readiness,
    allowed_next_stages,
    new_installation,
    new_operation,
    new_setup_session,
    readiness_check,
    schedule_disposition,
    transition_setup_session,
    validate_installation,
    validate_operation,
    validate_readiness_check,
    validate_setup_session,
)

ROOT = Path(__file__).resolve().parents[1]
SCHEMA_ROOT = ROOT / "schemas" / "setup-recovery" / "v1"
FORMAT_CHECKER = FormatChecker()
NOW = datetime(2026, 9, 25, 10, 0, tzinfo=timezone.utc)


def schema(name: str) -> dict:
    return json.loads((SCHEMA_ROOT / name).read_text(encoding="utf-8"))


def validate_schema(name: str, value: dict) -> None:
    Draft202012Validator(schema(name), format_checker=FORMAT_CHECKER).validate(value)


class SetupRecoverySchemaTests(unittest.TestCase):
    def test_schema_documents_are_valid_draft_2020_12(self):
        for path in sorted(SCHEMA_ROOT.glob("*.schema.json")):
            with self.subTest(path=path.name):
                Draft202012Validator.check_schema(json.loads(path.read_text(encoding="utf-8")))

    def test_factories_and_projections_match_public_schemas(self):
        operation = new_operation("Proof & State")
        installation = new_installation(operation["operation_id"], machine_label="replacement laptop")
        session = new_setup_session("fresh", operation_id=operation["operation_id"], now=NOW)
        check = readiness_check(
            code="software.python.supported",
            status="ready",
            severity="info",
            required=True,
            scope="software",
            evidence_method="runtime_version",
            summary="Supported Python runtime observed",
            consequence_boundary="local setup only",
            evidence={"major": 3, "minor": 10},
            observed_at=NOW,
        )
        summary = aggregate_readiness([check], mode="fresh")
        disposition = schedule_disposition(
            {
                "schedule_id": "sch_future",
                "status": "scheduled",
                "run_at": "2026-09-25T12:00:00Z",
            },
            context="healthy_migration",
            now=NOW,
        )

        validate_schema("operation.schema.json", operation)
        validate_schema("installation.schema.json", installation)
        validate_schema("setup-session.schema.json", session)
        validate_schema("readiness-check.schema.json", check)
        validate_schema("readiness-summary.schema.json", summary)
        validate_schema("schedule-disposition.schema.json", disposition)

    def test_operation_and_installation_authority_invariants(self):
        operation = new_operation("Owner")
        validate_operation(operation)

        invalid_active = deepcopy(operation)
        invalid_active["status"] = "active"
        with self.assertRaisesRegex(ValueError, "active installation"):
            validate_operation(invalid_active)

        installation = new_installation(operation["operation_id"])
        validate_installation(installation)
        invalid_candidate = deepcopy(installation)
        invalid_candidate["authority_generation"] = 1
        with self.assertRaisesRegex(ValueError, "Candidate"):
            validate_installation(invalid_candidate)

        valid_active = deepcopy(installation)
        valid_active["status"] = "active"
        valid_active["authority_generation"] = 1
        validate_installation(valid_active)
        validate_schema("installation.schema.json", valid_active)


class SetupStateMachineTests(unittest.TestCase):
    def _drive_to_terminal(self, mode: str) -> dict:
        session = new_setup_session(mode, now=NOW)
        while allowed_next_stages(session):
            next_stage = next(stage for stage in allowed_next_stages(session) if stage != SetupStage.ABORTED.value)
            session = transition_setup_session(session, next_stage, now=NOW)
        return session

    def test_each_mode_has_one_safe_forward_path(self):
        for mode in ("fresh", "different_operator", "migrate", "recover"):
            with self.subTest(mode=mode):
                session = self._drive_to_terminal(mode)
                self.assertEqual(session["stage"], "active")
                self.assertEqual(session["session_status"], "completed")
                validate_setup_session(session)
                validate_schema("setup-session.schema.json", session)

        explore = self._drive_to_terminal("explore")
        self.assertEqual(explore["stage"], "explore_ready")
        self.assertEqual(explore["session_status"], "completed")

    def test_cannot_skip_directly_to_activation_or_active(self):
        session = new_setup_session("fresh", now=NOW)
        for target in ("activation_ready", "active"):
            with self.subTest(target=target):
                with self.assertRaisesRegex(ValueError, "Illegal setup transition"):
                    transition_setup_session(session, target, now=NOW)

    def test_transition_revision_and_identity_are_monotonic_and_explicit(self):
        session = new_setup_session("fresh", now=NOW)
        first = transition_setup_session(session, "preflight_ready", now=NOW)
        second = transition_setup_session(first, "operation_ready", now=NOW)

        self.assertEqual(first["revision"], 2)
        self.assertEqual(second["revision"], 3)
        self.assertNotEqual(first["last_transition_id"], second["last_transition_id"])
        self.assertEqual(first["previous_stage"], "created")
        self.assertEqual(second["previous_stage"], "preflight_ready")

    def test_abort_is_terminal_without_becoming_active(self):
        session = new_setup_session("recover", now=NOW)
        aborted = transition_setup_session(session, "aborted", now=NOW)
        self.assertEqual(aborted["session_status"], "aborted")
        self.assertEqual(allowed_next_stages(aborted), ())
        with self.assertRaisesRegex(ValueError, "Illegal setup transition"):
            transition_setup_session(aborted, "active", now=NOW)


class ReadinessTests(unittest.TestCase):
    def _check(self, code: str, status: str, *, required: bool = True, severity: str = "blocker") -> dict:
        return readiness_check(
            code=code,
            status=status,
            severity=severity,
            required=required,
            scope="test",
            evidence_method="fixture",
            summary="Fixture check",
            consequence_boundary="test boundary",
            observed_at=NOW,
        )

    def test_ready_and_optional_gap_aggregation(self):
        ready = self._check("software.python.supported", "ready", severity="info")
        self.assertEqual(aggregate_readiness([ready], mode="fresh")["status"], "READY")

        optional = self._check("provider.readback.optional_missing", "unknown", required=False, severity="attention")
        value = aggregate_readiness([ready, optional], mode="fresh")
        self.assertEqual(value["status"], "READY_WITH_OPTIONAL_GAPS")
        self.assertEqual(value["attention_codes"], ["provider.readback.optional_missing"])

    def test_required_routine_unknown_blocks(self):
        blocked = self._check("provider.identity.mismatch", "unknown")
        value = aggregate_readiness([blocked], mode="fresh")
        self.assertEqual(value["status"], "BLOCKED")
        self.assertEqual(value["required_blocker_codes"], ["provider.identity.mismatch"])

    def test_recovery_sensitive_unknown_requires_recovery_review_only_in_recovery(self):
        stale = self._check("provider.stale_authority.unresolved", "unknown")
        self.assertEqual(aggregate_readiness([stale], mode="recover")["status"], "RECOVERY_REVIEW_REQUIRED")
        self.assertEqual(aggregate_readiness([stale], mode="migrate")["status"], "BLOCKED")

    def test_explore_mode_never_reports_automation_ready(self):
        ready = self._check("software.python.supported", "ready", severity="info")
        self.assertEqual(aggregate_readiness([ready], mode="explore")["status"], "EXPLORE_ONLY")

    def test_readiness_evidence_is_scalar_bounded(self):
        invalid = self._check("software.python.supported", "ready", severity="info")
        invalid["evidence"] = {"nested": {"secret": "not allowed"}}
        with self.assertRaisesRegex(ValueError, "JSON scalars"):
            validate_readiness_check(invalid)

        nonfinite = self._check("software.disk.free", "ready", severity="info")
        nonfinite["evidence"] = {"ratio": float("nan")}
        with self.assertRaisesRegex(ValueError, "finite"):
            validate_readiness_check(nonfinite)

    def test_duplicate_reason_codes_are_rejected(self):
        row = self._check("software.python.supported", "ready", severity="info")
        with self.assertRaisesRegex(ValueError, "Duplicate readiness reason code"):
            aggregate_readiness([row, deepcopy(row)], mode="fresh")


class ScheduleDispositionTests(unittest.TestCase):
    def test_terminal_effects_never_rearm(self):
        expected = {
            "published_verified": "historical_only",
            "ambiguous_effect": "terminal_review",
            "partial_effect": "terminal_review",
        }
        for status, disposition in expected.items():
            with self.subTest(status=status):
                value = schedule_disposition(
                    {"schedule_id": "sch_terminal", "status": status, "run_at": "2026-09-25T09:00:00Z"},
                    context="recovery",
                    now=NOW,
                )
                self.assertFalse(value["actionable_after_restore"])
                self.assertFalse(value["eligible_for_rearm"])
                self.assertFalse(value["automatic_retry_safe"])
                self.assertEqual(value["disposition"], disposition)

    def test_executing_blocks_healthy_seal_and_quarantines_recovery(self):
        record = {"schedule_id": "sch_exec", "status": "executing", "run_at": "2026-09-25T09:00:00Z"}
        self.assertEqual(
            schedule_disposition(record, context="healthy_migration", now=NOW)["disposition"],
            "block_sealing",
        )
        self.assertEqual(
            schedule_disposition(record, context="recovery", now=NOW)["disposition"],
            "recovery_quarantine",
        )

    def test_future_and_overdue_schedules_are_distinct(self):
        future = schedule_disposition(
            {"schedule_id": "sch_future", "status": "scheduled", "run_at": "2026-09-25T12:00:00Z"},
            context="healthy_migration",
            now=NOW,
        )
        overdue = schedule_disposition(
            {"schedule_id": "sch_overdue", "status": "scheduled", "run_at": "2026-09-25T08:00:00Z"},
            context="healthy_migration",
            now=NOW,
        )
        self.assertEqual(future["disposition"], "held_future")
        self.assertTrue(future["eligible_for_rearm"])
        self.assertEqual(overdue["disposition"], "handoff_expired")
        self.assertFalse(overdue["eligible_for_rearm"])

    def test_typed_preconsequence_deferral_is_recomputed_not_replayed(self):
        value = schedule_disposition(
            {
                "schedule_id": "sch_deferred",
                "status": "scheduled",
                "run_at": "2026-09-25T09:00:00Z",
                "failure_class": "provider_unavailable",
                "retry_at": "2026-09-25T11:00:00Z",
            },
            context="healthy_migration",
            now=NOW,
        )
        self.assertEqual(value["disposition"], "hold_recompute")
        self.assertTrue(value["eligible_for_rearm"])
        self.assertFalse(value["automatic_retry_safe"])

    def test_unknown_or_malformed_schedule_fails_closed(self):
        unknown = schedule_disposition(
            {"schedule_id": "sch_unknown", "status": "new_future_state"},
            context="recovery",
            now=NOW,
        )
        malformed = schedule_disposition(
            {"schedule_id": "sch_bad_time", "status": "scheduled", "run_at": "not-a-time"},
            context="recovery",
            now=NOW,
        )
        self.assertEqual(unknown["disposition"], "blocked_unknown")
        self.assertEqual(malformed["disposition"], "blocked_unknown")
        self.assertTrue(unknown["requires_review"])
        self.assertTrue(malformed["requires_review"])


if __name__ == "__main__":
    unittest.main()
