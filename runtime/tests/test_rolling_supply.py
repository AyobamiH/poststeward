from __future__ import annotations

from datetime import datetime, timedelta, timezone
import unittest
from unittest.mock import patch

from ocpf_post import rolling_supply

NOW = datetime(2026, 10, 2, 12, 0, tzinfo=timezone.utc)


def blocked_state(*, available=0, stage="admission"):
    return {
        "status": "blocked",
        "accounts": [{
            "provider": "x",
            "account_id": "acct-x",
            "publishing_intent": True,
            "status": "blocked",
            "stock_floor": 2,
            "runnable": available,
            "reserved": 0,
            "required_items": 5,
            "available_items": available,
            "unreserved_schedule_opportunities": 0,
        }],
        "recovery_work": [{
            "provider": "x",
            "account_id": "acct-x",
            "blocker_stage": stage,
            "blocker_reason": "test",
            "cadence_deficit": max(0, 2 - available),
            "reserve_deficit": max(0, 5 - available),
            "available_items": available,
            "stock_floor": 2,
        }],
    }


class RollingSupplyTests(unittest.TestCase):
    def test_forbidden_provider_consequence_commands_are_rejected(self):
        with self.assertRaisesRegex(ValueError, "cannot invoke"):
            rolling_supply._command_safe(["./ocpf-post", "run-due"])
        with self.assertRaisesRegex(ValueError, "cannot invoke"):
            rolling_supply._command_safe(["./ocpf-post", "publish", "--live"])

    def test_target_vaults_prioritises_exact_blocked_routes_before_unrelated_stale(self):
        state = blocked_state(stage="admission")
        policies = {
            "blocked-stale": {
                "enabled": True,
                "project": "project-a",
                "destinations": {"x": "x-main"},
            },
            "blocked-fresh": {
                "enabled": True,
                "project": "project-a",
                "destinations": {"x": "x-main"},
            },
            "unrelated-stale": {
                "enabled": True,
                "project": "project-b",
                "destinations": {"threads": "threads-main"},
            },
        }
        observations = {
            "blocked-stale": {
                "observed_at": (NOW - timedelta(hours=2)).isoformat(),
                "valid_until": (NOW - timedelta(minutes=1)).isoformat(),
            },
            "blocked-fresh": {
                "observed_at": (NOW - timedelta(minutes=2)).isoformat(),
                "valid_until": (NOW + timedelta(hours=1)).isoformat(),
            },
            "unrelated-stale": {
                "observed_at": (NOW - timedelta(hours=3)).isoformat(),
                "valid_until": (NOW - timedelta(hours=1)).isoformat(),
            },
        }
        accounts = {
            ("project-a", "x-main"): {"account_id": "acct-x"},
            ("project-b", "threads-main"): {"account_id": "acct-t"},
        }

        def resolve(project, alias, *, expected_provider=None):
            return {
                "provider": expected_provider,
                "account_id": accounts[(project, alias)]["account_id"],
            }

        cycle = {
            "vault_attempts": {
                "blocked-stale": (NOW - timedelta(minutes=5)).isoformat(),
                "blocked-fresh": (NOW - timedelta(hours=1)).isoformat(),
                "unrelated-stale": (NOW - timedelta(hours=2)).isoformat(),
            }
        }
        with patch("ocpf_post.vault_sync.policies", return_value=policies), \
             patch("ocpf_post.vault_sync.observations", return_value=observations), \
             patch("ocpf_post.registry.resolve_account", side_effect=resolve), \
             patch.object(rolling_supply.local_store, "read", return_value=cycle):
            rows = rolling_supply.target_vaults(state, now=NOW)

        self.assertEqual(
            [row["vault_id"] for row in rows],
            ["blocked-stale", "blocked-fresh", "unrelated-stale"],
        )
        self.assertTrue(rows[0]["blocked_routes"])
        self.assertFalse(rows[-1]["blocked_routes"])

    def test_initial_adequate_state_is_noop(self):
        adequate = {
            "status": "adequate",
            "accounts": [{
                "provider": "x", "account_id": "acct-x",
                "publishing_intent": True, "status": "adequate",
                "stock_floor": 2, "runnable": 5, "reserved": 0,
                "required_items": 5, "available_items": 5,
                "unreserved_schedule_opportunities": 0,
            }],
            "recovery_work": [],
        }
        with patch.object(rolling_supply, "_continuity_stage",
                          return_value={"status": "completed"}), \
             patch.object(rolling_supply, "_continuity_state", return_value=adequate), \
             patch.object(rolling_supply, "run_stage") as stage, \
             patch.object(rolling_supply.local_store, "read", return_value={}), \
             patch.object(rolling_supply.local_store, "write"):
            value = rolling_supply.reconcile(apply=True, now_fn=lambda: NOW)
        self.assertEqual(value["status"], "adequate")
        stage.assert_not_called()
        self.assertEqual(value["passes"], [])

    def test_no_progress_stops_after_one_bounded_pass(self):
        state = blocked_state(available=0, stage="admission")
        continuity = iter([state, state])

        def stage(name, args, *, timeout):
            rolling_supply._command_safe(args)
            return {"stage": name, "status": "completed", "exit_code": 0}

        with patch.object(rolling_supply, "_continuity_stage",
                          return_value={"stage": "continuity", "status": "completed"}), \
             patch.object(rolling_supply, "_continuity_state",
                          side_effect=lambda: next(continuity)), \
             patch.object(rolling_supply, "_sync_target_vaults", return_value=[]), \
             patch.object(rolling_supply, "_needs_source_observation", return_value=False), \
             patch.object(rolling_supply, "run_stage", side_effect=stage), \
             patch.object(rolling_supply.local_store, "read", return_value={}), \
             patch.object(rolling_supply.local_store, "write"):
            value = rolling_supply.reconcile(
                apply=True, max_passes=2, now_fn=lambda: NOW,
            )

        self.assertEqual(value["status"], "blocked_no_progress")
        self.assertEqual(len(value["passes"]), 1)
        self.assertFalse(value["passes"][0]["progress"])

    def test_progress_to_adequate_closes_in_same_cycle(self):
        before = blocked_state(available=0, stage="admission")
        after = {
            "status": "adequate",
            "accounts": [{
                "provider": "x", "account_id": "acct-x",
                "publishing_intent": True, "status": "adequate",
                "stock_floor": 2, "runnable": 5, "reserved": 0,
                "required_items": 5, "available_items": 5,
                "unreserved_schedule_opportunities": 0,
            }],
            "recovery_work": [],
        }
        continuity = iter([before, after])

        def stage(name, args, *, timeout):
            rolling_supply._command_safe(args)
            return {"stage": name, "status": "completed", "exit_code": 0}

        with patch.object(rolling_supply, "_continuity_stage",
                          return_value={"stage": "continuity", "status": "completed"}), \
             patch.object(rolling_supply, "_continuity_state",
                          side_effect=lambda: next(continuity)), \
             patch.object(rolling_supply, "_sync_target_vaults", return_value=[]), \
             patch.object(rolling_supply, "_needs_source_observation", return_value=False), \
             patch.object(rolling_supply, "run_stage", side_effect=stage), \
             patch.object(rolling_supply.local_store, "write"):
            value = rolling_supply.reconcile(
                apply=True, max_passes=2, now_fn=lambda: NOW,
            )

        self.assertEqual(value["status"], "adequate")
        self.assertEqual(len(value["passes"]), 1)
        self.assertTrue(value["passes"][0]["progress"])

    def test_source_admission_failure_does_not_block_existing_inventory_recovery(self):
        before = blocked_state(available=0, stage="admission")
        after = {
            "status": "adequate",
            "accounts": [{
                "provider": "x", "account_id": "acct-x",
                "publishing_intent": True, "status": "adequate",
                "stock_floor": 2, "runnable": 2, "reserved": 3,
                "required_items": 5, "available_items": 5,
                "unreserved_schedule_opportunities": 0,
            }],
            "recovery_work": [],
        }
        continuity = iter([before, after])

        def stage(name, args, *, timeout):
            rolling_supply._command_safe(args)
            if name == "source-admission":
                return {"stage": name, "status": "attention", "exit_code": 7}
            return {"stage": name, "status": "completed", "exit_code": 0}

        with patch.object(rolling_supply, "_continuity_stage",
                          return_value={"stage": "continuity", "status": "completed", "exit_code": 0}), \
             patch.object(rolling_supply, "_continuity_state",
                          side_effect=lambda: next(continuity)), \
             patch.object(rolling_supply, "_sync_target_vaults", return_value=[]), \
             patch.object(rolling_supply, "_needs_source_observation", return_value=False), \
             patch.object(rolling_supply, "run_stage", side_effect=stage), \
             patch.object(rolling_supply.local_store, "read", return_value={}), \
             patch.object(rolling_supply.local_store, "write"):
            value = rolling_supply.reconcile(apply=True, now_fn=lambda: NOW)

        self.assertEqual(value["status"], "adequate")
        self.assertNotIn("exit_code", value)

    def test_allocator_failure_remains_exact_cycle_failure(self):
        before = blocked_state(available=2, stage="scheduling")
        after = blocked_state(available=2, stage="scheduling")
        continuity = iter([before, after])

        def stage(name, args, *, timeout):
            rolling_supply._command_safe(args)
            if name == "allocator":
                return {"stage": name, "status": "attention", "exit_code": 7}
            return {"stage": name, "status": "completed", "exit_code": 0}

        with patch.object(rolling_supply, "_continuity_stage",
                          return_value={"stage": "continuity", "status": "completed", "exit_code": 0}), \
             patch.object(rolling_supply, "_continuity_state",
                          side_effect=lambda: next(continuity)), \
             patch.object(rolling_supply, "_sync_target_vaults", return_value=[]), \
             patch.object(rolling_supply, "_needs_source_observation", return_value=False), \
             patch.object(rolling_supply, "run_stage", side_effect=stage), \
             patch.object(rolling_supply.local_store, "read", return_value={}), \
             patch.object(rolling_supply.local_store, "write"):
            value = rolling_supply.reconcile(apply=True, now_fn=lambda: NOW)

        self.assertEqual(value["status"], "allocator_failed")
        self.assertEqual(value["exit_code"], 7)

    def test_cadence_ready_deep_reserve_shortfall_is_successful_recovery(self):
        before = {
            "status": "recovering",
            "accounts": [{
                "provider": "x", "account_id": "acct-x",
                "publishing_intent": True, "status": "recovering",
                "stock_floor": 2, "runnable": 2, "reserved": 0,
                "required_items": 7, "available_items": 2,
                "unreserved_schedule_opportunities": 0,
            }],
            "recovery_work": [{
                "provider": "x", "account_id": "acct-x",
                "blocker_stage": "generation",
                "blocker_reason": "cadence_ready_reserve_rebuilding",
                "cadence_deficit": 0, "reserve_deficit": 5,
                "available_items": 2, "stock_floor": 2,
            }],
        }
        continuity = iter([before, before])

        def stage(name, args, *, timeout):
            rolling_supply._command_safe(args)
            return {"stage": name, "status": "completed", "exit_code": 0}

        with patch.object(rolling_supply, "_continuity_stage",
                          return_value={"stage": "continuity", "status": "completed", "exit_code": 0}), \
             patch.object(rolling_supply, "_continuity_state",
                          side_effect=lambda: next(continuity)), \
             patch.object(rolling_supply, "_sync_target_vaults", return_value=[]), \
             patch.object(rolling_supply, "_needs_source_observation", return_value=False), \
             patch.object(rolling_supply, "run_stage", side_effect=stage), \
             patch.object(rolling_supply.local_store, "read", return_value={}), \
             patch.object(rolling_supply.local_store, "write"):
            value = rolling_supply.reconcile(apply=True, now_fn=lambda: NOW)

        self.assertEqual(value["status"], "recovering")
        self.assertEqual(value["reason"], "cadence_ready_reserve_rebuilding")
        self.assertEqual(value["measure"]["blocked_accounts"], 0)
        self.assertEqual(value["measure"]["recovering_accounts"], 1)

    def test_generation_blocker_requests_work_handoff_without_paid_fallback(self):
        state = blocked_state(stage="generation")
        self.assertTrue(rolling_supply._needs_work_handoff(state))
        self.assertFalse(rolling_supply._needs_source_observation(state))


if __name__ == "__main__":
    unittest.main()

