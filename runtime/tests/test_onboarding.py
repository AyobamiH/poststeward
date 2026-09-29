from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile
import time
import unittest
from unittest.mock import patch

from ocpf_post.campaigns import builtin_manifest, builtin_text, destination_binding
from ocpf_post.onboarding import OnboardingError, import_campaign, import_project, registry_path
from ocpf_post.portfolio import delivery_candidates
from ocpf_post.registry import RegistryError, load_registry, resolve_account
from ocpf_post.scheduler import ScheduleError, create_schedule, run_due
from ocpf_post.state import iter_receipts
from test_scheduler import FakeProvider

NOW = datetime(2026, 9, 10, 10, tzinfo=timezone.utc)


def project_input():
    return {"schema_version": 1, "project": "runtime-test", "label": "Runtime test",
            "campaign_prefixes": ["RTEST-"],
            "accounts": {"x-owner": {"provider": "x", "account_id": "12345"}},
            "default_accounts": {"x": "x-owner"}}


def campaign_input():
    return {"schema_version": 1, "campaign": "RTEST-001", "project": "runtime-test",
            "title": "Owner-approved campaign", "status": "COPY-READY",
            "destinations": {"x": "x-owner"}, "texts": {"x": "Exactly this approved text."},
            "source": {"type": "owner_approved", "source_id": "approval-001"}}


class OnboardingTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.env = patch.dict(os.environ, {"OCPF_POST_CONFIG_DIR": str(self.root / "config"),
                                          "OCPF_POST_STATE_DIR": str(self.root / "state")})
        self.env.start()

    def tearDown(self):
        self.env.stop()
        self.tmp.cleanup()

    def input(self, value, name="input.json"):
        path = self.root / name
        path.write_text(json.dumps(value), encoding="utf-8")
        return path

    def apply(self, function, value, **kwargs):
        path = self.input(value)
        preview = function(path, **kwargs)
        return function(path, apply=True, expected_sha256=preview["input_sha256"], **kwargs)

    def register(self):
        return self.apply(import_project, project_input())

    def test_preview_has_no_filesystem_or_network_effect(self):
        path = self.input(project_input())
        with patch("urllib.request.urlopen", side_effect=AssertionError("Network forbidden")):
            preview = import_project(path)
        self.assertEqual(preview["result"], "preview")
        self.assertFalse(preview["identity_verified"])
        self.assertEqual(list(self.root.iterdir()), [path])

    def test_apply_requires_reviewed_hash_and_rejects_changed_input(self):
        path = self.input(project_input())
        preview = import_project(path)
        with self.assertRaisesRegex(OnboardingError, "requires"):
            import_project(path, apply=True)
        changed = project_input()
        changed["accounts"]["x-owner"]["account_id"] = "999"
        self.input(changed)
        with self.assertRaisesRegex(OnboardingError, "changed"):
            import_project(path, apply=True, expected_sha256=preview["input_sha256"])
        self.assertFalse((self.root / "config").exists())

    def test_import_resolves_and_identical_retry_does_not_rewrite(self):
        self.register()
        before = registry_path().stat().st_mtime_ns
        self.assertEqual(resolve_account("runtime-test", "x-owner", expected_provider="x")["account_id"], "12345")
        self.assertEqual(self.register()["result"], "already_present")
        self.assertEqual(before, registry_path().stat().st_mtime_ns)
        self.assertEqual(registry_path().stat().st_mode & 0o777, 0o600)

    def test_existing_account_authority_cannot_be_replaced(self):
        self.register()
        value = project_input()
        value["accounts"]["x-owner"]["account_id"] = "987"
        with self.assertRaises(OnboardingError):
            self.apply(import_project, value)
        self.assertEqual(resolve_account("runtime-test", "x-owner")["account_id"], "12345")

    def test_packaged_project_and_prefix_takeover_are_rejected(self):
        for edit in ({"project": "oneclickpostfactory"}, {"campaign_prefixes": ["OCPF-"]}, {"campaign_prefixes": ["OCPF-MORE-"]}):
            with self.subTest(edit=edit), self.assertRaises(OnboardingError):
                import_project(self.input({**project_input(), **edit}))

    def test_unknown_fields_credentials_duplicate_keys_and_paths_rejected(self):
        for value in ({**project_input(), "token": "DO-NOT-IMPORT"}, {**project_input(), "project": "../../escape"}):
            with self.assertRaises(OnboardingError):
                import_project(self.input(value))
        path = self.input({})
        path.write_text('{"schema_version":1,"schema_version":1}')
        with self.assertRaises(OnboardingError):
            import_project(path)

    def test_corrupt_runtime_authority_fails_closed(self):
        self.register()
        registry_path().write_text('{"schema_version":1,"projects": {"wrong-key": ' + json.dumps(project_input()) + '}}')
        with self.assertRaises(RegistryError):
            load_registry()

    def test_concurrent_overlapping_project_imports_do_not_overwrite(self):
        values = [project_input(), {**project_input(), "project": "runtime-other"}]
        paths = [self.input(value, f"concurrent-{i}.json") for i, value in enumerate(values)]
        hashes = [hashlib.sha256(path.read_bytes()).hexdigest() for path in paths]
        def attempt(i):
            try:
                return import_project(paths[i], apply=True, expected_sha256=hashes[i])["result"]
            except (OnboardingError, RegistryError):
                return "refused"
        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(attempt, range(2)))
        self.assertEqual(sorted(results), ["imported", "refused"])
        self.assertEqual(len(json.loads(registry_path().read_text())["projects"]), 1)

    def test_bounded_source_lock_wait_recovers_from_transient_contention(self):
        from threading import Event
        from ocpf_post.runtime_sources import source_lock

        entered = Event()
        release = Event()

        def holder():
            with source_lock(operation="test-holder"):
                entered.set()
                release.wait(timeout=2)

        def waiter():
            with source_lock(operation="test-waiter", timeout_seconds=1.0):
                return True

        with ThreadPoolExecutor(max_workers=2) as executor:
            held = executor.submit(holder)
            self.assertTrue(entered.wait(timeout=1))
            waiting = executor.submit(waiter)
            time.sleep(0.05)
            self.assertFalse(waiting.done())
            release.set()
            self.assertTrue(waiting.result(timeout=2))
            held.result(timeout=2)

    def test_campaign_uses_runtime_registry_and_existing_schedule_receipts(self):
        self.register()
        path = self.input(campaign_input())
        preview = import_campaign(path)
        self.assertFalse((self.root / "state").exists())
        saved = import_campaign(path, apply=True, expected_sha256=preview["input_sha256"])
        self.assertFalse(saved["published"])
        self.assertFalse(saved["allocation_enabled"])
        self.assertEqual(destination_binding("RTEST-001", "x")["account_id"], "12345")
        self.assertEqual(builtin_text("RTEST-001", "x"), campaign_input()["texts"]["x"])
        provider = FakeProvider(account_id="12345")
        kwargs = dict(campaign="RTEST-001", provider="x", at=(NOW + timedelta(minutes=10)).isoformat(),
                      provider_factory=lambda _: provider, now=NOW)
        create_schedule(**kwargs)
        self.assertEqual(provider.published, [])
        self.assertEqual(run_due(now=NOW + timedelta(minutes=11), provider_factory=lambda _: provider)[0]["status"], "published_verified")
        self.assertEqual(run_due(now=NOW + timedelta(minutes=12), provider_factory=lambda _: provider), [])
        self.assertEqual(len(provider.published), 1)
        self.assertEqual(list(iter_receipts())[-1]["text_sha256"], saved["manifest"]["payload_sha256"]["x"])
        with self.assertRaisesRegex(ScheduleError, "terminal"):
            create_schedule(**kwargs)

    def test_import_does_not_bypass_live_account_or_ambiguous_effect_checks(self):
        self.register()
        self.apply(import_campaign, campaign_input())
        kwargs = dict(campaign="RTEST-001", provider="x", at=(NOW + timedelta(minutes=10)).isoformat(), now=NOW)
        with self.assertRaisesRegex(ScheduleError, "mismatch"):
            create_schedule(**kwargs, provider_factory=lambda _: FakeProvider(account_id="wrong"))
        provider = FakeProvider(account_id="12345", ambiguous=True)
        create_schedule(**kwargs, provider_factory=lambda _: provider)
        result = run_due(now=NOW + timedelta(minutes=11), provider_factory=lambda _: provider)
        self.assertEqual(result[0]["status"], "ambiguous_effect")
        self.assertEqual(run_due(now=NOW + timedelta(minutes=12), provider_factory=lambda _: provider), [])

    def test_multiple_campaigns_and_idempotency_without_editing_registry(self):
        self.register()
        authority = registry_path().read_bytes()
        self.apply(import_campaign, campaign_input())
        self.assertEqual(self.apply(import_campaign, campaign_input())["result"], "already_present")
        self.apply(import_campaign, {**campaign_input(), "campaign": "RTEST-002"})
        self.assertTrue(builtin_manifest("RTEST-002"))
        self.assertEqual(authority, registry_path().read_bytes())
        with self.assertRaises(OnboardingError):
            self.apply(import_campaign, {**campaign_input(), "texts": {"x": "Different text"}})

    def test_allocator_requires_explicit_fresh_opt_in(self):
        self.register()
        allocation = {"lane": "commercial", "priority": 70, "prepared_at": NOW.isoformat(),
                      "expires_at": (NOW + timedelta(days=1)).isoformat()}
        value = {**campaign_input(), "allocation": allocation}
        self.apply(import_campaign, value, now=NOW)
        self.assertNotIn("RTEST-001", [c["campaign"] for c in delivery_candidates(now=NOW)])
        self.apply(import_campaign, {**value, "campaign": "RTEST-002"}, allocate=True, now=NOW)
        self.assertIn("RTEST-002", [c["campaign"] for c in delivery_candidates(now=NOW)])
        with self.assertRaises(OnboardingError):
            self.apply(import_campaign, {**value, "campaign": "RTEST-003"}, allocate=True, now=NOW + timedelta(days=2))

    def test_imported_payload_tampering_preserves_approved_hash_and_blocks_execution(self):
        self.register()
        self.apply(import_campaign, campaign_input())
        approved = builtin_manifest("RTEST-001")["payload_sha256"]
        provider = FakeProvider(account_id="12345")
        create_schedule(campaign="RTEST-001", provider="x", at=(NOW + timedelta(minutes=10)).isoformat(),
                        provider_factory=lambda _: provider, now=NOW)
        (self.root / "state" / "runtime-campaigns" / "RTEST-001" / "x.txt").write_text("Unapproved edit\n")
        self.assertEqual(builtin_manifest("RTEST-001")["payload_sha256"], approved)
        self.assertIsNone(builtin_text("RTEST-001", "x"))
        results = run_due(now=NOW + timedelta(minutes=11), provider_factory=lambda _: provider)
        self.assertEqual(results[0]["status"], "drift_blocked")
        self.assertEqual(provider.published, [])

    def test_campaign_alias_provider_prefix_and_generated_names_rejected(self):
        self.register()
        invalid = [
            {**campaign_input(), "campaign": "OTHER-001"},
            {**campaign_input(), "campaign": "RTEST-EVENT-ABCDEF12"},
            {**campaign_input(), "destinations": {"threads": "x-owner"}, "texts": {"threads": "Approved"}},
            {**campaign_input(), "texts": {"x": "x" * 7000}},
            {**campaign_input(), "texts": {"x": " padded "}},
            {**campaign_input(), "allocation": {"enabled": True}},
        ]
        for value in invalid:
            with self.subTest(value=value), self.assertRaises((OnboardingError, RegistryError)):
                import_campaign(self.input(value))

    def test_command_line_previews_are_machine_readable(self):
        path = self.input(project_input())
        result = subprocess.run(["./ocpf-post", "registry", "import", "--file", str(path)], capture_output=True, text=True, check=True)
        preview = json.loads(result.stdout)
        subprocess.run(["./ocpf-post", "registry", "import", "--file", str(path), "--apply", "--expected-sha256", preview["input_sha256"]], capture_output=True, text=True, check=True)
        path = self.input(campaign_input())
        result = subprocess.run(["./ocpf-post", "campaign", "import", "--file", str(path)], capture_output=True, text=True, check=True)
        self.assertEqual(json.loads(result.stdout)["result"], "preview")


if __name__ == "__main__":
    unittest.main()
