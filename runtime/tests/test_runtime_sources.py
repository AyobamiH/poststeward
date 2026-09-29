from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from datetime import timedelta
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from ocpf_post.campaigns import builtin_manifest, builtin_text, campaign_ids, runtime_campaign_root
from ocpf_post.onboarding import OnboardingError, import_project
from ocpf_post.portfolio import apply_refill, delivery_candidates
from ocpf_post.portfolio_source_loader import merged_source_profiles
from ocpf_post.replenisher import ReplenisherError, refresh_sources, source_state_file
from ocpf_post.replenisher_reconcile import reconcile_runtime_sources
from ocpf_post.runtime_sources import (
    SourceError, disable_source, enable_source, import_source, list_sources,
    preview_source, source_file, source_lock,
)
from ocpf_post.scheduler import ScheduleError, create_schedule, run_due
from ocpf_post.state import iter_receipts
from test_onboarding import NOW, project_input
from test_scheduler import FakeProvider


def policy():
    return {"schema_version": 1, "project": "runtime-test", "repository": "owner/source",
            "label": "Runtime test", "campaign_prefix": "RTEST", "providers": ["x"],
            "destinations": {"x": "x-owner"}, "required_phrases_any": ["approved source truth"],
            "event_enabled": True, "allow_private_events": False,
            "claim_boundary": "Source progress only.", "event_problem": "Keep evidence with work.",
            "inventory": [{"title": "Receipts", "lane": "commercial", "priority": 90,
                           "hook": "Keep the receipt.", "body": "A publication needs evidence."}]}


class RuntimeSourceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.env = patch.dict(os.environ, {
            "OCPF_POST_CONFIG_DIR": str(self.root / "config"),
            "OCPF_POST_STATE_DIR": str(self.root / "state"),
            "POSTSTEWARD_RUNTIME_CONFIG_DIR": str(self.root / "config"),
            "POSTSTEWARD_RUNTIME_STATE_DIR": str(self.root / "state"),
        })
        self.env.start()
        self.addCleanup(self.tmp.cleanup)
        self.addCleanup(self.env.stop)
        self.apply_import(import_project, project_input())
        self.metadata = {"id": 123, "full_name": "owner/source", "private": False}
        self.commits = [{"sha": "a" * 40, "commit": {"message": "feat: initial capability"}}]
        self.readme_sha = "b" * 40
        for target, effect in (
            ("_github_token", lambda: None),
            ("_github_json", lambda *a, **k: deepcopy(self.metadata)),
            ("_repo_readme", lambda *a, **k: ("approved source truth", self.readme_sha)),
            ("_repo_commits", lambda *a, **k: deepcopy(self.commits)),
        ):
            patcher = patch("ocpf_post.replenisher." + target, side_effect=effect)
            patcher.start()
            self.addCleanup(patcher.stop)

    def input(self, data):
        path = self.root / "input.json"
        path.write_text(json.dumps(data), encoding="utf-8")
        return path

    def apply_import(self, function, data):
        path = self.input(data)
        preview = function(path)
        return function(path, apply=True, expected_sha256=preview["input_sha256"])

    def register(self, value=None):
        return self.apply_import(import_source, value or policy())

    def enable(self):
        preview = preview_source("runtime-test")
        return enable_source("runtime-test", expected_sha256=preview["review_sha256"])

    def refresh(self, **kw):
        return refresh_sources(project="runtime-test", now=NOW, **kw)

    def test_registration_inactive_offline_add_only_and_idempotent(self):
        with patch("ocpf_post.replenisher._github_json", side_effect=AssertionError("offline")):
            path = self.input(policy())
            import_source(path)
            self.assertFalse(source_file().exists())
            result = self.register()
            self.assertFalse(result["enabled"])
            before = source_file().stat().st_mtime_ns
            self.assertEqual(self.register()["result"], "already_present")
            self.assertEqual(source_file().stat().st_mtime_ns, before)
        self.assertNotIn("runtime-test", merged_source_profiles()["projects"])
        self.assertFalse(source_state_file().exists())
        changed = policy()
        changed["repository"] = "owner/replacement"
        with self.assertRaises(SourceError):
            self.register(changed)

    def test_registration_hash_unknown_fields_bad_repositories_and_binding(self):
        for changed in (
            {**policy(), "repository": "owner/source?token=secret"},
            {**policy(), "repository": "https://evil.test/source"},
            {**policy(), "repository": "owner/../../other"},
            {**policy(), "token": "secret"},
            {**policy(), "required_phrases_any": [" "]},
            {**policy(), "campaign_prefix": "OCPF"},
            {**policy(), "event_enabled": "true"},
            {**policy(), "destinations": {"x": "wrong-alias"}},
        ):
            with self.subTest(changed=changed), self.assertRaises(ValueError):
                self.register(changed)
        path = self.input(policy())
        with self.assertRaises(OnboardingError):
            import_source(path, apply=True)
        preview = import_source(path)
        path.write_text(path.read_text() + " ")
        with self.assertRaises(OnboardingError):
            import_source(path, apply=True, expected_sha256=preview["input_sha256"])

    def test_packaged_and_shared_repository_takeover_rejected(self):
        value = policy()
        value.update(project="oneclickpostfactory", campaign_prefix="OCPF", destinations={"x": "x-founder"})
        with self.assertRaises(SourceError):
            self.register(value)
        value = {**policy(), "repository": "AyobamiH/donestate"}
        with self.assertRaises(SourceError):
            self.register(value)

    def test_preview_reads_pinned_readme_and_writes_no_state(self):
        self.register()
        before = source_file().read_bytes()
        with patch("ocpf_post.replenisher._repo_readme", return_value=("approved source truth", self.readme_sha)) as readme:
            preview = preview_source("runtime-test")
        readme.assert_called_once_with("owner/source", token=None, ref="a" * 40)
        self.assertEqual(len(preview["review"]["static_candidates"]), 3)
        self.assertEqual(preview["review"]["accounts"]["x"]["account_id"], "12345")
        self.assertTrue(preview["review"]["historical_event_example"]["sample_only"])
        self.assertEqual(source_file().read_bytes(), before)
        self.assertFalse(source_state_file().exists())
        self.assertFalse((self.root / "state").exists())

    def test_enable_rejects_changed_head_readme_identity_or_missing_hash(self):
        self.register()
        digest = preview_source("runtime-test")["review_sha256"]
        for field, value in (("readme_sha", "c" * 40), ("commits", [{"sha": "c" * 40, "commit": {"message": "feat: changed"}}]),
                             ("metadata", {"id": 456, "full_name": "owner/source", "private": False})):
            old = getattr(self, field)
            setattr(self, field, value)
            with self.assertRaises(SourceError):
                enable_source("runtime-test", expected_sha256=digest)
            setattr(self, field, old)
        with self.assertRaises(SourceError):
            enable_source("runtime-test", expected_sha256="")
        self.assertFalse(list_sources()["runtime_sources"]["runtime-test"]["enabled"])

    def test_enable_no_history_replay_and_private_events_explicit(self):
        self.metadata["private"] = True
        self.register()
        with self.assertRaisesRegex(SourceError, "Private commit"):
            preview_source("runtime-test")
        # An independently registered policy explicitly permits private titles.
        source_file().unlink()
        self.register({**policy(), "allow_private_events": True})
        self.enable()
        result = self.refresh(apply=True)
        self.assertEqual(len(result["static_campaigns"]), 3)
        self.assertEqual(result["event_campaigns"], [])
        self.assertEqual(source_file().stat().st_mode & 0o777, 0o600)

    def test_source_to_allocator_schedule_receipt_and_duplicate_block(self):
        self.register()
        preview = preview_source("runtime-test")
        self.enable()
        self.assertFalse((self.root / "state").exists())
        result = self.refresh(apply=True)
        ids = {c["campaign"] for c in result["static_campaigns"]}
        candidates = {c["campaign"] for c in delivery_candidates(now=NOW)}
        # With no CTA, insight/question render exactly the same copy. Only one
        # may compete for this destination even though both packages exist.
        self.assertEqual(len(ids & candidates), 2)
        self.assertIn(preview["review"]["static_candidates"][0]["campaign"], candidates)
        example = preview["review"]["static_candidates"][0]
        cid = example["campaign"]
        self.assertEqual(builtin_text(cid, "x"), example["texts"]["x"])
        self.assertEqual(builtin_manifest(cid)["payload_sha256"], example["payload_sha256"])
        provider = FakeProvider(account_id="12345")
        kwargs = dict(campaign=cid, provider="x", at=(NOW + timedelta(minutes=10)).isoformat(), now=NOW)
        with self.assertRaisesRegex(ScheduleError, "mismatch"):
            create_schedule(**kwargs, provider_factory=lambda _: FakeProvider(account_id="wrong"))
        create_schedule(**kwargs, provider_factory=lambda _: provider)
        self.assertEqual(run_due(now=NOW + timedelta(minutes=11), provider_factory=lambda _: provider)[0]["status"], "published_verified")
        self.assertEqual(len(provider.published), 1)
        self.assertEqual(list(iter_receipts())[-1]["text_sha256"], example["payload_sha256"]["x"])
        with self.assertRaisesRegex(ScheduleError, "terminal"):
            create_schedule(**kwargs, provider_factory=lambda _: provider)
        self.assertEqual(self.refresh(apply=True)["static_campaigns"], [])

    def test_new_event_only_once_and_empty_or_sensitive_titles_ignored(self):
        self.register()
        self.enable()
        self.commits = [
            {"sha": "d" * 40, "commit": {"message": ""}},
            {"sha": "e" * 40, "commit": {"message": "fix: rotate secret"}},
            {"sha": "f" * 40, "commit": {"message": "feat: add receipt lookup"}},
            *self.commits,
        ]
        first = self.refresh(apply=True)
        self.assertEqual(len(first["event_campaigns"]), 1)
        self.assertEqual(first["event_campaigns"][0]["campaign"], "RTEST-EVENT-FFFFFFFF")
        self.assertEqual(self.refresh(apply=True)["event_campaigns"], [])

    def test_allocator_reservation_runs_through_real_scheduler_logic(self):
        self.register()
        self.enable()
        ids = [c["campaign"] for c in self.refresh(apply=True)["static_campaigns"]]
        allocation_policy = {"schema_version": 1, "timezone": "UTC", "horizon_minutes": 75,
                             "minimum_lead_minutes": 1, "planner_interval_minutes": 15,
                             "providers": {"x": {"daily_target": 1, "window_start": "10:10", "window_end": "11:10",
                                                 "development_max": 1, "commercial_min": 1}}, "reply_targets": {}}
        provider = FakeProvider(account_id="12345")
        with patch("ocpf_post.portfolio._campaign_ids", return_value=ids):
            result = apply_refill(now=NOW, policy=allocation_policy,
                                  schedule_creator=lambda **kw: create_schedule(**kw, provider_factory=lambda _: provider))
        self.assertEqual(result["errors"], [])
        self.assertEqual(len(result["scheduled"]), 1)
        self.assertEqual(provider.published, [])
        self.assertEqual(run_due(now=NOW + timedelta(minutes=11), provider_factory=lambda _: provider)[0]["status"], "published_verified")
        self.assertEqual(run_due(now=NOW + timedelta(minutes=12), provider_factory=lambda _: provider), [])

    def test_cursor_write_failure_retries_without_duplicate_campaigns(self):
        self.register()
        self.enable()
        from ocpf_post.state import write_private_json
        def write(path, value):
            if path == source_state_file():
                raise OSError("cursor interrupted")
            write_private_json(path, value)
        with patch("ocpf_post.replenisher.write_private_json", side_effect=write):
            with self.assertRaises(OSError):
                self.refresh(apply=True)
        before = {c for c in campaign_ids() if c.startswith("RTEST-")}
        self.assertEqual(len(before), 3)
        self.assertEqual(self.refresh(apply=True)["static_campaigns"], [])
        self.assertEqual({c for c in campaign_ids() if c.startswith("RTEST-")}, before)

    def test_repeated_enable_preserves_activation_and_cursor(self):
        self.register()
        initial = self.enable()["activation"]
        self.refresh(apply=True)
        self.commits = [{"sha": "f" * 40, "commit": {"message": "feat: new capability"}}, *self.commits]
        again = self.enable()
        self.assertEqual(again["result"], "already_enabled")
        self.assertEqual(again["activation"], initial)
        self.assertEqual(len(self.refresh(apply=True)["event_campaigns"]), 1)

    def test_replaced_repo_guard_failure_and_history_gap_do_not_advance(self):
        self.register()
        self.enable()
        self.refresh(apply=True)
        before = json.loads(source_state_file().read_text())["repositories"]["owner/source"]
        self.metadata["id"] = 999
        self.assertEqual(self.refresh(apply=True)["projects"][0]["status"], "source_unavailable")
        self.metadata["id"] = 123
        with patch("ocpf_post.replenisher._repo_readme", return_value=("unrelated content", "c" * 40)):
            self.assertEqual(self.refresh(apply=True)["projects"][0]["status"], "source_guard_failed")
            with self.assertRaises(SourceError):
                preview_source("runtime-test")
        self.assertEqual(len(reconcile_runtime_sources()), 3)
        after_guard = json.loads(source_state_file().read_text())["repositories"]["owner/source"]
        self.assertEqual(after_guard["head_sha"], before["head_sha"])
        self.assertFalse(after_guard["source_ok"])
        self.commits = [{"sha": "f" * 40, "commit": {"message": "feat: rewritten history"}}]
        self.assertIn("history gap", self.refresh(apply=True)["projects"][0]["detail"])
        self.assertEqual(json.loads(source_state_file().read_text())["repositories"]["owner/source"], after_guard)

    def test_disable_serialises_and_resume_baselines_new_history(self):
        self.register()
        self.enable()
        first = self.refresh(apply=True)
        cid = first["static_campaigns"][0]["campaign"]
        text = builtin_text(cid, "x")
        with source_lock(), ThreadPoolExecutor(max_workers=1) as executor:
            with self.assertRaises(OnboardingError):
                executor.submit(disable_source, "runtime-test").result()
            with self.assertRaises(OnboardingError):
                self.refresh(apply=True)
        disable_source("runtime-test")
        self.assertEqual(builtin_text(cid, "x"), text)
        self.assertNotIn("runtime-test", merged_source_profiles()["projects"])
        with self.assertRaises(ReplenisherError):
            self.refresh(apply=True)
        self.commits = [{"sha": "f" * 40, "commit": {"message": "feat: during pause"}}, *self.commits]
        self.enable()
        self.assertEqual(self.refresh(apply=True)["event_campaigns"], [])

    def test_atomic_generation_failure_and_retry(self):
        self.register()
        self.enable()
        with patch("ocpf_post.replenisher.os.rename", side_effect=OSError("interrupted")):
            with self.assertRaises(OSError):
                self.refresh(apply=True)
        self.assertFalse(any(c.startswith("RTEST-") for c in campaign_ids()))
        self.assertFalse(source_state_file().exists())
        self.assertEqual(len(self.refresh(apply=True)["static_campaigns"]), 3)

    def test_tampered_generated_payload_blocks_scheduled_execution(self):
        self.register()
        self.enable()
        cid = self.refresh(apply=True)["static_campaigns"][0]["campaign"]
        provider = FakeProvider(account_id="12345")
        create_schedule(campaign=cid, provider="x", at=(NOW + timedelta(minutes=10)).isoformat(), now=NOW, provider_factory=lambda _: provider)
        expected = builtin_manifest(cid)["payload_sha256"]
        (runtime_campaign_root() / cid / "x.txt").write_text("Changed text")
        self.assertIsNone(builtin_text(cid, "x"))
        self.assertEqual(builtin_manifest(cid)["payload_sha256"], expected)
        self.assertEqual(run_due(now=NOW + timedelta(minutes=11), provider_factory=lambda _: provider)[0]["status"], "drift_blocked")
        self.assertEqual(provider.published, [])

    def test_corrupt_authority_fails_closed(self):
        self.register()
        source_file().write_text('{"schema_version":1,"projects":[],"projects":{}}')
        with self.assertRaises(SourceError):
            merged_source_profiles()

    def test_cli_local_import_and_list_return_json(self):
        path = self.input(policy())
        r = subprocess.run(["sh", "./poststeward", "replenish", "source", "import", "--file", str(path)], capture_output=True, text=True, check=True)
        self.assertEqual(json.loads(r.stdout)["result"], "preview")
        self.register()
        r = subprocess.run(["sh", "./poststeward", "replenish", "source", "list"], capture_output=True, text=True, check=True)
        self.assertFalse(json.loads(r.stdout)["runtime_sources"]["runtime-test"]["enabled"])


if __name__ == "__main__":
    unittest.main()
