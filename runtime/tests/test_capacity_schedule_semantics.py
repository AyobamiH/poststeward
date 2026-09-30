from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone

from ocpf_post import capacity_experiment as trial
from ocpf_post.schedule_semantics import classify_schedule

UTC = timezone.utc


class CapacityScheduleSemanticsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.now = datetime(2026, 9, 12, 12, 0, tzinfo=UTC)
        self.account = trial.ACCOUNTS["x"]
        self.success_schedule = {
            "schedule_id": "sch-good",
            "campaign": "GOOD",
            "provider": "x",
            "account_id": self.account,
            "status": "published_verified",
            "post_id": "post-good",
            "text_sha256": "a" * 64,
            "readback_verified": True,
            "run_at": (self.now - timedelta(hours=1)).isoformat(),
            "updated_at": (self.now - timedelta(minutes=30)).isoformat(),
        }
        self.success_receipt = {
            **self.success_schedule,
            "recorded_at": self.success_schedule["updated_at"],
        }

    def _gate(self, row):
        return trial.delivery_gate(
            "x",
            self.now,
            receipts=[self.success_receipt],
            schedules=[self.success_schedule, row],
        )

    def test_every_requires_review_schedule_state_blocks_capacity_increase(self) -> None:
        statuses = (
            "failed",
            "executing",
            "ambiguous_effect",
            "published_unverified",
            "drift_blocked",
            "duplicate_blocked",
            "blocked_drift",
            "blocked_duplicate",
        )
        for status in statuses:
            with self.subTest(status=status):
                row = {
                    "schedule_id": "sch-review-" + status,
                    "campaign": "REVIEW-" + status,
                    "provider": "x",
                    "account_id": self.account,
                    "status": status,
                    "run_at": (self.now - timedelta(hours=2)).isoformat(),
                    "updated_at": self.now.isoformat(),
                }
                meaning = classify_schedule(row, now=self.now)
                self.assertTrue(meaning.requires_review)
                result = self._gate(row)
                self.assertFalse(result["healthy"])
                self.assertIn(row["schedule_id"], {b.get("schedule_id") for b in result["blockers"]})

    def test_safe_future_preflight_deferral_does_not_become_capacity_failure(self) -> None:
        row = {
            "schedule_id": "sch-deferred",
            "campaign": "DEFERRED",
            "provider": "x",
            "account_id": self.account,
            "status": "scheduled",
            "failure_class": "provider_unavailable",
            "run_at": (self.now - timedelta(minutes=5)).isoformat(),
            "retry_at": (self.now + timedelta(minutes=10)).isoformat(),
            "updated_at": self.now.isoformat(),
        }
        meaning = classify_schedule(row, now=self.now)
        self.assertEqual(meaning.state, "deferred")
        self.assertFalse(meaning.requires_review)
        result = self._gate(row)
        self.assertTrue(result["healthy"])
        self.assertEqual(result["blockers"], [])

    def test_verified_state_remains_non_blocking(self) -> None:
        meaning = classify_schedule(self.success_schedule, now=self.now)
        self.assertFalse(meaning.requires_review)
        result = trial.delivery_gate(
            "x", self.now, receipts=[self.success_receipt], schedules=[self.success_schedule]
        )
        self.assertTrue(result["healthy"])


if __name__ == "__main__":
    unittest.main()
