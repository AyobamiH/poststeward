from __future__ import annotations

from datetime import datetime, timezone
import io
import json
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch

from ocpf_post import portfolio_cli, volume_audit

UTC = timezone.utc


class HistoricalVolumeAuditTests(unittest.TestCase):
    def test_target_day_compares_adjacent_schedule_volume_and_exposes_execution_loss(self):
        schedules = [
            {"schedule_id": "prev-1", "campaign": "P1", "provider": "x", "account_id": "x1",
             "run_at": "2026-09-19T08:00:00Z", "status": "published_verified"},
            {"schedule_id": "prev-2", "campaign": "P2", "provider": "threads", "account_id": "t1",
             "run_at": "2026-09-19T09:00:00Z", "status": "published_verified"},
            {"schedule_id": "prev-3", "campaign": "P3", "provider": "linkedin", "account_id": "l1",
             "run_at": "2026-09-19T10:00:00Z", "status": "published_unverified"},
            {"schedule_id": "target-ok", "campaign": "T1", "provider": "x", "account_id": "x1",
             "run_at": "2026-09-20T08:00:00Z", "status": "published_verified",
             "last_event": "completed"},
            {"schedule_id": "target-failed", "campaign": "T2", "provider": "threads", "account_id": "t1",
             "run_at": "2026-09-20T09:00:00Z", "status": "failed",
             "last_event": "failed", "failure_class": "provider_rejected", "detail": "private prose"},
            {"schedule_id": "next-1", "campaign": "N1", "provider": "x", "account_id": "x1",
             "run_at": "2026-09-21T08:00:00Z", "status": "published_verified"},
            {"schedule_id": "next-2", "campaign": "N2", "provider": "threads", "account_id": "t1",
             "run_at": "2026-09-21T09:00:00Z", "status": "published_verified"},
            {"schedule_id": "next-3", "campaign": "N3", "provider": "linkedin", "account_id": "l1",
             "run_at": "2026-09-21T10:00:00Z", "status": "published_unverified"},
            {"schedule_id": "next-4", "campaign": "N4", "provider": "x", "account_id": "x1",
             "run_at": "2026-09-21T11:00:00Z", "status": "published_verified"},
        ]
        publications = {
            ("T1", "x", "x1", "post-1"): {
                "receipt": {
                    "campaign": "T1", "provider": "x", "account_id": "x1",
                    "schedule_id": "target-ok", "post_id": "post-1",
                    "status": "published_verified",
                },
                "at": datetime(2026, 9, 20, 8, 2, tzinfo=UTC),
                "effective_verified": True,
                "verification_basis": "receipt",
            },
            ("DIRECT", "linkedin", "l1", "post-d"): {
                "receipt": {
                    "campaign": "DIRECT", "provider": "linkedin", "account_id": "l1",
                    "post_id": "post-d", "status": "published_unverified",
                },
                "at": datetime(2026, 9, 20, 12, 0, tzinfo=UTC),
                "effective_verified": False,
                "verification_basis": "unverified",
            },
        }

        with patch("ocpf_post.scheduler.schedule_records", return_value=schedules), \
             patch("ocpf_post.performance_review.publications", return_value=publications), \
             patch("ocpf_post.portfolio.load_policy", return_value={"timezone": "Europe/London"}):
            result = volume_audit.audit(day="2026-09-20")

        self.assertEqual([row["scheduled_total"] for row in result["days"]], [3, 2, 4])
        self.assertEqual(result["comparison"]["relative_schedule_volume"], "lower_than_both_adjacent_days")
        self.assertEqual(result["target"]["scheduled_published_terminal"], 1)
        self.assertEqual(result["target"]["scheduled_nonpublished"], 1)
        self.assertEqual(result["target"]["publication_effects_on_day"], 2)
        self.assertEqual(result["target"]["publication_effects_for_scheduled_work"], 1)
        self.assertEqual(result["target"]["scheduled_without_receipt_effect"], 1)
        self.assertEqual(result["evidence_observation"]["code"], "nonpublished_schedule_outcomes_present")
        self.assertEqual(result["target"]["nonpublished_schedules"][0]["failure_class"], "provider_rejected")
        self.assertNotIn("detail", result["target"]["nonpublished_schedules"][0])
        self.assertIn("No provider call", result["boundary"])

    def test_historical_failed_schedule_is_safely_classified_from_retained_detail(self):
        schedules = [
            {
                "schedule_id": "target-rate",
                "campaign": "T-RATE",
                "provider": "x",
                "account_id": "x1",
                "run_at": "2026-09-21T08:00:00Z",
                "status": "failed",
                "last_event": "failed",
                "detail": (
                    'Provider rejected the consequence: provider rejected request '
                    '(HTTP 429): {"title":"Too Many Requests"}. This schedule will not auto-retry.'
                ),
            },
        ]
        with patch("ocpf_post.scheduler.schedule_records", return_value=schedules), \
             patch("ocpf_post.performance_review.publications", return_value={}), \
             patch("ocpf_post.portfolio.load_policy", return_value={"timezone": "Europe/London"}):
            result = volume_audit.audit(day="2026-09-21")

        target = result["target"]
        self.assertEqual(target["failure_class_counts"], {"provider_rate_limited": 1})
        self.assertEqual(target["failure_stage_counts"], {"provider_consequence": 1})
        self.assertEqual(target["provider_http_status_counts"], {"429": 1})
        row = target["nonpublished_schedules"][0]
        self.assertEqual(row["failure_class"], "provider_rate_limited")
        self.assertEqual(row["provider_http_status"], 429)
        self.assertFalse(row["automatic_retry"])
        self.assertNotIn("detail", row)

    def test_schedule_effect_can_complete_after_target_calendar_day(self):
        schedules = [
            {"schedule_id": "target-1", "campaign": "T1", "provider": "x", "account_id": "x1",
             "run_at": "2026-09-20T22:30:00Z", "status": "published_verified"},
        ]
        publications = {
            ("T1", "x", "x1", "post-1"): {
                "receipt": {
                    "campaign": "T1", "provider": "x", "account_id": "x1",
                    "schedule_id": "target-1", "post_id": "post-1",
                    "status": "published_verified",
                },
                "at": datetime(2026, 9, 21, 0, 5, tzinfo=UTC),
                "effective_verified": True,
                "verification_basis": "receipt",
            },
        }
        with patch("ocpf_post.scheduler.schedule_records", return_value=schedules), \
             patch("ocpf_post.performance_review.publications", return_value=publications), \
             patch("ocpf_post.portfolio.load_policy", return_value={"timezone": "Europe/London"}):
            result = volume_audit.audit(day="2026-09-20")

        target = result["target"]
        self.assertEqual(target["scheduled_total"], 1)
        self.assertEqual(target["publication_effects_on_day"], 0)
        self.assertEqual(target["publication_effects_for_scheduled_work"], 1)
        self.assertEqual(target["scheduled_without_receipt_effect"], 0)
        self.assertEqual(result["evidence_observation"]["code"], "scheduled_work_completed")

    def test_invalid_date_or_timezone_fails_before_any_provider_work(self):
        with self.assertRaisesRegex(ValueError, "YYYY-MM-DD"):
            volume_audit.audit(day="20-09-2026", timezone_name="Europe/London")
        with self.assertRaisesRegex(ValueError, "Unknown timezone"):
            volume_audit.audit(day="2026-09-20", timezone_name="Not/AZone")

    def test_portfolio_cli_volume_audit_is_read_only_json(self):
        value = {
            "schema_version": 1,
            "status": "observed",
            "date": "2026-09-20",
            "timezone": "Europe/London",
            "days": [],
            "target": {"nonpublished_schedules": []},
            "comparison": {},
            "evidence_observation": {"code": "scheduled_work_completed", "detail": "done"},
            "boundary": "read only",
        }
        with patch("ocpf_post.volume_audit.audit", return_value=value) as audit:
            parser = portfolio_cli.build_parser()
            args = parser.parse_args(["volume-audit", "--date", "2026-09-20", "--json"])
            output = io.StringIO()
            with redirect_stdout(output):
                args.func(args)

        audit.assert_called_once_with(day="2026-09-20", timezone_name=None)
        self.assertEqual(json.loads(output.getvalue())["date"], "2026-09-20")


if __name__ == "__main__":
    unittest.main()
