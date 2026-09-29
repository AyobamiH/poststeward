from __future__ import annotations

from copy import deepcopy
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch
from urllib.error import URLError

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("safe_upgrade_script", ROOT / "scripts" / "safe-runtime-upgrade.py")
assert SPEC and SPEC.loader
u = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(u)
OLD = "a" * 40
TARGET = "b" * 40
DIGEST = "c" * 64


class FakeSystemd:
    def __init__(self):
        self.rows = {
            name: {"LoadState": "loaded", "ActiveState": "active", "Triggers": service}
            for name, service in zip(u.TIMERS, u.SERVICES)
        }
        self.rows.update({
            name: {"LoadState": "loaded", "ActiveState": "inactive", "MainPID": "0", "ControlPID": "0"}
            for name in u.SERVICES
        })
        self.calls = []
        self.extra = set()
        self.fail_stop = None
        self.fail_start = None

    def timers(self):
        return set(u.TIMERS) | self.extra

    def show(self, unit):
        self.calls.append(("show", unit))
        return deepcopy(self.rows[unit])

    def stop(self, unit):
        assert unit in u.TIMERS, "Never stop a service"
        self.calls.append(("stop", unit))
        self.rows[unit]["ActiveState"] = "inactive"
        if self.fail_stop == unit:
            raise u.UpgradeError("injected_partial_stop_failure")

    def start(self, unit):
        assert unit in u.TIMERS, "Never start a service"
        self.calls.append(("start", unit))
        if self.fail_start == unit:
            raise u.UpgradeError("injected_resume_failure")
        self.rows[unit]["ActiveState"] = "active"


class FakeRelease:
    def __init__(self, systemd):
        self.systemd = systemd
        self.active = OLD
        self.saved_status = "switched"
        self.calls = []
        self.preview_status = "preview"
        self.error = None
        self.digest = DIGEST
        self.check_quiet = True
        self.auto_resume = False

    def status(self):
        return {"active_runtime_revision": self.active, "release_state": {"status": self.saved_status}}

    def preview(self, revision):
        self.calls.append(("preview", revision))
        if self.check_quiet:
            assert all(self.systemd.rows[x]["ActiveState"] == "inactive" for x in u.TIMERS)
            assert all(self.systemd.rows[x]["ActiveState"] in u.IDLE for x in u.SERVICES)
        return {"status": self.preview_status, "current_revision": self.active,
                "target_revision": revision, "review_sha256": self.digest}

    def switch(self, revision, *, apply, expected_sha256):
        self.calls.append(("switch", revision, apply, expected_sha256))
        assert expected_sha256 == DIGEST, "Must pass fresh approval unchanged"
        if self.error:
            raise self.error
        self.active = revision
        if self.auto_resume:
            for timer in u.TIMERS:
                self.systemd.rows[timer]["ActiveState"] = "active"
        return {"status": "switched", "current_revision": revision, "applied": True}


class SafeRuntimeUpgradeTests(unittest.TestCase):
    def setUp(self):
        self.systemd = FakeSystemd()
        self.release = FakeRelease(self.systemd)
        self.events = []
        self.now = 0.0

    def emit(self, key, value):
        self.events.append((key, value))

    def sleep(self, amount):
        self.now += amount

    def execute(self, **kw):
        return u.safe_upgrade(
            self.release, self.systemd, TARGET, apply=True, emit=self.emit,
            clock=lambda: self.now, sleep=self.sleep, **kw,
        )

    def assert_resumed(self):
        self.assertTrue(all(self.systemd.rows[x]["ActiveState"] == "active" for x in u.TIMERS))
        self.assertFalse(any(action == "stop" and unit.endswith(".service")
                             for action, unit in self.systemd.calls))

    def test_success_drains_then_reviews_then_applies_exact_hash_once(self):
        result = self.execute()
        self.assertEqual(result["status"], "switched")
        self.assertEqual(self.release.calls, [("preview", TARGET), ("switch", TARGET, True, DIGEST)])
        phases = [v for k, v in self.events if k == "phase"]
        self.assertLess(phases.index("allow_running_services_to_finish"), phases.index("fresh_review_after_services_are_idle"))
        self.assert_resumed()

    def test_running_provider_job_is_allowed_to_finish_not_killed(self):
        row = self.systemd.rows[u.SERVICES[0]]
        row.update(ActiveState="activating", MainPID="200")
        def tick(amount):
            self.now += amount
            self.assertEqual(self.release.calls, [])
            row.update(ActiveState="inactive", MainPID="0")
        self.sleep = tick
        self.execute()
        self.assertEqual(self.now, 1.0)
        self.assert_resumed()

    def test_nonzero_control_pid_keeps_service_busy(self):
        row = self.systemd.rows[u.SERVICES[1]]
        row.update(ControlPID="321")
        def tick(amount):
            self.now += amount
            self.assertFalse(self.release.calls)
            row.update(ControlPID="0")
        self.sleep = tick
        self.execute()
        self.assertEqual(self.now, 1)
        self.assert_resumed()

    def test_drain_timeout_restores_wakeups_without_apply(self):
        self.systemd.rows[u.SERVICES[2]].update(ActiveState="activating", MainPID="22")
        with self.assertRaisesRegex(u.UpgradeError, "drain_timeout"):
            self.execute(drain_timeout=2)
        self.assertEqual(self.release.calls, [])
        self.assertEqual(self.systemd.rows[u.SERVICES[2]]["MainPID"], "22")
        self.assert_resumed()

    def test_long_running_service_can_finish_inside_extended_drain_window(self):
        row = self.systemd.rows[u.SERVICES[1]]
        row.update(ActiveState="activating", MainPID="222")

        def tick(amount):
            self.now += amount
            self.assertEqual(self.release.calls, [])
            self.assertTrue(all(self.systemd.rows[t]["ActiveState"] == "inactive" for t in u.TIMERS))
            if self.now >= 1001:
                row.update(ActiveState="inactive", MainPID="0")

        self.sleep = tick
        result = self.execute(drain_timeout=1200)
        self.assertEqual(result["status"], "switched")
        self.assertGreaterEqual(self.now, 1001)
        self.assertIn(("timer_wakeups_paused", True), self.events)
        self.assertIn(("drain_timeout_seconds", 1200), self.events)
        self.assert_resumed()

    def test_drain_timeout_upper_bound_covers_hour_long_safe_window(self):
        self.release.check_quiet = False
        value = u.safe_upgrade(
            self.release, self.systemd, TARGET,
            apply=False, drain_timeout=3600, emit=self.emit,
        )
        self.assertEqual(value["status"], "preview")
        with self.assertRaisesRegex(u.UpgradeError, "1_to_3600"):
            u.safe_upgrade(
                self.release, self.systemd, TARGET,
                apply=False, drain_timeout=3601, emit=self.emit,
            )

    def test_partial_stop_failure_restores_timer_whose_stop_raised(self):
        self.systemd.fail_stop = u.TIMERS[1]
        with self.assertRaisesRegex(u.UpgradeError, "partial_stop"):
            self.execute()
        self.assertEqual(self.release.calls, [])
        self.assert_resumed()

    def test_blocked_integrity_review_cannot_apply(self):
        self.release.preview_status = "blocked"
        with self.assertRaisesRegex(u.UpgradeError, "fresh_review_blocked"):
            self.execute()
        self.assertEqual(self.release.calls, [("preview", TARGET)])
        self.assert_resumed()

    def test_other_writer_drift_still_rejects_once_never_retries(self):
        self.release.error = ValueError("Release switch review changed")
        with self.assertRaisesRegex(ValueError, "review changed"):
            self.execute()
        self.assertEqual(len([x for x in self.release.calls if x[0] == "switch"]), 1)
        self.assertEqual(self.release.active, OLD)
        self.assert_resumed()

    def test_apply_failure_still_restores_all_timer_wakeups(self):
        self.release.error = RuntimeError("injected_apply_failure")
        with self.assertRaisesRegex(RuntimeError, "injected_apply"):
            self.execute()
        self.assert_resumed()

    def test_resumption_attempts_continue_when_one_timer_cannot_start(self):
        self.systemd.fail_start = u.TIMERS[0]
        with self.assertRaisesRegex(u.UpgradeError, "timer_restore_failed"):
            self.execute()
        self.assertEqual([x[1] for x in self.systemd.calls if x[0] == "start"], list(u.TIMERS))
        self.assertIn(("timers_restored", False), self.events)

    def test_existing_installer_resumption_is_not_duplicated(self):
        self.release.auto_resume = True
        self.execute()
        self.assertFalse(any(x[0] == "start" for x in self.systemd.calls))
        self.assert_resumed()

    def test_disabled_or_missing_timer_is_not_enabled_by_upgrade(self):
        self.systemd.rows[u.TIMERS[0]]["ActiveState"] = "inactive"
        with self.assertRaisesRegex(u.UpgradeError, "must_already_be_active"):
            self.execute()
        self.assertFalse(any(x[0] in {"start", "stop"} for x in self.systemd.calls))
        self.assertEqual(self.release.calls, [])

    def test_unknown_timer_and_changed_trigger_fail_before_pause(self):
        self.systemd.extra.add("post-once-other.timer")
        with self.assertRaisesRegex(u.UpgradeError, "unrecognised"):
            self.execute()
        self.systemd.extra.clear()
        self.systemd.rows[u.TIMERS[0]]["Triggers"] = "other.service"
        with self.assertRaisesRegex(u.UpgradeError, "trigger_changed"):
            self.execute()
        self.assertFalse(any(x[0] == "stop" for x in self.systemd.calls))

    def test_stop_propagation_dependency_is_rejected_before_touching_worker(self):
        self.systemd.rows[u.SERVICES[0]]["PartOf"] = u.TIMERS[0]
        with self.assertRaisesRegex(u.UpgradeError, "stop_dependency"):
            self.execute()
        self.assertFalse(any(x[0] == "stop" for x in self.systemd.calls))

    def test_pending_switch_is_not_retried(self):
        self.release.saved_status = "switching"
        with self.assertRaisesRegex(u.UpgradeError, "previous_switch_incomplete"):
            self.execute()
        self.assertEqual(self.systemd.calls, [])

    def test_managed_metadata_required_prevents_checkout_sha_false_positive(self):
        self.release.saved_status = ""
        self.release.active = TARGET
        with self.assertRaisesRegex(u.UpgradeError, "managed_release_metadata"):
            self.execute()
        self.assertEqual(self.systemd.calls, [])

    def test_already_active_is_noop(self):
        self.release.active = TARGET
        result = self.execute()
        self.assertEqual(result["status"], "already_active")
        self.assertEqual(self.systemd.calls, [])
        self.assertEqual(self.release.calls, [])

    def test_preview_does_not_pause_anything(self):
        self.release.check_quiet = False
        result = u.safe_upgrade(self.release, self.systemd, TARGET)
        self.assertEqual(result["status"], "preview")
        self.assertEqual(self.systemd.calls, [])

    def test_cancel_during_drain_restores_timers_and_does_not_apply(self):
        self.systemd.rows[u.SERVICES[0]]["ActiveState"] = "activating"
        with self.assertRaisesRegex(u.UpgradeError, "cancelled_before"):
            self.execute(cancelled=lambda: self.now > 0)
        self.assertEqual(self.release.calls, [])
        self.assert_resumed()

    def test_invalid_hash_never_applies(self):
        self.release.digest = "bad"
        with self.assertRaisesRegex(u.UpgradeError, "digest_invalid"):
            self.execute()
        self.assertEqual(self.release.calls, [("preview", TARGET)])
        self.assert_resumed()

    def test_exact_revision_required(self):
        with self.assertRaisesRegex(u.UpgradeError, "exact_40"):
            u.safe_upgrade(self.release, self.systemd, "main", apply=True)
        self.assertEqual(self.systemd.calls, [])

    def test_systemd_parser_command_names_and_non_timer_refusal(self):
        content = "LoadState=loaded\nActiveState=active\nTriggers=post-once-run-due.service\nMainPID=0\n"
        result = subprocess.CompletedProcess([], 0, content, "")
        with patch.object(u.subprocess, "run", return_value=result) as run:
            self.assertEqual(u.Systemd().show(u.TIMERS[0])["LoadState"], "loaded")
            self.assertIn("--property=LoadState,ActiveState,MainPID,ControlPID,PartOf,BindsTo,PropagatesStopTo,Triggers", run.call_args.args[0])
        with self.assertRaisesRegex(u.UpgradeError, "refusing_to_stop_non_timer"):
            u.Systemd().stop(u.SERVICES[0])

    def test_exclusive_lock_rejects_concurrent_helper_without_state_writes(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            subprocess.run(["git", "init", "-q", str(repo)], check=True)
            with u.release_lock(repo):
                with self.assertRaisesRegex(u.UpgradeError, "another_safe_upgrade"):
                    with u.release_lock(repo):
                        self.fail("concurrent entry")
            with u.release_lock(repo):
                pass
            self.assertFalse((repo / "state").exists())


class ConsoleAcceptanceTests(unittest.TestCase):
    def snapshot(self, revision="one"):
        return {
            "revision": revision, "runtime": {"git_commit_sha": TARGET},
            "operator": {"stages": [{"id": x} for x in u.STAGES]},
            "state_integrity": {"scope": "critical_ledgers_only", "status": "observed", "invalid_count": 0},
            "projection_timing_ms": {"total": 200},
        }

    def test_changed_cache_revision_is_not_misreported_as_failure(self):
        values = [{"status": "ok"}, self.snapshot("one"), self.snapshot("two")]
        def response(*_a, **_kw):
            return io.BytesIO(json.dumps(values.pop(0)).encode())
        events = []
        with patch.object(u, "urlopen", side_effect=response):
            u.check_console(TARGET, 8767, lambda k, v: events.append((k, v)))
        self.assertIn(("snapshot_revision_same", False), events)
        self.assertIn(("LIVE_CONSOLE_ACCEPTANCE", "PASS"), events)

    def test_metadata_alone_cannot_prove_running_console_version(self):
        wrong = self.snapshot()
        wrong["runtime"]["git_commit_sha"] = OLD
        values = [{"status": "ok"}, wrong]
        with patch.object(u, "urlopen", side_effect=lambda *_a, **_kw: io.BytesIO(json.dumps(values.pop(0)).encode())):
            with self.assertRaisesRegex(u.UpgradeError, "not_running_target"):
                u.check_console(TARGET, 8767, lambda *_: None)

    def test_critical_attention_not_marked_as_accepted(self):
        wrong = self.snapshot()
        wrong["state_integrity"]["invalid_count"] = 1
        values = [{"status": "ok"}, wrong]
        with patch.object(u, "urlopen", side_effect=lambda *_a, **_kw: io.BytesIO(json.dumps(values.pop(0)).encode())):
            with self.assertRaisesRegex(u.UpgradeError, "integrity_attention"):
                u.check_console(TARGET, 8767, lambda *_: None)

    def test_console_startup_connection_refusal_is_bounded_readiness_not_switch_failure(self):
        values = [{"status": "ok"}, self.snapshot("one"), self.snapshot("one")]
        calls = [URLError("connection refused"), URLError("connection refused")]
        calls.extend(io.BytesIO(json.dumps(value).encode()) for value in values)
        ticks = iter([0.0, 0.0, 0.1, 0.1, 0.2, 0.2, 0.2, 0.2, 0.2, 0.2, 0.2, 0.2])
        events = []
        with patch.object(u, "urlopen", side_effect=calls):
            value = u.check_console(
                TARGET, 8767, lambda k, v: events.append((k, v)),
                readiness_seconds=1.0, sleep=lambda _s: None, clock=lambda: next(ticks),
            )
        self.assertEqual(value["runtime"]["git_commit_sha"], TARGET)
        self.assertIn(("console_readiness_attempts", 3), events)
        self.assertIn(("LIVE_CONSOLE_ACCEPTANCE", "PASS"), events)

    def test_console_timeout_is_not_a_runtime_upgrade_retry(self):
        with patch.object(u, "urlopen", side_effect=URLError("timeout")):
            with self.assertRaises(URLError):
                u.check_console(
                    TARGET, 8767, lambda *_: None,
                    readiness_seconds=0.0, sleep=lambda _s: None,
                    clock=lambda: 0.0,
                )


class RealReleaseGuardTests(unittest.TestCase):
    """Run the unchanged release guard against actual temporary Git/JSON files."""
    def setUp(self):
        import hashlib
        from ocpf_post import runtime_release
        self.release = runtime_release
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.repo = self.root / "repo"
        self.state = self.root / "state"
        self.state.mkdir()
        self.repo.mkdir()
        subprocess.run(["git", "init", "-q", str(self.repo)], check=True)
        for name, value in [("user.name", "Fixture"), ("user.email", "fixture@example.invalid"), ("commit.gpgsign", "false")]:
            self.git("config", name, value)
        (self.repo / "src" / "ocpf_post").mkdir(parents=True)
        (self.repo / "src" / "ocpf_post" / "__init__.py").write_text("RUNTIME_STATE_COMPATIBILITY = 1\n")
        cli = self.repo / "post-once"
        cli.write_text(
            "#!/bin/sh\n"
            "set -eu\n"
            "if [ \"$1\" = \"--version\" ]; then echo 0.26.0; exit 0; fi\n"
            "if [ \"$#\" -ge 2 ] && [ \"$1\" = \"help\" ] && [ \"$2\" = \"--json\" ]; then "
            "echo '{\"schema_version\":1,\"commands\":[{\"path\":\"help\"}]}'; exit 0; fi\n"
            "exit 0\n"
        )
        cli.chmod(0o755)
        (self.repo / "scripts").mkdir()
        (self.repo / "scripts" / "restore-local-runtime.py").write_text(
            "import os\n"
            "from pathlib import Path\n"
            "root=Path(__file__).resolve().parents[1]\n"
            "unit_dir=Path(os.environ['XDG_CONFIG_HOME'])/'systemd'/'user'\n"
            "unit_dir.mkdir(parents=True, exist_ok=True)\n"
            "routes={'post-once-run-due.service':'run-due',"
            "'post-once-portfolio-refill.service':'refill',"
            "'post-once-collection.service':'collect',"
            "'post-once-replies.service':'respond'}\n"
            "for name,route in routes.items():\n"
            "    (unit_dir/name).write_text("
            "f'[Service]\\nWorkingDirectory={root}\\n"
            "ExecStart=/bin/sh {root}/scripts/run-unattended {route}\\n')\n"
            "print('inert fixture installer: no systemd or provider calls')\n"
        )
        (self.repo / ".gitignore").write_text("__pycache__/\n")
        self.git("add", ".")
        self.git("commit", "-qm", "old fixture")
        self.old = self.git("rev-parse", "HEAD")
        (self.repo / "fixture-version.txt").write_text("new\n")
        self.git("add", ".")
        self.git("commit", "-qm", "target fixture")
        self.target = self.git("rev-parse", "HEAD")
        releases = self.root / "releases"
        releases.mkdir()
        subprocess.run(
            ["git", "-C", str(self.repo), "worktree", "add", "--detach", str(releases / self.old), self.old],
            check=True,
            capture_output=True,
            text=True,
        )
        env_patch = patch.dict(os.environ, {"XDG_CONFIG_HOME": str(self.root / "config")})
        env_patch.start()
        self.addCleanup(env_patch.stop)
        self.ledger = self.state / "publish-receipts.jsonl"
        self.ledger.write_text('{"campaign":"TEST","provider":"x","status":"published_unverified"}\n')
        self.metadata = self.state / "runtime-release.json"
        self.metadata.write_text(json.dumps({"schema_version": 1, "status": "switched", "current_revision": self.old}))
        def verify():
            return {"status": "observed", "files": [
                {"scope": "state", "path": p.name, "sha256": hashlib.sha256(p.read_bytes()).hexdigest()}
                for p in sorted(self.state.iterdir()) if p.is_file()
            ]}
        def read(path):
            return json.loads(path.read_text()) if path.exists() else {}
        def write(path, value):
            path.write_text(json.dumps(value))
        real_run = runtime_release._run
        def run(args, **kw):
            if args[0] == "systemctl":
                # This is the unchanged installer's read-only console-presence probe.
                return subprocess.CompletedProcess(args, 0, "not-found\n", "")
            return real_run(args, **kw)
        patches = [
            patch.object(runtime_release, "runtime_root", return_value=self.repo),
            patch.object(runtime_release, "state_dir", return_value=self.state),
            patch.object(runtime_release, "releases_root", return_value=self.root / "releases"),
            patch.object(runtime_release, "verify_state", side_effect=verify),
            patch.object(runtime_release.local_store, "read", side_effect=read),
            patch.object(runtime_release.local_store, "write", side_effect=write),
            patch.object(runtime_release, "_run", side_effect=run),
        ]
        for item in patches:
            item.start()
            self.addCleanup(item.stop)
        self.systemd = FakeSystemd()

    def git(self, *args):
        return subprocess.run(["git", "-C", str(self.repo), *args], check=True, capture_output=True, text=True).stdout.strip()

    def append_effect(self):
        with self.ledger.open("a") as handle:
            handle.write('{"campaign":"LATER","provider":"threads","status":"published_verified"}\n')

    def test_reproduces_original_review_changed_before_any_worktree_or_install(self):
        review = self.release.preview(self.target)
        before_metadata = self.metadata.read_bytes()
        self.append_effect()
        with patch.object(self.release, "_ensure_worktree", side_effect=AssertionError("must reject before filesystem switch")) as worktree:
            with self.assertRaisesRegex(ValueError, "Release switch review changed"):
                self.release.switch(self.target, apply=True, expected_sha256=review["review_sha256"])
        worktree.assert_not_called()
        self.assertEqual(self.metadata.read_bytes(), before_metadata)
        self.assertTrue((self.root / "releases" / self.old).is_dir())
        self.assertFalse((self.root / "releases" / self.target).exists())

    def test_drain_first_passes_real_guard_worktree_compile_and_inert_install(self):
        self.systemd.rows[u.SERVICES[0]].update(ActiveState="activating", MainPID="100")
        def tick(_seconds):
            self.append_effect()
            self.systemd.rows[u.SERVICES[0]].update(ActiveState="inactive", MainPID="0")
        result = u.safe_upgrade(self.release, self.systemd, self.target, apply=True, sleep=tick, emit=lambda *_: None)
        self.assertEqual(result["current_revision"], self.target)
        self.assertEqual(json.loads(self.metadata.read_text())["status"], "switched")
        self.assertIn('"campaign":"LATER"', self.ledger.read_text())
        release_dir = Path(result["release_directory"])
        self.assertEqual(subprocess.run(["git", "-C", str(release_dir), "rev-parse", "HEAD"], capture_output=True, text=True, check=True).stdout.strip(), self.target)
        self.assertTrue(all(self.systemd.rows[t]["ActiveState"] == "active" for t in u.TIMERS))

    def test_manual_writer_after_draining_is_still_rejected_and_timers_resume(self):
        def emit(name, value):
            if name == "phase" and value == "apply_existing_guard_once_no_retry":
                self.append_effect()
        with self.assertRaisesRegex(ValueError, "Release switch review changed"):
            u.safe_upgrade(self.release, self.systemd, self.target, apply=True, emit=emit)
        self.assertEqual(json.loads(self.metadata.read_text())["current_revision"], self.old)
        self.assertTrue(all(self.systemd.rows[t]["ActiveState"] == "active" for t in u.TIMERS))


if __name__ == "__main__":
    unittest.main()
