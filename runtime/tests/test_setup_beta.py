from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from ocpf_post import automation_authority, setup_beta
from ocpf_post.setup_engine import SetupEngine

UTC = timezone.utc


def make_runtime(root: Path) -> tuple[Path, str]:
    repo = root / "runtime"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.name", "Beta Fixture"], check=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.email", "beta@example.invalid"], check=True)
    subprocess.run(["git", "-C", str(repo), "config", "commit.gpgsign", "false"], check=True)
    (repo / "fixture.txt").write_text("candidate\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(repo), "add", "."], check=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-qm", "candidate"], check=True)
    revision = subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    return repo, revision


class BetaEvidenceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.workspace = self.root / "bootstrap"
        self.state = self.root / "state"
        self.config = self.root / "config"
        self.runtime, self.revision = make_runtime(self.root)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def make_rehearsal(self) -> SetupEngine:
        engine = SetupEngine(self.workspace)
        value = engine.start(
            "explore",
            operator_label=None,
            timezone="UTC",
            pace="occasional",
        )
        self.assertEqual(value["session"]["stage"], "explore_ready")
        return engine

    def test_missing_status_is_read_only(self) -> None:
        with self.assertRaises(setup_beta.BetaError) as caught:
            setup_beta.status(self.workspace)
        self.assertEqual(caught.exception.code, "beta.enrollment.missing")
        self.assertFalse((self.workspace / "beta").exists())

    def test_rehearsal_enroll_observe_decide_and_adoption_review(self) -> None:
        self.make_rehearsal()
        start = datetime(2026, 9, 27, 9, 0, tzinfo=UTC)
        enrolled = setup_beta.enroll(
            workspace=self.workspace,
            ring="rehearsal",
            target_revision=self.revision,
            state_root=self.state,
            config_root=self.config,
            runtime_root=self.runtime,
            now=start,
        )
        beta_id = enrolled["beta_id"]
        first = setup_beta.observe(
            workspace=self.workspace,
            beta_id=beta_id,
            state_root=self.state,
            config_root=self.config,
            runtime_root=self.runtime,
            operator_outcome="healthy",
            now=start,
        )
        second = setup_beta.observe(
            workspace=self.workspace,
            beta_id=beta_id,
            state_root=self.state,
            config_root=self.config,
            runtime_root=self.runtime,
            operator_outcome="healthy",
            now=start + timedelta(hours=1),
        )
        self.assertEqual(first["status"], "HEALTHY")
        self.assertEqual(second["status"], "HEALTHY")
        decision = setup_beta.decide(
            self.workspace,
            beta_id=beta_id,
            minimum_observations=2,
            minimum_hours=1,
            now=start + timedelta(hours=1),
        )
        self.assertEqual(decision["decision"], "OWNER_CANARY_READY")
        self.assertEqual(decision["blockers"], [])
        review = setup_beta.adoption_review(
            self.workspace,
            beta_id=beta_id,
            minimum_observations=2,
            minimum_hours=1,
        )
        self.assertEqual(review["status"], "BLOCKED")
        self.assertFalse(review["automatic_external_mutation"])
        self.assertIn("src/ocpf_post/setup_contracts.py", review["runtime_adoption_components"])
        self.assertIn("src/ocpf_post/setup_browser.py", review["incubate_longer"])

    def test_rehearsal_never_opens_authority(self) -> None:
        self.make_rehearsal()
        enrolled = setup_beta.enroll(
            workspace=self.workspace,
            ring="rehearsal",
            target_revision=self.revision,
            state_root=self.state,
            config_root=self.config,
            runtime_root=self.runtime,
        )
        current = setup_beta.status(self.workspace, beta_id=enrolled["beta_id"])
        self.assertEqual(current["ring"], "rehearsal")
        marker = automation_authority.read(self.state)
        self.assertEqual(marker["status"], "inactive")

    def test_issue_holds_and_rollback_requires_rollback(self) -> None:
        self.make_rehearsal()
        start = datetime(2026, 9, 27, 9, 0, tzinfo=UTC)
        enrolled = setup_beta.enroll(
            workspace=self.workspace,
            ring="rehearsal",
            target_revision=self.revision,
            state_root=self.state,
            config_root=self.config,
            runtime_root=self.runtime,
            now=start,
        )
        setup_beta.observe(
            workspace=self.workspace,
            beta_id=enrolled["beta_id"],
            state_root=self.state,
            config_root=self.config,
            runtime_root=self.runtime,
            operator_outcome="issue",
            now=start,
        )
        held = setup_beta.decide(
            self.workspace,
            beta_id=enrolled["beta_id"],
            minimum_observations=1,
            minimum_hours=0,
        )
        self.assertEqual(held["decision"], "HOLD")
        self.assertIn("beta.operator.issue", held["blockers"])

        other = self.root / "other-bootstrap"
        engine = SetupEngine(other)
        engine.start("explore", operator_label=None, timezone="UTC", pace="occasional")
        rollback = setup_beta.enroll(
            workspace=other,
            ring="rehearsal",
            target_revision=self.revision,
            state_root=self.root / "other-state",
            config_root=self.root / "other-config",
            runtime_root=self.runtime,
            now=start,
        )
        setup_beta.observe(
            workspace=other,
            beta_id=rollback["beta_id"],
            state_root=self.root / "other-state",
            config_root=self.root / "other-config",
            runtime_root=self.runtime,
            operator_outcome="rollback",
            now=start,
        )
        decision = setup_beta.decide(
            other,
            beta_id=rollback["beta_id"],
            minimum_observations=1,
            minimum_hours=0,
        )
        self.assertEqual(decision["decision"], "ROLLBACK_REQUIRED")

    def test_hash_chain_detects_tamper(self) -> None:
        self.make_rehearsal()
        enrolled = setup_beta.enroll(
            workspace=self.workspace,
            ring="rehearsal",
            target_revision=self.revision,
            state_root=self.state,
            config_root=self.config,
            runtime_root=self.runtime,
        )
        setup_beta.observe(
            workspace=self.workspace,
            beta_id=enrolled["beta_id"],
            state_root=self.state,
            config_root=self.config,
            runtime_root=self.runtime,
            operator_outcome="healthy",
        )
        path = setup_beta.ledger_path(self.workspace)
        rows = path.read_text(encoding="utf-8").splitlines()
        first = json.loads(rows[0])
        first["ring"] = "owner-canary"
        rows[0] = json.dumps(first, separators=(",", ":"))
        path.write_text("\n".join(rows) + "\n", encoding="utf-8")
        path.chmod(0o600)
        with self.assertRaises(setup_beta.BetaError) as caught:
            setup_beta.status(self.workspace)
        self.assertEqual(caught.exception.code, "beta.ledger.hash")

    def test_observation_time_cannot_move_backwards(self) -> None:
        self.make_rehearsal()
        start = datetime(2026, 9, 27, 9, 0, tzinfo=UTC)
        enrolled = setup_beta.enroll(
            workspace=self.workspace,
            ring="rehearsal",
            target_revision=self.revision,
            state_root=self.state,
            config_root=self.config,
            runtime_root=self.runtime,
            now=start,
        )
        setup_beta.observe(
            workspace=self.workspace,
            beta_id=enrolled["beta_id"],
            state_root=self.state,
            config_root=self.config,
            runtime_root=self.runtime,
            operator_outcome="healthy",
            now=start + timedelta(hours=1),
        )
        with self.assertRaises(setup_beta.BetaError) as caught:
            setup_beta.observe(
                workspace=self.workspace,
                beta_id=enrolled["beta_id"],
                state_root=self.state,
                config_root=self.config,
                runtime_root=self.runtime,
                operator_outcome="healthy",
                now=start + timedelta(minutes=30),
            )
        self.assertEqual(caught.exception.code, "beta.time.regressed")

    def test_runtime_revision_drift_blocks_observation(self) -> None:
        self.make_rehearsal()
        enrolled = setup_beta.enroll(
            workspace=self.workspace,
            ring="rehearsal",
            target_revision=self.revision,
            state_root=self.state,
            config_root=self.config,
            runtime_root=self.runtime,
        )
        (self.runtime / "second.txt").write_text("drift\n", encoding="utf-8")
        subprocess.run(["git", "-C", str(self.runtime), "add", "."], check=True)
        subprocess.run(["git", "-C", str(self.runtime), "commit", "-qm", "drift"], check=True)
        observed = setup_beta.observe(
            workspace=self.workspace,
            beta_id=enrolled["beta_id"],
            state_root=self.state,
            config_root=self.config,
            runtime_root=self.runtime,
            operator_outcome="healthy",
        )
        self.assertEqual(observed["status"], "ATTENTION")
        self.assertIn("beta.runtime_revision.drift", observed["blockers"])


class OwnerCanaryGateTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.workspace = self.root / "bootstrap"
        self.state = self.root / "state"
        self.config = self.root / "config"
        self.runtime, self.revision = make_runtime(self.root)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def active_engine(self) -> SetupEngine:
        engine = SetupEngine(self.workspace)
        value = engine.start(
            "fresh",
            operator_label="Beta Owner",
            timezone="UTC",
            pace="occasional",
        )
        session = value["session"]
        session = engine.store.transition(
            session["session_id"],
            expected_revision=session["revision"],
            next_stage="verification_ready",
            event_payload={"fixture": "owner-canary"},
        )
        committed = engine.store.commit_activation_authority(
            session["session_id"],
            expected_revision=session["revision"],
            target_generation=1,
            event_payload={"fixture": "owner-canary"},
        )
        automation_authority.activate(
            root=self.state,
            operation_id=committed["operation"]["operation_id"],
            installation_id=committed["installation"]["installation_id"],
            authority_generation=1,
            review_sha256="a" * 64,
            runtime_revision=self.revision,
        )
        return engine

    @staticmethod
    def ready_host(*_args, **_kwargs) -> dict:
        return {
            "schema_version": 1,
            "status": "READY",
            "profile": "production",
            "workspace": "/fixture",
            "checks": [],
            "blockers": [],
            "publishing_authority": False,
            "provider_consequence_attempted": False,
        }

    def test_owner_canary_preflight_is_read_only_and_proves_quiet_state(self) -> None:
        self.active_engine()
        with patch("ocpf_post.setup_engine.prove_host_capabilities", side_effect=self.ready_host):
            value = setup_beta.preflight(
                workspace=self.workspace,
                ring="owner-canary",
                target_revision=self.revision,
                state_root=self.state,
                config_root=self.config,
                runtime_root=self.runtime,
            )
        self.assertEqual(value["status"], "READY")
        self.assertTrue(value["owner_canary_quiet"]["quiet"])
        self.assertFalse(value["beta_evidence_written"])
        self.assertFalse(setup_beta.ledger_path(self.workspace).exists())

    def test_owner_canary_preflight_blocks_non_pristine_effect_state_without_writing(self) -> None:
        self.active_engine()
        self.state.mkdir(parents=True, exist_ok=True)
        (self.state / "publish-receipts.jsonl").write_text(
            '{"campaign":"CANARY","provider":"x","status":"published_verified"}\n',
            encoding="utf-8",
        )
        with patch("ocpf_post.setup_engine.prove_host_capabilities", side_effect=self.ready_host):
            value = setup_beta.preflight(
                workspace=self.workspace,
                ring="owner-canary",
                target_revision=self.revision,
                state_root=self.state,
                config_root=self.config,
                runtime_root=self.runtime,
            )
        self.assertEqual(value["status"], "BLOCKED")
        self.assertIn("beta.owner_canary.consequence_quiet_required", value["blockers"])
        self.assertFalse(value["beta_evidence_written"])
        self.assertFalse(setup_beta.ledger_path(self.workspace).exists())

    def test_owner_canary_requires_active_classified_installation(self) -> None:
        self.active_engine()
        with patch("ocpf_post.setup_engine.prove_host_capabilities", side_effect=self.ready_host):
            enrolled = setup_beta.enroll(
                workspace=self.workspace,
                ring="owner-canary",
                target_revision=self.revision,
                state_root=self.state,
                config_root=self.config,
                runtime_root=self.runtime,
            )
            self.assertEqual(enrolled["ring"], "owner-canary")
            observed = setup_beta.observe(
                workspace=self.workspace,
                beta_id=enrolled["beta_id"],
                state_root=self.state,
                config_root=self.config,
                runtime_root=self.runtime,
                operator_outcome="healthy",
            )
        self.assertEqual(observed["status"], "HEALTHY")

    def test_owner_canary_field_window_is_required_for_runtime_adoption_review(self) -> None:
        self.active_engine()
        start = datetime(2026, 9, 27, 9, 0, tzinfo=UTC)
        with patch("ocpf_post.setup_engine.prove_host_capabilities", side_effect=self.ready_host):
            enrolled = setup_beta.enroll(
                workspace=self.workspace,
                ring="owner-canary",
                target_revision=self.revision,
                state_root=self.state,
                config_root=self.config,
                runtime_root=self.runtime,
                now=start,
            )
            for offset in (0, 12, 24):
                observed = setup_beta.observe(
                    workspace=self.workspace,
                    beta_id=enrolled["beta_id"],
                    state_root=self.state,
                    config_root=self.config,
                    runtime_root=self.runtime,
                    operator_outcome="healthy",
                    now=start + timedelta(hours=offset),
                )
                self.assertEqual(observed["status"], "HEALTHY")
        decision = setup_beta.decide(
            self.workspace,
            beta_id=enrolled["beta_id"],
            now=start + timedelta(hours=24),
        )
        self.assertEqual(decision["decision"], "BOOTSTRAP_RUNTIME_ADOPTION_READY")
        review = setup_beta.adoption_review(
            self.workspace,
            beta_id=enrolled["beta_id"],
        )
        self.assertEqual(review["status"], "ADOPTION_REVIEW_READY")

    def test_owner_canary_identity_generation_drift_requires_rollback(self) -> None:
        engine = self.active_engine()
        current = engine.status()
        marker = automation_authority.read(self.state)
        start = datetime(2026, 9, 27, 9, 0, tzinfo=UTC)
        with patch("ocpf_post.setup_engine.prove_host_capabilities", side_effect=self.ready_host):
            enrolled = setup_beta.enroll(
                workspace=self.workspace,
                ring="owner-canary",
                target_revision=self.revision,
                state_root=self.state,
                config_root=self.config,
                runtime_root=self.runtime,
                now=start,
            )
            automation_authority.activate(
                root=self.state,
                operation_id=marker["operation_id"],
                installation_id=marker["installation_id"],
                authority_generation=2,
                review_sha256="c" * 64,
                runtime_revision=self.revision,
            )
            observed = setup_beta.observe(
                workspace=self.workspace,
                beta_id=enrolled["beta_id"],
                state_root=self.state,
                config_root=self.config,
                runtime_root=self.runtime,
                operator_outcome="healthy",
                now=start + timedelta(hours=1),
            )
        self.assertEqual(current["operation"]["authority_generation"], 1)
        self.assertEqual(observed["status"], "ATTENTION")
        self.assertIn("beta.authority_generation.drift", observed["blockers"])
        decision = setup_beta.decide(
            self.workspace,
            beta_id=enrolled["beta_id"],
            minimum_observations=1,
            minimum_hours=0,
        )
        self.assertEqual(decision["decision"], "ROLLBACK_REQUIRED")

    def test_owner_canary_requires_pristine_consequence_ledgers(self) -> None:
        self.active_engine()
        self.state.mkdir(parents=True, exist_ok=True)
        (self.state / "publish-receipts.jsonl").write_text(
            '{"campaign":"CANARY","provider":"x","status":"published_verified"}\n',
            encoding="utf-8",
        )
        with patch("ocpf_post.setup_engine.prove_host_capabilities", side_effect=self.ready_host):
            with self.assertRaises(setup_beta.BetaError) as caught:
                setup_beta.enroll(
                    workspace=self.workspace,
                    ring="owner-canary",
                    target_revision=self.revision,
                    state_root=self.state,
                    config_root=self.config,
                    runtime_root=self.runtime,
                )
        self.assertEqual(caught.exception.code, "beta.owner_canary.consequence_quiet_required")

    def test_owner_canary_quiet_baseline_drift_requires_rollback(self) -> None:
        self.active_engine()
        start = datetime(2026, 9, 27, 9, 0, tzinfo=UTC)
        with patch("ocpf_post.setup_engine.prove_host_capabilities", side_effect=self.ready_host):
            enrolled = setup_beta.enroll(
                workspace=self.workspace,
                ring="owner-canary",
                target_revision=self.revision,
                state_root=self.state,
                config_root=self.config,
                runtime_root=self.runtime,
                now=start,
            )
            self.assertTrue(enrolled["consequence_quiet_verified"])
            self.assertRegex(enrolled["quiet_evidence_sha256"], r"^[0-9a-f]{64}$")

            campaign = self.state / "runtime-campaigns" / "CANARY-001"
            campaign.mkdir(parents=True)
            (campaign / "manifest.json").write_text(
                json.dumps({
                    "campaign": "CANARY-001",
                    "allocation": {"enabled": False},
                    "runtime_imported": True,
                }) + "\n",
                encoding="utf-8",
            )

            observed = setup_beta.observe(
                workspace=self.workspace,
                beta_id=enrolled["beta_id"],
                state_root=self.state,
                config_root=self.config,
                runtime_root=self.runtime,
                operator_outcome="healthy",
                now=start + timedelta(hours=1),
            )
        self.assertEqual(observed["status"], "ATTENTION")
        self.assertIn("beta.owner_canary.quiet_baseline_drift", observed["blockers"])
        decision = setup_beta.decide(
            self.workspace,
            beta_id=enrolled["beta_id"],
            minimum_observations=1,
            minimum_hours=0,
        )
        self.assertEqual(decision["decision"], "ROLLBACK_REQUIRED")

    def test_owner_canary_new_provider_effect_receipt_requires_rollback(self) -> None:
        self.active_engine()
        start = datetime(2026, 9, 27, 9, 0, tzinfo=UTC)
        with patch("ocpf_post.setup_engine.prove_host_capabilities", side_effect=self.ready_host):
            enrolled = setup_beta.enroll(
                workspace=self.workspace,
                ring="owner-canary",
                target_revision=self.revision,
                state_root=self.state,
                config_root=self.config,
                runtime_root=self.runtime,
                now=start,
            )
            self.state.mkdir(parents=True, exist_ok=True)
            (self.state / "publish-receipts.jsonl").write_text(
                '{"campaign":"UNEXPECTED","provider":"x","status":"published_verified"}\n',
                encoding="utf-8",
            )
            observed = setup_beta.observe(
                workspace=self.workspace,
                beta_id=enrolled["beta_id"],
                state_root=self.state,
                config_root=self.config,
                runtime_root=self.runtime,
                operator_outcome="healthy",
                now=start + timedelta(hours=1),
            )
        self.assertEqual(observed["status"], "ATTENTION")
        self.assertIn("beta.owner_canary.consequence_eligible_work", observed["blockers"])
        self.assertIn("beta.owner_canary.quiet_baseline_drift", observed["blockers"])
        decision = setup_beta.decide(
            self.workspace,
            beta_id=enrolled["beta_id"],
            minimum_observations=1,
            minimum_hours=0,
        )
        self.assertEqual(decision["decision"], "ROLLBACK_REQUIRED")

    def test_owner_canary_rejects_closed_authority(self) -> None:
        engine = self.active_engine()
        current = engine.status()
        automation_authority.deactivate(
            root=self.state,
            review_sha256="b" * 64,
            reason="fixture closes authority",
        )
        with patch("ocpf_post.setup_engine.prove_host_capabilities", side_effect=self.ready_host):
            with self.assertRaises(setup_beta.BetaError) as caught:
                setup_beta.enroll(
                    workspace=self.workspace,
                    ring="owner-canary",
                    target_revision=self.revision,
                    state_root=self.state,
                    config_root=self.config,
                    runtime_root=self.runtime,
                )
        self.assertEqual(current["session"]["stage"], "active")
        self.assertEqual(caught.exception.code, "beta.owner_canary.not_active")


if __name__ == "__main__":
    unittest.main()
