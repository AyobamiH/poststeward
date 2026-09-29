from __future__ import annotations

from collections import Counter
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from zoneinfo import ZoneInfo

from ocpf_post import portfolio as base, portfolio_queue as queue, release_pacing as pacing
from ocpf_post import operator_guide, replenisher
from ocpf_post.publication_payload import build_publication

UTC = timezone.utc
NOW = datetime(2026, 9, 21, 0, 0, tzinfo=UTC)


def policy():
    value = deepcopy(base.DEFAULT_POLICY)
    for provider, amount in {"x": 13, "threads": 7, "linkedin": 5}.items():
        value["providers"][provider].update(daily_target=amount, development_max=1, commercial_min=0)
    value["owner_extension"] = {"keep": "unchanged"}
    return value


def fixture(stock, amount=20, used=0, now=NOW):
    value = policy()
    value["providers"] = {"x": {**value["providers"]["x"], "daily_target": amount}}
    value = pacing.prepare(value)
    candidates = [{"campaign": f"ITEM-{i:04d}", "project": f"project-{i}", "title": f"Original {i}",
                   "provider": "x", "lane": "evergreen", "priority": 72,
                   "prepared_at": (NOW - timedelta(days=1)).isoformat(),
                   "expires_at": (NOW + timedelta(days=7)).isoformat(),
                   "family": f"family-{i}", "topic_key": f"topic-{i}", "part_count": 3}
                  for i in range(stock)]
    count = {"total": used, "lanes": {"development": 0, "commercial": 0, "evergreen": used}}
    inputs = {"now": now.isoformat(), "horizon_minutes": 1440, "candidates": candidates,
              "histories": {"x": []}, "day_counts": {"x": {
                  "2026-09-21": deepcopy(count), "2026-09-22": deepcopy(count), "2026-09-23": deepcopy(count)}},
              "capacity": {"x": {"local_day": "2026-09-21", "daily_budget_used": used,
                                    "daily_budget_remaining": max(0, amount - used),
                                    "daily_target_met": used >= amount}}}
    return inputs, value


class PolicyTests(unittest.TestCase):
    def test_keeps_actual_saved_numbers_not_example_defaults(self):
        before = policy()
        frozen = deepcopy(before)
        after = pacing.prepare(before)
        self.assertEqual(before, frozen)
        self.assertEqual(after["owner_extension"], before["owner_extension"])
        for provider in before["providers"]:
            original = before["providers"][provider]
            self.assertEqual(after["providers"][provider], {**original, "flow_mode": "fixed"})
            self.assertEqual(after["providers"][provider]["hard_daily_ceiling"], 100)
        self.assertTrue(pacing.enabled(after))
        self.assertEqual(pacing.prepare(after), after)

    def test_missing_legacy_flow_keeps_numbers_and_preserves_100_safety(self):
        before = policy()
        for row in before["providers"].values():
            row.pop("flow_mode")
            row.pop("hard_daily_ceiling")
        after = pacing.prepare(before)
        self.assertEqual([after["providers"][p]["daily_target"] for p in ("x", "threads", "linkedin")], [13, 7, 5])
        self.assertTrue(all(row["hard_daily_ceiling"] == 100 for row in after["providers"].values()))

    def test_zero_is_a_deliberate_no_release_amount(self):
        before = policy()
        before["providers"]["x"].update(daily_target=0, development_max=0)
        after = pacing.prepare(before)
        self.assertEqual(base._daily_limit(after["providers"]["x"]), 0)
        self.assertEqual(base._grid(NOW.date(), after["providers"]["x"], ZoneInfo("Europe/London")), [])

    def test_smaller_existing_safety_ceiling_is_not_raised(self):
        before = policy()
        before["providers"]["x"]["hard_daily_ceiling"] = 50
        self.assertEqual(pacing.prepare(before)["providers"]["x"]["hard_daily_ceiling"], 50)

    def test_bad_numbers_or_target_above_ceiling_are_rejected(self):
        for bad in (None, True, 2.5, "13", -1, 101):
            with self.subTest(bad=bad):
                before = policy()
                before["providers"]["x"]["daily_target"] = bad
                with self.assertRaises((ValueError, RuntimeError, TypeError)):
                    pacing.prepare(before)
        before = policy()
        before["providers"]["x"]["hard_daily_ceiling"] = 12
        with self.assertRaisesRegex(ValueError, "exceeds"):
            pacing.prepare(before)

    def test_marker_cannot_lie_about_admission_mode(self):
        before = policy()
        before["release_pacing"] = dict(pacing.MARKER)
        with self.assertRaises((ValueError, RuntimeError)):
            base.validate_policy(before)

    def test_unknown_marker_refuses_silent_migration(self):
        before = policy()
        before["release_pacing"] = {"schema_version": 99, "mode": "unknown"}
        with self.assertRaises((ValueError, RuntimeError)):
            pacing.prepare(before)

    def test_unselected_legacy_admission_remains_unchanged(self):
        before = policy()
        self.assertEqual(base._daily_limit(before["providers"]["x"]), 100)
        self.assertEqual(len(base._grid(NOW.date(), before["providers"]["x"], ZoneInfo("Europe/London"))), 100)
        self.assertEqual(pacing.capacity_fields(before, before["providers"]["x"], 13), {})

    def test_additional_accounts_keep_their_own_amount_and_window(self):
        value = pacing.prepare(policy())
        account = {"provider": "x", "enabled": True,
                   "policy": {**value["providers"]["x"], "daily_target": 3,
                              "window_start": "09:00", "window_end": "17:00"}}
        scoped = queue._scoped_account_policy(value, account)
        self.assertEqual(scoped["daily_target"], 3)
        self.assertEqual(scoped["window_start"], "09:00")
        self.assertEqual(scoped["hard_daily_ceiling"], 100)
        self.assertEqual(scoped["flow_mode"], "fixed")
        self.assertEqual(base._daily_limit(scoped), 3)
        rows = pacing.summary(value, {"x:additional": account})["accounts"]
        self.assertEqual(rows[-1]["normal_originals_per_day"], 3)

    def test_capacity_names_do_not_conflate_daily_allowance_and_safety(self):
        value = pacing.prepare(policy())
        fields = pacing.capacity_fields(value, value["providers"]["x"], 13)
        self.assertTrue(fields["daily_release_limit_met"])
        self.assertFalse(fields["hard_daily_ceiling_met"])
        self.assertEqual(fields["daily_release_remaining"], 0)
        self.assertEqual(fields["hard_ceiling_remaining"], 87)
        self.assertEqual(fields["hard_daily_ceiling"], 100)


class LocalApplyTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.config = self.root / "config"
        self.state = self.root / "state"
        self.home = self.root / "home"
        self.home.mkdir()
        self.config.mkdir()
        self.state.mkdir()
        self.path = self.config / "portfolio-policy.json"
        self.raw = (json.dumps(policy(), indent=2) + "\n").encode()
        self.path.write_bytes(self.raw)
        self.env = patch.dict(os.environ, {"HOME": str(self.home), "OCPF_POST_CONFIG_DIR": str(self.config),
                                           "OCPF_POST_STATE_DIR": str(self.state),
                                           "OCPF_POST_PORTFOLIO_POLICY": str(self.path)})
        self.env.start()
        self.addCleanup(self.env.stop)

    def test_preview_reads_only_and_does_not_hash_live_ledgers(self):
        first = pacing.review(policy_path=self.path, accounts={})
        (self.state / "schedule-events.jsonl").write_text("new evidence\n")
        second = pacing.review(policy_path=self.path, accounts={})
        self.assertEqual(first["review_sha256"], second["review_sha256"])
        self.assertEqual(self.path.read_bytes(), self.raw)
        self.assertEqual(list(self.config.iterdir()), [self.path])
        self.assertFalse(first["daily_amounts_changed"])

    def test_apply_changes_only_policy_and_saves_exact_backup(self):
        ledger = self.state / "schedule-events.jsonl"
        ledger.write_bytes(b"historical evidence stays intact\n")
        before_hash = hashlib.sha256(ledger.read_bytes()).hexdigest()
        expected = pacing.review(policy_path=self.path, accounts={})["review_sha256"]
        with patch("urllib.request.urlopen", side_effect=AssertionError("network forbidden")), \
             patch.object(base, "create_schedule", side_effect=AssertionError("reservation forbidden")):
            result = pacing.apply(expected, policy_path=self.path, accounts={}, evidence_dir=self.root / "evidence")
        self.assertTrue(result["applied"])
        self.assertEqual(Path(result["backup_path"]).read_bytes(), self.raw)
        self.assertTrue(pacing.enabled(json.loads(self.path.read_bytes())))
        self.assertEqual(hashlib.sha256(ledger.read_bytes()).hexdigest(), before_hash)
        self.assertEqual(self.path.stat().st_mode & 0o777, 0o600)

    def test_changed_policy_refuses_stale_review(self):
        expected = pacing.review(policy_path=self.path, accounts={})["review_sha256"]
        value = policy()
        value["providers"]["x"]["daily_target"] = 14
        self.path.write_text(json.dumps(value))
        changed = self.path.read_bytes()
        with self.assertRaisesRegex(ValueError, "review changed"):
            pacing.apply(expected, policy_path=self.path, accounts={}, evidence_dir=self.root / "evidence")
        self.assertEqual(self.path.read_bytes(), changed)
        self.assertFalse((self.root / "evidence").exists())

    def test_changed_account_controls_refuse_stale_review(self):
        account = {"provider": "x", "enabled": True, "policy": {**policy()["providers"]["x"], "daily_target": 3}}
        first = pacing.review(policy_path=self.path, accounts={"x:test": account})
        account["policy"]["daily_target"] = 4
        with self.assertRaisesRegex(ValueError, "review changed"):
            pacing.apply(first["review_sha256"], policy_path=self.path, accounts={"x:test": account},
                         evidence_dir=self.root / "evidence")
        self.assertEqual(self.path.read_bytes(), self.raw)

    def test_idempotent_apply_makes_no_second_backup_or_write(self):
        self.path.write_text(json.dumps(pacing.prepare(policy())))
        before = self.path.read_bytes()
        expected = pacing.review(policy_path=self.path, accounts={})["review_sha256"]
        result = pacing.apply(expected, policy_path=self.path, accounts={}, evidence_dir=self.root / "evidence")
        self.assertEqual(result["status"], "already_enabled")
        self.assertFalse(result["applied"])
        self.assertEqual(self.path.read_bytes(), before)
        self.assertFalse((self.root / "evidence").exists())

    def test_saved_steady_policy_cannot_revive_the_old_capacity_trial(self):
        prepared = pacing.prepare(policy())
        self.path.write_text(json.dumps(prepared))
        with patch("ocpf_post.capacity_experiment.overlay", side_effect=AssertionError("trial overlay forbidden")):
            self.assertEqual(base.load_policy(), prepared)

    def test_missing_policy_never_invents_daily_amounts(self):
        self.path.unlink()
        with self.assertRaisesRegex(ValueError, "saved portfolio policy"):
            pacing.review(policy_path=self.path, accounts={})
        self.assertFalse(self.path.exists())

    def test_symlink_policy_is_not_mutated(self):
        real = self.root / "original.json"
        self.path.rename(real)
        self.path.symlink_to(real)
        with self.assertRaises(ValueError):
            pacing.review(policy_path=self.path, accounts={})
        self.assertEqual(real.read_bytes(), self.raw)

    def test_duplicate_keys_are_rejected(self):
        self.path.write_text('{"schema_version":1,"schema_version":1}')
        with self.assertRaisesRegex(ValueError, "Duplicate"):
            pacing.review(policy_path=self.path, accounts={})

    def test_oversized_policy_is_rejected(self):
        self.path.write_bytes(b" " * (pacing.MAX_POLICY_BYTES + 1))
        with self.assertRaisesRegex(ValueError, "size bound"):
            pacing.review(policy_path=self.path, accounts={})

    def test_policy_lock_wait_is_bounded_and_does_not_change_config(self):
        with pacing.policy_lock(self.path):
            with self.assertRaisesRegex(RuntimeError, "busy"):
                with pacing.policy_lock(self.path, timeout=0):
                    self.fail("contended lock acquired")
        self.assertEqual(self.path.read_bytes(), self.raw)

    def test_cli_requires_an_explicit_apply_authorisation(self):
        script = Path(__file__).resolve().parents[1] / "scripts" / "steady-release.py"
        result = subprocess.run([sys.executable, "-B", str(script), "--apply"], capture_output=True, text=True)
        self.assertEqual(result.returncode, 2)
        self.assertEqual(self.path.read_bytes(), self.raw)

    def test_cli_saved_amounts_apply_and_subsequent_noop(self):
        script = Path(__file__).resolve().parents[1] / "scripts" / "steady-release.py"
        result = subprocess.run([sys.executable, "-B", str(script), "--apply", "--use-saved-amounts"],
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        output = json.loads(result.stdout)
        self.assertEqual(output["status"], "enabled")
        self.assertFalse(output["daily_amounts_changed"])
        second = subprocess.run([sys.executable, "-B", str(script), "--apply", "--use-saved-amounts"],
                                capture_output=True, text=True)
        self.assertEqual(second.returncode, 0, second.stdout + second.stderr)
        self.assertEqual(json.loads(second.stdout)["status"], "already_enabled")

    def test_evidence_cannot_pollute_live_state(self):
        expected = pacing.review(policy_path=self.path, accounts={})["review_sha256"]
        with self.assertRaisesRegex(ValueError, "outside live"):
            pacing.apply(expected, policy_path=self.path, accounts={}, evidence_dir=self.state / "evidence")
        self.assertEqual(self.path.read_bytes(), self.raw)


class AllocatorTests(unittest.TestCase):
    def test_more_reserve_does_not_buy_more_daily_publications(self):
        for stock in (20, 60, 200):
            with self.subTest(stock=stock):
                inputs, value = fixture(stock)
                frozen = deepcopy(inputs)
                result = queue.fair_plan(inputs, value)
                self.assertEqual(len(result["plan"]), 20)
                self.assertEqual(len(inputs["candidates"]), stock)
                self.assertEqual(inputs, frozen)
                self.assertEqual(result["capacity"]["x"]["daily_release_limit"], 20)
                self.assertEqual(result["capacity"]["x"]["hard_daily_ceiling"], 100)

    def test_shortage_is_not_permission_to_invent_or_force_content(self):
        inputs, value = fixture(2)
        self.assertEqual(len(queue.fair_plan(inputs, value)["plan"]), 2)

    def test_existing_committed_work_consumes_the_release_allowance(self):
        inputs, value = fixture(60, used=18)
        self.assertEqual(len(queue.fair_plan(inputs, value)["plan"]), 2)

    def test_over_allowance_from_earlier_bookings_creates_no_more_reservations(self):
        inputs, value = fixture(60, used=25)
        before = deepcopy(inputs)
        result = queue.fair_plan(inputs, value)
        self.assertEqual(result["plan"], [])
        self.assertEqual(inputs, before)
        self.assertTrue(result["capacity"]["x"]["daily_release_limit_met"])
        self.assertFalse(result["capacity"]["x"]["hard_daily_ceiling_met"])

    def test_past_empty_slots_are_not_bunched_into_a_catchup_burst(self):
        inputs, value = fixture(60, now=NOW + timedelta(hours=18))
        inputs["horizon_minutes"] = 240
        rows = queue.fair_plan(inputs, value)["plan"]
        self.assertGreater(len(rows), 0)
        self.assertLess(len(rows), 20)
        grid = {slot.replace(microsecond=0) for slot in base._grid(NOW.date(), value["providers"]["x"], ZoneInfo("Europe/London"))}
        self.assertTrue(all(base._parse_dt(row["run_at"]) in grid for row in rows))

    def test_expiry_is_not_extended_to_make_the_daily_amount(self):
        inputs, value = fixture(60)
        for row in inputs["candidates"]:
            row["expires_at"] = NOW.isoformat()
        self.assertEqual(queue.fair_plan(inputs, value)["plan"], [])

    def test_original_thread_and_verification_updates_count_once(self):
        payload = build_publication("x", "A complete approved sentence needs to stay intact. " * 16)
        self.assertGreater(payload["part_count"], 1)
        receipts = [{"campaign": "ORIGINAL", "provider": "x", "account_id": "test",
                     "status": status, "recorded_at": "2026-09-21T10:00:00Z", **payload}
                    for status in ("published_unverified", "published_verified")]
        with patch.object(base, "schedule_records", return_value=[]), \
             patch.object(base, "iter_receipts", return_value=iter(receipts)), \
             patch.object(base, "_manifest_lane", return_value="evergreen"), \
             patch("ocpf_post.account_profiles.matches_scope", return_value=True):
            count, _ = base._day_counts("x", NOW.date(), ZoneInfo("Europe/London"), "test")
        self.assertEqual(count, 1)
        self.assertEqual(payload["text"], ("A complete approved sentence needs to stay intact. " * 16).strip())

    def test_satisfied_reserve_does_not_start_generation(self):
        with patch.object(replenisher, "_generative_enabled", return_value=True), \
             patch.object(replenisher, "_generative_daily_remaining", return_value=10), \
             patch.object(replenisher, "_generative_project_usage_today", return_value=Counter()), \
             patch.object(replenisher, "_generative_project_daily_cap", return_value=0), \
             patch.object(replenisher, "_generative_portfolio_projects", return_value=[]), \
             patch.object(replenisher, "_model_generate_supply", side_effect=AssertionError("generation forbidden")):
            rows, reason = replenisher._generative_campaigns_for_profile(
                {"project": "test"}, source_sha="a" * 40, now=NOW, apply=True)
        self.assertEqual(rows, [])
        self.assertEqual(reason, "no_low_stock_project")


class GuideTests(unittest.TestCase):
    def test_the_guide_explains_pace_and_reserve_without_execution(self):
        summary = pacing.summary(pacing.prepare(policy()))
        snapshot = {"observed_at": NOW.isoformat(), "portfolio": {"status": {"release_pacing": summary}}}
        value = operator_guide.build(snapshot, now=NOW)
        card = next(row for row in value["items"] if row["id"] == "steady-originals")
        self.assertEqual(card["action"]["command"], "./ocpf-post portfolio status --json")
        self.assertFalse(card["automatic_action"])
        self.assertEqual(card["state"], "informational")
        self.assertIn("reserve", card["meaning"])
        self.assertTrue(any(row["id"] == "release-pace" for row in operator_guide.topics()))

    def test_missing_pacing_evidence_is_not_claimed_enabled(self):
        value = operator_guide.build({"observed_at": NOW.isoformat()}, now=NOW)
        self.assertFalse(any(row["id"] == "steady-originals" for row in value["items"]))


if __name__ == "__main__":
    unittest.main()
