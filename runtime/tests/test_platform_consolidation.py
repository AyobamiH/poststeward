from __future__ import annotations

import io
import os
from pathlib import Path
import tempfile
import unittest
from contextlib import redirect_stdout
from types import SimpleNamespace
from unittest.mock import patch

from ocpf_post import __version__, capabilities, runtime_release, state_registry
from ocpf_post.ledger import LedgerIntegrityError
from ocpf_post.observer_server import DEFAULT_PORT as CONSOLE_PORT
from ocpf_post.providers.x import DEFAULT_REDIRECT_URI
from ocpf_post.state import append_receipt, iter_receipts, terminal_effect_receipt, provider_token_file, write_private_json


class PlatformConsolidationTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        env = patch.dict(os.environ, {
            "OCPF_POST_CONFIG_DIR": str(self.root / "config"),
            "OCPF_POST_STATE_DIR": str(self.root / "state"),
        })
        env.start()
        self.addCleanup(env.stop)

    def test_corrupt_receipt_ledger_fails_closed_instead_of_disappearing(self):
        path = self.root / "state" / "publish-receipts.jsonl"
        path.parent.mkdir(parents=True)
        path.write_text('{"campaign":"C","provider":"x","status":"published_verified","recorded_at":"2026-09-18T00:00:00Z"}\n{broken\n')
        with self.assertRaises(LedgerIntegrityError):
            list(iter_receipts())
        result = state_registry.verify()
        self.assertEqual(result["status"], "attention")
        self.assertEqual(result["critical_ledger_status"], "attention")

    def test_live_critical_integrity_never_walks_or_hashes_full_state_tree(self):
        path = self.root / "state" / "publish-receipts.jsonl"
        path.parent.mkdir(parents=True)
        path.write_text(
            '{"campaign":"C","provider":"x","status":"published_verified","recorded_at":"2026-09-18T00:00:00Z"}\n',
            encoding="utf-8",
        )
        with patch.object(state_registry, "inventory", side_effect=AssertionError("full inventory forbidden")), \
             patch.object(state_registry, "_sha256", side_effect=AssertionError("full fingerprint forbidden")):
            result = state_registry.verify_critical()

        self.assertEqual(result["status"], "observed")
        self.assertEqual(result["scope"], "critical_ledgers_only")
        self.assertEqual(result["invalid_count"], 0)
        self.assertEqual(result["critical_ledgers"]["publish-receipts.jsonl"], "observed")
        self.assertNotIn("files", result)

    def test_legacy_receipt_without_account_id_blocks_duplicate_uncertainty(self):
        append_receipt({
            "campaign": "LEGACY", "provider": "x", "status": "published_verified",
            "recorded_at": "2026-09-18T00:00:00Z", "post_id": "1",
        })
        receipt = terminal_effect_receipt("LEGACY", "x", "123")
        self.assertIsNotNone(receipt)
        self.assertIsNone(receipt.get("account_id"))

    def test_state_migration_never_repairs_corruption(self):
        path = self.root / "state" / "engagement.json"
        path.parent.mkdir(parents=True)
        path.write_text("{bad")
        result = state_registry.migrate(apply=True)
        self.assertEqual(result["status"], "blocked")
        self.assertFalse(result["applied"])

    def test_capability_vocabulary_separates_implementation_from_acceptance(self):
        value = capabilities.report()
        self.assertEqual(value["vocabulary"],
                         ["implemented", "configured", "authorised", "observed", "accepted", "blocked"])
        rows = {row["id"]: row for row in value["capabilities"]}
        self.assertEqual(rows["outcome-connectors"]["implementation"], "implemented")
        self.assertEqual(rows["outcome-connectors"]["configuration"], "blocked")
        self.assertEqual(rows["alert-delivery"]["implementation"], "implemented")
        self.assertEqual(rows["alert-delivery"]["authority"], "blocked")

    def test_capabilities_expose_recorded_linkedin_read_authority_without_token_values(self):
        write_private_json(
            provider_token_file("linkedin"),
            {
                "access_token": "PRIVATE-LINKEDIN-TOKEN",
                "scope": "openid profile email w_member_social",
            },
        )
        value = capabilities.report()
        row = next(item for item in value["capabilities"] if item["id"] == "provider-publishing")
        authority = row["detail"]["linkedin_recorded_read_authority"]
        self.assertEqual(authority["posts"]["status"], "missing")
        self.assertEqual(authority["posts"]["required_scope"], "r_member_social")
        self.assertEqual(authority["comments"]["status"], "missing")
        self.assertEqual(authority["comments"]["required_scope"], "r_member_social_feed")
        self.assertNotIn("PRIVATE-LINKEDIN-TOKEN", str(value))

    def test_live_capabilities_reuse_precomputed_readiness_inputs(self):
        state = {
            "status": "observed",
            "critical_ledger_status": "observed",
            "invalid_count": 0,
            "scope": "critical_ledgers_only",
        }
        connectors = {"status": "not_configured", "connectors": []}
        alerts = {"status": "not_configured", "enabled": False}
        outcomes = {"status": "not_configured", "event_counts": {}}
        engagement_state = {"linkedin": {"status": "partial"}}
        receipts = {"integrity": "observed", "published_effects": 12, "verified_effects": 7}

        with patch("ocpf_post.state_registry.verify", side_effect=AssertionError("full verify forbidden")), \
             patch("ocpf_post.outcome_connectors.report", side_effect=AssertionError("connector reread forbidden")), \
             patch("ocpf_post.alert_delivery.report", side_effect=AssertionError("alert reread forbidden")), \
             patch("ocpf_post.business_outcomes.report", side_effect=AssertionError("outcome reread forbidden")), \
             patch("ocpf_post.engagement.report", side_effect=AssertionError("engagement reread forbidden")):
            value = capabilities.report(
                state_override=state,
                connector_state_override=connectors,
                alert_state_override=alerts,
                outcomes_override=outcomes,
                engagement_state_override=engagement_state,
                receipt_summary_override=receipts,
            )

        rows = {row["id"]: row for row in value["capabilities"]}
        self.assertEqual(rows["provider-publishing"]["detail"]["published_effects"], 12)
        self.assertEqual(rows["provider-publishing"]["detail"]["verified_effects"], 7)
        self.assertEqual(rows["ledger-integrity"]["detail"]["scope"], "critical_ledgers_only")

    def test_optional_unconfigured_capabilities_are_pending_not_core_attention(self):
        rows = [
            {"id": "core", "required_for_core": True, "activated": True,
             "evidence": "observed", "acceptance": "accepted"},
            {"id": "analytics", "required_for_core": False, "activated": False,
             "evidence": "blocked", "acceptance": "blocked"},
        ]
        value = capabilities._readiness_summary(rows)
        self.assertEqual(value["status"], "observed")
        self.assertEqual(value["core_status"], "observed")
        self.assertEqual(value["optional_pending"], ["analytics"])
        self.assertEqual(value["optional_pending_count"], 1)

    def test_activated_optional_capability_can_raise_attention(self):
        rows = [
            {"id": "core", "required_for_core": True, "activated": True,
             "evidence": "observed", "acceptance": "accepted"},
            {"id": "alerts", "required_for_core": False, "activated": True,
             "evidence": "blocked", "acceptance": "blocked"},
        ]
        value = capabilities._readiness_summary(rows)
        self.assertEqual(value["status"], "attention")
        self.assertEqual(value["core_status"], "observed")
        self.assertEqual(value["activated_optional_blockers"], ["alerts"])

    def test_doctor_deep_text_names_health_attention_codes(self):
        from ocpf_post import cli
        capability = {
            "status": "observed", "core_status": "observed",
            "core_blockers": [], "activated_optional_blockers": [],
            "optional_pending_count": 2, "capabilities": [{}, {}, {}],
        }
        health = {
            "status": "attention",
            "findings": [
                {"level": "attention", "code": "reply_worker_stale"},
                {"level": "attention", "code": "reply_worker_stale"},
                {"level": "attention", "code": "overdue", "schedule_id": "sch-example", "campaign": "EXAMPLE", "provider": "x",
                 "forensic_status": "forensic_no_match", "forensic_candidate_count": 0,
                 "forensic_automatic_retry": False, "forensic_next_action": "manual_review_no_resend"},
                {"level": "warning", "code": "source_stale"},
            ],
        }
        state = {"status": "observed", "invalid_count": 0, "critical_ledger_status": "observed"}
        runtime = {"git_commit_sha": "a" * 40, "checkout_clean": True}
        output = io.StringIO()
        with patch("ocpf_post.capabilities.report", return_value=capability), \
             patch("ocpf_post.state_registry.verify", return_value=state), \
             patch("ocpf_post.runtime_attestation.attest", return_value=runtime), \
             patch("ocpf_post.health.report", return_value=health), \
             redirect_stdout(output):
            cli.cmd_doctor(SimpleNamespace(deep=True, json=False))
        text = output.getvalue()
        self.assertIn("core=observed", text)
        self.assertIn("optional_pending=2", text)
        self.assertIn("Health attention:", text)
        self.assertIn("overdue", text)
        self.assertIn("reply_worker_stale×2", text)
        self.assertNotIn("source_stale", text)

        self.assertIn("Health example:", text)
        self.assertIn('"forensic_status": "forensic_no_match"', text)
        self.assertIn('"forensic_candidate_count": 0', text)
        self.assertIn('"forensic_automatic_retry": false', text)
        self.assertIn('"forensic_next_action": "manual_review_no_resend"', text)
        self.assertNotIn("PRIVATE_COPY", text)

    def test_console_and_x_oauth_use_distinct_default_ports(self):
        self.assertEqual(CONSOLE_PORT, 8767)
        self.assertEqual(DEFAULT_REDIRECT_URI, "http://127.0.0.1:8765/callback")


    def test_capabilities_remain_structured_when_receipt_integrity_is_blocked(self):
        path = self.root / "state" / "publish-receipts.jsonl"
        path.parent.mkdir(parents=True)
        path.write_text("{broken\n")
        value = capabilities.report()
        rows = {row["id"]: row for row in value["capabilities"]}
        self.assertEqual(rows["provider-publishing"]["detail"]["receipt_integrity"], "blocked")
        self.assertEqual(rows["provider-publishing"]["acceptance"], "blocked")

    def test_state_archive_is_preview_first_and_excludes_config(self):
        state = self.root / "state"
        config = self.root / "config"
        state.mkdir(parents=True)
        config.mkdir(parents=True)
        (state / "engagement.json").write_text('{"schema_version":1,"polls":{},"inbox":{}}')
        (config / "linkedin-token.json").write_text('{"access_token":"secret"}')
        output = self.root / "archive.tgz"
        preview = state_registry.archive(output)
        self.assertEqual(preview["status"], "preview")
        self.assertFalse(output.exists())
        applied = state_registry.archive(output, apply=True)
        self.assertEqual(applied["status"], "archived")
        self.assertTrue(output.exists())

    def _git_repo(self):
        import subprocess
        repo = self.root / "repo"
        (repo / "src" / "ocpf_post").mkdir(parents=True)
        (repo / "src" / "ocpf_post" / "__init__.py").write_text(
            "__version__ = '0.26.0'\nRUNTIME_STATE_COMPATIBILITY = 1\n"
        )
        subprocess.run(["git", "init", "-q", str(repo)], check=True)
        subprocess.run(["git", "-C", str(repo), "config", "user.email", "test@example.com"], check=True)
        subprocess.run(["git", "-C", str(repo), "config", "user.name", "Test"], check=True)
        # Hermetic temp repos must not inherit an operator's global signing policy.
        subprocess.run(["git", "-C", str(repo), "config", "commit.gpgsign", "false"], check=True)
        subprocess.run(["git", "-C", str(repo), "add", "."], check=True)
        subprocess.run(["git", "-C", str(repo), "commit", "-qm", "initial"], check=True)
        return repo

    def test_runtime_release_preview_is_exact_and_state_compatible(self):
        repo = self._git_repo()
        with patch.object(runtime_release, "runtime_root", return_value=repo), \
             patch.object(runtime_release, "verify_state", return_value={"status": "observed", "files": []}):
            value = runtime_release.preview("HEAD")
        self.assertEqual(value["status"], "preview")
        self.assertRegex(value["target_revision"], r"^[0-9a-f]{40}$")
        self.assertEqual(value["current_revision"], value["target_revision"])
        self.assertEqual(value["state_compatibility"], 1)
        self.assertRegex(value["review_sha256"], r"^[0-9a-f]{64}$")

    def test_runtime_release_switch_persists_intent_before_unit_reconcile(self):
        release = self.root / "release"
        (release / "src").mkdir(parents=True)
        (release / "scripts").mkdir()
        (release / "scripts" / "restore-local-runtime.py").write_text("# placeholder")
        target = "b" * 40
        current = "a" * 40
        review = {
            "schema_version": 1, "status": "preview", "applied": False,
            "current_revision": current, "target_revision": target,
            "state_sha256": "c" * 64, "state_compatibility": 1,
            "review_sha256": "d" * 64, "release_directory": str(release),
        }
        writes = []
        class Result:
            def __init__(self, returncode=0, stdout=""):
                self.returncode = returncode
                self.stdout = stdout
                self.stderr = ""
        def fake_run(args, **kwargs):
            if args[:4] == ["systemctl", "--user", "show", "post-once-console.service"]:
                return Result(0, "not-found\n")
            return Result(0, "")
        with patch.object(runtime_release, "preview", return_value=review), \
             patch.object(runtime_release, "runtime_root", return_value=self.root), \
             patch.object(runtime_release, "_ensure_worktree", return_value=release), \
             patch.object(runtime_release, "_runtime_directory_for_revision", return_value=self.root), \
             patch.object(
                 runtime_release,
                 "_prove_candidate_runtime",
                 side_effect=[
                     {"target_revision": target},
                     {"target_revision": current},
                     {"target_revision": target},
                 ],
             ), \
             patch.object(
                 runtime_release,
                 "_reconcile_runtime",
                 return_value={"release_directory": str(release), "units": []},
             ), \
             patch.object(runtime_release, "_run", side_effect=fake_run), \
             patch.object(runtime_release.local_store, "write",
                          side_effect=lambda path, value: writes.append(dict(value))):
            value = runtime_release.switch(target, apply=True, expected_sha256="d" * 64)
        self.assertEqual(value["status"], "switched")
        self.assertEqual(writes[0]["status"], "switching")
        self.assertEqual(writes[-1]["status"], "switched")
        self.assertEqual(writes[-1]["previous_revision"], current)

class PlatformRepositoryContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.root = Path(__file__).resolve().parents[1]

    def test_generated_manual_and_completions_exist_and_cover_console(self):
        manual = (self.root / "man" / "ocpf-post.1").read_text()
        self.assertIn(r"ocpf\-post console", manual)
        self.assertIn(r"ocpf\-post state verify", manual)
        self.assertIn(r"ocpf\-post runtime status", manual)
        self.assertIn(f"post-once {__version__}", manual)
        self.assertIn("Supply -> Admission -> Automatic schedule -> Execute -> Provider -> Verify.", manual)
        self.assertIn("safety circuit breaker", manual)
        for path in (
            self.root / "completions" / "ocpf-post.bash",
            self.root / "completions" / "_ocpf-post",
            self.root / "completions" / "ocpf-post.fish",
        ):
            self.assertTrue(path.exists())
            self.assertIn("console", path.read_text())

    def test_automation_units_share_security_baseline_and_symmetric_uninstall(self):
        for name in ("install-user-scheduler-timer", "install-user-portfolio-timer"):
            text = (self.root / "scripts" / name).read_text()
            for directive in (
                "UMask=0077", "NoNewPrivileges=true", "PrivateTmp=true",
                "ProtectSystem=strict", "ProtectHome=read-only", "ReadWritePaths=",
                "RestrictSUIDSGID=true",
            ):
                self.assertIn(directive, text, name)
        self.assertTrue((self.root / "scripts" / "uninstall-user-portfolio-timer").exists())

    def test_current_docs_do_not_repeat_superseded_runtime_contracts(self):
        roadmap = (self.root / "docs" / "ROADMAP.md").read_text()
        conversation = (self.root / "docs" / "CONVERSATION_AND_FEEDBACK.md").read_text()
        pipeline = (self.root / "docs" / "PROVIDER_PIPELINES.md").read_text()
        health = (self.root / "docs" / "HEALTH.md").read_text()
        self.assertNotIn("LinkedIn comments remain unsupported", roadmap)
        self.assertNotIn("comment collection/sending remains unsupported", conversation)
        self.assertNotIn("canonical two-stage API flow", pipeline)
        self.assertIn("auto_publish_text=true", pipeline)
        self.assertIn("Optional bounded external alert delivery", health)

    def test_historical_ledgers_are_not_labelled_current_runtime_authority(self):
        loop = (self.root / "docs" / "plans" / "OPERATING_LOOP.md").read_text()
        continuity = (self.root / "docs" / "CONTINUITY.md").read_text()
        self.assertIn("## Historical task register", loop)
        self.assertIn("Historical engineering/evidence continuity ledger", continuity)


if __name__ == "__main__":
    unittest.main()
