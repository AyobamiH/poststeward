from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from ocpf_post.health import analyse, at, fold_schedules, read_log, report, timer_state, _queue_issue_level

NOW = datetime(2026, 9, 9, 12, tzinfo=timezone.utc)


def receipt(**overrides):
    return {"campaign": "TEST-001", "provider": "threads", "account_id": "account",
            "status": "published_verified", "recorded_at": NOW.isoformat(), "post_id": "post",
            "readback_verified": True, "text_sha256": hashlib.sha256(b"hello").hexdigest(), **overrides}


def schedule(**overrides):
    return {**receipt(), "schedule_id": "sch_test", "status": "scheduled", "text": "hello",
            "run_at": (NOW + timedelta(minutes=10)).isoformat(), "updated_at": NOW.isoformat(), **overrides}


class HealthTests(unittest.TestCase):
    def codes(self, schedules=(), receipts=()):
        return {f["code"] for f in analyse(list(schedules), list(receipts), now=NOW)["findings"]}

    def test_provisional_and_verified_are_one_effect(self):
        result = analyse([], [receipt(status="published_unverified", readback_verified=False), receipt()], now=NOW)
        self.assertEqual(len(result["publications"]), 1)
        self.assertEqual(result["published_by_provider_status"], {"threads:published_verified": 1})

    def test_later_verification_does_not_move_old_publication_into_window(self):
        first = receipt(status="published_unverified", readback_verified=False, recorded_at=(NOW - timedelta(days=2)).isoformat())
        self.assertEqual(analyse([], [first, receipt()], now=NOW)["publications"], [])

    def test_accounts_are_separate_and_distinct_ids_flag_duplicates(self):
        self.assertNotIn("multiple_post_ids", self.codes(receipts=[receipt(), receipt(account_id="other", post_id="two")]))
        self.assertIn("multiple_post_ids", self.codes(receipts=[receipt(), receipt(post_id="two")]))

    def test_overdue_and_stuck_execution(self):
        old = (NOW - timedelta(hours=1)).isoformat()
        self.assertIn("overdue", self.codes([schedule(run_at=old)]))
        self.assertIn("stuck_executing", self.codes([schedule(status="executing", updated_at=old)]))

    def test_preflight_deferred_is_warning_not_overdue_until_retry_window(self):
        row = schedule(
            run_at=(NOW - timedelta(hours=1)).isoformat(),
            retry_at=(NOW + timedelta(minutes=10)).isoformat(),
            failure_class="provider_unavailable",
            preflight_attempts=2,
        )
        result = analyse([row], [], now=NOW)
        findings = {item["code"]: item for item in result["findings"]}
        self.assertIn("preflight_deferred", findings)
        self.assertEqual(findings["preflight_deferred"]["level"], "warning")
        self.assertEqual(findings["preflight_deferred"]["preflight_attempts"], 2)
        self.assertNotIn("overdue", findings)

        expired = dict(row, retry_at=(NOW - timedelta(minutes=1)).isoformat())
        self.assertIn("overdue", self.codes([expired]))

    def test_fold_schedules_preserves_and_clears_preflight_state(self):
        created = schedule()
        created.update(event="scheduled", recorded_at=(NOW - timedelta(minutes=20)).isoformat())
        deferred = {
            "event": "preflight_deferred",
            "status": "scheduled",
            "schedule_id": created["schedule_id"],
            "recorded_at": (NOW - timedelta(minutes=10)).isoformat(),
            "retry_at": (NOW + timedelta(minutes=1)).isoformat(),
            "preflight_attempts": 1,
            "failure_class": "provider_unavailable",
            "detail": "no publish request began",
        }
        claimed = {
            "event": "claimed",
            "status": "executing",
            "schedule_id": created["schedule_id"],
            "recorded_at": NOW.isoformat(),
            "retry_at": None,
            "failure_class": None,
        }
        deferred_record = fold_schedules([created, deferred])[0]
        self.assertEqual(deferred_record["failure_class"], "provider_unavailable")
        self.assertEqual(deferred_record["preflight_attempts"], 1)
        self.assertEqual(deferred_record["detail"], "no publish request began")
        claimed_record = fold_schedules([created, deferred, claimed])[0]
        self.assertIsNone(claimed_record["retry_at"])
        self.assertIsNone(claimed_record["failure_class"])

    def test_duplicate_reservation_and_terminal_overlap(self):
        codes = self.codes([schedule(), schedule(schedule_id="second")], [receipt()])
        self.assertTrue({"duplicate_reservations", "active_terminal_overlap"} <= codes)

    def test_corrupt_payload_and_false_verified(self):
        self.assertIn("payload_integrity", self.codes([schedule(text="changed")]))
        self.assertIn("false_verified", self.codes(receipts=[receipt(readback_verified=False)]))

    def test_schedule_must_match_receipt_hash_id_and_readback(self):
        self.assertIn("receipt_mismatch", self.codes([schedule(status="published_verified")], [receipt(text_sha256="different")]))
        self.assertIn("schedule_readback_mismatch", self.codes([schedule(status="published_verified", readback_verified=False)], [receipt()]))

    def test_linkedin_unverified_is_not_failed_or_verified(self):
        row = receipt(provider="linkedin", status="published_unverified", readback_verified=False)
        result = analyse([], [row], now=NOW)
        self.assertEqual(result["published_by_provider_status"], {"linkedin:published_unverified": 1})
        self.assertEqual(result["findings"], [])

    def test_partial_thread_effect_is_terminal_attention(self):
        row = receipt(status="partial_effect", readback_verified=False)
        result = analyse([], [row], now=NOW)
        findings = {item["code"]: item for item in result["findings"]}
        self.assertIn("partial_effect", findings)
        self.assertEqual(findings["partial_effect"]["level"], "attention")
        self.assertEqual(result["publications"], [])

    def test_old_ambiguity_remains_visible(self):
        self.assertIn("ambiguous_effect", self.codes(receipts=[receipt(status="ambiguous_effect", recorded_at=(NOW - timedelta(days=30)).isoformat())]))

    def test_matching_ambiguous_schedule_and_receipt_are_one_root_attention(self):
        result = analyse(
            [schedule(status="ambiguous_effect")],
            [receipt(status="ambiguous_effect", readback_verified=False)],
            now=NOW,
        )
        findings = {row["code"]: row for row in result["findings"]}
        self.assertEqual(findings["ambiguous_effect"]["level"], "attention")
        self.assertEqual(findings["ambiguous_schedule"]["level"], "warning")

    def test_unresolved_ambiguity_exposes_last_forensic_status(self):
        digest = hashlib.sha256(b"hello").hexdigest()
        receipt_row = receipt(
            status="ambiguous_effect",
            readback_verified=False,
            schedule_id="sch-forensic",
            text_sha256=digest,
        )
        reconciled = [{
            "status": "forensic_no_match",
            "campaign": "TEST-001",
            "provider": "threads",
            "account_id": "account",
            "schedule_id": "sch-forensic",
            "text_sha256": digest,
            "candidate_count": 0,
            "observed_at": NOW.isoformat(),
            "automatic_retry": False,
            "next_action": "manual_review_no_resend",
        }]
        result = analyse([], [receipt_row], now=NOW, reconciled=reconciled)
        finding = next(row for row in result["findings"] if row["code"] == "ambiguous_effect")
        self.assertEqual(finding["forensic_status"], "forensic_no_match")
        self.assertEqual(finding["forensic_candidate_count"], 0)
        self.assertEqual(finding["forensic_observed_at"], NOW.isoformat())
        self.assertFalse(finding["forensic_automatic_retry"])
        self.assertEqual(finding["forensic_next_action"], "manual_review_no_resend")

    def test_verified_forensic_evidence_resolves_root_without_rewriting_history(self):
        digest = hashlib.sha256(b"hello").hexdigest()
        schedule_row = schedule(
            status="ambiguous_effect",
            schedule_id="sch-forensic",
            text_sha256=digest,
        )
        receipt_row = receipt(
            status="ambiguous_effect",
            readback_verified=False,
            schedule_id="sch-forensic",
            text_sha256=digest,
        )
        reconciled = [{
            "status": "verified_discovered",
            "campaign": "TEST-001",
            "provider": "threads",
            "account_id": "account",
            "schedule_id": "sch-forensic",
            "text_sha256": digest,
            "discovered_post_id": "987654321",
        }]
        result = analyse([schedule_row], [receipt_row], now=NOW, reconciled=reconciled)
        findings = {row["code"]: row for row in result["findings"]}
        self.assertNotIn("ambiguous_effect", findings)
        self.assertEqual(findings["ambiguity_reconciled"]["level"], "warning")
        self.assertEqual(findings["ambiguity_reconciled"]["discovered_post_id"], "987654321")
        self.assertEqual(findings["ambiguous_schedule"]["level"], "warning")

    def test_closed_expired_queue_history_is_warning_not_attention(self):
        self.assertEqual(_queue_issue_level("expired_unpublished"), "warning")
        self.assertEqual(_queue_issue_level("ambiguous_effect"), "warning")
        self.assertEqual(_queue_issue_level("schedule_requires_review"), "attention")

    def test_no_evidence_is_unknown(self):
        self.assertIn("no_recent_publications", self.codes())

    def test_malformed_log_and_orphan_transition_raise(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "events.jsonl"
            p.write_text('{}\nnot json\n')
            with self.assertRaises(ValueError):
                read_log(p)
        with self.assertRaises(ValueError):
            fold_schedules([{"event": "completed", "schedule_id": "missing"}])
        with self.assertRaises(ValueError):
            at("2026-09-09T12:00:00")

    def test_partial_report_redacts_errors_and_never_contacts_providers(self):
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {"OCPF_POST_STATE_DIR": tmp, "OCPF_POST_CONFIG_DIR": tmp}):
            p = Path(tmp) / "schedule-events.jsonl"
            p.write_text('secret-token=never-output\n')
            before = {str(p): p.read_bytes()}
            with patch("urllib.request.urlopen", side_effect=AssertionError("Network forbidden")), patch("subprocess.run", side_effect=AssertionError("Processes forbidden")):
                result = report(now=NOW, check_timers=False)
            self.assertEqual(result["status"], "unknown")
            self.assertEqual(result["checks"]["schedules"], "unavailable")
            self.assertEqual(result["checks"]["portfolio_sources_freshness_mix"], "observed")
            self.assertNotIn("secret-token", json.dumps(result))
            self.assertEqual(before, {str(p): p.read_bytes() for p in Path(tmp).iterdir()})

    def test_timer_timeout_is_unknown_and_command_is_read_only(self):
        import subprocess
        with patch("subprocess.run", side_effect=subprocess.TimeoutExpired("systemctl", 5)) as call:
            self.assertEqual(timer_state("ocpf-post-run-due.timer"), {"available": False})
        self.assertEqual(call.call_args.args[0][:3], ["systemctl", "--user", "show"])


if __name__ == "__main__":
    unittest.main()
