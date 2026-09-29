#!/usr/bin/env python3
"""Review/enable steady original-content pacing using the owner's saved amounts."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from ocpf_post.release_pacing import apply, review  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="Change only the saved release policy after review")
    parser.add_argument("--expected-sha256", help="Exact review_sha256 returned by the preview")
    parser.add_argument("--use-saved-amounts", action="store_true",
                        help="Explicitly authorise the saved daily amounts; review and apply without choosing new numbers")
    parser.add_argument("--evidence-dir", type=Path, help="New private folder outside live config/state")
    parser.add_argument("--open-log", action="store_true", help="Open a saved result in WSL Notepad when available")
    args = parser.parse_args()
    if args.apply and not (args.expected_sha256 or args.use_saved_amounts):
        parser.error("--apply requires --expected-sha256 or explicit --use-saved-amounts")
    if args.expected_sha256 and args.use_saved_amounts:
        parser.error("Choose either an exact review SHA or the saved-amounts authorisation, not both")
    if args.use_saved_amounts and not args.apply:
        parser.error("--use-saved-amounts requires --apply")
    if not args.apply and args.expected_sha256:
        parser.error("--expected-sha256 requires --apply")
    if not args.apply and (args.evidence_dir or args.open_log):
        parser.error("Preview is read-only; evidence options require --apply")
    evidence = args.evidence_dir or Path.home() / (
        "post-once-steady-release-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    )
    try:
        expected = review()["review_sha256"] if args.use_saved_amounts else args.expected_sha256
        value = apply(expected, evidence_dir=evidence) if args.apply else review()
    except (OSError, ValueError, RuntimeError, KeyError, TypeError) as exc:
        print(json.dumps({"status": "error", "error_type": type(exc).__name__, "detail": str(exc),
                          "boundary": "No blind retry. Inspect the error and any saved backup before another apply."}, indent=2))
        return 2
    print(json.dumps(value, indent=2, ensure_ascii=False, allow_nan=False))
    if args.apply and args.open_log and value.get("applied"):
        result_path = evidence / "result.json"
        try:
            converted = subprocess.run(["wslpath", "-w", str(result_path)], capture_output=True,
                                       text=True, check=True, timeout=5)
            subprocess.Popen(["notepad.exe", converted.stdout.strip()], stdin=subprocess.DEVNULL,
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
        except (OSError, subprocess.SubprocessError):
            print(f"Result saved: {result_path}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
