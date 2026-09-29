from __future__ import annotations

from contextlib import nullcontext
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from ocpf_post import admission, learning_admission, source_pipeline

NOW = datetime(2026, 9, 13, 18, tzinfo=timezone.utc)


class LearningAdmissionReserveTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory(); self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.policy = deepcopy(admission.DEFAULT_POLICY)
        self.rows = []
        # Keep the system globally overloaded and the owner X account locally full.
        self.rows += [
            {"campaign": f"X-{i}", "provider": "x", "account_id": "123", "project": "busy"}
            for i in range(150)
        ]
        self.rows += [
            {"campaign": f"LI-{i}", "provider": "linkedin", "account_id": "li-owner", "project": "busy"}
            for i in range(145)
        ]
        self.rows += [
            {"campaign": f"TH-{i}", "provider": "threads", "account_id": "456", "project": "busy"}
            for i in range(6)
        ]
        self.rows += [
            {"campaign": f"OX-{i}", "provider": "x", "account_id": "789", "project": "other"}
            for i in range(4)
        ]
        self.schedules = []
        self.registry = {"projects": {
            "brand": {"accounts": {
                "x": {"provider": "x", "account_id": "123"},
                "threads": {"provider": "threads", "account_id": "456"},
            }},
            "other": {"accounts": {"x": {"provider": "x", "account_id": "789"}}},
            "busy": {"accounts": {
                "x": {"provider": "x", "account_id": "123"},
                "threads": {"provider": "threads", "account_id": "456"},
                "linkedin": {"provider": "linkedin", "account_id": "li-owner"},
            }},
        }}
        self.manifests = {}
        patches = (
            patch.dict(os.environ, OCPF_POST_STATE_DIR=str(self.root), OCPF_POST_CONFIG_DIR=str(self.root)),
            patch("ocpf_post.portfolio.delivery_candidates", side_effect=lambda **kw: deepcopy(self.rows)),
            patch("ocpf_post.scheduler.schedule_records", side_effect=lambda: deepcopy(self.schedules)),
            patch("ocpf_post.registry.load_registry", side_effect=lambda: deepcopy(self.registry)),
            patch("ocpf_post.account_profiles.unavailable", return_value=None),
            patch.object(admission, "load_policy", side_effect=lambda: deepcopy(self.policy)),
            patch.object(admission, "_queue_ages", return_value=(0, "observed")),
            patch.object(learning_admission, "builtin_manifest", side_effect=lambda cid: deepcopy(self.manifests.get(cid, {}))),
        )
        for item in patches:
            item.start(); self.addCleanup(item.stop)

    def test_overloaded_registered_scope_gets_one_protected_learning_item(self):
        budget = learning_admission.LearningBudget(NOW, True)
        result = budget.admit("brand", "x", "123", expires_at=(NOW + timedelta(days=7)).isoformat(),
                              learning_role="baseline")
        self.assertTrue(result["admitted"])
        self.assertTrue(result["learning_protected"])
        self.assertEqual(result["reasons"], ["protected_learning_inventory"])
        self.assertIn("global_resource_ceiling", result["pressure_reasons"])
        self.assertEqual(result["learning_reserve_limit"], 2)
        self.assertEqual(result["learning_scope_reserve_limit"], 1)
        self.assertTrue(learning_admission.LearningBudget(NOW + timedelta(minutes=5), False)._cooldown_active("x:123", "brand"))

    def test_scope_cooldown_survives_restart_even_after_item_leaves_queue(self):
        first = learning_admission.LearningBudget(NOW, True).admit(
            "brand", "x", "123", expires_at=(NOW + timedelta(days=7)).isoformat(), learning_role="baseline")
        self.assertTrue(first["learning_protected"])
        second = learning_admission.LearningBudget(NOW + timedelta(hours=1), False).admit(
            "brand", "x", "123", expires_at=(NOW + timedelta(days=7)).isoformat(), learning_role="challenger")
        self.assertFalse(second["admitted"])
        self.assertEqual(second["learning_reserve_reason"], "learning_scope_cooldown")

    def test_exact_equivalence_releases_campaign_bound_cooldown_without_capacity_change(self):
        first = learning_admission.LearningBudget(NOW, True).admit(
            "brand", "x", "123", expires_at=(NOW + timedelta(days=7)).isoformat(),
            learning_role="baseline", learning_campaign="BRAND-AUTO-01I-NEW-X")
        self.assertTrue(first["learning_protected"])
        with patch.object(learning_admission.learning_equivalence, "satisfied_baseline", return_value=True):
            budget = learning_admission.LearningBudget(NOW + timedelta(hours=1), False)
            self.assertFalse(budget._cooldown_active("x:123", "brand"))
            second = budget.admit(
                "brand", "x", "123", expires_at=(NOW + timedelta(days=7)).isoformat(),
                learning_role="baseline", learning_campaign="BRAND-AUTO-02I-NEW-X")
        self.assertTrue(second["admitted"])
        self.assertTrue(second["learning_protected"])
        self.assertEqual(second["learning_reserve_limit"], 2)

    def test_legacy_marker_releases_only_for_equivalence_of_same_admission(self):
        # v0.23 markers did not store campaign identity. Match the equivalence
        # to the baseline manifest's exact admitted_at before releasing it.
        first = learning_admission.LearningBudget(NOW, True).admit(
            "brand", "x", "123", expires_at=(NOW + timedelta(days=7)).isoformat(), learning_role="baseline")
        self.assertTrue(first["learning_protected"])
        self.manifests["LEGACY-BASE"] = {
            "project": "brand",
            "source": {"admitted_at": NOW.isoformat()},
        }
        state = {"entries": [{
            "baseline_campaign": "LEGACY-BASE",
            "baseline_project": "brand",
            "provider": "x",
            "account_id": "123",
            "recorded_at": (NOW + timedelta(minutes=5)).isoformat(),
        }]}
        with patch.object(learning_admission.learning_equivalence, "load", return_value=state):
            self.assertFalse(
                learning_admission.LearningBudget(NOW + timedelta(hours=1), False)._cooldown_active("x:123", "brand")
            )
        self.manifests["LEGACY-BASE"]["source"]["admitted_at"] = (NOW - timedelta(minutes=2)).isoformat()
        with patch.object(learning_admission.learning_equivalence, "load", return_value=state):
            self.assertTrue(
                learning_admission.LearningBudget(NOW + timedelta(hours=1), False)._cooldown_active("x:123", "brand")
            )

    def test_global_reserve_is_two_and_scope_reserve_is_one(self):
        budget = learning_admission.LearningBudget(NOW, False)
        first = budget.admit("brand", "x", "123", expires_at=(NOW + timedelta(days=7)).isoformat(),
                             learning_role="baseline")
        second = budget.admit("brand", "threads", "456", expires_at=(NOW + timedelta(days=7)).isoformat(),
                              learning_role="baseline")
        self.assertTrue(first["admitted"])
        self.assertTrue(second["admitted"])
        third = budget.admit("other", "x", "789", expires_at=(NOW + timedelta(days=7)).isoformat(),
                             learning_role="baseline")
        self.assertFalse(third["admitted"])
        self.assertEqual(third["learning_reserve_reason"], "learning_reserve_full")
        self.assertEqual(third["learning_stock_before"], 2)

    def test_scheduled_learning_work_still_consumes_reserve(self):
        self.manifests["SCHEDULED-LEARN"] = {
            "project": "brand", "payload_frozen": True,
            "source": {"type": "repository_product_truth", "source_id": "brand-README-rev-1-insight",
                       "source_sha": "rev", "comparison_variant": "question"},
        }
        self.schedules.append({"campaign": "SCHEDULED-LEARN", "project": "brand", "provider": "x",
                               "account_id": "123", "status": "scheduled"})
        result = learning_admission.LearningBudget(NOW, False).admit(
            "brand", "x", "123", expires_at=(NOW + timedelta(days=7)).isoformat(), learning_role="challenger")
        self.assertFalse(result["admitted"])
        self.assertEqual(result["learning_scope_stock_before"], 1)
        self.assertEqual(result["learning_reserve_reason"], "learning_reserve_full")

    def test_linkedin_near_expiry_and_unregistered_work_are_never_protected(self):
        budget = learning_admission.LearningBudget(NOW, False)
        linkedin = budget.admit("busy", "linkedin", "li-owner", expires_at=(NOW + timedelta(days=7)).isoformat(),
                                learning_role="baseline")
        self.assertFalse(linkedin["admitted"])
        near = budget.admit("brand", "x", "123", expires_at=(NOW + timedelta(hours=1)).isoformat(),
                            learning_role="baseline")
        self.assertFalse(near["admitted"])
        missing = budget.admit("missing", "x", "999", expires_at=(NOW + timedelta(days=7)).isoformat(),
                               learning_role="baseline")
        self.assertFalse(missing["admitted"])

    def test_open_ordinary_path_is_unchanged_and_not_marked_protected(self):
        self.rows.clear()
        result = learning_admission.LearningBudget(NOW, False).admit(
            "brand", "x", "123", expires_at=(NOW + timedelta(days=7)).isoformat(), learning_role="baseline")
        self.assertTrue(result["admitted"])
        self.assertFalse(result.get("learning_protected", False))
        self.assertEqual(result["reasons"], ["within_destination_budget"])


class SourcePipelineLearningReserveTests(unittest.TestCase):
    def test_only_supported_qualified_source_items_receive_learning_roles(self):
        profile = {
            "project": "sample",
            "providers": ["x", "threads", "linkedin"],
            "destinations": {"x": "x", "threads": "threads", "linkedin": "linkedin"},
            "inventory": [{"hook": "one", "body": "body", "priority": 70, "ttl_hours": 336}],
        }
        observation = {
            "readme_sha": "a" * 40,
            "readme_observed_at": NOW.isoformat(),
            "observed_at": NOW.isoformat(),
            "profile_sha256": "fp",
            "status": "observed",
            "source_ok": True,
            "pending": [],
        }
        baseline = {"id": "SAMPLE-AUTO-01I-AAAAAAA", "source_id": "sample-README-aaaaaaaaaaaa-1-insight",
                    "sha": "a" * 40, "title": "one [insight]", "lane": "evergreen", "priority": 70,
                    "ttl": 336, "at": NOW.isoformat(), "type": "repository_product_truth",
                    "item": profile["inventory"][0], "variant": "insight", "comparison_variant": "practical"}
        challenger = {**baseline, "id": "SAMPLE-AUTO-01P-AAAAAAA",
                      "source_id": "sample-README-aaaaaaaaaaaa-1-practical", "variant": "practical",
                      "predecessor_source_id": baseline["source_id"]}
        calls = []

        class FakeBudget:
            def __init__(self, now, apply):
                pass
            def admit(self, project, provider, account, **kwargs):
                calls.append((provider, kwargs.get("learning_role"), kwargs.get("learning_campaign")))
                return {"admitted": False, "project": project, "reasons": ["global_resource_ceiling"]}

        class FakeSampling:
            active = True
            def __init__(self, now):
                pass
            def reason(self, item, provider, account, project):
                return "predecessor_not_published"
            def record(self, project, provider):
                raise AssertionError("deferred challenger must not record sampling")

        account_ids = {"x": "123", "threads": "456", "linkedin": "li"}
        with patch("ocpf_post.portfolio_source_loader.merged_source_profiles", return_value={"projects": {"sample": profile}}), \
             patch.object(source_pipeline.observations, "load", return_value={"schema_version": 1, "projects": {"sample": observation}}), \
             patch.object(source_pipeline.observations, "fingerprint", return_value="fp"), \
             patch("ocpf_post.runtime_sources.source_lock", side_effect=lambda **_kwargs: nullcontext()), \
             patch("ocpf_post.registry.resolve_account", side_effect=lambda project, alias, expected_provider=None: {"account_id": account_ids[expected_provider]}), \
             patch.object(source_pipeline, "_known_deliveries", return_value=set()), \
             patch.object(source_pipeline, "_items", return_value=iter([baseline, challenger])), \
             patch.object(source_pipeline, "LearningBudget", FakeBudget), \
             patch("ocpf_post.learning_supply.SamplingBudget", FakeSampling):
            result = source_pipeline.refresh(apply=False, project="sample", now=NOW)

        self.assertEqual(calls, [
            ("x", "baseline", "SAMPLE-AUTO-01I-AAAAAAA-X"),
            ("threads", "baseline", "SAMPLE-AUTO-01I-AAAAAAA-THREADS"),
            ("linkedin", None, None),
        ])
        self.assertEqual(len(result["sampling_deferred"]), 3)
        self.assertEqual({row["reason"] for row in result["sampling_deferred"]}, {"predecessor_not_published"})


if __name__ == "__main__":
    unittest.main()
