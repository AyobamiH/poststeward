from __future__ import annotations

import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]


class OwnerDailyLauncherTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.home = self.root / "home"
        self.home.mkdir()
        self.repo = self.home / "a differently named clone"
        self.package = self.repo / "src" / "ocpf_post"
        self.package.mkdir(parents=True)
        scripts = self.repo / "scripts"
        scripts.mkdir()
        for name in ("owner-daily.py", "owner-daily.sh"):
            shutil.copyfile(ROOT / "scripts" / name, scripts / name)
        self.state = self.root / "state"
        self.state.mkdir()
        self.ledger = self.state / "publish-receipts.jsonl"
        self.ledger.write_text("{}\n", encoding="utf-8")
        self.config = self.root / "config"
        self.config.mkdir()
        (self.package / "__init__.py").write_text("", encoding="utf-8")
        (self.package / "state.py").write_text(
            "from pathlib import Path\ndef state_dir(): return Path(" + repr(str(self.state)) + ")\n"
            "def config_dir(): return Path(" + repr(str(self.config)) + ")\n", encoding="utf-8",
        )
        (self.package / "campaigns.py").write_text(
            "def builtin_manifest(c): return {'project':'fixture-project','vault':{'id':'fixture-vault','base_campaign':'ENTRY'}}\n"
            "def builtin_text(c,p): return 'fixture copy'\n", encoding="utf-8",
        )
        payload = {}
        for provider in ("x", "threads", "linkedin"):
            payload[provider] = {
                "at": "2026-09-20T08:00:00+00:00", "effective_verified": provider != "linkedin",
                "verification_basis": "receipt" if provider != "linkedin" else "unverified",
                "receipt": {"provider": provider, "campaign": "FIXTURE-" + provider,
                            "account_id": "account", "post_id": "post-" + provider,
                            "status": "published_verified" if provider != "linkedin" else "published_unverified",
                            "text_sha256": hashlib.sha256(b"fixture copy").hexdigest()},
            }
        (self.package / "performance_review.py").write_text(
            "from datetime import datetime\ndef publications():\n    d=" + repr(payload) +
            '\n    for r in d.values(): r["at"]=datetime.fromisoformat(r["at"])\n    return d\n',
            encoding="utf-8",
        )
        self.env = {**os.environ, "HOME": str(self.home)}

    def launch(self, *args):
        return subprocess.run(
            ["bash", str(self.repo / "scripts" / "owner-daily.sh"), *args],
            env=self.env, cwd=self.root, text=True, capture_output=True, timeout=15,
        )

    def test_clone_relative_default_all_platforms_and_private_output(self):
        before = {p: hashlib.sha256(p.read_bytes()).hexdigest() for p in self.root.rglob("*") if p.is_file()}
        result = self.launch("--date", "2026-09-20", "--no-open")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("OWNER_REPORT=PASS", result.stdout)
        self.assertIn("LINKEDIN: 1 published | 0 verified | 1 published without verified readback", result.stdout)
        self.assertIn("fixture-vault", result.stdout)
        self.assertIn("fixture copy", result.stdout)
        reports = list(self.home.glob("post-once-owner-report-*"))
        self.assertEqual(len(reports), 1)
        self.assertEqual(reports[0].stat().st_mode & 0o777, 0o700)
        for name in ("summary.txt", "publications.json"):
            self.assertEqual((reports[0] / name).stat().st_mode & 0o777, 0o600)
        for path, digest in before.items():
            self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), digest)
        self.assertFalse(list(self.package.rglob("*.pyc")))

    def test_missing_ledger_fails_without_misleading_zero_or_saved_success(self):
        self.ledger.unlink()
        result = self.launch("--date", "2026-09-20", "--no-open")
        self.assertEqual(result.returncode, 1)
        self.assertIn("OWNER_REPORT=FAIL", result.stderr)
        self.assertNotIn("OWNER_REPORT=PASS", result.stdout)
        self.assertFalse(list(self.home.glob("post-once-owner-report-*")))

    def test_help_needs_no_ledger_and_writes_no_report(self):
        self.ledger.unlink()
        result = self.launch("--help")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("--no-open", result.stdout)
        self.assertFalse(list(self.home.glob("post-once-owner-report-*")))

    def test_invalid_date_returns_parser_error_and_writes_nothing(self):
        result = self.launch("--date", "not-a-date", "--no-open")
        self.assertEqual(result.returncode, 2)
        self.assertFalse(list(self.home.glob("post-once-owner-report-*")))

    def test_future_date_is_not_reported_as_zero(self):
        result = self.launch("--date", "9999-01-01", "--no-open")
        self.assertEqual(result.returncode, 1)
        self.assertNotIn("0 published", result.stdout)
        self.assertFalse(list(self.home.glob("post-once-owner-report-*")))

    def test_direct_python_prints_json_without_files(self):
        result = subprocess.run(
            [sys.executable, "-B", str(self.repo / "scripts" / "owner-daily.py"), "--date", "2026-09-20"],
            env=self.env, text=True, capture_output=True, timeout=15,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["counts"]["threads"]["published"], 1)
        self.assertFalse(list(self.home.glob("post-once-owner-report-*")))

    def test_separate_runs_preserve_previous_report(self):
        first = self.launch("--date", "2026-09-20", "--no-open")
        report = next(self.home.glob("post-once-owner-report-*/summary.txt"))
        before = report.read_bytes()
        second = self.launch("--date", "2026-09-20", "--no-open")
        self.assertEqual((first.returncode, second.returncode), (0, 0))
        self.assertEqual(report.read_bytes(), before)
        self.assertEqual(len(list(self.home.glob("post-once-owner-report-*"))), 2)

    def test_explicit_repo_override_from_another_script_location(self):
        result = subprocess.run(
            [sys.executable, "-B", str(ROOT / "scripts" / "owner-daily.py"),
             "--repo", str(self.repo), "--date", "2026-09-20"],
            env=self.env, text=True, capture_output=True, timeout=15,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["counts"]["x"]["published"], 1)

    def test_never_saves_reports_under_live_state(self):
        (self.package / "state.py").write_text(
            "from pathlib import Path\ndef state_dir(): return Path.home()\n"
            "def config_dir(): return Path(" + repr(str(self.config)) + ")\n", encoding="utf-8",
        )
        (self.home / "publish-receipts.jsonl").write_text("{}\n", encoding="utf-8")
        result = self.launch("--date", "2026-09-20", "--no-open")
        self.assertEqual(result.returncode, 1)
        self.assertFalse(list(self.home.glob("post-once-owner-report-*")))

    def test_optional_notepad_failure_is_nonfatal(self):
        path = ROOT / "scripts" / "owner-daily.py"
        spec = importlib.util.spec_from_file_location("owner_daily_optional_open", path)
        assert spec is not None and spec.loader is not None
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        with patch.object(mod.shutil, "which", return_value="present"), \
             patch.object(mod.subprocess, "run", side_effect=OSError("private-data")):
            mod.open_summary(Path("summary.txt"))


if __name__ == "__main__":
    unittest.main()
