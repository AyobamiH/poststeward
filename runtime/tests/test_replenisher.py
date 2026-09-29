from __future__ import annotations

from collections import Counter
import hashlib
import os
import tempfile
import unittest
from datetime import datetime, timezone
from unittest.mock import patch

from ocpf_post.campaigns import builtin_manifest, builtin_text, campaign_ids
from ocpf_post.replenisher import refresh_sources, replenisher_status
from ocpf_post.publication_payload import build_publication, x_weighted_length

UTC = timezone.utc


class ReplenisherTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.old_state = os.environ.get("OCPF_POST_STATE_DIR")
        self.old_config = os.environ.get("OCPF_POST_CONFIG_DIR")
        os.environ["OCPF_POST_STATE_DIR"] = self.tmp.name
        os.environ["OCPF_POST_CONFIG_DIR"] = self.tmp.name
        self.now = datetime(2026, 9, 8, 20, 0, tzinfo=UTC)
        self.profile = {
            "schema_version": 1,
            "projects": {
                "oneclickpostfactory": {
                    "label": "OneClickPostFactory",
                    "repository": "AyobamiH/oneclickpostfactory",
                    "campaign_prefix": "OCPF",
                    "providers": ["x", "threads", "linkedin"],
                    "destinations": {"x": "x-founder", "threads": "threads-founder", "linkedin": "linkedin-founder"},
                    "required_phrases_any": ["tenant-owned publications"],
                    "event_enabled": True,
                    "event_problem": "Keep source material moving through a governed publishing workflow.",
                    "event_cta": "Where does your workflow break?",
                    "claim_boundary": "Source-level progress only.",
                    "inventory": [
                        {"title": "Queue supply", "lane": "commercial", "priority": 90, "hook": "Calendars are easy.", "body": "Supplying useful source material is harder. " * 12, "cta": "What empties your queue?"}
                    ],
                }
            },
        }

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

    def _baseline(self):
        commits = [{"sha": "a" * 40, "commit": {"message": "feat: current source change"}}]
        with patch("ocpf_post.replenisher.source_profiles", return_value=self.profile), \
             patch("ocpf_post.replenisher._repo_readme", return_value=("tenant-owned publications", "r" * 40)), \
             patch("ocpf_post.replenisher._repo_commits", return_value=commits), \
             patch("ocpf_post.replenisher._github_token", return_value=None):
            return refresh_sources(apply=True, now=self.now)

    def test_first_apply_baselines_events_and_creates_runtime_inventory(self) -> None:
        result = self._baseline()
        self.assertEqual(len(result["static_campaigns"]), 3)
        self.assertEqual(result["event_campaigns"], [])
        generated = result["static_campaigns"][0]["campaign"]
        self.assertIn(generated, campaign_ids())
        x_text = builtin_text(generated, "x") or ""
        self.assertTrue(x_text)
        publication = build_publication("x", x_text)
        self.assertTrue(all(x_weighted_length(row["text"]) <= 275 for row in publication["parts"]))
        self.assertEqual(" ".join(row["text"] for row in publication["parts"]).split(), x_text.split())
        manifest = builtin_manifest(generated)
        self.assertTrue(manifest.get("runtime_generated"))
        self.assertEqual(manifest["payload_sha256"]["x"], hashlib.sha256(x_text.encode("utf-8")).hexdigest())

    def test_runtime_variants_get_staggered_priorities(self) -> None:
        result = self._baseline()
        priorities = {}
        for item in result["static_campaigns"]:
            campaign = item["campaign"]
            priorities[campaign.split("-")[-2][-1]] = builtin_manifest(campaign)["allocation"]["priority"]
        self.assertEqual(priorities["I"], 90)
        self.assertEqual(priorities["Q"], 86)
        self.assertEqual(priorities["P"], 82)

    def test_second_apply_emits_only_safe_new_development_event(self) -> None:
        old = {"sha": "a" * 40, "commit": {"message": "feat: baseline"}}
        with patch("ocpf_post.replenisher.source_profiles", return_value=self.profile), \
             patch("ocpf_post.replenisher._repo_readme", return_value=("tenant-owned publications", "r" * 40)), \
             patch("ocpf_post.replenisher._repo_commits", return_value=[old]), \
             patch("ocpf_post.replenisher._github_token", return_value=None):
            refresh_sources(apply=True, now=self.now)
        new = {"sha": "b" * 40, "commit": {"message": "feat: add bounded source review"}}
        ignored = {"sha": "c" * 40, "commit": {"message": "docs: internal notes"}}
        with patch("ocpf_post.replenisher.source_profiles", return_value=self.profile), \
             patch("ocpf_post.replenisher._repo_readme", return_value=("tenant-owned publications", "r" * 40)), \
             patch("ocpf_post.replenisher._repo_commits", return_value=[ignored, new, old]), \
             patch("ocpf_post.replenisher._github_token", return_value=None):
            result = refresh_sources(apply=True, now=self.now)
        self.assertEqual(len(result["event_campaigns"]), 1)
        campaign = result["event_campaigns"][0]["campaign"]
        self.assertIn("EVENT", campaign)
        text = builtin_text(campaign, "x") or ""
        self.assertLessEqual(len(text), 275)
        self.assertIn("source-level progress", text)
        self.assertNotIn("deployment is live", text.lower())

    def test_source_guard_failure_creates_nothing(self) -> None:
        with patch("ocpf_post.replenisher.source_profiles", return_value=self.profile), \
             patch("ocpf_post.replenisher._repo_readme", return_value=("unrelated readme", "r" * 40)), \
             patch("ocpf_post.replenisher._github_token", return_value=None):
            result = refresh_sources(apply=True, now=self.now)
        self.assertEqual(result["static_campaigns"], [])
        self.assertEqual(result["event_campaigns"], [])
        self.assertEqual(result["projects"][0]["status"], "source_guard_failed")

    def test_global_bill_forge_guard_matches_current_readme_language(self) -> None:
        from ocpf_post.replenisher import _source_valid, source_profiles

        profile = source_profiles()["projects"]["global-bill-forge"]
        current_readme_contract = (
            "Creates invoice and receipt documents. "
            "Supports international currencies, locale-aware formatting and right-to-left layouts. "
            "Exports documents as PDF or PNG."
        )
        valid, reason = _source_valid(profile, current_readme_contract)
        self.assertTrue(valid, reason)

    def test_generative_deficit_uses_multi_day_reserve_trigger(self) -> None:
        from ocpf_post.replenisher import _generative_deficits

        profile = {
            **self.profile["projects"]["oneclickpostfactory"],
            "project": "oneclickpostfactory",
        }
        with patch("ocpf_post.editorial_continuity.profile_reserve", return_value=[
            {
                "project": "oneclickpostfactory",
                "provider": "x",
                "fallback_required": True,
                "target_deficit": 4,
            },
            {
                "project": "oneclickpostfactory",
                "provider": "threads",
                "fallback_required": False,
                "target_deficit": 2,
            },
        ]):
            providers, deficit = _generative_deficits(profile, now=self.now)
        self.assertEqual(providers, ["x"])
        self.assertEqual(deficit, 4)

    def test_generative_portfolio_rotation_spreads_daily_allowance_across_projects(self) -> None:
        from ocpf_post.replenisher import _generative_portfolio_projects

        profiles = {
            "projects": {
                name: {"providers": ["x"], "destinations": {"x": "x-founder"}}
                for name in ("p0", "p1", "p2", "p3", "p4")
            }
        }
        with patch("ocpf_post.replenisher.source_profiles", return_value=profiles), \
             patch("ocpf_post.replenisher._generative_daily_remaining", return_value=3), \
             patch("ocpf_post.replenisher._generative_project_usage_today", return_value=Counter()), \
             patch("ocpf_post.replenisher._generative_project_daily_cap", return_value=2), \
             patch("ocpf_post.replenisher._generative_deficits", return_value=(["x"], 4)):
            selected = _generative_portfolio_projects(self.now)
        self.assertEqual(selected, ["p3", "p4", "p0"])

    def test_generative_project_gets_at_most_one_candidate_per_day(self) -> None:
        from ocpf_post.replenisher import _generative_routes

        profile = {
            **self.profile["projects"]["oneclickpostfactory"],
            "project": "oneclickpostfactory",
        }
        with patch("ocpf_post.replenisher._generative_enabled", return_value=True), \
             patch("ocpf_post.replenisher._generative_daily_remaining", return_value=40), \
             patch("ocpf_post.replenisher._generative_project_usage_today", return_value=Counter()), \
             patch("ocpf_post.replenisher._generative_project_daily_cap", return_value=2), \
             patch("ocpf_post.replenisher._generative_deficits", return_value=(["x", "threads"], 9)):
            providers, count = _generative_routes(profile, now=self.now)
        self.assertEqual(providers, ["x", "threads"])
        self.assertEqual(count, 2)

    def test_generative_portfolio_skips_projects_already_served_today(self) -> None:
        from ocpf_post.replenisher import _generative_portfolio_projects

        profiles = {
            "projects": {
                name: {"providers": ["x"], "destinations": {"x": "x-founder"}}
                for name in ("p0", "p1", "p2", "p3")
            }
        }
        with patch("ocpf_post.replenisher.source_profiles", return_value=profiles), \
             patch("ocpf_post.replenisher._generative_daily_remaining", return_value=3), \
             patch("ocpf_post.replenisher._generative_project_usage_today", return_value=Counter({"p3": 2})), \
             patch("ocpf_post.replenisher._generative_project_daily_cap", return_value=2), \
             patch("ocpf_post.replenisher._generative_deficits", return_value=(["x"], 2)):
            selected = _generative_portfolio_projects(self.now)
        self.assertNotIn("p3", selected)
        self.assertEqual(len(selected), 3)

    def test_emergency_project_daily_cap_tracks_observed_route_consumption(self) -> None:
        from ocpf_post.replenisher import _generative_project_daily_cap

        profile = {
            **self.profile["projects"]["oneclickpostfactory"],
            "project": "oneclickpostfactory",
        }
        reserve = [
            {
                "provider": "x",
                "fallback_required": True,
                "target_deficit": 10,
                "observed_daily_rate": 9.0,
                "reserve_daily_rate": 1.43,
                "reserve_status": "empty",
            },
            {
                "provider": "threads",
                "fallback_required": True,
                "target_deficit": 12,
                "observed_daily_rate": 8.0,
                "reserve_daily_rate": 1.86,
                "reserve_status": "empty",
            },
            {
                "provider": "linkedin",
                "fallback_required": True,
                "target_deficit": 9,
                "observed_daily_rate": 7.0,
                "reserve_daily_rate": 1.29,
                "reserve_status": "emergency",
            },
        ]
        with patch("ocpf_post.editorial_continuity.profile_reserve", return_value=reserve):
            self.assertEqual(_generative_project_daily_cap(profile, now=self.now), 2)

    def test_emergency_cap_uses_reserve_rate_not_yesterdays_burst(self) -> None:
        from ocpf_post.replenisher import _generative_project_daily_cap

        profile = {
            **self.profile["projects"]["oneclickpostfactory"],
            "project": "oneclickpostfactory",
        }
        reserve = [{
            "provider": "x",
            "fallback_required": True,
            "target_deficit": 7,
            "observed_daily_rate": 9.0,
            "reserve_daily_rate": 1.0,
            "fallback_trigger_items": 5,
            "reserve_available_items": 0,
            "reserve_status": "empty",
        }]
        with patch("ocpf_post.editorial_continuity.profile_reserve", return_value=reserve):
            self.assertEqual(_generative_project_daily_cap(profile, now=self.now), 2)

    def test_project_daily_cap_adds_five_day_catchup_capacity(self) -> None:
        from ocpf_post.replenisher import _generative_project_daily_cap

        profile = {
            **self.profile["projects"]["oneclickpostfactory"],
            "project": "oneclickpostfactory",
        }
        reserve = [{
            "provider": "threads",
            "fallback_required": True,
            "target_deficit": 12,
            "observed_daily_rate": 1.86,
            "fallback_trigger_items": 10,
            "reserve_available_items": 1,
            "reserve_status": "emergency",
        }]
        with patch("ocpf_post.editorial_continuity.profile_reserve", return_value=reserve):
            self.assertEqual(_generative_project_daily_cap(profile, now=self.now), 4)

    def test_project_daily_cap_is_bounded_at_five(self) -> None:
        from ocpf_post.replenisher import _generative_project_daily_cap

        profile = {
            **self.profile["projects"]["oneclickpostfactory"],
            "project": "oneclickpostfactory",
        }
        reserve = [{
            "provider": "x",
            "fallback_required": True,
            "target_deficit": 30,
            "observed_daily_rate": 5.2,
            "fallback_trigger_items": 20,
            "reserve_available_items": 0,
            "reserve_status": "empty",
        }]
        with patch("ocpf_post.editorial_continuity.profile_reserve", return_value=reserve):
            self.assertEqual(_generative_project_daily_cap(profile, now=self.now), 5)

    def test_status_reports_runtime_inventory_without_network(self) -> None:
        with patch("ocpf_post.replenisher._github_token", return_value=None):
            result = replenisher_status()
        self.assertEqual(result["runtime_campaign_count"], 0)
        self.assertEqual(result["observed_repositories"], 0)


if __name__ == "__main__":
    unittest.main()
