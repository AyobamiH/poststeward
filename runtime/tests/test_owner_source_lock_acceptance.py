from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "owner-source-lock-acceptance.py"


class OwnerSourceLockAcceptanceTests(unittest.TestCase):
    def test_real_posix_contention_acceptance_isolated(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            report = Path(temp) / "report.json"
            result = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPT),
                    "--release-root",
                    str(ROOT),
                    "--output",
                    str(report),
                ],
                cwd=ROOT,
                capture_output=True,
                text=True,
                timeout=30,
                check=False,
            )
            self.assertEqual(result.returncode, 0, result.stdout + "\n" + result.stderr)
            value = json.loads(report.read_text(encoding="utf-8"))
            self.assertTrue(value["pass"])
            self.assertTrue(value["tests"]["wait_then_acquire"]["pass"])
            self.assertTrue(value["tests"]["bounded_timeout_no_campaign_write"]["pass"])
            self.assertTrue(
                value["tests"]["bounded_timeout_no_campaign_write"][
                    "runtime_campaign_fingerprint_unchanged"
                ]
            )
            self.assertEqual(value["consequence"], "NO_LIVE_PROVIDER_OR_STATE_EFFECT")
            self.assertIn("overall=PASS", result.stdout)


if __name__ == "__main__":
    unittest.main()
