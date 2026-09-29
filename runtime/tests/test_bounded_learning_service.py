from __future__ import annotations

import copy
import unittest
from datetime import datetime, timezone

from ocpf_post import portfolio as base
from ocpf_post import portfolio_queue as queue

UTC = timezone.utc
NOW = datetime(2026, 9, 10, 5, 0, tzinfo=UTC)


def candidate(name, *, priority=72, lane="evergreen", learning_role=None, project="alpha"):
    row = {
        "campaign": name,
        "project": project,
        "title": name,
        "provider": "x",
        "lane": lane,
        "priority": priority,
        "prepared_at": "2026-09-09T00:00:00Z",
        "expires_at": "2026-09-12T00:00:00Z",
        "topic_key": name,
        "family": project,
        "account_id": "123",
        "text_sha256": "sha-" + name,
    }
    if learning_role:
        row["learning_role"] = learning_role
    return row


def fixture(candidates, *, target=8):
    policy = {
        **copy.deepcopy(base.DEFAULT_POLICY),
        "minimum_lead_minutes": 1,
        "providers": {
            "x": {
                "daily_target": target,
                "window_start": "07:00",
                "window_end": "15:00",
                "development_max": 1,
                "commercial_min": 0,
            }
        },
    }
    counts = {"total": 0, "lanes": {"development": 0, "commercial": 0, "evergreen": 0}}
    inputs = {
        "now": queue._stamp(NOW),
        "horizon_minutes": 720,
        "candidates": candidates,
        "histories": {"x": []},
        "day_counts": {
            "x": {
                "2026-09-10": copy.deepcopy(counts),
                "2026-09-11": copy.deepcopy(counts),
            }
        },
        "capacity": {
            "x": {
                "local_day": "2026-09-10",
                "daily_budget_used": 0,
                "daily_budget_remaining": target,
                "daily_target_met": False,
            }
        },
    }
    return inputs, policy


class LearningRoleTests(unittest.TestCase):
    def manifest(self, *, variant="insight", arm="practical", predecessor=None, sha="payload"):
        source_id = f"sample-README-rev-1-{variant}"
        source = {
            "type": "repository_product_truth",
            "source_id": source_id,
            "source_sha": "rev",
            "comparison_variant": arm,
        }
        if predecessor:
            source["sampling_predecessor"] = predecessor
        return {
            "project": "sample",
            "payload_sha256": {"x": sha},
            "allocation": {"lane": "evergreen"},
            "source": source,
        }

    def test_only_explicit_frozen_experiment_inventory_gets_learning_role(self):
        row = {"provider": "x", "text_sha256": "payload"}
        self.assertEqual(queue._learning_role(row, self.manifest()), "baseline")
        self.assertEqual(
            queue._learning_role(
                row,
                self.manifest(variant="practical", predecessor="sample-README-rev-1-insight"),
            ),
            "challenger",
        )

        legacy = self.manifest()
        legacy["source"].pop("comparison_variant")
        self.assertIsNone(queue._learning_role(row, legacy))
        self.assertIsNone(queue._learning_role({**row, "text_sha256": "different"}, self.manifest()))

    def test_invalid_challenger_arm_is_not_promoted(self):
        row = {"provider": "x", "text_sha256": "payload"}
        manifest = self.manifest(variant="question", arm="practical", predecessor="baseline")
        self.assertIsNone(queue._learning_role(row, manifest))


class BoundedLearningServiceTests(unittest.TestCase):
    def test_learning_work_gets_one_existing_standard_turn_without_extra_capacity(self):
        learning = candidate("learn", priority=1, learning_role="baseline")
        ordinary = [candidate(f"ordinary-{i}", priority=99) for i in range(10)]
        inputs, policy = fixture([learning, *ordinary], target=8)

        result = queue.fair_plan(inputs, policy)
        selected = [row["campaign"] for row in result["plan"]]
        learning_decision = next(d for d in result["decisions"] if d.get("campaign") == "learn")

        self.assertEqual(len(result["plan"]), 8)
        self.assertIn("learn", selected)
        self.assertEqual(learning_decision["reason"], "bounded_learning_service")
        self.assertFalse(result["service_policy"]["capacity_changed"])
        self.assertEqual(result["service_policy"]["learning_standard_turn"]["modulus"], 8)
        self.assertEqual(result["service_policy"]["learning_standard_turn"]["remainder"], 3)

    def test_no_learning_candidate_borrows_the_turn_for_ordinary_work(self):
        inputs, policy = fixture([candidate(f"ordinary-{i}", priority=50 + i) for i in range(10)], target=8)
        result = queue.fair_plan(inputs, policy)
        self.assertEqual(len(result["plan"]), 8)
        self.assertFalse(any(d.get("reason") == "bounded_learning_service" for d in result["decisions"]))

    def test_hard_development_cap_still_blocks_learning_candidate(self):
        learning = candidate("learn-dev", priority=99, lane="development", learning_role="challenger")
        normal = candidate("normal", priority=1, project="beta")
        inputs, policy = fixture([learning, normal], target=4)
        inputs["day_counts"]["x"]["2026-09-10"] = {
            "total": 1,
            "lanes": {"development": 1, "commercial": 0, "evergreen": 0},
        }
        result = queue.fair_plan(inputs, policy)
        self.assertNotIn("learn-dev", [row["campaign"] for row in result["plan"]])
        self.assertIn("normal", [row["campaign"] for row in result["plan"]])

    def test_daily_budget_remains_a_hard_limit(self):
        learning = candidate("learn", priority=99, learning_role="baseline")
        inputs, policy = fixture([learning, candidate("ordinary", priority=1)], target=4)
        inputs["day_counts"]["x"]["2026-09-10"]["total"] = 3
        result = queue.fair_plan(inputs, policy)
        self.assertLessEqual(len(result["plan"]), 1)
        self.assertTrue(any(d["reason"] == "daily_budget_used" for d in result["decisions"]))


if __name__ == "__main__":
    unittest.main()
