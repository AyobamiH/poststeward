from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from threading import Event
import json
import os
import tempfile
import time
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from ocpf_post import admission, admission_runtime, local_store, scoped_admission
from ocpf_post.queue_watch import identity

UTC = timezone.utc


def candidate(index: int, *, provider: str = "x", project: str = "p1", account: str = "a1",
              expires_at: datetime | None = None) -> dict:
    return {
        "campaign": f"C-{index}",
        "provider": provider,
        "project": project,
        "account_id": account,
        "text_sha256": f"{index:064x}"[-64:],
        "expires_at": expires_at.isoformat().replace("+00:00", "Z") if expires_at else None,
    }


class AdmissionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.old_state = os.environ.get("OCPF_POST_STATE_DIR")
        self.old_config = os.environ.get("OCPF_POST_CONFIG_DIR")
        os.environ["OCPF_POST_STATE_DIR"] = str(Path(self.tmp.name) / "state")
        os.environ["OCPF_POST_CONFIG_DIR"] = str(Path(self.tmp.name) / "config")
        self.now = datetime(2026, 9, 12, 9, 0, tzinfo=UTC)
        self.policy = {
            **admission.DEFAULT_POLICY,
            "global_high_water": 5,
            "global_recovery_water": 2,
            "provider_high_water": {"x": 5, "threads": 5, "linkedin": 5},
            "provider_recovery_water": {"x": 2, "threads": 2, "linkedin": 2},
            "account_high_water": 5,
            "account_recovery_water": 2,
            "project_high_water": 5,
            "project_recovery_water": 2,
            "aged_high_water": 3,
            "aged_recovery_water": 1,
            "expiry_risk_high_water": 3,
            "expiry_risk_recovery_water": 1,
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

    def decide_with(self, rows, *, now=None, persist=False, project="p1", projected=None):
        with patch("ocpf_post.admission.load_policy", return_value=self.policy), \
             patch("ocpf_post.portfolio.delivery_candidates", return_value=list(rows)):
            return admission.decide(project=project, projected_addition=projected,
                                    now=now or self.now, persist=persist)

    def test_global_high_water_pauses_new_admission_without_mutating_inventory(self) -> None:
        rows = [candidate(i) for i in range(5)]
        before = json.loads(json.dumps(rows))
        result = self.decide_with(rows, persist=True)
        self.assertFalse(result["admitted"])
        self.assertEqual(result["mode"], "paused")
        self.assertIn("global_high_water", result["reasons"])
        self.assertEqual(rows, before)
        self.assertEqual(json.loads(admission.state_file().read_text())["mode"], "paused")

    def test_sustained_arrivals_stay_paused_until_recovery_water_not_high_water(self) -> None:
        day1 = self.decide_with([candidate(i) for i in range(5)], persist=True)
        self.assertFalse(day1["admitted"])

        # Pressure has fallen below the high-water mark, but not below the
        # deliberate recovery boundary. Hysteresis keeps ordinary arrivals out.
        day2 = self.decide_with([candidate(i) for i in range(4)], now=self.now + timedelta(days=1), persist=True)
        self.assertFalse(day2["admitted"])
        self.assertEqual(day2["reasons"], ["hysteresis_recovery_not_reached"])

        day3 = self.decide_with([candidate(i) for i in range(2)], now=self.now + timedelta(days=2), persist=True)
        self.assertTrue(day3["admitted"])
        self.assertEqual(day3["mode"], "open")

    def test_project_provider_and_account_budgets_are_independent_pressure_reasons(self) -> None:
        policy = {**self.policy, "global_high_water": 50, "global_recovery_water": 20,
                  "project_high_water": 3, "project_recovery_water": 1,
                  "account_high_water": 3, "account_recovery_water": 1,
                  "provider_high_water": {"x": 3, "threads": 40, "linkedin": 40},
                  "provider_recovery_water": {"x": 1, "threads": 20, "linkedin": 20}}
        rows = [candidate(i, project="p1", provider="x", account="a1") for i in range(3)]
        with patch("ocpf_post.admission.load_policy", return_value=policy), \
             patch("ocpf_post.portfolio.delivery_candidates", return_value=rows):
            result = admission.decide(project="p1", now=self.now)
        self.assertIn("project_high_water:p1", result["reasons"])
        self.assertIn("provider_high_water:x", result["reasons"])
        self.assertIn("account_high_water:a1", result["reasons"])

    def test_projected_additions_cannot_jump_the_high_water_boundary(self) -> None:
        rows = [candidate(1), candidate(2)]
        projected = {"total": 3, "by_provider": {"x": 3}, "by_project": {"p1": 3},
                     "by_account": {"a1": 3}, "expiry_risk": 0}
        result = self.decide_with(rows, projected=projected)
        self.assertFalse(result["admitted"])
        self.assertEqual(result["projected_metrics"]["eligible_unreserved"], 5)
        self.assertIn("global_high_water", result["reasons"])

    def test_aged_waiters_and_expiry_risk_apply_pressure_without_deleting_them(self) -> None:
        rows = [candidate(i, expires_at=self.now + timedelta(hours=2)) for i in range(3)]
        state_dir = Path(os.environ["OCPF_POST_STATE_DIR"])
        state_dir.mkdir(parents=True, exist_ok=True)
        records = {}
        for row in rows:
            key = identity(row)
            records[key] = {**row, "state": "waiting",
                            "first_eligible_at": (self.now - timedelta(hours=30)).isoformat().replace("+00:00", "Z"),
                            "last_seen_at": self.now.isoformat().replace("+00:00", "Z")}
        (state_dir / "queue-watch.json").write_text(json.dumps({
            "schema_version": 1,
            "observed_at": self.now.isoformat().replace("+00:00", "Z"),
            "records": records,
        }))
        result = self.decide_with(rows)
        self.assertFalse(result["admitted"])
        self.assertEqual(result["metrics"]["aged_count"], 3)
        self.assertEqual(result["metrics"]["expiry_risk_count"], 3)
        self.assertIn("aged_high_water", result["reasons"])
        self.assertIn("expiry_risk_high_water", result["reasons"])
        self.assertEqual(len(records), 3)

    def test_closed_history_is_not_an_admission_input(self) -> None:
        # Admission consumes delivery_candidates, whose contract is current
        # eligible/unreserved inventory. Arbitrarily large terminal history cannot
        # inflate this snapshot.
        rows = [candidate(1), candidate(2)]
        result = self.decide_with(rows)
        self.assertTrue(result["admitted"])
        self.assertEqual(result["metrics"]["eligible_unreserved"], 2)

    def test_clock_regression_fails_closed_for_new_inventory(self) -> None:
        self.decide_with([candidate(1)], persist=True)
        result = self.decide_with([candidate(1)], now=self.now - timedelta(hours=1), persist=True)
        self.assertFalse(result["admitted"])
        self.assertEqual(result["reasons"], ["clock_regression"])

    def test_corrupt_observation_pauses_source_admission_without_raising(self) -> None:
        with patch("ocpf_post.admission_runtime._controlled_refresh", side_effect=admission.AdmissionError("corrupt")), \
             patch("ocpf_post.portfolio_source_loader.merged_source_profiles",
                   return_value={"schema_version": 1, "projects": {"p1": {}}}):
            result = admission_runtime.controlled_refresh(apply=True, project="p1", now=self.now)
        self.assertEqual(result["projects"][0]["status"], "admission_paused")
        self.assertEqual(result["static_campaigns"], [])
        self.assertEqual(result["event_campaigns"], [])
        self.assertIn("admission_observation_unavailable", result["projects"][0]["reasons"])

    def test_automatic_refresh_waits_boundedly_at_source_lock_authority(self) -> None:
        expected = {"schema_version": 1, "projects": []}
        with patch("ocpf_post.admission_runtime._controlled_refresh", return_value=expected) as refresh:
            result = admission_runtime.controlled_refresh(apply=True, project="p1", now=self.now)
        self.assertEqual(result, expected)
        refresh.assert_called_once_with(
            apply=True,
            project="p1",
            now=self.now,
            source_lock_timeout_seconds=admission_runtime.SOURCE_OPERATION_WAIT_SECONDS,
        )

    def test_automatic_refresh_timeout_fails_closed_without_campaign_writes(self) -> None:
        from ocpf_post.runtime_sources import source_lock

        entered = Event()
        release = Event()

        def holder():
            with source_lock(operation="test-collector"):
                entered.set()
                release.wait(timeout=2)

        def refresh(*, apply, project, now, source_lock_timeout_seconds):
            with source_lock(
                operation="replenish_refresh",
                timeout_seconds=source_lock_timeout_seconds,
            ):
                raise AssertionError("timed-out waiter must not acquire source authority")

        with ThreadPoolExecutor(max_workers=1) as executor:
            held = executor.submit(holder)
            self.assertTrue(entered.wait(timeout=1))
            with patch.object(admission_runtime, "SOURCE_OPERATION_WAIT_SECONDS", 0.05), \
                 patch("ocpf_post.admission_runtime._controlled_refresh", side_effect=refresh), \
                 patch("ocpf_post.portfolio_source_loader.merged_source_profiles",
                       return_value={"schema_version": 1, "projects": {"p1": {}}}):
                result = admission_runtime.controlled_refresh(
                    apply=True, project="p1", now=self.now,
                )
            self.assertEqual(result["projects"][0]["status"], "admission_paused")
            self.assertIn("source_operation_active", result["projects"][0]["reasons"])
            self.assertEqual(result["generative_campaigns"], [])
            self.assertFalse((Path(os.environ["OCPF_POST_STATE_DIR"]) / "runtime-campaigns").exists())
            release.set()
            held.result(timeout=2)

    def test_source_wait_does_not_monopolise_admission_writer(self) -> None:
        from ocpf_post.runtime_sources import source_lock
        from ocpf_post.source_pipeline import _refresh_authority

        source_entered = Event()
        release_source = Event()
        refresh_entered = Event()
        expected = {"schema_version": 1, "projects": [{"project": "p1", "status": "ok"}]}

        def holder():
            with source_lock(operation="test-collector"):
                source_entered.set()
                release_source.wait(timeout=2)

        def refresh(*, apply, project, now, source_lock_timeout_seconds):
            with _refresh_authority(
                apply=apply,
                source_lock_timeout_seconds=source_lock_timeout_seconds,
            ):
                refresh_entered.set()
                return expected

        with ThreadPoolExecutor(max_workers=2) as executor:
            held = executor.submit(holder)
            self.assertTrue(source_entered.wait(timeout=1))
            with patch.object(admission_runtime, "SOURCE_OPERATION_WAIT_SECONDS", 1.0), \
                 patch("ocpf_post.admission_runtime._controlled_refresh", side_effect=refresh):
                waiting = executor.submit(
                    admission_runtime.controlled_refresh,
                    apply=True,
                    project="p1",
                    now=self.now,
                )
                time.sleep(0.05)
                self.assertFalse(waiting.done())
                # The slow source wait must not own admission authority. A vault
                # sync can still obtain this writer and admit reviewed copy.
                with local_store.try_locked(admission.state_file()) as acquired:
                    self.assertTrue(acquired)
                self.assertFalse(refresh_entered.is_set())
                release_source.set()
                self.assertEqual(waiting.result(timeout=2), expected)
                self.assertTrue(refresh_entered.is_set())
            held.result(timeout=2)

    def test_automatic_refresh_recovers_after_real_transient_source_contention(self) -> None:
        from ocpf_post.runtime_sources import source_lock

        entered = Event()
        release = Event()
        expected = {"schema_version": 1, "projects": [{"project": "p1", "status": "ok"}]}

        def holder():
            with source_lock(operation="test-collector"):
                entered.set()
                release.wait(timeout=2)

        def refresh(*, apply, project, now, source_lock_timeout_seconds):
            with source_lock(
                operation="replenish_refresh",
                timeout_seconds=source_lock_timeout_seconds,
            ):
                return expected

        with ThreadPoolExecutor(max_workers=2) as executor:
            held = executor.submit(holder)
            self.assertTrue(entered.wait(timeout=1))
            with patch.object(admission_runtime, "SOURCE_OPERATION_WAIT_SECONDS", 1.0), \
                 patch("ocpf_post.admission_runtime._controlled_refresh", side_effect=refresh):
                waiting = executor.submit(
                    admission_runtime.controlled_refresh,
                    apply=True,
                    project="p1",
                    now=self.now,
                )
                time.sleep(0.05)
                self.assertFalse(waiting.done())
                release.set()
                self.assertEqual(waiting.result(timeout=2), expected)
            held.result(timeout=2)

    def test_vault_budget_reports_busy_after_bounded_wait(self) -> None:
        admission.state_file().parent.mkdir(parents=True, exist_ok=True)
        with local_store.locked(admission.state_file()):
            with scoped_admission.vault_budget(self.now, wait_seconds=0) as budget:
                result = budget.admit("p1", "x", "a1")
        self.assertFalse(result["admitted"])
        self.assertEqual(result["reasons"], ["admission_writer_busy"])
        self.assertEqual(result["error_type"], "BlockingIOError")

    def test_vault_budget_preserves_typed_observation_failure(self) -> None:
        with patch.object(
            scoped_admission.Budget,
            "__init__",
            side_effect=admission.AdmissionError("private state detail"),
        ):
            with scoped_admission.vault_budget(self.now, wait_seconds=0) as budget:
                result = budget.admit("p1", "x", "a1")
        self.assertFalse(result["admitted"])
        self.assertEqual(result["reasons"], ["admission_observation_unavailable"])
        self.assertEqual(result["error_type"], "AdmissionError")
        self.assertNotIn("private state detail", str(result))

    def test_concurrent_admission_writer_pauses_second_writer(self) -> None:
        admission.state_file().parent.mkdir(parents=True, exist_ok=True)
        with local_store.locked(admission.state_file()):
            with patch("ocpf_post.portfolio_source_loader.merged_source_profiles",
                       return_value={"schema_version": 1, "projects": {"p1": {}}}):
                result = admission_runtime.controlled_refresh(apply=True, project="p1", now=self.now)
        self.assertEqual(result["projects"][0]["status"], "admission_paused")
        self.assertIn("admission_writer_busy", result["projects"][0]["reasons"])


if __name__ == "__main__":
    unittest.main()
