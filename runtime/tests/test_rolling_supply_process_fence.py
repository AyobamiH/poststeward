"""Real child-process proof: a timed-out supply stage must not keep mutating state."""
from __future__ import annotations

import signal
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from ocpf_post import rolling_supply


def stage_with_grandchild(marker: Path, *, exit_early: bool, ignore_term: bool = False) -> list[str]:
    child = (
        "import pathlib,sys,time,signal;"
        + ("signal.signal(signal.SIGTERM, signal.SIG_IGN);" if ignore_term else "")
        + "time.sleep(1.5);"
        "pathlib.Path(sys.argv[1]).write_text('late-stage-mutation')"
    )
    parent = (
        "import subprocess,sys,time;"
        f"subprocess.Popen([sys.executable, '-c', {child!r}, sys.argv[1]], "
        "stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,close_fds=True);"
        + ("time.sleep(0.3);sys.exit(0)" if exit_early else "time.sleep(20)")
    )
    return [sys.executable, "-c", parent, str(marker)]


@unittest.skipUnless(sys.platform.startswith("linux") or sys.platform == "darwin",
                     "Subprocess-group fencing requires POSIX")
class SupplyStageProcessFenceTests(unittest.TestCase):
    def test_timeout_prevents_late_grandchild_state_mutation(self):
        with tempfile.TemporaryDirectory(prefix="ps-stage-timeout-") as d:
            marker = Path(d) / "late.txt"
            result = rolling_supply.run_stage(
                "isolated-reserve", stage_with_grandchild(marker, exit_early=False), timeout=1
            )
            self.assertEqual(result["status"], "timed_out")
            self.assertEqual(result["exit_code"], 124, "Stage timeouts must fail the allocator cycle")
            time.sleep(1.2)
            self.assertFalse(marker.exists(), "Killed stage left a live mutating grandchild")

    def test_successful_parent_cannot_leave_untracked_grandchild(self):
        with tempfile.TemporaryDirectory(prefix="ps-stage-success-") as d:
            marker = Path(d) / "late.txt"
            result = rolling_supply.run_stage(
                "isolated-admission", stage_with_grandchild(marker, exit_early=True), timeout=3
            )
            self.assertEqual(result["status"], "attention")
            self.assertEqual(result["exit_code"], 125, "Early stage exit with live child is incomplete")
            time.sleep(1.8)
            self.assertFalse(marker.exists(), "Completed stage left a late mutating grandchild")

    def test_sigterm_resistant_child_is_killed_after_parent_exits(self):
        with tempfile.TemporaryDirectory(prefix="ps-stage-resistant-") as d:
            marker = Path(d) / "late.txt"
            result = rolling_supply.run_stage(
                "isolated-admission",
                stage_with_grandchild(marker, exit_early=True, ignore_term=True), timeout=3,
            )
            self.assertEqual(result["exit_code"], 125)
            time.sleep(1.8)
            self.assertFalse(marker.exists(), "SIGTERM-resistant child escaped stage cleanup")

    def test_process_group_disappears_between_probe_and_term(self):
        """An ESRCH cleanup race must not discard a completed stage receipt."""
        calls = []

        def group_disappears(pid, sig):
            calls.append(sig)
            if sig == signal.SIGTERM:
                raise ProcessLookupError("group exited after probe")

        with patch.object(rolling_supply.os, "killpg", side_effect=group_disappears):
            result = rolling_supply.run_stage(
                "safe", [sys.executable, "-c", "raise SystemExit(0)"], timeout=2,
            )
        self.assertEqual((result["status"], result["exit_code"]), ("completed", 0))
        self.assertEqual(calls, [0, signal.SIGTERM])

    def test_direct_success_and_failure_reporting_preserved(self):
        ok = rolling_supply.run_stage("safe", [sys.executable, "-c", "print('done')"], timeout=2)
        err = rolling_supply.run_stage("safe", [sys.executable, "-c", "raise SystemExit(7)"], timeout=2)
        self.assertEqual((ok["status"], ok["exit_code"]), ("completed", 0))
        self.assertEqual((err["status"], err["exit_code"]), ("attention", 7))


if __name__ == "__main__":
    unittest.main()
