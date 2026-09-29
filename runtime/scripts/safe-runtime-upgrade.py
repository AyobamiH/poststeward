#!/usr/bin/env python3
"""Drain the managed timers before making a state-bound runtime upgrade review.

Uses the existing runtime_release preview/switch guard unchanged. It does not
publish, clear locks, repair evidence, or retry a rejected apply. Timer wake-ups
are restored in finally; already-running services are never stopped or killed.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import fcntl
import json
import os
from pathlib import Path
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from typing import Any, Callable
from urllib.error import URLError
from urllib.request import urlopen

NAMES = ("run-due", "portfolio-refill", "collection", "replies")
TIMERS = tuple(f"post-once-{name}.timer" for name in NAMES)
SERVICES = tuple(f"post-once-{name}.service" for name in NAMES)
IDLE = {"inactive", "failed"}
SHA = re.compile(r"[0-9a-f]{40}")
STAGES = ["supply", "admission", "automatic_schedule", "execute", "provider", "verify"]


class UpgradeError(RuntimeError):
    """Messages are fixed local diagnostics, never raw provider output."""


class Systemd:
    def _run(self, *args: str) -> str:
        result = subprocess.run(
            ["systemctl", "--user", *args], text=True, capture_output=True,
            timeout=30, check=False,
        )
        if result.returncode:
            raise UpgradeError("systemctl_failed:" + args[0])
        return result.stdout

    def show(self, unit: str) -> dict[str, str]:
        text = self._run(
            "show", unit, "--no-pager",
            "--property=LoadState,ActiveState,MainPID,ControlPID,PartOf,BindsTo,PropagatesStopTo,Triggers",
        )
        value = dict(line.split("=", 1) for line in text.splitlines() if "=" in line)
        if not value.get("LoadState") or not value.get("ActiveState"):
            raise UpgradeError("unit_state_unreadable:" + unit)
        return value

    def timers(self) -> set[str]:
        text = self._run(
            "list-units", "--all", "--type=timer", "--plain", "--no-legend",
            "--no-pager", "post-once-*.timer",
        )
        return {line.split()[0] for line in text.splitlines() if line.strip()}

    def stop(self, unit: str) -> None:
        if unit not in TIMERS:
            raise UpgradeError("refusing_to_stop_non_timer")
        self._run("stop", unit)

    def start(self, unit: str) -> None:
        if unit not in TIMERS:
            raise UpgradeError("refusing_to_start_non_timer")
        self._run("start", unit)


def managed_state(systemd: Any) -> dict[str, dict[str, str]]:
    extra = systemd.timers() - set(TIMERS)
    if extra:
        raise UpgradeError("unrecognised_post_once_timers:" + ",".join(sorted(extra)))
    original = {unit: systemd.show(unit) for unit in TIMERS + SERVICES}
    # The existing release installer starts these four timers. Refuse to use it
    # on a deliberately inactive/missing timer rather than silently enable it.
    for timer, service in zip(TIMERS, SERVICES):
        row = original[timer]
        if row["LoadState"] != "loaded" or row["ActiveState"] != "active":
            raise UpgradeError("timer_must_already_be_active:" + timer)
        if original[service]["LoadState"] != "loaded":
            raise UpgradeError("managed_service_missing:" + service)
        if row.get("Triggers", "").split() != [service]:
            raise UpgradeError("timer_trigger_changed:" + timer)
        if row.get("PropagatesStopTo", "").strip():
            raise UpgradeError("timer_stop_propagation_present:" + timer)
        # A user drop-in could otherwise make stopping a timer stop its worker.
        for dependency in ("PartOf", "BindsTo"):
            if set(original[service].get(dependency, "").split()) & set(TIMERS):
                raise UpgradeError("service_stop_dependency_present:" + service)
    return original


def safe_upgrade(
    release: Any, systemd: Any, revision: str, *, apply: bool = False,
    drain_timeout: int = 3600, emit: Callable[..., None] = print,
    clock: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
    cancelled: Callable[[], bool] = lambda: False,
) -> dict[str, Any]:
    if not SHA.fullmatch(revision):
        raise UpgradeError("exact_40_character_commit_required")
    if not 1 <= drain_timeout <= 3600:
        raise UpgradeError("drain_timeout_must_be_1_to_3600_seconds")
    if cancelled():
        raise UpgradeError("cancelled_before_runtime_switch")
    status = release.status()
    saved = status.get("release_state") or {}
    if saved.get("status") == "switching":
        raise UpgradeError("previous_switch_incomplete_inspect_before_retry")
    if not apply:
        return release.preview(revision)
    if saved.get("status") != "switched":
        raise UpgradeError("managed_release_metadata_required")
    if status.get("active_runtime_revision") == revision:
        return {"status": "already_active", "current_revision": revision, "applied": False}

    original = managed_state(systemd)
    emit("timer_state_before", original)
    resume: list[str] = []
    try:
        emit("phase", "pause_timer_wakeups_only")
        for timer in TIMERS:
            if cancelled():
                raise UpgradeError("cancelled_before_runtime_switch")
            # Record intent before stopping: restore even when stop partly fails.
            resume.append(timer)
            systemd.stop(timer)
        for timer in TIMERS:
            if systemd.show(timer)["ActiveState"] != "inactive":
                raise UpgradeError("timer_not_quiet:" + timer)

        emit("timer_wakeups_paused", True)
        emit("drain_timeout_seconds", drain_timeout)
        emit("phase", "allow_running_services_to_finish")
        deadline = clock() + drain_timeout
        last_report = -float("inf")
        while True:
            if cancelled():
                raise UpgradeError("cancelled_before_runtime_switch")
            busy = []
            for service in SERVICES:
                row = systemd.show(service)
                if row["LoadState"] != "loaded":
                    raise UpgradeError("service_disappeared:" + service)
                if (row["ActiveState"] not in IDLE
                        or row.get("MainPID", "0") != "0"
                        or row.get("ControlPID", "0") != "0"):
                    busy.append(service)
            if not busy:
                break
            if clock() >= deadline:
                raise UpgradeError("drain_timeout_running_services_left_untouched")
            if clock() - last_report >= 10:
                emit("running_services", busy)
                last_report = clock()
            sleep(1)

        emit("phase", "fresh_review_after_services_are_idle")
        review = release.preview(revision)
        emit("runtime_preview", review)
        if review.get("status") != "preview" or review.get("target_revision") != revision:
            raise UpgradeError("fresh_review_blocked_or_wrong_target")
        digest = review.get("review_sha256")
        if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
            raise UpgradeError("fresh_review_digest_invalid")
        if cancelled():
            raise UpgradeError("cancelled_before_runtime_switch")
        emit("phase", "apply_existing_guard_once_no_retry")
        result = release.switch(revision, apply=True, expected_sha256=digest)
        emit("runtime_apply", result)
        if result.get("status") != "switched" or result.get("current_revision") != revision:
            raise UpgradeError("release_did_not_report_target_active")
        after = release.status()
        if (after.get("active_runtime_revision") != revision
                or (after.get("release_state") or {}).get("status") != "switched"):
            raise UpgradeError("release_metadata_after_switch_mismatch")
        return result
    finally:
        # No service stop, kill, disable, reset-failed, refill or publish command.
        emit("phase", "restore_original_timer_wakeups")
        errors = []
        for timer in resume:
            try:
                if systemd.show(timer)["ActiveState"] != "active":
                    systemd.start(timer)
                if systemd.show(timer)["ActiveState"] != "active":
                    errors.append(timer)
            except Exception:
                errors.append(timer)
        emit("timers_restored", not bool(errors))
        if errors:
            emit("timer_restore_failed", errors)
            raise UpgradeError("timer_restore_failed_inspect_systemd:" + ",".join(errors))


@contextmanager
def release_lock(repo: Path):
    value = subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "--git-common-dir"],
        capture_output=True, text=True, check=True, timeout=15,
    ).stdout.strip()
    common = Path(value)
    if not common.is_absolute():
        common = repo / common
    lock_path = common / "post-once-safe-upgrade.lock"
    descriptor = os.open(lock_path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    try:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise UpgradeError("another_safe_upgrade_is_running") from exc
        yield
    finally:
        os.close(descriptor)


def check_console(revision: str, port: int, emit: Callable[..., None],
                  *, readiness_seconds: float = 15.0,
                  sleep: Callable[[float], None] = time.sleep,
                  clock: Callable[[], float] = time.monotonic) -> dict[str, Any]:
    if not 0 <= readiness_seconds <= 60:
        raise UpgradeError("console_readiness_seconds_must_be_0_to_60")

    def fetch(path: str, timeout: int):
        start = clock()
        with urlopen(f"http://127.0.0.1:{port}/{path}", timeout=timeout) as response:
            value = json.loads(response.read())
        return value, round(clock() - start, 3)

    # systemd Type=simple considers the restarted process active before Python has
    # necessarily bound the loopback socket. Treat connection refusal during this
    # bounded startup window as readiness, never as a reason to repeat the switch.
    deadline = clock() + readiness_seconds
    attempts = 0
    while True:
        attempts += 1
        try:
            health, health_seconds = fetch("healthz", 5)
            break
        except URLError:
            if clock() >= deadline:
                raise
            sleep(0.25)
    emit("console_readiness_attempts", attempts)
    emit("healthz_seconds", health_seconds)
    if health.get("status") != "ok":
        raise UpgradeError("console_health_not_ok")
    first, duration = fetch("api/snapshot", 30)
    emit("snapshot_http_seconds_first", duration)
    observed = (first.get("runtime") or {}).get("git_commit_sha")
    emit("console_runtime_revision", observed)
    if observed != revision:
        raise UpgradeError("console_process_not_running_target_revision")
    ids = [row.get("id") for row in (first.get("operator") or {}).get("stages", [])]
    if ids != STAGES:
        raise UpgradeError("console_operator_model_mismatch")
    integrity = first.get("state_integrity") or {}
    emit("state_integrity", integrity)
    if (integrity.get("scope") != "critical_ledgers_only"
            or integrity.get("status") != "observed"
            or integrity.get("invalid_count") != 0 or "files" in integrity):
        raise UpgradeError("console_critical_integrity_attention_or_wrong_scope")
    emit("projection_timing_ms", first.get("projection_timing_ms") or {})
    emit("volume_diagnosis", (first.get("operator") or {}).get("diagnosis"))
    emit("reserve", (first.get("operator") or {}).get("reserve"))
    second, duration = fetch("api/snapshot", 10)
    emit("snapshot_http_seconds_second", duration)
    # State can legitimately advance at cache expiry; a new revision is not failure.
    emit("snapshot_revision_same", first.get("revision") == second.get("revision"))
    if (second.get("runtime") or {}).get("git_commit_sha") != revision:
        raise UpgradeError("second_console_runtime_mismatch")
    emit("LIVE_CONSOLE_ACCEPTANCE", "PASS")
    return first


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--revision", required=True, help="Exact locally available Git commit")
    parser.add_argument("--apply", action="store_true", help="Pause/drain, review and switch")
    parser.add_argument("--drain-timeout", type=int, default=3600)
    parser.add_argument("--check-console", action="store_true")
    parser.add_argument("--port", type=int, default=8877)
    parser.add_argument("--open-log", action="store_true")
    args = parser.parse_args()
    if not SHA.fullmatch(args.revision) or not 1024 <= args.port <= 65535:
        parser.error("Use an exact 40-character revision and port 1024..65535")
    if args.check_console and not args.apply:
        parser.error("--check-console requires --apply")
    if not 1 <= args.drain_timeout <= 3600:
        parser.error("--drain-timeout must be 1..3600")
    repo = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(repo / "src"))
    sys.dont_write_bytecode = True
    os.environ.setdefault("GIT_OPTIONAL_LOCKS", "0")
    from ocpf_post.product_runtime import apply_environment
    apply_environment()
    from ocpf_post import runtime_release
    from ocpf_post.state import config_dir, state_dir

    # Logs must not be created inside the tree included in the review digest.
    home = Path.home().resolve()
    for root in (state_dir().resolve(), config_dir().resolve()):
        if home == root or root in home.parents:
            raise UpgradeError("home_is_inside_live_state_choose_external_log_location")
    output = Path(tempfile.mkdtemp(prefix="post-once-safe-upgrade-", dir=home))
    log_path = output / "summary.txt"
    print("OUTPUT_SAVED=" + str(log_path), flush=True)
    print("ARTIFACT_DIR=" + str(output), flush=True)
    code = 0
    with log_path.open("x", encoding="utf-8") as log:
        os.chmod(log_path, 0o600)
        def emit(name: str, value: Any) -> None:
            line = name + "=" + (value if isinstance(value, str) else json.dumps(value, sort_keys=True))
            print(line, flush=True)
            log.write(line + "\n")
            log.flush()
        emit("observed_at", datetime.now(timezone.utc).isoformat())
        emit("target_revision", args.revision)
        deferred = []
        def defer(signum, _frame):
            deferred.append(signum)
            emit("signal_deferred_until_timers_restored", signum)
        try:
            with release_lock(repo):
                prior = {sig: signal.signal(sig, defer) for sig in (signal.SIGINT, signal.SIGTERM)}
                try:
                    result = safe_upgrade(
                        runtime_release, Systemd(), args.revision, apply=args.apply,
                        drain_timeout=args.drain_timeout, emit=emit,
                        cancelled=lambda: bool(deferred),
                    )
                finally:
                    for sig, handler in prior.items():
                        signal.signal(sig, handler)
                emit("result", result)
            if args.apply:
                emit("RUNTIME_SWITCH", "PASS")
            if args.check_console and not deferred:
                try:
                    snapshot = check_console(args.revision, args.port, emit)
                    path = output / "live-snapshot.json"
                    with path.open("x", encoding="utf-8") as handle:
                        os.chmod(path, 0o600)
                        json.dump(snapshot, handle, indent=2, ensure_ascii=False)
                except Exception as exc:
                    emit("console_check", "FAIL")
                    emit("console_error_type", type(exc).__name__)
                    if isinstance(exc, UpgradeError):
                        emit("console_error", str(exc))
                    emit("boundary", "Upgrade may have succeeded; do not repeat it for a console-check failure")
                    code = 3
        except Exception as exc:
            emit("UPGRADE", "STOPPED")
            emit("error_type", type(exc).__name__)
            # Only fixed helper diagnostics or the known guard text are public.
            if isinstance(exc, UpgradeError) or str(exc) == "Release switch review changed":
                emit("reason", str(exc))
            emit("automatic_retry", False)
            code = 2
        if deferred:
            emit("deferred_signals", deferred)
        emit("exit_code", code)
    print("OUTPUT_SAVED=" + str(log_path), flush=True)
    if args.open_log and shutil.which("notepad.exe") and shutil.which("wslpath"):
        try:
            windows_path = subprocess.run(
                ["wslpath", "-w", str(log_path)], capture_output=True, text=True,
                check=True, timeout=10,
            ).stdout.strip()
            subprocess.Popen(
                ["notepad.exe", windows_path], stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True,
            )
        except (OSError, subprocess.SubprocessError):
            print("OPEN_LOG=unavailable; use OUTPUT_SAVED", flush=True)
    return code


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except UpgradeError as exc:
        print("UPGRADE_STOPPED=" + str(exc), file=sys.stderr)
        raise SystemExit(2)
