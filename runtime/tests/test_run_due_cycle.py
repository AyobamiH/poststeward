from __future__ import annotations

import subprocess
import unittest
from unittest.mock import Mock, patch

from ocpf_post import run_due_cycle as cycle


def row(schedule_id, status, **extra):
    return {
        "schedule_id": schedule_id,
        "campaign": "EXAMPLE",
        "provider": "x",
        "account_id": "acct",
        "status": status,
        **extra,
    }


class RunDueCycleTests(unittest.TestCase):
    def test_empty_tick_does_not_wake_refill(self):
        wake = Mock(return_value={"status": "signalled"})
        with patch.object(cycle.local_store, "write"):
            value = cycle.cycle(
                due_runner=lambda **_kw: [],
                refill_waker=wake,
            )
        self.assertEqual(value["due_result_count"], 0)
        self.assertEqual(value["consumed_count"], 0)
        self.assertEqual(value["refill_wake"]["status"], "not_needed")
        wake.assert_not_called()

    def test_transient_preflight_deferral_stays_scheduled_and_does_not_wake(self):
        deferred = row(
            "sch-1",
            "scheduled",
            failure_class="provider_unavailable",
            retry_at="2026-10-02T15:45:00Z",
        )
        wake = Mock(return_value={"status": "signalled"})
        with patch.object(cycle.local_store, "write"):
            value = cycle.cycle(
                due_runner=lambda **_kw: [deferred],
                refill_waker=wake,
            )
        self.assertEqual(value["consumed_count"], 0)
        self.assertEqual(value["attention_count"], 0)
        wake.assert_not_called()

    def test_verified_publication_wakes_existing_refill_service_once(self):
        wake = Mock(return_value={"status": "signalled"})
        with patch.object(cycle.local_store, "write"):
            value = cycle.cycle(
                due_runner=lambda **_kw: [row("sch-1", "published_verified")],
                refill_waker=wake,
            )
        self.assertEqual(value["consumed_count"], 1)
        self.assertEqual(value["attention_count"], 0)
        self.assertEqual(value["consumed_schedule_ids"], ["sch-1"])
        wake.assert_called_once_with()

    def test_terminal_attention_still_wakes_refill_without_granting_retry(self):
        wake = Mock(return_value={"status": "signalled"})
        with patch.object(cycle.local_store, "write"):
            value = cycle.cycle(
                due_runner=lambda **_kw: [
                    row(
                        "sch-ambiguous",
                        "ambiguous_effect",
                        automatic_retry=False,
                    )
                ],
                refill_waker=wake,
            )
        self.assertEqual(value["consumed_count"], 1)
        self.assertEqual(value["attention_count"], 1)
        self.assertFalse(value["results"][0]["automatic_retry"])
        wake.assert_called_once_with()

    def test_refill_signal_failure_defers_to_periodic_timer(self):
        failed = subprocess.CompletedProcess(
            args=["systemctl"], returncode=1, stdout="", stderr="busy",
        )
        result = cycle.wake_refill(
            runner=lambda *args, **kwargs: failed,
        )
        self.assertEqual(result["status"], "deferred_to_periodic_timer")
        self.assertEqual(result["systemctl_exit_code"], 1)

    def test_post_consumption_evidence_failure_does_not_change_publication_result(self):
        wake = Mock(return_value={"status": "signalled"})
        with patch.object(cycle.local_store, "write", side_effect=OSError("disk busy")):
            value = cycle.cycle(
                due_runner=lambda **_kw: [row("sch-1", "published_verified")],
                refill_waker=wake,
            )
        self.assertEqual(value["consumed_count"], 1)
        self.assertEqual(value["attention_count"], 0)
        self.assertEqual(value["evidence_persist_status"], "unavailable")
        wake.assert_called_once_with()

    def test_systemd_installer_uses_canonical_wrapper_not_direct_publisher(self):
        from pathlib import Path
        root = Path(__file__).resolve().parents[1]
        installer = (root / "scripts" / "install-user-scheduler-timer").read_text()
        self.assertIn("ExecStart=/bin/sh $ROOT/scripts/run-unattended run-due", installer)
        cli = (root / "src/ocpf_post/scheduler_cli.py").read_text()
        self.assertIn("cycle(limit=args.limit, due_runner=", cli)
        self.assertNotIn("ExecStart=$ROOT/ocpf-post run-due", installer)


if __name__ == "__main__":
    unittest.main()

