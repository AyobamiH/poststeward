from __future__ import annotations

import copy
import json
import os
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from ocpf_post import portfolio as base, portfolio_queue as queue
from ocpf_post.portfolio_cross_platform import plan_refill

UTC = timezone.utc
NOW = datetime(2026, 9, 10, 5, 0, tzinfo=UTC)


def candidate(name, project, provider="x", priority=72, lane="evergreen", **extra):
    return {"campaign": name, "project": project, "title": name, "provider": provider,
            "lane": lane, "priority": priority, "prepared_at": "2026-09-09T00:00:00Z",
            "expires_at": "2026-09-12T00:00:00Z", "topic_key": name, "family": project, **extra}


def fixture(candidates, target=4, providers=("x",)):
    policy = {**copy.deepcopy(base.DEFAULT_POLICY), "minimum_lead_minutes": 1,
              "providers": {p: {"daily_target": target, "window_start": "07:00", "window_end": "11:00",
                                 "development_max": 1, "commercial_min": 0} for p in providers}}
    counts = {"total": 0, "lanes": {"development": 0, "commercial": 0, "evergreen": 0}}
    inputs = {"now": queue._stamp(NOW), "horizon_minutes": 600, "candidates": candidates,
              "histories": {p: [] for p in providers},
              "day_counts": {p: {d: copy.deepcopy(counts) for d in ("2026-09-10", "2026-09-11")} for p in providers},
              "capacity": {p: {"local_day": "2026-09-10", "daily_budget_used": 0,
                               "daily_budget_remaining": target, "daily_target_met": False} for p in providers}}
    return inputs, policy


class QueueTests(unittest.TestCase):
    def test_live_legacy_matches_frozen_legacy(self):
        inputs, policy = fixture([candidate("a", "alpha"), candidate("b", "beta")])
        with patch.object(base, "delivery_candidates", return_value=inputs["candidates"]), \
             patch.object(queue.diversity, "_enrich", side_effect=lambda c: dict(c)), \
             patch.object(queue.diversity, "_history", return_value=[]), \
             patch.object(base, "_day_counts", side_effect=lambda *a: (0, {k: 0 for k in base.LANES})), \
             patch.object(base, "daily_capacity", return_value=inputs["capacity"]["x"]), \
             patch("ocpf_post.account_profiles.profiles", return_value={}):
            live = plan_refill(now=NOW, policy=policy, horizon_minutes=600)
        self.assertEqual(live, queue._legacy(inputs, policy))

    def test_snapshot_round_trip_is_offline_and_detects_tampering(self):
        inputs, policy = fixture([candidate("a", "alpha"), candidate("b", "beta")])
        with patch.object(queue, "capture_inputs", return_value=inputs), patch.object(base, "load_policy", return_value=policy):
            snapshot = json.loads(json.dumps(queue.snapshot(now=NOW, horizon_minutes=600)))
        with patch.object(base, "load_policy", side_effect=AssertionError("host read")), \
             patch.object(base, "delivery_candidates", side_effect=AssertionError("host read")), \
             patch.object(base, "_day_counts", side_effect=AssertionError("host read")), \
             patch.object(queue.diversity, "_history", side_effect=AssertionError("host read")), \
             patch.object(queue.diversity, "_enrich", side_effect=AssertionError("manifest read")):
            report = queue.replay(snapshot)
            self.assertTrue(report["baseline_reproduced"])
        snapshot["inputs"]["candidates"][0]["priority"] = 100
        with self.assertRaisesRegex(base.PortfolioError, "hash mismatch"):
            queue.replay(snapshot)

    def test_snapshot_engine_bump_rejects_old_fairness_semantics(self):
        inputs, policy = fixture([candidate("a", "alpha")])
        with patch.object(queue, "capture_inputs", return_value=inputs), patch.object(base, "load_policy", return_value=policy):
            snapshot = json.loads(json.dumps(queue.snapshot(now=NOW, horizon_minutes=600)))
        self.assertEqual(snapshot["engine"], "portfolio-queue-v12")
        snapshot["engine"] = "portfolio-queue-v11"
        with self.assertRaisesRegex(base.PortfolioError, "Unsupported queue snapshot"):
            queue.replay(snapshot)

    def test_capture_refuses_changing_inventory(self):
        inputs, policy = fixture([candidate("a", "alpha")])
        changed = copy.deepcopy(inputs)
        changed["candidates"] = []
        with patch.object(queue, "capture_inputs", side_effect=[inputs, changed]), patch.object(base, "load_policy", return_value=policy):
            with self.assertRaisesRegex(base.PortfolioError, "changed during capture"):
                queue.snapshot(now=NOW)

    def test_more_inventory_does_not_buy_more_project_turns(self):
        candidates = [candidate(f"a{i}", "alpha", priority=94) for i in range(20)]
        candidates += [candidate("b", "beta", priority=60), candidate("c", "gamma", priority=61)]
        inputs, policy = fixture(candidates)
        result = queue.fair_plan(inputs, policy)
        self.assertEqual({c["project"] for c in result["plan"][:3]}, {"alpha", "beta", "gamma"})
        self.assertEqual(len(result["plan"]), 4)
        self.assertEqual(inputs["day_counts"]["x"]["2026-09-10"]["total"], 0)

    def test_age_bands_beat_priority_within_project(self):
        inputs, policy = fixture([candidate("old", "alpha", priority=60), candidate("new", "alpha", priority=94,
                                  prepared_at="2026-09-10T04:00:00Z")])
        result = queue.fair_plan(inputs, policy)
        self.assertEqual(result["plan"][0]["campaign"], "old")

    def test_recent_service_is_counted_once_per_campaign(self):
        inputs, policy = fixture([candidate("b", "beta"), candidate("a", "alpha", priority=94)])
        events = [{"campaign": "previous", "project": "alpha", "family": "alpha", "topic_key": "previous",
                   "at": "2026-09-10T04:00:00Z"}]
        inputs["histories"]["x"] = events
        once = queue.fair_plan(inputs, policy)
        inputs["histories"]["x"] = events * 3
        twice = queue.fair_plan(inputs, policy)
        self.assertEqual(once["plan"], twice["plan"])
        self.assertEqual(once["plan"][0]["project"], "beta")

    def test_aged_waiting_crosses_soft_lane_boundary_within_project(self):
        aged = candidate(
            "aged-brief", "alpha", lane="evergreen", priority=40,
            queue_first_eligible_at="2026-09-08T00:00:00Z",
            expires_at="2026-09-15T18:00:00Z",
        )
        fresh = candidate(
            "fresh-commercial", "alpha", lane="commercial", priority=99,
            prepared_at="2026-09-10T04:30:00Z",
            expires_at="2026-09-15T00:00:00Z",
        )
        inputs, policy = fixture([aged, fresh])
        result = queue.fair_plan(inputs, policy)
        self.assertEqual(result["plan"][0]["campaign"], "aged-brief")
        decision = next(d for d in result["decisions"] if d.get("campaign") == "aged-brief")
        self.assertEqual(decision["reason"], "observed_waiting_threshold_overrides_soft_lane_balance")

    def test_aged_waiting_does_not_bypass_other_project_fairness(self):
        aged = candidate(
            "aged-brief", "alpha", lane="evergreen", priority=99,
            queue_first_eligible_at="2026-09-08T00:00:00Z",
            expires_at="2026-09-15T18:00:00Z",
        )
        other = candidate(
            "fresh-commercial", "beta", lane="commercial", priority=40,
            prepared_at="2026-09-10T04:30:00Z",
            expires_at="2026-09-15T00:00:00Z",
        )
        inputs, policy = fixture([aged, other])
        inputs["histories"]["x"] = [
            {"campaign": "served-alpha", "project": "alpha", "family": "alpha", "topic_key": "other",
             "at": "2026-09-10T04:00:00Z"},
        ]
        result = queue.fair_plan(inputs, policy)
        self.assertEqual(result["plan"][0]["campaign"], "fresh-commercial")

    def test_aged_cohort_uses_earlier_deadline_after_project_service_tie(self):
        earlier = candidate(
            "earlier", "alpha", priority=40,
            queue_first_eligible_at="2026-09-08T00:00:00Z",
            expires_at="2026-09-10T10:00:00Z",
        )
        later = candidate(
            "later", "alpha", priority=99,
            queue_first_eligible_at="2026-09-08T00:00:00Z",
            expires_at="2026-09-10T11:00:00Z",
        )
        inputs, policy = fixture([earlier, later])
        result = queue.fair_plan(inputs, policy)
        self.assertEqual(result["plan"][0]["campaign"], "earlier")

    def test_near_deadline_gets_bounded_service_across_soft_lane(self):
        deadline = candidate(
            "deadline", "alpha", lane="evergreen", priority=99,
            prepared_at="2026-09-10T04:30:00Z",
            expires_at="2026-09-10T10:00:00Z",
        )
        normal = candidate(
            "normal-commercial", "alpha", lane="commercial", priority=40,
            prepared_at="2026-09-10T04:30:00Z",
            expires_at="2026-09-15T00:00:00Z",
        )
        inputs, policy = fixture([deadline, normal])
        result = queue.fair_plan(inputs, policy)
        self.assertEqual(result["plan"][0]["campaign"], "deadline")
        decision = next(d for d in result["decisions"] if d.get("campaign") == "deadline")
        self.assertEqual(decision["reason"], "bounded_rescue_service")
        self.assertIn("normal-commercial", [r["campaign"] for r in result["plan"]])

    def test_rescue_turn_drains_earliest_valid_warming_content(self):
        early = candidate(
            "rescue-early", "alpha", lane="evergreen", priority=20,
            queue_first_eligible_at="2026-09-08T00:00:00Z",
            expires_at="2026-09-10T08:00:00Z",
        )
        later = candidate(
            "rescue-later", "beta", lane="commercial", priority=99,
            queue_first_eligible_at="2026-09-08T00:00:00Z",
            expires_at="2026-09-10T09:00:00Z",
        )
        ordinary = candidate(
            "ordinary", "gamma", lane="commercial", priority=100,
            expires_at="2026-09-15T00:00:00Z",
        )
        inputs, policy = fixture([ordinary, later, early])
        inputs["day_counts"]["x"]["2026-09-10"]["total"] = 2
        result = queue.fair_plan(inputs, policy)
        self.assertEqual(result["plan"][0]["campaign"], "rescue-early")
        decision = next(d for d in result["decisions"] if d.get("campaign") == "rescue-early")
        self.assertEqual(decision["reason"], "bounded_rescue_service")
        self.assertEqual(result["service_policy"]["cycle"], ["timely", "aged", "rescue", "standard"])
        self.assertFalse(result["service_policy"]["rescue"]["extends_expiry"])
        self.assertLessEqual(
            inputs["day_counts"]["x"]["2026-09-10"]["total"] + len(result["plan"]),
            policy["providers"]["x"]["daily_target"],
        )

    def test_capacity_feasibility_starts_rescue_before_fixed_deadline_window(self):
        expiry = "2026-09-11T10:00:00Z"
        candidates = [
            candidate(
                f"risk-{i}", f"p{i}", lane="evergreen",
                queue_first_eligible_at="2026-09-08T00:00:00Z",
                expires_at=expiry,
            )
            for i in range(9)
        ]
        inputs, policy = fixture(candidates, target=4)
        state = queue._rescue_state(
            candidates[0], candidates, datetime(2026, 9, 10, 7, tzinfo=UTC),
            "x", policy["providers"]["x"], inputs["day_counts"],
            queue.ZoneInfo(policy["timezone"]), {"wait_warning_hours": 24, "deadline_warning_hours": 24},
        )
        self.assertFalse(state["near_deadline"])
        self.assertTrue(state["capacity_pressure"])
        self.assertTrue(state["rescue"])
        self.assertEqual(state["trigger"], "capacity_feasibility")
        self.assertGreater(state["due_count"], state["authorised_slots_before_expiry"])

        inputs["day_counts"]["x"]["2026-09-10"]["total"] = 2
        result = queue.fair_plan(inputs, policy)
        first = result["plan"][0]
        decision = next(d for d in result["decisions"] if d.get("campaign") == first["campaign"])
        self.assertEqual(decision["reason"], "bounded_rescue_capacity_feasibility")
        self.assertEqual(
            result["service_policy"]["rescue"]["trigger"],
            "deadline_warning_or_capacity_feasibility",
        )
        self.assertFalse(result["service_policy"]["rescue"]["quota_changed"])

    def test_rescue_turn_never_converts_development_into_warming_inventory(self):
        development = candidate(
            "dev-near-expiry", "alpha", lane="development", priority=99,
            expires_at="2026-09-10T08:00:00Z",
        )
        evergreen = candidate(
            "evergreen-rescue", "beta", lane="evergreen", priority=30,
            queue_first_eligible_at="2026-09-08T00:00:00Z",
            expires_at="2026-09-10T09:00:00Z",
        )
        inputs, policy = fixture([development, evergreen])
        inputs["day_counts"]["x"]["2026-09-10"] = {
            "total": 2,
            "lanes": {"development": 1, "commercial": 0, "evergreen": 1},
        }
        result = queue.fair_plan(inputs, policy)
        self.assertEqual(result["plan"][0]["campaign"], "evergreen-rescue")
        self.assertNotIn("dev-near-expiry", [row["campaign"] for row in result["plan"]])

    def test_capacity_pressure_does_not_remove_aged_service_entitlement(self):
        old = candidate(
            "old-risk", "alpha", lane="evergreen", priority=20,
            queue_first_eligible_at="2026-09-08T00:00:00Z",
            expires_at="2026-09-11T10:00:00Z",
        )
        arrivals = [
            candidate(
                f"new-{i}", f"p{i}", lane="evergreen", priority=94,
                prepared_at="2026-09-10T04:00:00Z",
                expires_at="2026-09-10T12:00:00Z",
            )
            for i in range(12)
        ]
        inputs, policy = fixture([old, *arrivals])
        result = queue.fair_plan(inputs, policy)
        self.assertIn("old-risk", [row["campaign"] for row in result["plan"][:2]])
        self.assertLessEqual(len(result["plan"]), policy["providers"]["x"]["daily_target"])

    def test_aged_development_cannot_bypass_hard_development_cap(self):
        aged_dev = candidate(
            "aged-dev", "alpha", lane="development", priority=99,
            queue_first_eligible_at="2026-09-08T00:00:00Z",
            expires_at="2026-09-11T12:00:00Z",
        )
        evergreen = candidate("evergreen", "beta", lane="evergreen", priority=50)
        inputs, policy = fixture([aged_dev, evergreen])
        inputs["day_counts"]["x"]["2026-09-10"] = {
            "total": 1,
            "lanes": {"development": 1, "commercial": 0, "evergreen": 0},
        }
        result = queue.fair_plan(inputs, policy)
        self.assertEqual(result["plan"][0]["campaign"], "evergreen")
        self.assertNotIn("aged-dev", [row["campaign"] for row in result["plan"]])

    def test_urgent_allowance_and_daily_budget_are_hard_limits(self):
        candidates = [candidate(f"dev{i}", "alpha", lane="development", priority=99) for i in range(5)]
        candidates += [candidate(f"e{i}", "beta") for i in range(5)]
        inputs, policy = fixture(candidates)
        inputs["day_counts"]["x"]["2026-09-10"]["total"] = 2
        result = queue.fair_plan(inputs, policy)
        self.assertEqual(len(result["plan"]), 2)
        self.assertEqual(result["plan"][0]["lane"], "evergreen")
        self.assertLessEqual(sum(c["lane"] == "development" for c in result["plan"]), 1)
        self.assertTrue(any(d["reason"] == "daily_budget_used" for d in result["decisions"]))

    def test_admission_flow_can_exceed_legacy_target_but_never_candidate_supply(self):
        candidates = [candidate(f"a{i}", f"p{i}") for i in range(12)]
        inputs, policy = fixture(candidates, target=4)
        settings = policy["providers"]["x"]
        settings.update(
            flow_mode="admission",
            hard_daily_ceiling=100,
            minimum_spacing_minutes=10,
        )
        result = queue.fair_plan(inputs, policy)
        self.assertGreater(len(result["plan"]), 4)
        self.assertEqual(len(result["plan"]), len(candidates))
        self.assertEqual(result["capacity"]["x"]["flow_mode"], "admission")
        self.assertEqual(result["capacity"]["x"]["daily_ceiling"], 100)

    def test_additional_account_inherits_provider_admission_ceiling(self):
        policy = copy.deepcopy(base.DEFAULT_POLICY)
        row = {
            "provider": "threads",
            "policy": {
                "daily_target": 20,
                "window_start": "09:00",
                "window_end": "18:00",
                "development_max": 4,
                "commercial_min": 8,
                "minimum_spacing_minutes": 12,
            },
        }
        scoped = queue._scoped_account_policy(policy, row)
        self.assertEqual(scoped["flow_mode"], "admission")
        self.assertEqual(scoped["hard_daily_ceiling"], 100)
        self.assertEqual(scoped["daily_target"], 20)
        self.assertEqual(scoped["window_start"], "09:00")
        self.assertEqual(scoped["minimum_spacing_minutes"], 12)

    def test_linkedin_admission_flow_reserves_beyond_legacy_six_in_rolling_horizon(self):
        candidates = [
            candidate(f"li-{i}", f"project-{i}", provider="linkedin")
            for i in range(20)
        ]
        inputs, policy = fixture(candidates, target=6, providers=("linkedin",))
        settings = policy["providers"]["linkedin"]
        settings.update(
            flow_mode="admission",
            hard_daily_ceiling=100,
            window_start="08:00",
            window_end="20:00",
            minimum_spacing_minutes=5,
            development_max=2,
            commercial_min=3,
        )
        inputs["now"] = "2026-09-10T09:00:00Z"
        inputs["horizon_minutes"] = 75
        result = queue.fair_plan(inputs, policy)
        self.assertGreater(len(result["plan"]), 6)
        self.assertTrue(all(row["provider"] == "linkedin" for row in result["plan"]))
        self.assertEqual(result["capacity"]["linkedin"]["flow_mode"], "admission")
        self.assertEqual(result["capacity"]["linkedin"]["daily_ceiling"], 100)

    def test_admission_flow_scales_development_guard_instead_of_removing_it(self):
        candidates = [
            candidate(f"dev{i}", f"dev-project-{i}", lane="development", priority=99)
            for i in range(80)
        ]
        candidates += [candidate(f"evergreen{i}", f"evergreen-project-{i}") for i in range(40)]
        inputs, policy = fixture(candidates, target=20)
        settings = policy["providers"]["x"]
        settings.update(
            flow_mode="admission",
            hard_daily_ceiling=100,
            minimum_spacing_minutes=5,
            development_max=6,
            commercial_min=8,
        )
        result = queue.fair_plan(inputs, policy)
        development = sum(row["lane"] == "development" for row in result["plan"])
        self.assertLessEqual(development, 30)
        self.assertGreater(sum(row["lane"] == "evergreen" for row in result["plan"]), 0)

    def test_admission_flow_hard_ceiling_is_still_enforced(self):
        candidates = [candidate(f"a{i}", f"p{i}") for i in range(20)]
        inputs, policy = fixture(candidates, target=4)
        settings = policy["providers"]["x"]
        settings.update(
            flow_mode="admission",
            hard_daily_ceiling=7,
            minimum_spacing_minutes=10,
        )
        result = queue.fair_plan(inputs, policy)
        self.assertEqual(len(result["plan"]), 7)
        self.assertEqual(result["capacity"]["x"]["daily_ceiling"], 7)

    def test_day_boundary_reopens_budget_without_raising_target(self):
        inputs, policy = fixture([candidate(f"a{i}", "alpha") for i in range(10)])
        inputs["now"] = "2026-09-10T09:30:00Z"
        inputs["horizon_minutes"] = 1440
        inputs["day_counts"]["x"]["2026-09-10"]["total"] = 4
        inputs["day_counts"]["x"]["2026-09-12"] = copy.deepcopy(inputs["day_counts"]["x"]["2026-09-11"])
        result = queue.fair_plan(inputs, policy)
        self.assertEqual(len(result["plan"]), 3)
        self.assertTrue(all(c["run_at"].startswith("2026-09-11") for c in result["plan"]))

    def test_expiry_at_slot_and_waiting_report(self):
        inputs, policy = fixture([candidate("expires", "alpha", expires_at="2026-09-10T06:00:00Z"), candidate("valid", "beta")])
        result = queue.fair_plan(inputs, policy)
        self.assertEqual([c["campaign"] for c in result["plan"]], ["valid"])
        explanation = queue.explanations(inputs, result)[0]
        self.assertEqual(explanation["expiry_risk"], "unselected_expires_within_horizon")
        self.assertIsNone(explanation["projected_run_at"])
        self.assertEqual(explanation["waiting_age_hours"], 29)

    def test_existing_reservation_slot_is_preserved(self):
        inputs, policy = fixture([candidate(f"a{i}", "alpha") for i in range(5)])
        inputs["histories"]["x"] = [{"at": "2026-09-10T06:00:00Z", "project": "beta",
                                       "campaign": "reserved", "family": "beta", "topic_key": "other"}]
        inputs["day_counts"]["x"]["2026-09-10"]["total"] = 1
        result = queue.fair_plan(inputs, policy)
        self.assertEqual(len(result["plan"]), 3)
        self.assertNotIn("2026-09-10T06:00:00Z", [c["run_at"] for c in result["plan"]])

    def test_cross_provider_rotation_cannot_reintroduce_expired_copy(self):
        candidates = [candidate("same", "alpha", p, priority=90, topic_key="same") for p in ("x", "threads")]
        candidates += [candidate("other", "beta", "threads", priority=60),
                       candidate("expired", "gamma", "threads", priority=100, expires_at="2026-09-10T05:30:00Z")]
        inputs, policy = fixture(candidates, providers=("x", "threads"))
        result = queue.fair_plan(inputs, policy)
        first = result["plan"][:2]
        self.assertEqual([c["campaign"] for c in first], ["same", "other"])
        self.assertNotIn("expired", [c["campaign"] for c in result["plan"]])

    def test_policy_change_preserves_other_settings_and_rolls_back(self):
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {"OCPF_POST_PORTFOLIO_POLICY": tmp + "/policy.json"}):
            policy = copy.deepcopy(base.DEFAULT_POLICY)
            policy["providers"]["x"]["daily_target"] = 30
            base.write_private_json(Path(tmp) / "policy.json", policy)
            queue.set_selection("fair")
            self.assertEqual(base.load_policy(), {**policy, "selection": "fair"})
            queue.set_selection("legacy")
            self.assertEqual(base.load_policy(), {**policy, "selection": "legacy"})
            self.assertEqual((Path(tmp) / "policy.json").stat().st_mode & 0o777, 0o600)

    def test_fair_runtime_dispatch_uses_same_replayed_planner(self):
        inputs, policy = fixture([candidate("a", "alpha")])
        policy["selection"] = "fair"
        with patch.object(queue, "capture_inputs", return_value=inputs) as capture:
            result = plan_refill(now=NOW, policy=policy, horizon_minutes=600)
        self.assertEqual(result, queue.fair_plan(inputs, policy))
        capture.assert_called_once()


if __name__ == "__main__":
    unittest.main()
