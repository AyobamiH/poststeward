"""Exercise the real Darwin launchd control plane without customer authority or provider effects.

Only run in an isolated macOS CI user session. Existing product launch agents
are treated as a hard conflict and never stopped or replaced.
"""
from __future__ import annotations

import os
from pathlib import Path
import plistlib
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from ocpf_post.launchd_services import JOBS, LaunchdServiceController


@unittest.skipUnless(sys.platform == "darwin", "requires genuine macOS launchctl")
class NativeLaunchdLifecycleTests(unittest.TestCase):
    def test_isolated_bootstrap_readback_and_disarm(self):
        domain = f"gui/{os.getuid()}"
        root_check = subprocess.run(
            ["launchctl", "print", domain], capture_output=True, text=True, timeout=10
        )
        self.assertEqual(root_check.returncode, 0, "real logged-in GUI launchd domain required")

        for label, _interval in JOBS.values():
            existing = subprocess.run(
                ["launchctl", "print", f"{domain}/{label}"],
                capture_output=True, text=True, timeout=10,
            )
            self.assertNotEqual(
                existing.returncode, 0, f"refusing to replace existing agent: {label}"
            )

        with tempfile.TemporaryDirectory(prefix="poststeward-native-launchd-") as scratch:
            root = Path(scratch)
            env = {
                "HOME": str(root),
                "XDG_CONFIG_HOME": str(root / "config"),
                "XDG_STATE_HOME": str(root / "state"),
                "XDG_DATA_HOME": str(root / "data"),
                "POSTSTEWARD_RUNTIME_CONFIG_DIR": str(root / "config"),
                "POSTSTEWARD_RUNTIME_STATE_DIR": str(root / "state"),
            }
            with patch.dict(os.environ, env):
                controller = LaunchdServiceController()
                runtime = Path(__file__).resolve().parents[1]
                controller.stage(
                    runtime_root=runtime,
                    state_root=root / "state",
                    config_root=root / "config",
                )
                self.assertEqual(len(list(controller.directory.glob("*.plist"))), len(JOBS))
                for label, _interval in JOBS.values():
                    path = controller.directory / f"{label}.plist"
                    job = plistlib.loads(path.read_bytes())
                    self.assertEqual(job["PostStewardManaged"], 1)
                    self.assertEqual(job["EnvironmentVariables"]["POSTSTEWARD_REQUIRE_CLOUD_FENCE"], "1")
                    # Replace the execution target with an inert system binary:
                    # exercise genuine launchctl only, never the publishing runtime.
                    job["ProgramArguments"] = ["/usr/bin/true"]
                    job["RunAtLoad"] = False
                    path.write_bytes(plistlib.dumps(job))
                try:
                    armed = controller.arm()
                    self.assertEqual(armed["manager"], "launchd")
                    self.assertTrue(armed["all_active"])
                    self.assertTrue(armed["all_enabled"])
                finally:
                    disarmed = controller.disarm()
                    self.assertFalse(disarmed["all_active"])
                    self.assertFalse(disarmed["all_enabled"])
                    for label, _interval in JOBS.values():
                        (controller.directory / f"{label}.plist").unlink(missing_ok=True)
                self.assertFalse(any(row["active"] for row in controller.inspect()["timers"]))


if __name__ == "__main__":
    unittest.main()
