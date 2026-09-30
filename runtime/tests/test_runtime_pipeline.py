import copy
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from ocpf_post import admission, local_store, source_observations as obs, source_pipeline as pipeline
from ocpf_post import replenisher as r, scoped_admission, operating_cycles, operating_calendar
from ocpf_post.campaigns import builtin_manifest, campaign_ids
from test_portfolio_queue import fixture, candidate
from ocpf_post import portfolio_queue as queue

NOW = datetime(2026, 9, 12, 12, tzinfo=timezone.utc)


class PipelineTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.env = patch.dict(os.environ, OCPF_POST_STATE_DIR=self.temp.name, OCPF_POST_CONFIG_DIR=self.temp.name)
        self.env.start()
        self.profile = {"project": "sample", "repository": "owner/sample", "label": "Sample",
                        "campaign_prefix": "SAMPLE", "providers": ["x", "threads"],
                        "destinations": {"x": "x-founder", "threads": "threads-founder"},
                        "event_enabled": True, "required_phrases_any": ["source proof"], "inventory": []}
        self.profiles = patch("ocpf_post.portfolio_source_loader.merged_source_profiles", return_value={"projects": {"sample": self.profile}})
        self.profiles.start()
        self.candidates = patch("ocpf_post.portfolio.delivery_candidates", return_value=[])
        self.candidates.start()
        self.identity = patch("ocpf_post.registry.resolve_account", side_effect=lambda project, alias, expected_provider: {"account_id": expected_provider + "-id"})
        self.identity.start()
        self.addCleanup(self.temp.cleanup)
        self.addCleanup(self.env.stop)
        self.addCleanup(self.profiles.stop)
        self.addCleanup(self.candidates.stop)
        self.addCleanup(self.identity.stop)

    def commit(self, letter, title="feat: Add a reviewed result"):
        return {"sha": letter * 40, "commit": {"message": title, "committer": {"date": "2026-09-12T10:00:00Z"}}}

    def observe(self, commits, *, now=NOW, identity=9, private=False):
        def github(url, **kwargs):
            if "/commits?" in url:
                return commits
            return {"full_name": "owner/sample", "id": identity, "private": private}
        with patch.object(r, "_github_json", side_effect=github), patch.object(r, "_repo_commits", return_value=commits[:1]), \
             patch.object(r, "_repo_readme", return_value=("source proof", "d" * 40)), patch.object(r, "_github_token", return_value=None):
            return obs.observe(apply=True, now=now)

    def seed(self):
        self.observe([self.commit("a")])
        self.observe([self.commit("b"), self.commit("a")])

    def test_full_queue_does_not_stop_observation_or_consume_events(self):
        local_store.write(admission.state_file(), {"schema_version": 1, "mode": "paused", "observed_at": NOW.isoformat()})
        self.seed()
        row = obs.load()["projects"]["sample"]
        self.assertEqual(row["head_sha"], "b" * 40)
        self.assertEqual(row["pending"][0]["sha"], "b" * 40)
        self.assertNotIn("last_processed_sha", row)
        self.assertFalse(any(cid.startswith("SAMPLE") for cid in campaign_ids()))
        self.assertFalse(r.source_state_file().exists())

    def test_partial_destinations_survive_restart_without_duplicate_or_lost_copy(self):
        self.seed()
        original = scoped_admission.Budget.admit
        def gate(budget, project, provider, account, **kwargs):
            return {"admitted": False, "reasons": ["provider_high_water"]} if provider == "x" else original(budget, project, provider, account, **kwargs)
        with patch.object(scoped_admission.Budget, "admit", gate):
            first = pipeline.refresh(apply=True, now=NOW)
        self.assertEqual(first["event_campaigns"][0]["providers"], ["threads"])
        self.assertEqual(obs.load()["projects"]["sample"]["pending"][0]["completed_providers"], ["threads"])
        with patch.object(r, "_github_json", side_effect=AssertionError("admission must be offline")):
            second = pipeline.refresh(apply=True, now=NOW)
            third = pipeline.refresh(apply=True, now=NOW)
        self.assertEqual(second["event_campaigns"][0]["providers"], ["x"])
        self.assertEqual(third["event_campaigns"], [])
        self.assertEqual(obs.load()["projects"]["sample"]["pending"], [])
        for created in first["event_campaigns"] + second["event_campaigns"]:
            manifest = builtin_manifest(created["campaign"])
            self.assertEqual(manifest["source"]["source_sha"], "b" * 40)
            self.assertEqual(manifest["source"]["source_event_at"], "2026-09-12T10:00:00Z")

    def test_crash_after_packages_before_cursor_is_idempotent(self):
        self.seed()
        original = local_store.write
        def crash(path, value):
            if path == obs.path():
                raise OSError("simulated interruption")
            return original(path, value)
        with patch.object(local_store, "write", side_effect=crash):
            with self.assertRaises(OSError):
                pipeline.refresh(apply=True, now=NOW)
        self.assertEqual(len(obs.load()["projects"]["sample"]["pending"]), 1)
        self.assertEqual(pipeline.refresh(apply=True, now=NOW)["event_campaigns"], [])
        self.assertEqual(obs.load()["projects"]["sample"]["pending"], [])
        self.assertEqual(len([c for c in campaign_ids() if c.startswith("SAMPLE")]), 2)

    def test_history_gap_stays_visible_across_later_poll(self):
        self.observe([self.commit("a")])
        self.observe([self.commit("c")])
        row = obs.load()["projects"]["sample"]
        self.assertEqual(row["status"], "history_gap")
        self.assertEqual(row["buffered_head_sha"], "a" * 40)
        self.observe([self.commit("c")], now=NOW + timedelta(minutes=15))
        self.assertEqual(obs.load()["projects"]["sample"]["status"], "history_gap")
        self.assertEqual(pipeline.refresh(apply=True, now=NOW)["event_campaigns"], [])

    def test_gap_checkpoint_requires_review_and_archives_unconsumed_evidence(self):
        self.seed()
        self.observe([self.commit("c")])
        review = obs.acknowledge_gap(project="sample", now=NOW)
        with self.assertRaises(ValueError):
            obs.acknowledge_gap(project="sample", apply=True, expected_sha256="wrong", now=NOW)
        result = obs.acknowledge_gap(project="sample", apply=True, expected_sha256=review["review_sha256"], now=NOW)
        self.assertEqual(result["result"], "acknowledged")
        row = obs.load()["projects"]["sample"]
        self.assertEqual(row["buffered_head_sha"], "c" * 40)
        self.assertEqual(row["pending"][0]["sha"], "b" * 40)
        self.assertTrue(list((Path(self.temp.name) / "source-gap-reviews").glob("*.json")))

    def test_frozen_generated_payload_rejects_later_local_text_edit(self):
        from ocpf_post.campaigns import builtin_text, runtime_campaign_root
        self.seed()
        result = pipeline.refresh(apply=True, now=NOW)
        cid = result["event_campaigns"][0]["campaign"]
        before = builtin_manifest(cid)["payload_sha256"]
        (runtime_campaign_root() / cid / "x.txt").write_text("Different copy")
        self.assertIsNone(builtin_text(cid, "x"))
        self.assertEqual(builtin_manifest(cid)["payload_sha256"], before)

    def test_observed_readme_change_blocks_reserved_copy_before_next_refill(self):
        from ocpf_post.scheduler import create_schedule, run_due, schedule_records
        from test_scheduler import FakeProvider
        self.profile['inventory'] = [{'title': 'A useful fact', 'hook': 'Source evidence matters.', 'body': 'Keep the original observation.', 'lane': 'evergreen'}]
        self.seed()
        cid = pipeline.refresh(apply=True, now=NOW)['static_campaigns'][0]['campaign']
        fake = FakeProvider(account_id='x-id')
        with patch('ocpf_post.campaigns.resolve_account', return_value={'provider': 'x', 'account_id': 'x-id', 'label': 'test'}):
            saved = create_schedule(campaign=cid, provider='x', at=(NOW + timedelta(minutes=2)).isoformat(), now=NOW, provider_factory=lambda _: fake)
        state = obs.load()
        state['projects']['sample']['readme_sha'] = 'e' * 40
        local_store.write(obs.path(), state)
        with patch('ocpf_post.campaigns.resolve_account', return_value={'provider': 'x', 'account_id': 'x-id', 'label': 'test'}):
            run_due(now=NOW + timedelta(minutes=3), provider_factory=lambda _: fake)
        row = next(r for r in schedule_records() if r['schedule_id'] == saved['schedule_id'])
        self.assertEqual(row['status'], 'drift_blocked')
        self.assertEqual(fake.published, [])

    def test_publisher_lock_defers_observation_commit_without_consuming_history(self):
        from ocpf_post.scheduler import RunnerLock
        self.seed()
        before = obs.load()
        with RunnerLock():
            result = self.observe([self.commit('c'), self.commit('b')])
        self.assertEqual(result['projects'][0]['status'], 'deferred_publisher_busy')
        self.assertEqual(obs.load(), before)

    def test_overflow_preserves_buffer_and_cursor(self):
        self.seed()
        with patch.object(obs, "MAX_PENDING", 1):
            self.observe([self.commit("c"), self.commit("b")])
        row = obs.load()["projects"]["sample"]
        self.assertEqual(row["status"], "pending_overflow")
        self.assertEqual(row["pending"][0]["sha"], "b" * 40)
        self.assertEqual(row["buffered_head_sha"], "b" * 40)

    def test_identity_or_private_authority_failure_retains_last_observation(self):
        self.seed()
        self.observe([self.commit("c"), self.commit("b")], identity=99)
        row = obs.load()["projects"]["sample"]
        self.assertTrue(row["collection_error"])
        self.assertEqual(row["head_sha"], "b" * 40)
        self.profile["allow_private_events"] = False
        self.observe([self.commit("c"), self.commit("b")], private=True)
        self.assertTrue(obs.load()["projects"]["sample"]["collection_error"])

    def test_profile_change_cannot_reauthorise_buffered_copy(self):
        self.seed()
        self.profile["event_cta"] = "A different approved question?"
        self.observe([self.commit("b"), self.commit("a")])
        self.assertEqual(obs.load()["projects"]["sample"]["status"], "profile_changed_review_required")
        self.assertEqual(pipeline.refresh(apply=True, now=NOW)["event_campaigns"], [])

    def test_expiry_is_not_renewed_after_admission_pause(self):
        self.seed()
        later = NOW + timedelta(hours=31)
        self.observe([self.commit("b"), self.commit("a")], now=later)
        report = pipeline.refresh(apply=True, now=later)
        self.assertEqual(report["event_campaigns"], [])
        self.assertEqual(obs.load()["projects"]["sample"]["expired_delivery_count"], 2)

    def test_maintenance_commits_are_filtered_and_text_is_date_neutral(self):
        self.observe([self.commit("a")])
        self.observe([self.commit("c", "docs: update runtime notes"), self.commit("b"), self.commit("a")])
        self.assertEqual(len(obs.load()["projects"]["sample"]["pending"]), 1)
        for provider in ("x", "threads", "linkedin"):
            text = r._render_event(self.profile, "Add a result", provider)
            self.assertNotIn("today", text)
            self.assertIn("source", text)

    def test_destination_pressure_does_not_block_unrelated_provider_or_account(self):
        rows = [{"campaign": str(i), "project": "another", "provider": "x", "account_id": "busy", "text_sha256": str(i)} for i in range(2)]
        policy = copy.deepcopy(admission.DEFAULT_POLICY)
        policy["account_high_water"], policy["account_recovery_water"] = 2, 1
        with patch("ocpf_post.portfolio.delivery_candidates", return_value=rows), patch.object(admission, "load_policy", return_value=policy):
            budget = scoped_admission.Budget(NOW, True)
            self.assertFalse(budget.admit("sample", "x", "busy")["admitted"])
            self.assertTrue(budget.admit("sample", "x", "other")["admitted"])
            self.assertTrue(budget.admit("sample", "threads", "brand")["admitted"])

    def test_global_ceiling_remains_shared(self):
        policy = copy.deepcopy(admission.DEFAULT_POLICY)
        policy["global_high_water"], policy["global_recovery_water"] = 1, 0
        with patch.object(admission, "load_policy", return_value=policy):
            budget = scoped_admission.Budget(NOW, True)
            self.assertTrue(budget.admit("sample", "x", "x-id")["admitted"])
            self.assertFalse(budget.admit("other", "threads", "brand")["admitted"])

    def test_disabled_destination_retains_pending_evidence(self):
        self.seed()
        with patch("ocpf_post.account_profiles.unavailable", side_effect=lambda provider, account: "inactive" if provider == "x" else None):
            result = pipeline.refresh(apply=True, now=NOW)
        self.assertEqual([r["providers"] for r in result["event_campaigns"]], [["threads"]])
        self.assertEqual(obs.load()["projects"]["sample"]["pending"][0]["completed_providers"], ["threads"])

    def test_collector_continues_after_one_failed_stage(self):
        calls = []
        def stage(name, args, seconds):
            calls.append((name, args, seconds))
            return {"stage": name, "status": "timed_out" if name == "source-observation" else "completed", "exit_code": None if name == "source-observation" else 0}
        with patch.object(operating_cycles, "stage", side_effect=stage), patch("ocpf_post.vault_sync.policies", return_value={"vault-one": {"enabled": True}}):
            result = operating_cycles.collect()
        self.assertEqual([c[0] for c in calls], ["source-observation", "vault:vault-one", "metrics", "performance-feedback", "inbound-replies", "outcome-connectors", "acceptance-views", "operations"])
        self.assertIn("--vault-id", calls[1][1])
        self.assertEqual(result["stages"][0]["status"], "timed_out")
        self.assertTrue(result["completed_at"])

    def test_provider_error_in_successful_cli_response_is_attention(self):
        result = operating_cycles.stage("vault", [sys.executable, "-c", 'print(\'{"vaults":[{"result":"unavailable"}]}\')'], 2)
        self.assertEqual(result["exit_code"], 0)
        self.assertEqual(result["status"], "attention")

    def test_timeout_is_bounded_and_does_not_echo_child_output(self):
        result = operating_cycles.stage("test", [sys.executable, "-c", "import time; print('private child output', flush=True); time.sleep(10)"], 0.05)
        self.assertEqual(result["status"], "timed_out")
        self.assertLess(result["duration_seconds"], 4)
        self.assertNotIn("private child output", json.dumps(result))

    def test_calendar_retains_reservations_and_separates_forecasts(self):
        inputs, policy = fixture([candidate("later", "alpha", account_id="x-id")])
        scheduled = {"schedule_id": "sch_saved", "campaign": "existing", "provider": "linkedin", "account_id": "li-id", "status": "scheduled", "run_at": "2026-09-10T09:24:00Z", "updated_at": "2026-09-10T05:00:00Z"}
        with patch("ocpf_post.portfolio.load_policy", return_value={**policy, "selection": "fair"}), patch.object(queue, "capture_inputs", return_value=inputs), patch("ocpf_post.scheduler.schedule_records", return_value=[scheduled]):
            result = operating_calendar.report(now=datetime.fromisoformat(inputs["now"].replace("Z", "+00:00")), horizon_minutes=600)
        self.assertEqual(result["accounts"]["linkedin:li-id"]["next_reservation"]["campaign"], "existing")
        self.assertEqual(result["accounts"]["x:x-id"]["next_forecast"]["kind"], "forecast")
        self.assertIsNone(result["accounts"]["x:x-id"]["next_reservation"])

    def test_incidents_are_deduplicated_without_resetting_uncertain_schedule(self):
        row = {"schedule_id": "sch_uncertain", "campaign": "original", "provider": "threads", "account_id": "brand", "status": "ambiguous_effect"}
        with patch("ocpf_post.scheduler.schedule_records", return_value=[row]):
            first = operating_cycles.summary()
            second = operating_cycles.summary()
        self.assertEqual(len(first["new_incident_ids"]), 1)
        self.assertEqual(second["new_incident_ids"], [])
        self.assertEqual(row["status"], "ambiguous_effect")


class ServiceTests(unittest.TestCase):
    def test_reviewed_briefs_get_bounded_turns_while_other_projects_continue(self):
        rows = [candidate(f"brief{i}", "busy", service_kind="reviewed_brief", queue_first_eligible_at="2026-09-07T00:00:00Z", expires_at="2026-09-17T00:00:00Z") for i in range(3)]
        rows += [candidate(f"normal{i}", "p" + str(i), priority=99) for i in range(40)]
        inputs, policy = fixture(rows, target=20)
        inputs["histories"]["x"] = [{"campaign": "served", "project": "busy", "family": "busy", "topic_key": "different", "at": "2026-09-10T04:00:00Z"}]
        previous = queue.fair_plan(inputs, policy, service=False)
        current = queue.fair_plan(inputs, policy)
        self.assertFalse(any(c["campaign"].startswith("brief") for c in previous["plan"]))
        self.assertEqual(sum(c["campaign"].startswith("brief") for c in current["plan"]), 3)
        self.assertEqual(len(current["plan"]), 20)
        self.assertGreaterEqual(len({c["project"] for c in current["plan"] if c["project"] != "busy"}), 10)
        self.assertLessEqual(sum(c["lane"] == "development" for c in current["plan"]), policy["providers"]["x"]["development_max"])

    def test_empty_classes_lend_capacity(self):
        inputs, policy = fixture([candidate(str(i), str(i)) for i in range(20)], target=20)
        self.assertEqual(len(queue.fair_plan(inputs, policy)["plan"]), 20)


if __name__ == "__main__":
    unittest.main()
