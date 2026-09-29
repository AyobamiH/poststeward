from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta, timezone
from io import StringIO
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

from ocpf_post import capacity_experiment as trial, local_store, portfolio
from ocpf_post.portfolio_policy import apply_provider_patch, preview_provider_patch
from ocpf_post.portfolio_queue import fair_plan

UTC = timezone.utc


class LinkedInCapacityPolicyTests(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        env = patch.dict(
            os.environ,
            {
                "OCPF_POST_CONFIG_DIR": str(self.root / "config"),
                "OCPF_POST_STATE_DIR": str(self.root / "state"),
            },
        )
        env.start()
        self.addCleanup(env.stop)
        self.now = datetime(2026, 9, 17, 11, 0, tzinfo=UTC)
        self.policy = deepcopy(portfolio.DEFAULT_POLICY)
        self.policy["selection"] = "fair"
        for provider, row in self.policy["providers"].items():
            row["flow_mode"] = "fixed"
            row["hard_daily_ceiling"] = int(row["daily_target"])
        self.policy["cross_platform"] = {"topic_cooldown_minutes": 90, "future_key": "preserve-me"}
        portfolio.write_private_json(portfolio.policy_file(), self.policy)

    def _legacy_trial(self, *, x=24, threads=24) -> dict:
        baseline = deepcopy(self.policy)
        data = {
            "schema_version": 1,
            "id": trial.TRIAL,
            "status": "active",
            "started_at": trial.stamp(self.now - timedelta(days=7)),
            "ends_at": trial.stamp(self.now + timedelta(days=7)),
            "observed_at": trial.stamp(self.now),
            "baseline_policy": baseline,
            "policy_sha256": trial.fingerprint(baseline),
            "history": [
                {
                    "observed_at": trial.stamp(self.now - timedelta(minutes=15)),
                    "targets": {"x": x, "threads": threads, "linkedin": 6},
                    "status": "active",
                }
            ],
            "targets": {"x": x, "threads": threads, "linkedin": 6},
            "delivery_gates": {},
        }
        local_store.write(trial.path(), data)
        return data

    def _preview_15(self) -> dict:
        return preview_provider_patch(
            "linkedin",
            daily_target=15,
            development_max=5,
            commercial_min=8,
        )

    def test_bootstrap_default_remains_conservative(self) -> None:
        linkedin = portfolio.DEFAULT_POLICY["providers"]["linkedin"]
        self.assertEqual(linkedin["daily_target"], 6)
        self.assertEqual(linkedin["development_max"], 2)
        self.assertEqual(linkedin["commercial_min"], 3)
        self.assertEqual(portfolio.DEFAULT_POLICY["reply_targets"]["linkedin"], 10)
        self.assertEqual(linkedin["flow_mode"], "admission")
        self.assertEqual(linkedin["hard_daily_ceiling"], 100)

    def test_legacy_trial_rows_remain_readable_and_new_shape_is_valid(self) -> None:
        self._legacy_trial()
        self.assertEqual(trial.read()["targets"]["linkedin"], 6)
        data = trial.read()
        data["targets"] = {"x": 24, "threads": 24}
        data["history"].append(
            {
                "observed_at": trial.stamp(self.now),
                "targets": {"x": 24, "threads": 24},
                "status": "active",
            }
        )
        local_store.write(trial.path(), data)
        self.assertEqual(set(trial.read()["targets"]), {"x", "threads"})

    def test_linkedin_patch_does_not_change_active_x_threads_trial_scope(self) -> None:
        self._legacy_trial()
        preview = self._preview_15()
        self.assertFalse(preview["experiment_impact"]["changes_active_trial_scope"])
        applied = apply_provider_patch(
            "linkedin",
            daily_target=15,
            development_max=5,
            commercial_min=8,
            expected_sha256=preview["review_sha256"],
        )
        self.assertEqual(applied["result"], "updated")
        raw = portfolio.load_policy(effective=False)
        effective = trial.overlay(raw, now=self.now)
        self.assertEqual(effective["providers"]["x"]["daily_target"], 24)
        self.assertEqual(effective["providers"]["threads"]["daily_target"], 24)
        self.assertEqual(effective["providers"]["linkedin"]["daily_target"], 15)
        self.assertEqual(trial.report(now=self.now)["status"], "active")

    def test_legacy_linkedin_target_never_overwrites_new_linkedin_policy(self) -> None:
        self._legacy_trial()
        updated = deepcopy(self.policy)
        updated["providers"]["linkedin"].update(
            daily_target=15,
            development_max=5,
            commercial_min=8,
        )
        portfolio.write_private_json(portfolio.policy_file(), updated)
        overlaid = trial.overlay(updated, now=self.now)
        self.assertEqual(overlaid["providers"]["x"]["daily_target"], 24)
        self.assertEqual(overlaid["providers"]["threads"]["daily_target"], 24)
        self.assertEqual(overlaid["providers"]["linkedin"]["daily_target"], 15)

    def test_x_threads_policy_change_still_invalidates_trial(self) -> None:
        self._legacy_trial()
        changed = deepcopy(self.policy)
        changed["providers"]["x"]["window_start"] = "08:00"
        portfolio.write_private_json(portfolio.policy_file(), changed)
        self.assertFalse(trial.policy_matches_baseline(changed, trial.read()))
        self.assertEqual(trial.report(now=self.now)["status"], "policy_changed")
        self.assertEqual(trial.overlay(changed, now=self.now), changed)

    def test_preview_is_read_only_and_scoped_to_linkedin(self) -> None:
        self._legacy_trial()
        before = portfolio.policy_file().read_bytes()
        preview = self._preview_15()
        self.assertEqual(before, portfolio.policy_file().read_bytes())
        self.assertEqual(preview["before"]["daily_target"], 6)
        self.assertEqual(preview["after"]["daily_target"], 15)
        self.assertEqual(preview["after"]["development_max"], 5)
        self.assertEqual(preview["after"]["commercial_min"], 8)
        self.assertEqual(preview["result"], "preview")
        self.assertEqual(len(preview["review_sha256"]), 64)

    def test_apply_requires_exact_fresh_review_hash(self) -> None:
        preview = self._preview_15()
        with self.assertRaises(portfolio.PortfolioError):
            apply_provider_patch(
                "linkedin",
                daily_target=15,
                development_max=5,
                commercial_min=8,
                expected_sha256="0" * 64,
            )
        self.assertEqual(portfolio.load_policy(effective=False)["providers"]["linkedin"]["daily_target"], 6)

        changed = deepcopy(self.policy)
        changed["future_top_level"] = {"keep": True}
        portfolio.write_private_json(portfolio.policy_file(), changed)
        with self.assertRaises(portfolio.PortfolioError):
            apply_provider_patch(
                "linkedin",
                daily_target=15,
                development_max=5,
                commercial_min=8,
                expected_sha256=preview["review_sha256"],
            )

    def test_apply_preserves_every_unrelated_policy_field_and_can_roll_back(self) -> None:
        self._legacy_trial()
        before = portfolio.load_policy(effective=False)
        preview = self._preview_15()
        apply_provider_patch(
            "linkedin",
            daily_target=15,
            development_max=5,
            commercial_min=8,
            expected_sha256=preview["review_sha256"],
        )
        after = portfolio.load_policy(effective=False)
        self.assertEqual(after["selection"], before["selection"])
        self.assertEqual(after["timezone"], before["timezone"])
        self.assertEqual(after["horizon_minutes"], before["horizon_minutes"])
        self.assertEqual(after["providers"]["x"], before["providers"]["x"])
        self.assertEqual(after["providers"]["threads"], before["providers"]["threads"])
        self.assertEqual(after["reply_targets"], before["reply_targets"])
        self.assertEqual(after["cross_platform"], before["cross_platform"])

        rollback = preview_provider_patch(
            "linkedin",
            daily_target=6,
            development_max=2,
            commercial_min=3,
        )
        apply_provider_patch(
            "linkedin",
            daily_target=6,
            development_max=2,
            commercial_min=3,
            expected_sha256=rollback["review_sha256"],
        )
        self.assertEqual(portfolio.load_policy(effective=False), before)

    def test_active_trial_blocks_reviewed_x_threads_base_patch(self) -> None:
        self._legacy_trial()
        preview = preview_provider_patch("x", daily_target=21)
        self.assertTrue(preview["experiment_impact"]["changes_active_trial_scope"])
        with self.assertRaises(portfolio.PortfolioError):
            apply_provider_patch(
                "x",
                daily_target=21,
                expected_sha256=preview["review_sha256"],
            )
        self.assertEqual(portfolio.load_policy(effective=False)["providers"]["x"]["daily_target"], 20)

    def test_fair_planner_respects_fifteen_per_local_day(self) -> None:
        policy = deepcopy(self.policy)
        policy["providers"] = {"linkedin": deepcopy(policy["providers"]["linkedin"])}
        policy["providers"]["linkedin"].update(
            daily_target=15,
            development_max=5,
            commercial_min=8,
        )
        rows = [
            {
                "campaign": f"LI-{index}",
                "provider": "linkedin",
                "project": f"project-{index}",
                "title": "copy",
                "lane": "commercial",
                "priority": 80,
                "family": f"project-{index}",
                "topic_key": f"topic-{index}",
                "prepared_at": trial.stamp(self.now - timedelta(days=1)),
                "expires_at": trial.stamp(self.now + timedelta(days=2)),
            }
            for index in range(40)
        ]
        inputs = {
            "now": trial.stamp(self.now),
            "horizon_minutes": 1440,
            "candidates": rows,
            "histories": {"linkedin": []},
            "day_counts": {
                "linkedin": {
                    "2026-09-17": {
                        "total": 3,
                        "lanes": {"development": 0, "commercial": 3, "evergreen": 0},
                    },
                    "2026-09-18": {
                        "total": 0,
                        "lanes": {"development": 0, "commercial": 0, "evergreen": 0},
                    },
                }
            },
            "capacity": {"linkedin": {}},
        }
        result = fair_plan(inputs, policy)
        first_day = [row for row in result["plan"] if row["run_at"].startswith("2026-09-17")]
        self.assertLessEqual(3 + len(first_day), 15)

    def test_dispatch_routes_portfolio_policy_preview(self) -> None:
        out = StringIO()
        argv = [
            "ocpf-post", "portfolio", "policy", "--provider", "linkedin",
            "--daily-target", "15", "--development-max", "5", "--commercial-min", "8", "--json",
        ]
        from ocpf_post import dispatch
        with patch.object(sys, "argv", argv), patch("sys.stdout", out):
            dispatch.main()
        value = json.loads(out.getvalue())
        self.assertEqual(value["kind"], "provider_policy_patch")
        self.assertEqual(value["after"]["daily_target"], 15)


if __name__ == "__main__":
    unittest.main()
