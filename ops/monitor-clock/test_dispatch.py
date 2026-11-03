"""Exercise the monitor clock without contacting GitHub or changing the real home."""
import importlib.util
import json
import os
import stat
import subprocess
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch


spec = importlib.util.spec_from_file_location("monitor_dispatch", Path(__file__).with_name("dispatch.py"))
clock = importlib.util.module_from_spec(spec)
spec.loader.exec_module(clock)


class DispatchTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.home = Path(temporary.name)
        self.receipt = self.home / ".local/state/poststeward-monitor-clock/alerts.json"
        self.slot = "2026-11-03T12:25Z"
        now = datetime(2026, 11, 3, 12, 25, tzinfo=timezone.utc).timestamp()
        for mocked in [
            patch.object(clock.Path, "home", return_value=self.home),
            patch.object(clock.time, "time", return_value=now),
        ]:
            mocked.start()
            self.addCleanup(mocked.stop)
        # Every possible GitHub subprocess is replaced in every test.
        self.submit = patch.object(clock.subprocess, "run").start()
        self.lookup = patch.object(clock.subprocess, "check_output").start()
        self.addCleanup(patch.stopall)

    def state(self):
        return json.loads(self.receipt.read_text())

    def test_accepted_slot_is_not_submitted_twice(self):
        clock.dispatch("alerts")
        clock.dispatch("alerts")
        self.submit.assert_called_once()
        self.lookup.assert_not_called()
        self.assertEqual(self.state(), {"slot": self.slot, "status": "accepted"})
        payload = json.loads(self.submit.call_args.kwargs["input"])
        self.assertEqual(payload, {
            "event_type": "poststeward-monitor-alerts",
            "client_payload": {"slot": self.slot},
        })

    def test_lost_response_recovers_matching_run_without_replay(self):
        self.submit.side_effect = subprocess.TimeoutExpired("gh api", 35)
        with self.assertRaises(subprocess.TimeoutExpired):
            clock.dispatch("alerts")
        self.assertEqual(self.state()["status"], "submitting")
        self.lookup.return_value = json.dumps([{
            "displayTitle": "Operational alert control plane — " + self.slot,
        }]).encode()
        clock.dispatch("alerts")
        self.submit.assert_called_once()
        self.lookup.assert_called_once()
        self.assertEqual(self.state()["status"], "accepted")

    def test_uncertain_response_without_matching_run_remains_held(self):
        self.submit.side_effect = subprocess.TimeoutExpired("gh api", 35)
        with self.assertRaises(subprocess.TimeoutExpired):
            clock.dispatch("alerts")
        self.lookup.return_value = json.dumps([{
            "displayTitle": "Operational alert control plane — 2026-11-03T12:20Z",
        }]).encode()
        with self.assertRaisesRegex(SystemExit, "Uncertain submission.*Inspect GitHub before replay"):
            clock.dispatch("alerts")
        self.submit.assert_called_once()
        self.assertEqual(self.state()["status"], "submitting")

    def test_receipt_file_and_directory_are_synced_before_http(self):
        events = []
        real_sync, real_replace = os.fsync, os.replace

        def sync(descriptor):
            events.append("directory" if stat.S_ISDIR(os.fstat(descriptor).st_mode) else "file")
            real_sync(descriptor)

        def replace(source, destination):
            events.append("replace")
            real_replace(source, destination)

        def submit(*args, **kwargs):
            self.assertEqual(self.state(), {"slot": self.slot, "status": "submitting"})
            self.assertEqual(events[-3:], ["file", "replace", "directory"])
            # A fresh state directory and its newly created parents are persisted.
            self.assertEqual(events[:3], ["directory", "directory", "directory"])
            events.append("http")

        self.submit.side_effect = submit
        with patch.object(clock.os, "fsync", side_effect=sync), patch.object(clock.os, "replace", side_effect=replace):
            clock.dispatch("alerts")
        self.assertEqual(events[-3:], ["file", "replace", "directory"])
        self.assertEqual(self.state()["status"], "accepted")

    def test_failed_receipt_sync_prevents_external_dispatch(self):
        self.receipt.parent.mkdir(parents=True)
        real_sync = os.fsync

        def fail_directory_sync(descriptor):
            if stat.S_ISDIR(os.fstat(descriptor).st_mode):
                raise OSError("directory persistence failed")
            real_sync(descriptor)

        with patch.object(clock.os, "fsync", side_effect=fail_directory_sync):
            with self.assertRaisesRegex(OSError, "directory persistence failed"):
                clock.dispatch("alerts")
        self.submit.assert_not_called()
        self.lookup.assert_not_called()


if __name__ == "__main__":
    unittest.main()
