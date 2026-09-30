from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone

from ocpf_post import scheduler, state
from ocpf_post.schedule_semantics import (
    ACTIVE_STATUSES,
    TERMINAL_EFFECT_STATUSES,
    classify_receipt,
    classify_schedule,
    due_actionable,
)

UTC = timezone.utc


class ScheduleSemanticsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.now = datetime(2026, 9, 12, 10, 0, tzinfo=UTC)

    def record(self, **changes):
        row = {
            "status": "scheduled",
            "run_at": (self.now - timedelta(minutes=1)).isoformat().replace("+00:00", "Z"),
        }
        row.update(changes)
        return row

    def test_scheduler_and_state_import_authoritative_sets(self) -> None:
        self.assertEqual(scheduler.ACTIVE_STATUSES, set(ACTIVE_STATUSES))
        self.assertEqual(state.TERMINAL_EFFECT_STATUSES, TERMINAL_EFFECT_STATUSES)

    def test_typed_preflight_deferral_is_not_actionable_before_retry_at(self) -> None:
        row = self.record(
            failure_class="provider_unavailable",
            retry_at=(self.now + timedelta(minutes=5)).isoformat().replace("+00:00", "Z"),
        )
        meaning = classify_schedule(row, now=self.now)
        self.assertEqual(meaning.state, "deferred")
        self.assertTrue(meaning.active)
        self.assertTrue(meaning.automatic_retry_safe)
        self.assertFalse(meaning.actionable)
        self.assertFalse(due_actionable(row, now=self.now))

    def test_typed_preflight_deferral_becomes_actionable_after_retry_window(self) -> None:
        row = self.record(
            failure_class="provider_unavailable",
            retry_at=(self.now - timedelta(seconds=1)).isoformat().replace("+00:00", "Z"),
        )
        meaning = classify_schedule(row, now=self.now)
        self.assertEqual(meaning.state, "actionable_deferred")
        self.assertTrue(meaning.actionable)
        self.assertTrue(meaning.automatic_retry_safe)
        self.assertTrue(due_actionable(row, now=self.now))

    def test_provider_circuit_deferral_is_retry_safe_only_before_consequence(self) -> None:
        row = self.record(
            failure_class="provider_circuit_open",
            circuit_failure_class="provider_usage_limit",
            retry_at=(self.now + timedelta(minutes=15)).isoformat().replace("+00:00", "Z"),
        )
        meaning = classify_schedule(row, now=self.now)
        self.assertEqual(meaning.state, "deferred")
        self.assertEqual(meaning.consequence_stage, "pre_consequence")
        self.assertTrue(meaning.active)
        self.assertTrue(meaning.automatic_retry_safe)
        self.assertFalse(meaning.actionable)
        self.assertFalse(due_actionable(row, now=self.now))

    def test_plain_scheduled_work_is_actionable_but_not_retry_authority(self) -> None:
        meaning = classify_schedule(self.record(), now=self.now)
        self.assertEqual(meaning.consequence_stage, "pre_consequence")
        self.assertTrue(meaning.actionable)
        self.assertFalse(meaning.automatic_retry_safe)

    def test_credential_rejection_and_historical_failure_are_terminal(self) -> None:
        meaning = classify_schedule(self.record(status="failed", detail="invalid token"), now=self.now)
        self.assertTrue(meaning.terminal)
        self.assertFalse(meaning.actionable)
        self.assertFalse(meaning.automatic_retry_safe)
        self.assertEqual(meaning.consequence_stage, "pre_consequence")

    def test_executing_is_consequence_ambiguous_and_never_retryable(self) -> None:
        meaning = classify_schedule(self.record(status="executing"), now=self.now)
        self.assertEqual(meaning.consequence_stage, "consequence_started")
        self.assertTrue(meaning.ambiguous)
        self.assertTrue(meaning.requires_review)
        self.assertFalse(meaning.automatic_retry_safe)
        self.assertFalse(meaning.actionable)

    def test_published_unverified_is_terminal_and_non_retryable(self) -> None:
        meaning = classify_schedule(self.record(status="published_unverified", post_id="p1"), now=self.now)
        self.assertTrue(meaning.terminal)
        self.assertTrue(meaning.published)
        self.assertTrue(meaning.requires_review)
        self.assertFalse(meaning.readback_verified)
        self.assertFalse(meaning.automatic_retry_safe)
        receipt = classify_receipt({"status": "published_unverified", "post_id": "p1"})
        self.assertTrue(receipt["terminal"])
        self.assertFalse(receipt["automatic_retry_safe"])

    def test_published_verified_requires_truthful_readback_flag(self) -> None:
        good = classify_schedule(self.record(status="published_verified", readback_verified=True), now=self.now)
        bad = classify_schedule(self.record(status="published_verified", readback_verified=False), now=self.now)
        self.assertTrue(good.readback_verified)
        self.assertFalse(good.requires_review)
        self.assertFalse(bad.readback_verified)

    def test_ambiguous_effect_is_terminal_and_non_retryable(self) -> None:
        meaning = classify_schedule(self.record(status="ambiguous_effect"), now=self.now)
        self.assertTrue(meaning.terminal)
        self.assertTrue(meaning.ambiguous)
        self.assertTrue(meaning.requires_review)
        self.assertFalse(meaning.automatic_retry_safe)

    def test_unknown_state_fails_towards_review_not_action(self) -> None:
        meaning = classify_schedule(self.record(status="mystery"), now=self.now)
        self.assertEqual(meaning.state, "unknown")
        self.assertTrue(meaning.requires_review)
        self.assertTrue(meaning.ambiguous)
        self.assertFalse(meaning.actionable)
        self.assertFalse(meaning.automatic_retry_safe)


if __name__ == "__main__":
    unittest.main()
