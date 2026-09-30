from __future__ import annotations

import json
import os
from pathlib import Path
import tempfile
import unittest

from jsonschema import Draft202012Validator, FormatChecker

from ocpf_post.setup_engine import (
    HARD_CEILING,
    PACE_PROFILES,
    SetupEngine,
    SetupEngineError,
    resolve_pace,
    validate_timezone,
)


class SetupEngineTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.env = {
            name: os.environ.get(name)
            for name in ("OCPF_POST_STATE_DIR", "OCPF_POST_CONFIG_DIR", "OCPF_POST_SETUP_STATE_DIR")
        }
        os.environ["OCPF_POST_STATE_DIR"] = str(self.root / "production-state")
        os.environ["OCPF_POST_CONFIG_DIR"] = str(self.root / "production-config")
        os.environ.pop("OCPF_POST_SETUP_STATE_DIR", None)

    def tearDown(self) -> None:
        for name, value in self.env.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value
        self.tmp.cleanup()

    def engine(self, name: str = "setup") -> SetupEngine:
        return SetupEngine(self.root / name)

    def test_pace_profiles_are_explicit_and_separate_from_hard_ceiling(self) -> None:
        self.assertEqual(PACE_PROFILES, {
            "occasional": 3,
            "regular": 5,
            "active": 10,
            "high": 20,
        })
        self.assertEqual(HARD_CEILING, 100)
        self.assertEqual(resolve_pace("regular"), ("regular", 5))
        self.assertEqual(resolve_pace("custom", 17), ("custom", 17))
        with self.assertRaises(SetupEngineError) as caught:
            resolve_pace("", None)
        self.assertEqual(caught.exception.code, "setup.pace.required")
        with self.assertRaises(SetupEngineError):
            resolve_pace("custom", 101)
        with self.assertRaises(SetupEngineError):
            resolve_pace("active", 10)

    def test_timezone_must_be_valid_iana_zone(self) -> None:
        self.assertEqual(validate_timezone("Europe/London"), "Europe/London")
        with self.assertRaises(SetupEngineError) as caught:
            validate_timezone("Not/AZone")
        self.assertEqual(caught.exception.code, "setup.timezone.invalid")

    def test_fresh_noninteractive_setup_stops_at_configuration_ready(self) -> None:
        value = self.engine().start(
            "fresh",
            operator_label="Example Ltd",
            timezone="Europe/London",
            pace="regular",
        )
        self.assertEqual(value["session"]["stage"], "configuration_ready")
        self.assertEqual(value["session"]["session_status"], "open")
        self.assertEqual(value["operation"]["status"], "prepared")
        self.assertIsNone(value["operation"]["active_installation_id"])
        self.assertEqual(value["installation"]["status"], "candidate")
        self.assertIsNone(value["installation"]["authority_generation"])
        self.assertEqual(value["configuration"]["daily_originals"], 5)
        self.assertEqual(value["configuration"]["hard_ceiling"], 100)
        self.assertFalse(value["configuration"]["publishing_authority"])
        self.assertFalse(value["publishing_authority"])
        self.assertFalse(value["automation_enabled"])
        self.assertIn("provider.configuration.required", value["blockers"])
        self.assertIn("source.configuration.required", value["blockers"])
        schema = json.loads(
            (
                Path(__file__).resolve().parents[1]
                / "schemas"
                / "setup-recovery"
                / "v1"
                / "setup-configuration.schema.json"
            ).read_text(encoding="utf-8")
        )
        Draft202012Validator(schema, format_checker=FormatChecker()).validate(
            value["configuration"]
        )

    def test_fresh_setup_does_not_inherit_any_unasked_pace(self) -> None:
        for profile, expected in (
            ("occasional", 3),
            ("regular", 5),
            ("active", 10),
            ("high", 20),
        ):
            with self.subTest(profile=profile):
                value = self.engine(profile).start(
                    "fresh",
                    operator_label="Example",
                    timezone="Europe/London",
                    pace=profile,
                )
                self.assertEqual(value["configuration"]["daily_originals"], expected)

    def test_explore_reaches_explore_ready_without_operation_or_authority(self) -> None:
        value = self.engine().start(
            "explore",
            operator_label=None,
            timezone="Europe/London",
            pace="occasional",
        )
        self.assertEqual(value["session"]["stage"], "explore_ready")
        self.assertEqual(value["session"]["session_status"], "completed")
        self.assertIsNone(value["operation"])
        self.assertIsNone(value["installation"])
        self.assertFalse(value["publishing_authority"])
        self.assertFalse(value["automation_enabled"])
        self.assertEqual(value["configuration"]["daily_originals"], 3)
        self.assertEqual(value["configuration"]["hard_ceiling"], 100)
        self.assertEqual(value["next_actions"], ["explore_without_publishing"])

    def test_unimplemented_modes_fail_before_creating_store(self) -> None:
        engine = self.engine()
        for mode in ("different_operator",):
            with self.subTest(mode=mode):
                with self.assertRaises(SetupEngineError) as caught:
                    engine.start(
                        mode,
                        operator_label="Example",
                        timezone="Europe/London",
                        pace="regular",
                    )
                self.assertEqual(caught.exception.code, "setup.mode.not_implemented")
        self.assertFalse(engine.workspace.exists())

    def test_recover_start_requires_recovery_bundle(self) -> None:
        engine = self.engine("recover")
        with self.assertRaises(SetupEngineError) as caught:
            engine.start(
                "recover",
                operator_label="Example",
                timezone=None,
                pace=None,
            )
        self.assertEqual(caught.exception.code, "setup.recovery.bundle_required")
        self.assertFalse(engine.workspace.exists())

    def test_migrate_source_starts_without_replacing_policy(self) -> None:
        engine = self.engine()
        value = engine.start("migrate", operator_label="Example", machine_label="old-host")
        self.assertEqual(value["session"]["stage"], "preflight_ready")
        self.assertEqual(value["operation"]["status"], "active")
        self.assertEqual(value["installation"]["status"], "active")
        self.assertIsNone(value["configuration"])
        self.assertFalse(value["publishing_authority"])

    def test_invalid_noninteractive_input_fails_before_session_creation(self) -> None:
        engine = self.engine()
        with self.assertRaises(SetupEngineError):
            engine.start(
                "fresh",
                operator_label="Example",
                timezone="Bad/Zone",
                pace="regular",
            )
        self.assertFalse(engine.workspace.exists())

    def test_begin_then_resume_is_idempotent_and_does_not_duplicate_transitions(self) -> None:
        engine = self.engine()
        begun = engine.begin("fresh", operator_label="Example")
        self.assertEqual(begun["session"]["stage"], "operation_ready")
        session_id = begun["session"]["session_id"]
        before_count = len(begun["completed_transitions"])

        resumed = engine.resume(
            session_id=session_id,
            timezone="Europe/London",
            pace="active",
        )
        self.assertEqual(resumed["session"]["stage"], "configuration_ready")
        after_count = len(resumed["completed_transitions"])
        self.assertGreater(after_count, before_count)

        repeated = engine.resume(session_id=session_id)
        self.assertEqual(repeated["session"]["revision"], resumed["session"]["revision"])
        self.assertEqual(len(repeated["completed_transitions"]), after_count)

    def test_resume_without_config_leaves_operation_ready(self) -> None:
        engine = self.engine()
        begun = engine.begin("fresh", operator_label="Example")
        resumed = engine.resume(session_id=begun["session"]["session_id"])
        self.assertEqual(resumed["session"]["stage"], "operation_ready")
        self.assertIn("setup.configuration.required", resumed["blockers"])

    def test_committed_configuration_is_not_silently_replaced(self) -> None:
        engine = self.engine()
        value = engine.start(
            "fresh",
            operator_label="Example",
            timezone="Europe/London",
            pace="regular",
        )
        with self.assertRaises(SetupEngineError) as caught:
            engine.configure(
                value["session"]["session_id"],
                timezone="Europe/London",
                pace="high",
            )
        self.assertEqual(caught.exception.code, "setup.configuration.already_committed")

    def test_status_on_missing_store_is_read_only_and_creates_nothing(self) -> None:
        engine = self.engine("missing")
        with self.assertRaises(SetupEngineError) as caught:
            engine.status()
        self.assertEqual(caught.exception.code, "setup.store.missing")
        self.assertFalse(engine.workspace.exists())

    def test_second_start_refuses_to_replace_open_session(self) -> None:
        engine = self.engine()
        first = engine.begin("fresh", operator_label="Example")
        with self.assertRaises(SetupEngineError) as caught:
            engine.start(
                "explore",
                operator_label=None,
                timezone="Europe/London",
                pace="regular",
            )
        self.assertEqual(caught.exception.code, "setup.session.open_exists")
        current = engine.status(first["session"]["session_id"])
        self.assertEqual(current["session"]["stage"], "operation_ready")


if __name__ == "__main__":
    unittest.main()
