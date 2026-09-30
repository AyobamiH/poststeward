from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import shlex
import unittest
from unittest.mock import patch

from ocpf_post import operator_guide as guide

NOW = datetime(2026, 9, 21, 18, 0, tzinfo=timezone.utc)


def snapshot():
    return {
        "revision": "fixture-revision", "observed_at": NOW.isoformat(),
        "state_integrity": {"status": "observed", "critical_ledger_status": "observed", "invalid_count": 0, "scope": "critical_ledgers_only"},
        "work": {"status": "observed", "items": []},
        "activity": {"active_schedules": [], "recent_receipts": []},
        "capabilities": {"status": "observed", "capabilities": [], "core_blockers": [], "activated_optional_blockers": []},
        "admission": {"mode": "open"},
        "operator": {"stages": [{"id": "supply"}], "reserve": {"status_counts": {}, "available_items": 10}},
        "execution": {"events": []},
    }


class OperatorGuideTests(unittest.TestCase):
    def test_unknown_is_not_healthy(self):
        self.assertFalse(guide.build({}, now=NOW)["fresh"])
        value = guide.build({"observed_at": NOW.isoformat()}, now=NOW)
        self.assertEqual(value["items"][0]["id"], "evidence-incomplete")
        self.assertFalse(value["complete_checks"])

    def test_healthy_is_scoped_not_global_success(self):
        value = guide.build(snapshot(), now=NOW)
        self.assertTrue(value["fresh"])
        self.assertEqual(value["items"][0]["id"], "no-urgent")
        self.assertIn("checks shown", value["items"][0]["title"])

    def test_stale_evidence_suppresses_specific_repair_suggestions(self):
        data = snapshot()
        data["observed_at"] = (NOW - timedelta(minutes=4)).isoformat()
        data["admission"] = {"mode": "paused"}
        self.assertEqual([c["id"] for c in guide.build(data, now=NOW)["items"]], ["evidence-stale"])

    def test_future_timestamp_is_not_fresh(self):
        data = snapshot(); data["observed_at"] = (NOW + timedelta(days=1)).isoformat()
        self.assertFalse(guide.build(data, now=NOW)["fresh"])

    def test_supply_does_not_claim_zero_runnable_or_stopped_publishing(self):
        data = snapshot(); data["operator"]["reserve"] = {"status_counts": {"empty": 32, "emergency": 16}, "available_items": 31}
        card = guide.build(data, now=NOW)["items"][0]
        self.assertEqual(card["id"], "reserve-pressure")
        self.assertIn("does not by itself mean publishing has stopped", card["meaning"])
        self.assertEqual(card["action"]["command"], "./ocpf-post replenish status")

    def test_admission_does_not_suggest_bypassing_policy(self):
        data = snapshot(); data["admission"] = {"mode": "paused", "reasons": ["pressure"]}
        card = guide.build(data, now=NOW)["items"][0]
        self.assertEqual(card["state"], "waiting")
        self.assertIn("Previously accepted work", card["meaning"])
        self.assertNotIn("--apply", card["action"]["command"])

    def test_current_effect_review_scopes_exact_safe_inspection(self):
        data = snapshot(); data["work"]["items"] = [{"state": "manual_review", "do_not_replay": True,
            "blocker": "partial_effect", "evidence": {"schedule_id": "sch_example", "campaign": "EXAMPLE-1", "provider": "threads"}}]
        card = guide.build(data, now=NOW)["items"][0]
        self.assertEqual(card["action"]["argv"], ["./ocpf-post", "schedule", "inspect", "sch_example", "--json"])
        self.assertIn("Don’t resend", card["title"])
        self.assertFalse(card["automatic_action"])

    def test_shell_text_in_evidence_cannot_become_a_command(self):
        data = snapshot(); data["work"]["items"] = [{"state": "manual_review", "do_not_replay": True,
            "safe_next_action": "rm -rf ~", "evidence": {"schedule_id": "$(touch /tmp/unsafe)", "campaign": "A;echo secret", "provider": "x"}}]
        action = guide.build(data, now=NOW)["items"][0]["action"]
        self.assertEqual(action["command"], "./ocpf-post work status --json")
        self.assertNotIn("unsafe", action["command"])

    def test_option_like_id_cannot_become_an_option(self):
        self.assertIsNone(guide.selected_inspection({"schedule_id": "--apply"}))
        action = guide.selected_inspection({"campaign": "C-1", "provider": "linkedin"})
        self.assertEqual(shlex.split(action["command"]), action["argv"])
        self.assertIn("--campaign=C-1", action["argv"])

    def test_historical_ambiguity_is_not_a_current_blocker(self):
        data = snapshot(); data["execution"]["events"] = [{"id": "old", "kind": "ambiguous_effect", "schedule_id": "sch_old"}]
        value = guide.build(data, now=NOW)
        self.assertEqual(value["items"][0]["id"], "no-urgent")
        self.assertIn("old", value["contexts"])
        self.assertIn("historical", value["contexts"]["old"]["boundary"])

    def test_future_schedule_waits_rather_than_fails(self):
        data = snapshot(); data["activity"]["active_schedules"] = [{"status": "scheduled", "schedule_id": "sch_future",
            "run_at": (NOW + timedelta(hours=1)).isoformat(), "provider": "x"}]
        card = guide.build(data, now=NOW)["items"][0]
        self.assertEqual(card["id"], "next-scheduled")
        self.assertEqual(card["state"], "waiting")
        self.assertIn("no need", card["meaning"])

    def test_unknown_checks_do_not_approve_future_work(self):
        data = snapshot(); data["state_integrity"] = {"status": "unavailable"}
        data["activity"]["active_schedules"] = [{"status": "scheduled", "run_at": (NOW + timedelta(hours=1)).isoformat()}]
        self.assertEqual(guide.build(data, now=NOW)["items"][0]["id"], "evidence-incomplete")

    def test_integrity_precedes_supply(self):
        data = snapshot(); data["state_integrity"].update(status="attention", invalid_count=1)
        data["operator"]["reserve"]["status_counts"] = {"empty": 1}
        value = guide.build(data, now=NOW)
        self.assertEqual(value["items"][0]["id"], "ledger-attention")
        self.assertEqual(value["items"][0]["action"]["path"], "state verify")

    def test_optional_not_enabled_is_not_failure(self):
        data = snapshot(); data["capabilities"]["optional_pending"] = ["alerts", "pages"]
        self.assertEqual(guide.build(data, now=NOW)["items"][0]["id"], "no-urgent")
        data["capabilities"]["activated_optional_blockers"] = ["alerts"]
        self.assertEqual(guide.build(data, now=NOW)["items"][0]["id"], "capability-blocked")

    def test_linkedin_permission_is_not_a_failed_send(self):
        data = snapshot(); data["capabilities"]["capabilities"] = [{"id": "provider-publishing", "detail": {
            "linkedin_recorded_read_authority": {"posts": {"status": "missing", "required_scope": "r_member_social"}}}}]
        card = guide.build(data, now=NOW)["items"][0]
        self.assertEqual(card["state"], "external")
        self.assertIn("not permission to publish again", card["meaning"])

    def test_measured_cost_has_no_claim_of_publishing_delay(self):
        data = snapshot(); data["projection_timing_ms"] = {"portfolio": 6641.2, "total": 8552.8}
        card = guide.build(data, now=NOW)["items"][0]
        self.assertEqual(card["evidence"]["component"], "portfolio")
        self.assertIn("not proof", card["meaning"])
        self.assertEqual(card["state"], "engineering")

    def test_deadline_does_not_renew_or_call_future_work_failed(self):
        data = snapshot(); data["work"]["items"] = [{"state": "deadline", "deadline": (NOW + timedelta(hours=2)).isoformat()}]
        card = guide.build(data, now=NOW)["items"][0]
        self.assertTrue(card["id"].startswith("deadline"))
        self.assertIn("adding time is not", card["meaning"])
        data["work"]["items"][0]["deadline"] = (NOW - timedelta(hours=2)).isoformat()
        self.assertEqual(guide.build(data, now=NOW)["items"][0]["id"], "no-urgent")

    def test_projection_never_mutates_input_or_runs_tools(self):
        data = snapshot(); before = deepcopy(data)
        with patch("subprocess.run", side_effect=AssertionError("no commands")), patch("urllib.request.urlopen", side_effect=AssertionError("no network")):
            value = guide.build(data, now=NOW)
        self.assertEqual(data, before)
        self.assertEqual(value["source_revision"], data["revision"])
        json.dumps(value, allow_nan=False)

    def test_large_backlog_is_bounded(self):
        data = snapshot(); data["work"]["items"] = [{"state": "manual_review", "do_not_replay": True} for _ in range(1000)]
        value = guide.build(data, now=NOW)
        self.assertEqual(len(value["items"]), 12)
        self.assertGreater(value["additional_count"], 0)

    def test_mutations_cannot_be_recipes(self):
        for command in ("publish", "portfolio refill", "runtime upgrade", "x auth", "not-a-command"):
            with self.subTest(command=command), self.assertRaises(ValueError):
                guide.inspection(command)
        for option in ("--apply", "--live", "--allow-duplicate", "bad\ncommand"):
            with self.subTest(option=option), self.assertRaises(ValueError):
                guide.inspection("health", option)

    def test_library_uses_all_native_commands_and_help_only_copy(self):
        from ocpf_post.cli_catalog import COMMANDS
        guide.command_library.cache_clear()
        library = guide.command_library()
        self.assertEqual({c["path"] for c in library["commands"]}, {c.path for c in COMMANDS})
        for row in library["commands"]:
            self.assertTrue(row["help_command"].startswith("./ocpf-post help "))
            self.assertNotIn("--apply", row["help_command"])
        self.assertIs(library, guide.command_library())
        self.assertEqual({topic["id"] for topic in library["topics"]}, {
            "overview", "today", "supply", "admission", "release-pace", "schedule",
            "receipts", "verify", "learning", "replies", "runtime",
        })
        self.assertEqual(len(library["topics"]), 11)

    def test_page_composition_preserves_cockpit_and_one_stream(self):
        raw = b'<html><head></head><body><script>const model = {};const stream = new EventSource("/api/stream");</script></body></html>'
        value = guide.decorate_page(raw)
        self.assertIn(b'snapshot: () => model.snapshot', value)
        self.assertEqual(value.count(b'new EventSource'), 1)
        self.assertEqual(value.count(b'/guide.js'), 1)
        with self.assertRaises(ValueError):
            guide.decorate_page(b'no template boundaries')

    def test_real_execution_page_exposes_bridge_contract(self):
        html = (Path(__file__).resolve().parents[1] / 'src/ocpf_post/ui/execution.html').read_text()
        for required in ("const model=", "const stream=new EventSource", "function openSystem()", "function closeSystem()", 'id="systemDrawer"', 'id="updatedBadge"', 'id="inspectTitle"'):
            self.assertIn(required, html)
        value = guide.decorate_page(html.encode())
        self.assertEqual(value.count(b'new EventSource'), 1)


if __name__ == '__main__':
    unittest.main()
