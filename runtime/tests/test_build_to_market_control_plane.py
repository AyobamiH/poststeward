from __future__ import annotations

from argparse import Namespace
from contextlib import redirect_stdout
from datetime import datetime, timedelta, timezone
import io
import json
import os
import tempfile
import unittest
from unittest.mock import patch

from ocpf_post import execution_observer, replenisher, replenisher_cli, work_status
from ocpf_post.admission_runtime import controlled_refresh
from ocpf_post.onboarding import OnboardingError, _import_lock, coordination_status, wait_for_coordination

NOW = datetime(2026, 9, 20, 17, 0, tzinfo=timezone.utc)


class CoordinationContractTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        env = patch.dict(os.environ, {
            "OCPF_POST_CONFIG_DIR": self.tmp.name + "/config",
            "OCPF_POST_STATE_DIR": self.tmp.name + "/state",
        })
        env.start()
        self.addCleanup(env.stop)

    def test_source_lock_exposes_holder_without_weakening_flock_authority(self):
        with _import_lock("source-onboarding", operation="replenish_refresh"):
            value = coordination_status("source-onboarding")
            self.assertEqual(value["status"], "active")
            self.assertTrue(value["active"])
            self.assertEqual(value["operation"], "replenish_refresh")
            self.assertRegex(value["holder_identity"], r"^pid:\d+$")
            self.assertGreater(value["holder_pid"], 0)
            waited = wait_for_coordination("source-onboarding", timeout_seconds=0)
            self.assertEqual(waited["wait_status"], "timeout")
            self.assertTrue(waited["retryable"])
        self.assertEqual(coordination_status("source-onboarding")["status"], "available")

    def test_controlled_refresh_classifies_source_contention_without_throwing_prose(self):
        class Lock:
            def __enter__(self): return None
            def __exit__(self, *_args): return False

        with patch("ocpf_post.admission_runtime.local_store.locked", return_value=Lock()), \
             patch(
                 "ocpf_post.admission_runtime._controlled_refresh",
                 side_effect=OnboardingError("Another source-onboarding operation is active; retry after it completes"),
             ), \
             patch("ocpf_post.portfolio_source_loader.merged_source_profiles",
                   return_value={"projects": {"alpha": {}}}):
            value = controlled_refresh(apply=True, now=NOW)
        self.assertEqual(value["projects"][0]["status"], "admission_paused")
        self.assertEqual(value["projects"][0]["reasons"], ["source_operation_active"])
        self.assertEqual(value["generative_campaigns"], [])


class MachineReadableCliTests(unittest.TestCase):
    def test_json_error_remains_json_on_failure(self):
        output = io.StringIO()
        with redirect_stdout(output), self.assertRaises(SystemExit) as caught:
            replenisher_cli._die(
                Namespace(json=True),
                "Another source operation is active",
                error_code="SOURCE_OPERATION_ACTIVE",
                retryable=True,
                metadata={"operation": "replenish_refresh"},
            )
        self.assertEqual(caught.exception.code, 2)
        value = json.loads(output.getvalue())
        self.assertFalse(value["ok"])
        self.assertEqual(value["error"]["code"], "SOURCE_OPERATION_ACTIVE")
        self.assertTrue(value["error"]["retryable"])
        self.assertEqual(value["error"]["metadata"]["operation"], "replenish_refresh")
        self.assertEqual(value["consequence"], "NO_PROVIDER_EFFECT")

    def test_portfolio_generation_limit_is_reported_once_not_per_project(self):
        projects = [
            {"project": f"project-{index}", "generative_status": "daily_limit_reached"}
            for index in range(20)
        ]
        with patch.dict(os.environ, {
            "OCPF_POST_GENERATIVE_SUPPLY_ENABLED": "1",
            "OCPF_POST_GENERATIVE_DAILY_LIMIT": "3",
        }), patch(
            "ocpf_post.replenisher.campaign_ids",
            return_value=[
                "A-GEN-20260920-01",
                "B-GEN-20260920-01",
                "C-GEN-20260920-01",
            ],
        ):
            value = replenisher.generative_summary(projects, [], now=NOW)
        self.assertEqual(value["portfolio_daily_limit"], 3)
        self.assertEqual(value["api_candidates_used"], 3)
        self.assertEqual(value["api_candidates_remaining"], 0)
        self.assertEqual(value["projects_blocked_by_global_limit"], 20)
        self.assertEqual(value["status_counts"]["daily_limit_reached"], 20)
        self.assertFalse(value["degraded"])


class WorkProjectionTests(unittest.TestCase):
    def test_queue_ambiguity_is_collapsed_when_specific_readback_root_exists(self):
        queue = work_status._item(
            "QUEUE:campaign:threads:ambiguous_effect", "Queue ambiguity",
            kind="queue", state="manual_review", dependency="manual_review",
            observed_at=NOW.isoformat(), blocker="ambiguous_effect",
            safe_next_action="inspect_existing_effect_without_replay",
            do_not_replay=True,
            evidence={"campaign": "CAMP-1", "provider": "threads"},
        )
        readback = work_status._item(
            "READBACK:sch_1", "Campaign readback",
            kind="publication_readback", state="manual_review", dependency="manual_review",
            observed_at=NOW.isoformat(), blocker="forensic_review_required",
            safe_next_action="bounded_reconciliation_without_resend",
            do_not_replay=True,
            evidence={"campaign": "CAMP-1", "provider": "threads", "schedule_id": "sch_1"},
        )
        value = work_status._collapse_duplicate_roots([queue, readback])
        self.assertEqual([row["id"] for row in value], ["READBACK:sch_1"])

    def test_near_term_imported_vault_deadline_is_visible_before_queue_rescue(self):
        manifest = {
            "runtime_imported": True,
            "project": "oneclickpostfactory",
            "providers": ["x"],
            "vault": {"id": "ocpf-brand", "base_campaign": "OCPF-POSTY-20260914-04"},
            "allocation": {
                "enabled": True,
                "expires_at": (NOW + timedelta(hours=6)).isoformat(),
            },
        }
        with patch("ocpf_post.source_receipts.publication_inputs", return_value=(
            {"OCPF-POSTY-20260914-04-V1-X": manifest}, [], [],
        )):
            rows = work_status._vault_deadline_items(NOW, NOW.isoformat())
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(row["state"], "deadline")
        self.assertEqual(row["blocker"], "approved_vault_expiry_within_24h")
        self.assertTrue(row["do_not_replay"])
        self.assertTrue(row["do_not_renew"])
        self.assertEqual(row["evidence"]["hours_to_expiry"], 6.0)

    def test_terminal_vault_effect_does_not_remain_a_deadline(self):
        campaign = "PAS-THVAULT-20260914-001-V1-THREADS"
        manifest = {
            "runtime_imported": True,
            "project": "proof-and-state",
            "providers": ["threads"],
            "vault": {"id": "pas-brand", "base_campaign": "PAS-THVAULT-20260914-001"},
            "allocation": {
                "enabled": True,
                "expires_at": (NOW + timedelta(hours=6)).isoformat(),
            },
        }
        receipt = {"campaign": campaign, "provider": "threads", "status": "published_verified"}
        with patch("ocpf_post.source_receipts.publication_inputs", return_value=(
            {campaign: manifest}, [], [receipt],
        )):
            rows = work_status._vault_deadline_items(NOW, NOW.isoformat())
        self.assertEqual(rows, [])

    def test_no_replay_vault_effect_remains_visible_until_reconciled(self):
        campaign = "PAS-THVAULT-20260914-001-V1-THREADS"
        manifest = {
            "runtime_imported": True,
            "project": "proof-and-state",
            "providers": ["threads"],
            "vault": {"id": "pas-brand", "base_campaign": "PAS-THVAULT-20260914-001"},
            "allocation": {
                "enabled": True,
                "expires_at": (NOW + timedelta(hours=6)).isoformat(),
            },
        }
        expectations = {
            "ambiguous_effect": "forensically_reconcile_existing_effect_without_replay_or_renewal",
            "partial_effect": "reconcile_partial_effect_without_replay_or_renewal",
        }
        for status, next_action in expectations.items():
            with self.subTest(status=status):
                receipt = {
                    "campaign": campaign,
                    "provider": "threads",
                    "status": status,
                    "schedule_id": "sch_recovery",
                    "post_id": None,
                }
                with patch("ocpf_post.source_receipts.publication_inputs", return_value=(
                    {campaign: manifest}, [], [receipt],
                )):
                    rows = work_status._vault_deadline_items(NOW, NOW.isoformat())
                self.assertEqual(len(rows), 1)
                row = rows[0]
                self.assertEqual(row["state"], "manual_review")
                self.assertEqual(row["dependency"], "manual_review")
                self.assertEqual(row["blocker"], status)
                self.assertEqual(row["safe_next_action"], next_action)
                self.assertTrue(row["do_not_replay"])
                self.assertTrue(row["do_not_renew"])
                self.assertEqual(row["evidence"]["receipt_status"], status)
                self.assertEqual(row["evidence"]["schedule_id"], "sch_recovery")
                self.assertEqual(
                    row["deadline"],
                    (NOW + timedelta(hours=6)).isoformat().replace("+00:00", "Z"),
                )

    def test_specific_queue_effect_replaces_generic_vault_recovery_deadline(self):
        deadline = work_status._item(
            "VAULT-DEADLINE:CAMP-1:threads", "Vault recovery deadline", kind="vault_deadline",
            state="manual_review", dependency="manual_review", observed_at=NOW.isoformat(),
            blocker="ambiguous_effect", deadline=(NOW + timedelta(hours=4)).isoformat(),
            safe_next_action="forensically_reconcile_existing_effect_without_replay_or_renewal",
            do_not_replay=True, do_not_renew=True,
            evidence={"campaign": "CAMP-1", "provider": "threads"},
        )
        queue = work_status._item(
            "QUEUE:CAMP-1:threads:ambiguous_effect", "Queue ambiguity", kind="queue",
            state="manual_review", dependency="manual_review", observed_at=NOW.isoformat(),
            blocker="ambiguous_effect", deadline=(NOW + timedelta(hours=4)).isoformat(),
            safe_next_action="inspect_existing_effect_without_replay",
            do_not_replay=True, do_not_renew=True,
            evidence={"campaign": "CAMP-1", "provider": "threads"},
        )
        value = work_status._collapse_duplicate_roots([deadline, queue])
        self.assertEqual(
            [row["id"] for row in value],
            ["QUEUE:CAMP-1:threads:ambiguous_effect"],
        )

    def test_specific_queue_expiry_replaces_generic_vault_deadline(self):
        deadline = work_status._item(
            "VAULT-DEADLINE:CAMP-1:x", "Vault deadline", kind="vault_deadline",
            state="deadline", dependency="local_operator", observed_at=NOW.isoformat(),
            blocker="approved_vault_expiry_within_24h", deadline=(NOW + timedelta(hours=4)).isoformat(),
            safe_next_action="reconcile", do_not_replay=True, do_not_renew=True,
            evidence={"campaign": "CAMP-1", "provider": "x"},
        )
        queue = work_status._item(
            "QUEUE:CAMP-1:x:expiry_approaching", "Queue expiry", kind="queue",
            state="deadline", dependency="local_operator", observed_at=NOW.isoformat(),
            blocker="expiry_approaching", deadline=(NOW + timedelta(hours=4)).isoformat(),
            safe_next_action="allow_normal_allocator", do_not_renew=True,
            evidence={"campaign": "CAMP-1", "provider": "x"},
        )
        value = work_status._collapse_duplicate_roots([deadline, queue])
        self.assertEqual([row["id"] for row in value], ["QUEUE:CAMP-1:x:expiry_approaching"])

    def test_generated_work_surface_preserves_deadline_and_no_replay_semantics(self):
        deadline = (NOW + timedelta(hours=6)).isoformat()
        editorial = work_status._item(
            "EDITORIAL:one", "Editorial continuity", kind="editorial_supply",
            state="actionable", dependency="editorial_work", observed_at=NOW.isoformat(),
            blocker="market_presence_cold", deadline=deadline,
            safe_next_action="author_reviewed_supply", do_not_replay=True, do_not_renew=True,
            evidence={"market_cold": True},
        )
        external = work_status._item(
            "READBACK:one", "Historical readback", kind="publication_readback",
            state="manual_review", dependency="manual_review", observed_at=NOW.isoformat(),
            blocker="forensic_review_required", safe_next_action="review_without_resend",
            do_not_replay=True, do_not_renew=True,
        )
        with patch.object(work_status, "_editorial_items", return_value=[editorial]), \
             patch.object(work_status, "_queue_items", return_value=[]), \
             patch.object(work_status, "_acceptance_items", return_value=[external]), \
             patch.object(work_status, "_external_items", return_value=[]):
            value = work_status.build(now=NOW)
        self.assertEqual(value["status"], "attention")
        self.assertEqual(value["item_count"], 2)
        self.assertEqual(value["summary"]["deadlines_within_24h"], 1)
        self.assertEqual(value["summary"]["no_replay_count"], 2)
        self.assertEqual(value["summary"]["no_renew_count"], 2)
        self.assertTrue(any(row["evidence"].get("market_cold") for row in value["items"]))
        self.assertIn("never publishes", value["boundary"])


class LivingTunnelTests(unittest.TestCase):
    def test_execution_topology_keeps_deterministic_and_vault_supply_paths_distinct(self):
        base = {
            "portfolio": {"status": {"eligible_deliveries": 2}, "plan": {"plan": []}},
            "replenishment": {"source_observations": {}, "observed_repositories": 0},
            "activity": {"active_schedules": [], "recent_receipts": []},
            "engagement": {"counts": {}},
            "performance": {"recent": []},
            "feedback": {},
        }
        acceptance = {
            "sections": {
                "pc01_editorial_supply": {
                    "open_requests": [{
                        "request_id": "edr-1", "project": "proof-and-state",
                        "provider": "threads", "market_cold": True,
                        "last_observed_at": NOW.isoformat(),
                        "demand_reasons": ["market_presence_cold"],
                    }],
                },
                "pc02_source_vault": {
                    "vaults": [{
                        "vault_id": "proof-and-state-gtm", "status": "observed",
                        "active_entries": 4,
                    }],
                },
            },
        }
        work = {"status": "attention", "item_count": 3}
        value = execution_observer._execution_projection(
            base, {"metrics": {}, "mode": "open"}, {}, acceptance, work,
        )
        nodes = {row["id"]: row for row in value["nodes"]}
        edges = {(row["source"], row["target"], row["mode"]) for row in value["edges"]}
        self.assertEqual(nodes["editorial"]["value"], 1)
        self.assertEqual(nodes["vault"]["value"], 4)
        self.assertEqual(nodes["work"]["value"], 3)
        self.assertIn(("observer", "buffer", "evidence"), edges)
        self.assertIn(("buffer", "admission", "state"), edges)
        self.assertIn(("sources", "editorial", "state"), edges)
        self.assertIn(("editorial", "vault", "state"), edges)
        self.assertIn(("vault", "admission", "state"), edges)
        editorial_events = [row for row in value["events"] if row["kind"] == "editorial_demand"]
        self.assertEqual(len(editorial_events), 1)
        self.assertEqual(editorial_events[0]["motion"], "pulse")
        self.assertEqual(editorial_events[0]["evidence"], "editorial-continuity")


if __name__ == "__main__":
    unittest.main()
