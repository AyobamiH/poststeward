from __future__ import annotations

import unittest
from datetime import datetime, timezone
from unittest.mock import patch

import ocpf_post.portfolio as portfolio_module
import ocpf_post.portfolio_cli as portfolio_cli
from ocpf_post.portfolio_cross_platform import plan_refill as cross_plan_refill

UTC = timezone.utc


def item(campaign: str, provider: str, project: str, *, lane: str = "commercial", priority: int = 90, run_at: str = "2026-09-09T08:00:00Z") -> dict:
    return {
        "campaign": campaign,
        "provider": provider,
        "project": project,
        "title": campaign,
        "lane": lane,
        "priority": priority,
        "run_at": run_at,
        "display_timezone": "Europe/London",
    }


class CrossPlatformPlannerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.now = datetime(2026, 9, 9, 6, 0, tzinfo=UTC)
        profiles = patch("ocpf_post.account_profiles.profiles", return_value={})
        profiles.start()
        self.addCleanup(profiles.stop)
        self.policy = {
            "schema_version": 1,
            "timezone": "Europe/London",
            "horizon_minutes": 180,
            "minimum_lead_minutes": 1,
            "planner_interval_minutes": 15,
            "providers": {
                "x": {"daily_target": 20, "window_start": "07:00", "window_end": "23:00", "development_max": 6, "commercial_min": 8},
                "threads": {"daily_target": 20, "window_start": "07:00", "window_end": "23:00", "development_max": 6, "commercial_min": 8},
            },
            "reply_targets": {"x": 10, "threads": 10},
            "cross_platform": {"topic_cooldown_minutes": 90},
        }

    def test_production_cli_uses_cross_platform_planner(self) -> None:
        self.assertIs(portfolio_cli.plan_refill, cross_plan_refill)
        self.assertIs(portfolio_module.plan_refill, cross_plan_refill)

    def test_same_topic_is_replaced_on_second_provider(self) -> None:
        base_plan = {
            "generated_at": "2026-09-09T06:00:00Z",
            "horizon_minutes": 180,
            "timezone": "Europe/London",
            "plan": [
                item("PDI-AUTO-01I-ABC1234", "x", "public-decision-intelligence"),
                item("PDI-AUTO-01I-ABC1234", "threads", "public-decision-intelligence"),
            ],
            "capacity": {},
            "diversity": {},
            "reply_boundary": "",
        }
        candidates = [
            {"campaign": "PDI-AUTO-01I-ABC1234", "provider": "x", "project": "public-decision-intelligence", "title": "PDI", "lane": "commercial", "priority": 94},
            {"campaign": "PDI-AUTO-01I-ABC1234", "provider": "threads", "project": "public-decision-intelligence", "title": "PDI", "lane": "commercial", "priority": 94},
            {"campaign": "OCPF-AUTO-01I-DEF5678", "provider": "threads", "project": "oneclickpostfactory", "title": "OCPF", "lane": "commercial", "priority": 93},
        ]
        def enrich(value):
            result = dict(value)
            campaign = result["campaign"]
            result["topic_key"] = "pdi-topic" if campaign.startswith("PDI-") else "ocpf-topic"
            result["family"] = result["project"]
            return result
        with patch("ocpf_post.portfolio_cross_platform.diversity.plan_refill", return_value=base_plan), \
             patch("ocpf_post.portfolio_cross_platform.base.delivery_candidates", return_value=candidates), \
             patch("ocpf_post.portfolio_cross_platform.diversity._enrich", side_effect=enrich):
            result = cross_plan_refill(now=self.now, horizon_minutes=180, policy=self.policy)
        selected = {(row["provider"], row["campaign"]) for row in result["plan"]}
        self.assertIn(("x", "PDI-AUTO-01I-ABC1234"), selected)
        self.assertIn(("threads", "OCPF-AUTO-01I-DEF5678"), selected)
        self.assertEqual(len(result["cross_platform_replacements"]), 1)

    def test_urgent_development_may_mirror_across_platforms(self) -> None:
        base_plan = {
            "generated_at": "2026-09-09T06:00:00Z",
            "horizon_minutes": 180,
            "timezone": "Europe/London",
            "plan": [
                item("DEV-EVENT-ABC", "x", "alpha", lane="development", priority=99),
                item("DEV-EVENT-ABC", "threads", "alpha", lane="development", priority=99),
            ],
            "capacity": {},
            "diversity": {},
            "reply_boundary": "",
        }
        candidates = [
            {"campaign": "DEV-EVENT-ABC", "provider": "x", "project": "alpha", "title": "DEV", "lane": "development", "priority": 99},
            {"campaign": "DEV-EVENT-ABC", "provider": "threads", "project": "alpha", "title": "DEV", "lane": "development", "priority": 99},
        ]
        def enrich(value):
            result = dict(value)
            result["topic_key"] = "dev-topic"
            result["family"] = "alpha"
            return result
        with patch("ocpf_post.portfolio_cross_platform.diversity.plan_refill", return_value=base_plan), \
             patch("ocpf_post.portfolio_cross_platform.base.delivery_candidates", return_value=candidates), \
             patch("ocpf_post.portfolio_cross_platform.diversity._enrich", side_effect=enrich):
            result = cross_plan_refill(now=self.now, horizon_minutes=180, policy=self.policy)
        self.assertEqual({row["campaign"] for row in result["plan"]}, {"DEV-EVENT-ABC"})
        self.assertEqual(result["cross_platform_replacements"], [])


if __name__ == "__main__":
    unittest.main()
