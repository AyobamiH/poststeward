from __future__ import annotations

import os
import tempfile
import unittest
from datetime import datetime, timezone

from ocpf_post.portfolio import DEFAULT_POLICY, apply_refill, delivery_candidates, plan_refill, portfolio_status

UTC = timezone.utc


class PortfolioAllocatorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.old_state = os.environ.get("OCPF_POST_STATE_DIR")
        self.old_config = os.environ.get("OCPF_POST_CONFIG_DIR")
        os.environ["OCPF_POST_STATE_DIR"] = self.tmp.name
        os.environ["OCPF_POST_CONFIG_DIR"] = self.tmp.name
        self.now = datetime(2026, 9, 9, 7, 0, tzinfo=UTC)

    def tearDown(self) -> None:
        if self.old_state is None:
            os.environ.pop("OCPF_POST_STATE_DIR", None)
        else:
            os.environ["OCPF_POST_STATE_DIR"] = self.old_state
        if self.old_config is None:
            os.environ.pop("OCPF_POST_CONFIG_DIR", None)
        else:
            os.environ["OCPF_POST_CONFIG_DIR"] = self.old_config
        self.tmp.cleanup()

    def _x_only_policy(self) -> dict:
        return {"schema_version": 1, "timezone": "Europe/London", "horizon_minutes": 180, "minimum_lead_minutes": 1, "planner_interval_minutes": 15, "providers": {"x": {"daily_target": 3, "window_start": "08:00", "window_end": "12:00", "development_max": 1, "commercial_min": 2}}, "reply_targets": {"x": 10}}

    def test_seeded_portfolio_campaigns_are_opted_in(self) -> None:
        ids = {item["campaign"] for item in delivery_candidates(now=self.now)}
        self.assertIn("CAPINT-001", ids)
        self.assertIn("OCO-001", ids)
        self.assertIn("TWW-001", ids)
        self.assertIn("AGENTPROOF-001", ids)
        self.assertNotIn("OCPF-011", ids)

    def test_plan_is_dry_and_respects_provider_target(self) -> None:
        result = plan_refill(now=self.now, horizon_minutes=180, policy=self._x_only_policy())
        self.assertLessEqual(len(result["plan"]), 3)
        self.assertTrue(all(item["provider"] == "x" for item in result["plan"]))
        self.assertEqual(result["capacity"]["x"]["reply_target"], 10)

    def test_apply_only_creates_reservations_through_scheduler_boundary(self) -> None:
        fake_records = []
        def fake_create_schedule(*, campaign, provider, at, timezone_name, now):
            record = {"schedule_id": f"sch-{campaign}-{provider}", "campaign": campaign, "provider": provider, "run_at": at, "status": "scheduled"}
            fake_records.append(record)
            return record
        result = apply_refill(
            now=self.now,
            horizon_minutes=180,
            policy=self._x_only_policy(),
            schedule_creator=fake_create_schedule,
        )
        self.assertEqual(result["errors"], [])
        self.assertEqual(result["scheduled"], fake_records)

    def test_status_exposes_post_and_reply_targets(self) -> None:
        result = portfolio_status(now=self.now, policy=DEFAULT_POLICY)
        self.assertEqual(result["targets"]["x"]["posts_per_day"], 20)
        self.assertEqual(result["targets"]["x"]["flow_mode"], "admission")
        self.assertEqual(result["targets"]["x"]["hard_daily_ceiling"], 100)
        self.assertEqual(result["targets"]["x"]["replies_per_day"], 10)
        self.assertEqual(result["targets"]["threads"]["posts_per_day"], 20)
        self.assertEqual(result["targets"]["threads"]["hard_daily_ceiling"], 100)
        self.assertEqual(result["targets"]["linkedin"]["posts_per_day"], 6)
        self.assertEqual(result["targets"]["linkedin"]["hard_daily_ceiling"], 100)
        self.assertEqual(result["refill_horizon_minutes"], 75)
        self.assertEqual(result["active_schedules_by_provider"], {"x": 0, "threads": 0, "linkedin": 0})
        self.assertEqual(
            result["active_schedules_in_refill_horizon_by_provider"],
            {"x": 0, "threads": 0, "linkedin": 0},
        )
        self.assertEqual(
            result["allocator_owned_active_by_provider"],
            {"x": 0, "threads": 0, "linkedin": 0},
        )
        self.assertEqual(
            result["next_active_run_at_by_provider"],
            {"x": None, "threads": None, "linkedin": None},
        )
        self.assertIn("separate engagement workflow", result["reply_boundary"])


if __name__ == "__main__":
    unittest.main()
