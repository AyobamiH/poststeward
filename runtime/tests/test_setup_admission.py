from __future__ import annotations

import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from ocpf_post import runtime_release
from ocpf_post.setup_admission import classify_existing_installation, prove_host_capabilities
from ocpf_post.setup_engine import SetupEngine, SetupEngineError


ROOT = Path(__file__).resolve().parents[1]


class HostCapabilityProofTests(unittest.TestCase):
    def test_bootstrap_profile_proves_required_local_primitives(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            workspace = Path(temp) / "bootstrap"
            value = prove_host_capabilities(workspace, production=False)
            self.assertEqual(value["status"], "READY")
            self.assertFalse(value["publishing_authority"])
            self.assertFalse(value["provider_consequence_attempted"])
            by_code = {row["code"]: row for row in value["checks"]}
            for code in (
                "host.python",
                "host.linux",
                "host.private_permissions",
                "host.local_locking",
                "host.atomic_replace",
                "host.sqlite_durability",
                "host.loopback",
            ):
                self.assertEqual(by_code[code]["status"], "ready")

    def test_production_profile_blocks_missing_systemd_user_manager(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            workspace = Path(temp) / "bootstrap"
            with patch("ocpf_post.setup_admission.shutil.which", return_value=None):
                value = prove_host_capabilities(workspace, production=True)
            self.assertEqual(value["status"], "BLOCKED")
            self.assertIn("host.systemd_user", value["blockers"])


class ExistingInstallationClassificationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.workspace = self.root / "bootstrap"
        self.state = self.root / "state"
        self.config = self.root / "config"

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def classify(self):
        return classify_existing_installation(
            self.workspace,
            state_root=self.state,
            config_root=self.config,
        )

    def test_empty_host_is_new(self) -> None:
        value = self.classify()
        self.assertEqual(value["classification"], "new_host")
        self.assertEqual(value["recommended_action"], "start")

    def test_existing_runtime_state_without_setup_is_legacy_not_fresh(self) -> None:
        self.state.mkdir()
        (self.state / "publish-receipts.jsonl").write_text("{}\n", encoding="utf-8")
        value = self.classify()
        self.assertEqual(value["classification"], "legacy_existing_installation")
        self.assertEqual(value["recommended_action"], "inspect_or_migrate")

    def test_open_setup_is_resumable(self) -> None:
        engine = SetupEngine(self.workspace)
        current = engine.begin("fresh", operator_label="Example")
        value = self.classify()
        self.assertEqual(value["classification"], "incomplete_bootstrap")
        self.assertEqual(value["recommended_action"], "resume")
        self.assertEqual(value["setup"]["session_id"], current["session"]["session_id"])

    def test_completed_workspace_cannot_silently_start_again(self) -> None:
        engine = SetupEngine(self.workspace)
        completed = engine.start(
            "explore",
            operator_label=None,
            timezone="UTC",
            pace="occasional",
        )
        self.assertEqual(completed["session"]["stage"], "explore_ready")
        value = self.classify()
        self.assertEqual(value["classification"], "explore_only")
        with self.assertRaises(SetupEngineError) as caught:
            engine.start(
                "explore",
                operator_label=None,
                timezone="UTC",
                pace="occasional",
            )
        self.assertEqual(caught.exception.code, "setup.installation.exists")

    def test_interrupted_runtime_change_routes_to_repair(self) -> None:
        self.state.mkdir()
        (self.state / "runtime-release.json").write_text(
            '{"schema_version":1,"status":"switching"}\n',
            encoding="utf-8",
        )
        value = self.classify()
        self.assertEqual(value["classification"], "interrupted_runtime_change")
        self.assertEqual(value["recommended_action"], "repair_runtime")


class RuntimeCandidateProofTests(unittest.TestCase):
    def test_current_checkout_satisfies_candidate_cli_proof(self) -> None:
        target = runtime_release._resolve(ROOT, "HEAD")
        value = runtime_release._prove_candidate_runtime(ROOT, target)
        self.assertEqual(value["target_revision"], target)
        self.assertEqual(value["help_schema_version"], 1)
        self.assertGreater(value["command_count"], 0)

    def test_unit_readback_must_point_to_promoted_release(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            release = root / "release"
            release.mkdir()
            config_home = root / "config"
            unit_dir = config_home / "systemd" / "user"
            unit_dir.mkdir(parents=True)
            for name, route in runtime_release.RUNTIME_UNIT_ROUTES.items():
                (unit_dir / name).write_text(
                    f"[Service]\nWorkingDirectory={release}\n"
                    f"ExecStart=/bin/sh {release}/scripts/run-unattended {route}\n",
                    encoding="utf-8",
                )
            old = os.environ.get("XDG_CONFIG_HOME")
            os.environ["XDG_CONFIG_HOME"] = str(config_home)
            try:
                value = runtime_release._prove_persisted_runtime_paths(
                    release,
                    console_expected=False,
                )
            finally:
                if old is None:
                    os.environ.pop("XDG_CONFIG_HOME", None)
                else:
                    os.environ["XDG_CONFIG_HOME"] = old
            self.assertEqual(len(value["units"]), len(runtime_release.RUNTIME_UNIT_FILES))

    def test_failed_promotion_restores_previous_proven_runtime(self) -> None:
        previous = "a" * 40
        target = "b" * 40
        review = {
            "schema_version": 1,
            "status": "preview",
            "current_revision": previous,
            "target_revision": target,
            "review_sha256": "c" * 64,
        }
        control = Path("/tmp/control")
        candidate = Path("/tmp/candidate")
        prior_runtime = Path("/tmp/previous")
        completed = subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")
        console_missing = subprocess.CompletedProcess(args=[], returncode=1, stdout="", stderr="")
        writes: list[dict] = []

        with (
            patch.object(runtime_release, "preview", return_value=review),
            patch.object(runtime_release, "runtime_root", return_value=control),
            patch.object(runtime_release, "_ensure_worktree", return_value=candidate),
            patch.object(runtime_release, "_runtime_directory_for_revision", return_value=prior_runtime),
            patch.object(
                runtime_release,
                "_prove_candidate_runtime",
                side_effect=[
                    {"target_revision": target},
                    {"target_revision": previous},
                ],
            ),
            patch.object(
                runtime_release,
                "_reconcile_runtime",
                side_effect=[
                    ValueError("candidate reconciliation failed"),
                    {"release_directory": str(prior_runtime), "units": []},
                ],
            ),
            patch.object(runtime_release, "_run", side_effect=[completed, console_missing]),
            patch.object(
                runtime_release.local_store,
                "write",
                side_effect=lambda _path, value: writes.append(value.copy()),
            ),
        ):
            with self.assertRaisesRegex(ValueError, "previous working runtime was restored"):
                runtime_release.switch(
                    target,
                    apply=True,
                    expected_sha256=review["review_sha256"],
                )

        self.assertEqual(writes[0]["status"], "switching")
        self.assertEqual(writes[-1]["status"], "rollback_restored")
        self.assertEqual(writes[-1]["current_revision"], previous)
        self.assertEqual(writes[-1]["failed_target_revision"], target)


if __name__ == "__main__":
    unittest.main()
