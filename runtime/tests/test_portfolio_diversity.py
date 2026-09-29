from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import ocpf_post.portfolio as portfolio_module
import ocpf_post.portfolio_cli as portfolio_cli
from ocpf_post.portfolio_cross_platform import plan_refill as production_plan_refill
from ocpf_post.portfolio_diversity import DEFAULT_DIVERSITY, _choose_diverse

UTC = timezone.utc
ZONE = ZoneInfo("Europe/London")
POLICY = {"daily_target": 20, "development_max": 6, "commercial_min": 8}
LANES = {"development": 0, "commercial": 0, "evergreen": 0}


def candidate(campaign: str, project: str, *, lane: str = "commercial", priority: int = 90, topic: str | None = None, family: str | None = None):
    return {
        "campaign": campaign,
        "project": project,
        "lane": lane,
        "priority": priority,
        "topic_key": topic or campaign,
        "family": family or project,
    }


def event(at: datetime, campaign: str, project: str, *, topic: str | None = None, family: str | None = None):
    return {
        "at": at,
        "campaign": campaign,
        "project": project,
        "topic_key": topic or campaign,
        "family": family or project,
    }


class PortfolioDiversityTests(unittest.TestCase):
    def setUp(self) -> None:
        self.slot = datetime(2026, 9, 9, 10, 0, tzinfo=UTC)

    def test_production_cli_keeps_diversity_under_cross_platform_wrapper(self) -> None:
        self.assertIs(portfolio_cli.plan_refill, production_plan_refill)
        self.assertIs(portfolio_module.plan_refill, production_plan_refill)

    def test_avoids_consecutive_same_project_when_alternative_exists(self) -> None:
        candidates = [
            candidate("A-2", "alpha", priority=99),
            candidate("B-1", "beta", priority=85),
        ]
        history = [event(self.slot - timedelta(hours=1), "A-1", "alpha")]
        chosen = _choose_diverse(
            candidates,
            provider_policy=POLICY,
            current_lanes=dict(LANES),
            slot=self.slot,
            events=history,
            diversity=dict(DEFAULT_DIVERSITY),
            zone=ZONE,
        )
        self.assertEqual(chosen["project"], "beta")

    def test_topic_cooldown_prevents_near_duplicate_variant(self) -> None:
        candidates = [
            candidate("A-Q", "alpha", priority=99, topic="alpha-topic"),
            candidate("B-1", "beta", priority=80, topic="beta-topic"),
        ]
        history = [event(self.slot - timedelta(hours=2), "A-I", "gamma", topic="alpha-topic")]
        chosen = _choose_diverse(
            candidates,
            provider_policy=POLICY,
            current_lanes=dict(LANES),
            slot=self.slot,
            events=history,
            diversity=dict(DEFAULT_DIVERSITY),
            zone=ZONE,
        )
        self.assertEqual(chosen["campaign"], "B-1")

    def test_urgent_development_can_preempt_diversity(self) -> None:
        candidates = [
            candidate("DEV-2", "alpha", lane="development", priority=99),
            candidate("B-1", "beta", priority=95),
        ]
        history = [event(self.slot - timedelta(minutes=30), "DEV-1", "alpha")]
        chosen = _choose_diverse(
            candidates,
            provider_policy=POLICY,
            current_lanes=dict(LANES),
            slot=self.slot,
            events=history,
            diversity=dict(DEFAULT_DIVERSITY),
            zone=ZONE,
        )
        self.assertEqual(chosen["campaign"], "DEV-2")

    def test_family_penalty_prefers_unrelated_product_when_scores_are_close(self) -> None:
        candidates = [
            candidate("OPS-2", "opstruth-chatgpt-plugin", priority=94, family="proof-state"),
            candidate("PDI-1", "public-decision-intelligence", priority=90, family="public-decision-intelligence"),
        ]
        history = [event(self.slot - timedelta(hours=1), "DONE-1", "donestate", family="proof-state")]
        chosen = _choose_diverse(
            candidates,
            provider_policy=POLICY,
            current_lanes=dict(LANES),
            slot=self.slot,
            events=history,
            diversity=dict(DEFAULT_DIVERSITY),
            zone=ZONE,
        )
        self.assertEqual(chosen["campaign"], "PDI-1")

    def test_diversity_relaxes_when_only_one_project_is_available(self) -> None:
        candidates = [candidate("A-2", "alpha", priority=90)]
        history = [event(self.slot - timedelta(hours=1), "A-1", "alpha")]
        chosen = _choose_diverse(
            candidates,
            provider_policy=POLICY,
            current_lanes=dict(LANES),
            slot=self.slot,
            events=history,
            diversity=dict(DEFAULT_DIVERSITY),
            zone=ZONE,
        )
        self.assertEqual(chosen["campaign"], "A-2")

    def test_same_day_project_penalty_balances_repeated_exposure(self) -> None:
        candidates = [
            candidate("A-3", "alpha", priority=92),
            candidate("B-1", "beta", priority=90),
        ]
        history = [
            event(self.slot - timedelta(hours=3), "A-1", "alpha"),
            event(self.slot - timedelta(hours=2), "C-1", "gamma"),
            event(self.slot - timedelta(hours=1), "A-2", "alpha"),
        ]
        chosen = _choose_diverse(
            candidates,
            provider_policy=POLICY,
            current_lanes=dict(LANES),
            slot=self.slot,
            events=history,
            diversity={**DEFAULT_DIVERSITY, "avoid_consecutive_project": False},
            zone=ZONE,
        )
        self.assertEqual(chosen["campaign"], "B-1")


if __name__ == "__main__":
    unittest.main()
