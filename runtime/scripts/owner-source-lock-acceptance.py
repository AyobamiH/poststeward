#!/usr/bin/env python3
"""Owner-host acceptance for the 0.28.18+ source-lock starvation fix.

This harness intentionally uses private temporary config/state roots. It exercises
real POSIX flock contention and the production controlled_refresh wrapper without
touching live Post-Once state, systemd units, provider APIs, schedules or receipts.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import time
from threading import Event
from typing import Any

UTC = timezone.utc


def _git_sha(root: Path) -> str:
    result = subprocess.run(
        ["git", "-C", str(root), "rev-parse", "HEAD"],
        capture_output=True, text=True, check=True, timeout=15,
    )
    return result.stdout.strip()


def _version(root: Path) -> str:
    text = (root / "pyproject.toml").read_text(encoding="utf-8")
    match = re.search(r'^version\s*=\s*"([^"]+)"', text, re.MULTILINE)
    return match.group(1) if match else "unknown"


def _runtime_campaign_fingerprint(state_root: Path) -> str:
    root = state_root / "runtime-campaigns"
    digest = hashlib.sha256()
    if not root.exists():
        digest.update(b"missing")
        return digest.hexdigest()
    for path in sorted(p for p in root.rglob("*") if p.is_file()):
        digest.update(path.relative_to(root).as_posix().encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def _reasons(result: dict[str, Any]) -> list[str]:
    found: list[str] = []
    for field in ("projects", "admission"):
        for row in result.get(field, []) if isinstance(result.get(field), list) else []:
            if isinstance(row, dict):
                values = row.get("reasons")
                if isinstance(values, list):
                    found.extend(str(value) for value in values)
    return sorted(set(found))


def _isolated_environment(root: Path) -> tuple[Path, Path]:
    config = root / "config"
    state = root / "state"
    config.mkdir(mode=0o700)
    state.mkdir(mode=0o700)
    os.environ["OCPF_POST_CONFIG_DIR"] = str(config)
    os.environ["OCPF_POST_STATE_DIR"] = str(state)
    # This acceptance is lock/liveness only. Do not invoke optional model supply.
    os.environ["OCPF_POST_GENERATIVE_SUPPLY_ENABLED"] = "0"
    os.environ.pop("OCPF_POST_OPENAI_API_KEY", None)
    return config, state


def run_acceptance(
    release_root: Path,
    *,
    success_hold_seconds: float = 0.25,
    success_timeout_seconds: float = 2.0,
    failure_hold_seconds: float = 0.75,
    failure_timeout_seconds: float = 0.10,
) -> dict[str, Any]:
    if not (release_root / "src" / "ocpf_post" / "admission_runtime.py").is_file():
        raise RuntimeError("release_root_does_not_look_like_post_once")
    if not (0.05 <= success_hold_seconds < success_timeout_seconds <= 10.0):
        raise ValueError("invalid_success_timing")
    if not (0.05 <= failure_timeout_seconds < failure_hold_seconds <= 10.0):
        raise ValueError("invalid_failure_timing")

    sys.path.insert(0, str(release_root / "src"))
    from ocpf_post import admission_runtime
    from ocpf_post.runtime_sources import source_lock

    report: dict[str, Any] = {
        "schema_version": 1,
        "observed_at": datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "git_commit_sha": _git_sha(release_root),
        "version": _version(release_root),
        "platform": sys.platform,
        "tests": {},
        "boundary": (
            "ISOLATED OWNER-HOST ACCEPTANCE: temporary config/state only; "
            "no systemd mutation, provider call, schedule, receipt, replay or live-state write."
        ),
    }

    with tempfile.TemporaryDirectory(prefix="post-once-source-lock-acceptance-") as temp:
        temp_root = Path(temp)
        _, state_root = _isolated_environment(temp_root)

        # Test 1: a real holder owns the exact source lock. The production wrapper
        # must remain waiting, then acquire after release instead of returning
        # source_operation_active.
        success_entered = Event()
        success_release = Event()

        def success_holder() -> None:
            with source_lock(operation="owner-acceptance-holder"):
                success_entered.set()
                if not success_release.wait(timeout=success_timeout_seconds + 5):
                    raise RuntimeError("success_holder_release_timeout")

        original_wait = admission_runtime.SOURCE_OPERATION_WAIT_SECONDS
        admission_runtime.SOURCE_OPERATION_WAIT_SECONDS = success_timeout_seconds
        try:
            with ThreadPoolExecutor(max_workers=2) as executor:
                held = executor.submit(success_holder)
                if not success_entered.wait(timeout=2):
                    raise RuntimeError("success_holder_never_acquired")
                started = time.monotonic()
                waiting = executor.submit(admission_runtime.controlled_refresh, apply=True)
                time.sleep(success_hold_seconds)
                completed_before_release = waiting.done()
                success_release.set()
                result = waiting.result(timeout=success_timeout_seconds + 5)
                elapsed = time.monotonic() - started
                held.result(timeout=2)
        finally:
            success_release.set()
            admission_runtime.SOURCE_OPERATION_WAIT_SECONDS = original_wait

        success_reasons = _reasons(result)
        success_pass = (
            not completed_before_release
            and "source_operation_active" not in success_reasons
            and elapsed >= success_hold_seconds * 0.75
        )
        report["tests"]["wait_then_acquire"] = {
            "pass": success_pass,
            "completed_before_release": completed_before_release,
            "elapsed_seconds": round(elapsed, 3),
            "reasons": success_reasons,
            "project_status_counts": _status_counts(result),
        }

        # Test 2: if the holder outlives the bounded budget, preserve the existing
        # fail-closed result and prove the isolated runtime-campaign tree is unchanged.
        timeout_entered = Event()
        timeout_release = Event()

        def timeout_holder() -> None:
            with source_lock(operation="owner-acceptance-timeout-holder"):
                timeout_entered.set()
                if not timeout_release.wait(timeout=failure_hold_seconds + 5):
                    raise RuntimeError("timeout_holder_release_timeout")

        before = _runtime_campaign_fingerprint(state_root)
        original_wait = admission_runtime.SOURCE_OPERATION_WAIT_SECONDS
        admission_runtime.SOURCE_OPERATION_WAIT_SECONDS = failure_timeout_seconds
        try:
            with ThreadPoolExecutor(max_workers=1) as executor:
                held = executor.submit(timeout_holder)
                if not timeout_entered.wait(timeout=2):
                    raise RuntimeError("timeout_holder_never_acquired")
                started = time.monotonic()
                timeout_result = admission_runtime.controlled_refresh(apply=True)
                timeout_elapsed = time.monotonic() - started
                timeout_release.set()
                held.result(timeout=2)
        finally:
            timeout_release.set()
            admission_runtime.SOURCE_OPERATION_WAIT_SECONDS = original_wait
        after = _runtime_campaign_fingerprint(state_root)

        timeout_reasons = _reasons(timeout_result)
        timeout_pass = (
            "source_operation_active" in timeout_reasons
            and before == after
            and timeout_elapsed >= failure_timeout_seconds * 0.75
            and timeout_elapsed < failure_hold_seconds
        )
        report["tests"]["bounded_timeout_no_campaign_write"] = {
            "pass": timeout_pass,
            "elapsed_seconds": round(timeout_elapsed, 3),
            "reasons": timeout_reasons,
            "runtime_campaign_fingerprint_unchanged": before == after,
            "project_status_counts": _status_counts(timeout_result),
        }

    report["pass"] = all(
        bool(row.get("pass")) for row in report["tests"].values()
        if isinstance(row, dict)
    )
    report["consequence"] = "NO_LIVE_PROVIDER_OR_STATE_EFFECT"
    return report


def _status_counts(result: dict[str, Any]) -> dict[str, int]:
    counts: dict[str, int] = {}
    rows = result.get("projects")
    if isinstance(rows, list):
        for row in rows:
            if not isinstance(row, dict):
                continue
            status = str(row.get("status") or "unknown")
            counts[status] = counts.get(status, 0) + 1
    return dict(sorted(counts.items()))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--release-root",
        default=str(Path(__file__).resolve().parents[1]),
        help="Post-Once checkout/worktree whose code should be exercised",
    )
    parser.add_argument("--output", help="Optional JSON report path")
    args = parser.parse_args()

    root = Path(args.release_root).expanduser().resolve()
    report = run_acceptance(root)
    output = (
        Path(args.output).expanduser().resolve()
        if args.output
        else Path.home() / (
            "post-once-source-lock-acceptance-"
            + datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
            + ".json"
        )
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    try:
        output.chmod(0o600)
    except OSError:
        pass

    print("git_commit_sha=" + report["git_commit_sha"])
    print("version=" + report["version"])
    for name, row in report["tests"].items():
        print(name + "=" + ("PASS" if row["pass"] else "FAIL"))
        print(name + "_elapsed_seconds=" + str(row["elapsed_seconds"]))
        print(name + "_reasons=" + json.dumps(row["reasons"]))
    print("overall=" + ("PASS" if report["pass"] else "FAIL"))
    print("consequence=" + report["consequence"])
    print("report=" + str(output))
    return 0 if report["pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
