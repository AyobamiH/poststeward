from __future__ import annotations

import argparse
from contextlib import redirect_stderr, redirect_stdout
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from ocpf_post import dispatch

from ocpf_post.setup_cli import build_parser, cmd_setup_interactive


class SetupCliTests(unittest.TestCase):
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

    def run_parser(self, argv: list[str]) -> tuple[int, str, str]:
        parser = build_parser()
        args = parser.parse_args(argv)
        out = io.StringIO()
        err = io.StringIO()
        code = 0
        try:
            with redirect_stdout(out), redirect_stderr(err):
                args.func(args)
        except SystemExit as exc:
            code = int(exc.code or 0)
        return code, out.getvalue(), err.getvalue()

    def test_noninteractive_fresh_json_reaches_configuration_ready(self) -> None:
        workspace = self.root / "fresh"
        code, output, error = self.run_parser([
            "start",
            "--workspace", str(workspace),
            "--mode", "fresh",
            "--operator-label", "Example Ltd",
            "--timezone", "Europe/London",
            "--pace", "regular",
            "--json",
        ])
        self.assertEqual(code, 0, error)
        value = json.loads(output)
        self.assertEqual(value["session"]["stage"], "configuration_ready")
        self.assertEqual(value["configuration"]["daily_originals"], 5)
        self.assertEqual(value["configuration"]["hard_ceiling"], 100)
        self.assertFalse(value["publishing_authority"])
        self.assertFalse(value["automation_enabled"])

    def test_noninteractive_explore_json_has_no_operation(self) -> None:
        workspace = self.root / "explore"
        code, output, error = self.run_parser([
            "start",
            "--workspace", str(workspace),
            "--mode", "explore",
            "--timezone", "Europe/London",
            "--pace", "occasional",
            "--json",
        ])
        self.assertEqual(code, 0, error)
        value = json.loads(output)
        self.assertEqual(value["session"]["stage"], "explore_ready")
        self.assertIsNone(value["operation"])
        self.assertIsNone(value["installation"])
        self.assertFalse(value["publishing_authority"])

    def test_status_missing_is_blocked_and_does_not_create_workspace(self) -> None:
        workspace = self.root / "missing"
        code, output, _error = self.run_parser([
            "status",
            "--workspace", str(workspace),
            "--json",
        ])
        self.assertEqual(code, 3)
        value = json.loads(output)
        self.assertEqual(value["code"], "setup.store.missing")
        self.assertFalse(workspace.exists())

    def test_unimplemented_mode_returns_stable_blocker_without_store(self) -> None:
        workspace = self.root / "recover"
        code, output, _error = self.run_parser([
            "start",
            "--workspace", str(workspace),
            "--mode", "recover",
            "--timezone", "Europe/London",
            "--pace", "regular",
            "--json",
        ])
        self.assertEqual(code, 3)
        value = json.loads(output)
        self.assertEqual(value["code"], "setup.recovery.bundle_required")
        self.assertFalse(workspace.exists())

    def test_resume_can_supply_configuration_without_restarting(self) -> None:
        workspace = self.root / "resume"
        parser = build_parser()
        args = parser.parse_args([
            "start",
            "--workspace", str(workspace),
            "--mode", "fresh",
            "--operator-label", "Example Ltd",
            "--timezone", "Europe/London",
            "--pace", "regular",
            "--json",
        ])
        # Start fully, then status/resume should preserve revision and never duplicate.
        with redirect_stdout(io.StringIO()):
            args.func(args)
        code, output, error = self.run_parser([
            "resume",
            "--workspace", str(workspace),
            "--json",
        ])
        self.assertEqual(code, 0, error)
        value = json.loads(output)
        self.assertEqual(value["session"]["stage"], "configuration_ready")

    def test_interactive_fresh_has_no_default_pace_and_saves_explicit_choice(self) -> None:
        workspace = self.root / "interactive-fresh"
        args = argparse.Namespace(workspace=str(workspace))
        answers = iter([
            "1",              # fresh
            "Example Ltd",    # operator
            "Europe/London",  # timezone
            "2",              # regular
            "y",              # save
        ])
        out = io.StringIO()
        with patch("ocpf_post.setup_cli.detect_timezone", return_value=None), patch(
            "builtins.input", side_effect=lambda _prompt="": next(answers)
        ), redirect_stdout(out):
            cmd_setup_interactive(args)

        rendered = out.getvalue()
        self.assertIn("Publishing authority: NO", rendered)
        self.assertIn("Stage:     configuration_ready", rendered)
        self.assertIn("Pace:      regular (5 logical originals/day)", rendered)
        self.assertIn("Hard ceiling: 100", rendered)

    def test_interactive_cancel_before_configuration_keeps_resumable_session(self) -> None:
        workspace = self.root / "interactive-pause"
        args = argparse.Namespace(workspace=str(workspace))
        answers = iter([
            "1",
            "Example Ltd",
            "Europe/London",
            "3",  # active
            "n",  # do not commit config
        ])
        with patch("ocpf_post.setup_cli.detect_timezone", return_value=None), patch(
            "builtins.input", side_effect=lambda _prompt="": next(answers)
        ), redirect_stdout(io.StringIO()):
            cmd_setup_interactive(args)

        code, output, error = self.run_parser([
            "status",
            "--workspace", str(workspace),
            "--json",
        ])
        self.assertEqual(code, 0, error)
        value = json.loads(output)
        self.assertEqual(value["session"]["stage"], "operation_ready")
        self.assertEqual(value["session"]["session_status"], "open")
        self.assertIsNone(value["configuration"])

    def test_interactive_keyboard_interrupt_preserves_committed_progress(self) -> None:
        workspace = self.root / "interactive-interrupt"
        args = argparse.Namespace(workspace=str(workspace))
        calls = iter(["1", "Example Ltd"])

        def interrupting_input(_prompt: str = "") -> str:
            try:
                return next(calls)
            except StopIteration:
                raise KeyboardInterrupt

        err = io.StringIO()
        with patch("ocpf_post.setup_cli.detect_timezone", return_value=None), patch(
            "builtins.input", side_effect=interrupting_input
        ), redirect_stdout(io.StringIO()), redirect_stderr(err):
            with self.assertRaises(SystemExit) as caught:
                cmd_setup_interactive(args)
        self.assertEqual(caught.exception.code, 130)
        self.assertIn("Setup paused", err.getvalue())

        code, output, status_error = self.run_parser([
            "status",
            "--workspace", str(workspace),
            "--json",
        ])
        self.assertEqual(code, 0, status_error)
        value = json.loads(output)
        self.assertEqual(value["session"]["stage"], "operation_ready")
        self.assertEqual(value["session"]["session_status"], "open")

    def test_dispatch_routes_setup_without_provider_credential_bootstrap(self) -> None:
        original = list(__import__("sys").argv)
        try:
            __import__("sys").argv = ["ocpf-post", "setup", "status", "--workspace", str(self.root / "none"), "--json"]
            with patch.object(dispatch, "_bootstrap_x_runtime", side_effect=AssertionError("provider bootstrap must not run")), patch(
                "ocpf_post.setup_cli.main", side_effect=SystemExit(3)
            ):
                with self.assertRaises(SystemExit) as caught:
                    dispatch.main()
            self.assertEqual(caught.exception.code, 3)
        finally:
            __import__("sys").argv = original

    def test_setup_parser_has_no_pace_default(self) -> None:
        parser = build_parser()
        args = parser.parse_args([
            "start",
            "--mode", "fresh",
            "--operator-label", "Example",
            "--timezone", "Europe/London",
        ])
        self.assertIsNone(args.pace)
        self.assertIsNone(args.daily_originals)


if __name__ == "__main__":
    unittest.main()
