#!/usr/bin/env python3
"""Review/apply the owner-approved 24/24/20 steady publication pace."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from ocpf_post import local_store  # noqa: E402
from ocpf_post.portfolio import policy_file, validate_policy  # noqa: E402
from ocpf_post.release_pacing import MARKER, policy_lock  # noqa: E402
from ocpf_post.state import config_dir, state_dir  # noqa: E402

TARGETS = {"x": 24, "threads": 24, "linkedin": 20}


def _read(path: Path) -> bytes:
    if path.is_symlink() or not path.is_file():
        raise ValueError("A real saved portfolio policy is required")
    return path.read_bytes()


def _decode(raw: bytes) -> dict:
    value = json.loads(raw)
    if not isinstance(value, dict):
        raise ValueError("Portfolio policy must be an object")
    return value


def _digest(value) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    ).hexdigest()


def proposal(path: Path | None = None) -> dict:
    path = path or policy_file()
    raw = _read(path)
    before = validate_policy(_decode(raw))
    after = json.loads(json.dumps(before))
    if set(after.get("providers") or {}) != set(TARGETS):
        raise ValueError("Expected exactly x, threads and linkedin provider policies")
    for provider, target in TARGETS.items():
        settings = after["providers"][provider]
        ceiling = int(settings.get("hard_daily_ceiling", 100))
        if target > ceiling:
            raise ValueError(f"{provider} target exceeds its saved hard ceiling")
        settings["daily_target"] = target
        settings["flow_mode"] = "fixed"
    after["release_pacing"] = dict(MARKER)
    after = validate_policy(after)
    identity = {
        "schema_version": 1,
        "operation": "steady_targets_24_24_20",
        "policy_path": str(path.resolve()),
        "policy_sha256": hashlib.sha256(raw).hexdigest(),
        "proposed_policy_sha256": _digest(after),
        "targets": TARGETS,
    }
    return {
        **identity,
        "review_sha256": _digest(identity),
        "status": "already_applied" if before == after else "preview",
        "before_targets": {p: before["providers"][p]["daily_target"] for p in TARGETS},
        "after_targets": dict(TARGETS),
        "hard_daily_ceilings": {
            p: after["providers"][p].get("hard_daily_ceiling", after["providers"][p]["daily_target"])
            for p in TARGETS
        },
        "boundary": (
            "Changes only saved normal logical-publication pace to 24/24/20. "
            "Existing schedules, receipts, copy, windows, credentials and the 100/account/day "
            "hard safety ceiling are retained."
        ),
    }


def apply(review_sha256: str, *, evidence_dir: Path) -> dict:
    path = policy_file()
    evidence = evidence_dir.resolve()
    for live in (config_dir().resolve(), state_dir().resolve()):
        if evidence == live or live in evidence.parents:
            raise ValueError("Evidence directory must be outside live config/state")
    with policy_lock(path):
        current = proposal(path)
        if current["review_sha256"] != review_sha256:
            raise ValueError("Steady-target review changed; nothing applied")
        if current["status"] == "already_applied":
            return {**current, "applied": False}
        raw = _read(path)
        if hashlib.sha256(raw).hexdigest() != current["policy_sha256"]:
            raise ValueError("Portfolio policy changed before apply")
        before = validate_policy(_decode(raw))
        after = json.loads(json.dumps(before))
        for provider, target in TARGETS.items():
            after["providers"][provider]["daily_target"] = target
            after["providers"][provider]["flow_mode"] = "fixed"
        after["release_pacing"] = dict(MARKER)
        after = validate_policy(after)

        evidence.mkdir(mode=0o700, parents=False, exist_ok=False)
        backup = evidence / "portfolio-policy.before.json"
        with backup.open("xb") as handle:
            os.chmod(backup, 0o600)
            handle.write(raw)
            handle.flush()
            os.fsync(handle.fileno())

        if _read(path) != raw:
            raise ValueError("Portfolio policy changed before commit; backup retained")
        local_store.write(path, after)
        if validate_policy(_decode(_read(path))) != after:
            raise ValueError("Policy write readback failed")
        result = {
            **current,
            "status": "applied",
            "applied": True,
            "backup_path": str(backup),
            "policy_sha256_after": hashlib.sha256(_read(path)).hexdigest(),
        }
        local_store.write(evidence / "result.json", result)
        return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--expected-sha256")
    parser.add_argument("--approve-24-24-20", action="store_true")
    parser.add_argument("--evidence-dir", type=Path)
    parser.add_argument("--open-log", action="store_true")
    args = parser.parse_args()

    if args.apply and not (args.expected_sha256 or args.approve_24_24_20):
        parser.error("--apply requires --expected-sha256 or --approve-24-24-20")
    if args.expected_sha256 and args.approve_24_24_20:
        parser.error("Choose one approval form")
    if not args.apply and (args.expected_sha256 or args.approve_24_24_20):
        parser.error("Approval flags require --apply")

    try:
        preview = proposal()
        if not args.apply:
            value = preview
        else:
            expected = preview["review_sha256"] if args.approve_24_24_20 else args.expected_sha256
            evidence = args.evidence_dir or Path.home() / (
                "post-once-steady-targets-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
            )
            value = apply(str(expected), evidence_dir=evidence)
    except (OSError, ValueError, RuntimeError, KeyError, TypeError) as exc:
        print(json.dumps({
            "status": "error",
            "error_type": type(exc).__name__,
            "detail": str(exc),
            "boundary": "No blind retry; inspect the saved policy/evidence first.",
        }, indent=2))
        return 2

    print(json.dumps(value, indent=2, ensure_ascii=False, allow_nan=False))
    if args.apply and args.open_log and value.get("applied"):
        result_path = Path(value["backup_path"]).parent / "result.json"
        try:
            converted = subprocess.run(
                ["wslpath", "-w", str(result_path)],
                capture_output=True, text=True, check=True, timeout=5,
            )
            subprocess.Popen(
                ["notepad.exe", converted.stdout.strip()],
                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL, start_new_session=True,
            )
        except (OSError, subprocess.SubprocessError):
            pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
