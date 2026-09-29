from copy import deepcopy
from datetime import datetime, timedelta, timezone
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from ocpf_post import admission, local_store, scoped_admission

NOW = datetime(2026, 9, 12, 17, tzinfo=timezone.utc)


class AccountInventoryReserveTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.policy = deepcopy(admission.DEFAULT_POLICY)
        self.rows = []
        for provider, count in (("x", 165), ("threads", 106), ("linkedin", 160)):
            self.rows.extend({"campaign": f"OLD-{provider}-{i}", "provider": provider,
                              "account_id": provider + "-founder", "project": "busy"}
                             for i in range(count))
        self.schedules = []
        self.registry = {"projects": {
            "brand": {"accounts": {"brand": {"provider": "threads", "account_id": "54321"}}},
            "other": {"accounts": {"alias": {"provider": "threads", "account_id": "54321"}}},
            "busy": {"accounts": {p: {"provider": p, "account_id": p + "-founder"}
                                     for p in ("x", "threads", "linkedin")}},
        }}
        for mock in (
            patch.dict(os.environ, OCPF_POST_STATE_DIR=str(self.root), OCPF_POST_CONFIG_DIR=str(self.root)),
            patch("ocpf_post.portfolio.delivery_candidates", side_effect=lambda **kw: deepcopy(self.rows)),
            patch("ocpf_post.scheduler.schedule_records", side_effect=lambda: deepcopy(self.schedules)),
            patch("ocpf_post.registry.load_registry", side_effect=lambda: deepcopy(self.registry)),
            patch.object(admission, "load_policy", side_effect=lambda: deepcopy(self.policy)),
        ):
            mock.start()
            self.addCleanup(mock.stop)

    def test_owner_backlog_allows_three_brand_items_not_a_global_reopening(self):
        budget = scoped_admission.Budget(NOW, True)
        for _ in range(3):
            result = budget.admit("brand", "threads", "54321")
            self.assertTrue(result["admitted"])
            self.assertTrue(result["protected"])
            self.assertIn("global_resource_ceiling", result["pressure_reasons"])
        self.assertFalse(budget.admit("brand", "threads", "54321")["admitted"])
        self.assertFalse(budget.admit("busy", "x", "x-founder")["admitted"])
        self.assertTrue(scoped_admission.status()["global_paused"])
        self.assertEqual(budget.total, 434)
        self.assertEqual(len(self.rows), 431)

    def test_aliases_and_projects_share_one_account_stock_allowance(self):
        self.registry["projects"]["brand"]["accounts"]["another-alias"] = {"provider": "threads", "account_id": "54321"}
        budget = scoped_admission.Budget(NOW, True)
        self.assertTrue(budget.admit("brand", "threads", "54321")["admitted"])
        self.assertTrue(budget.admit("other", "threads", "54321")["admitted"])
        self.assertTrue(budget.admit("brand", "threads", "54321")["admitted"])
        self.assertFalse(budget.admit("other", "threads", "54321")["admitted"])

    def test_restart_counts_waiting_scheduled_and_executing_stock(self):
        self.rows.append({"campaign": "WAITING", "provider": "threads", "account_id": "54321", "project": "brand"})
        self.schedules = [{"campaign": state, "provider": "threads", "account_id": "54321", "status": state}
                          for state in ("scheduled", "executing")]
        for _ in range(2):
            result = scoped_admission.Budget(NOW, True).admit("brand", "threads", "54321")
            self.assertFalse(result["admitted"])
            self.assertEqual(result["stock_before"], 3)

    def test_account_moving_to_reservation_does_not_double_count(self):
        row = {"campaign": "ONE", "provider": "threads", "account_id": "54321", "project": "brand"}
        self.rows.append(row)
        self.schedules.append({**row, "status": "scheduled"})
        result = scoped_admission.Budget(NOW, False).admit("brand", "threads", "54321")
        self.assertTrue(result["admitted"])
        self.assertEqual(result["stock_before"], 1)
        self.assertFalse(scoped_admission.path().exists())

    def test_unregistered_wrong_project_and_inactive_accounts_stay_blocked(self):
        for project, account in (("missing", "54321"), ("brand", "unknown")):
            self.assertFalse(scoped_admission.Budget(NOW, True).admit(project, "threads", account)["admitted"])
        with patch("ocpf_post.account_profiles.unavailable", return_value="inactive"):
            result = scoped_admission.Budget(NOW, True).admit("brand", "threads", "54321")
        self.assertEqual(result["reasons"], ["account_unavailable"])

    def test_aggregate_provider_and_project_pressure_cannot_starve_empty_account(self):
        self.rows = [{**r, "project": "brand"} for r in self.rows]
        self.policy["provider_high_water"]["threads"] = 100
        self.policy["provider_recovery_water"]["threads"] = 60
        result = scoped_admission.Budget(NOW, True).admit("brand", "threads", "54321")
        self.assertTrue(result["protected"])
        self.assertIn("provider_high_water", result["pressure_reasons"])
        self.assertIn("project_high_water", result["pressure_reasons"])

    def test_local_age_recovery_cannot_be_cleared_by_repeated_attempts(self):
        self.policy.update(aged_high_water=2, aged_recovery_water=0)
        local_store.write(scoped_admission.path(), {"schema_version": 1, "scopes": {
            "threads:54321": {"mode": "paused", "reasons": ["aged_high_water"]}
        }})
        with patch.object(admission, "_queue_ages", return_value=(1, "observed")):
            for _ in range(3):
                self.assertFalse(scoped_admission.Budget(NOW, True).admit("brand", "threads", "54321")["admitted"])
                self.assertTrue(scoped_admission.status()["scopes"]["threads:54321"]["local_paused"])
        with patch.object(admission, "_queue_ages", return_value=(0, "observed")):
            self.assertTrue(scoped_admission.Budget(NOW, True).admit("brand", "threads", "54321")["admitted"])

    def test_near_expiry_limit_and_account_limit_still_apply(self):
        self.policy.update(expiry_risk_high_water=1, expiry_risk_recovery_water=0)
        budget = scoped_admission.Budget(NOW, True)
        soon = (NOW + timedelta(hours=1)).isoformat()
        self.assertTrue(budget.admit("brand", "threads", "54321", expires_at=soon)["admitted"])
        self.assertFalse(budget.admit("brand", "threads", "54321", expires_at=soon)["admitted"])
        self.policy.update(account_high_water=1, account_recovery_water=0)
        budget = scoped_admission.Budget(NOW, True)
        self.assertTrue(budget.admit("brand", "threads", "54321")["admitted"])
        self.assertFalse(budget.admit("brand", "threads", "54321")["admitted"])

    def test_reserve_can_be_disabled_and_corrupt_state_is_not_recovery(self):
        self.policy["account_inventory_reserve"] = 0
        self.assertFalse(scoped_admission.Budget(NOW, True).admit("brand", "threads", "54321")["admitted"])
        local_store.write(scoped_admission.path(), {"schema_version": 1, "scopes": {"threads:54321": {"mode": "paused", "local_paused": "false"}}})
        with self.assertRaises(admission.AdmissionError):
            scoped_admission.Budget(NOW, True).admit("brand", "threads", "54321")

    def test_policy_validation_rejects_unbounded_or_non_integer_reserves(self):
        for value in (-1, 21, True, 3.5, "3"):
            with self.subTest(value=value), self.assertRaises(admission.AdmissionError):
                admission.validate_policy({"schema_version": 1, "account_inventory_reserve": value})
        self.assertEqual(admission.validate_policy({"schema_version": 1})["account_inventory_reserve"], 3)

    def test_legacy_recovery_only_state_retains_local_recovery_and_global_corruption_blocks(self):
        self.policy.update(aged_high_water=2, aged_recovery_water=0)
        local_store.write(scoped_admission.path(), {"schema_version": 1, "scopes": {
            "threads:54321": {"mode": "paused", "reasons": ["scope_recovery_not_reached"]}
        }})
        with patch.object(admission, "_queue_ages", return_value=(1, "observed")):
            self.assertFalse(scoped_admission.Budget(NOW, True).admit("brand", "threads", "54321")["admitted"])
        local_store.write(scoped_admission.path(), {"schema_version": 1, "scopes": {}, "global_paused": "false"})
        with self.assertRaises(admission.AdmissionError):
            scoped_admission.Budget(NOW, True)
