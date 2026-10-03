#!/usr/bin/env python3
"""Run the bounded rolling-supply desired-state controller.

This is a local inventory/scheduling controller. It never executes run-due or a
social-provider publication command.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.dont_write_bytecode = True

from ocpf_post.rolling_supply import reconcile


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--horizon-minutes", type=int, default=75)
    parser.add_argument("--max-passes", type=int, default=1)
    args = parser.parse_args()
    try:
        value = reconcile(
            apply=args.apply,
            horizon_minutes=args.horizon_minutes,
            max_passes=args.max_passes,
        )
    except (OSError, ValueError, RuntimeError, KeyError, TypeError) as exc:
        value = {
            "schema_version": 1,
            "status": "unavailable",
            "error_type": type(exc).__name__,
            "apply": args.apply,
            "boundary": (
                "Rolling supply controller failed closed. No run-due, provider publication, "
                "quota change or paid-model fallback was attempted."
            ),
        }
        print(json.dumps(value, indent=2, ensure_ascii=False))
        return 2
    print(json.dumps(value, indent=2, ensure_ascii=False, allow_nan=False))
    if value.get("status") in {"adequate", "recovering", "no_enabled_destinations", "preview"}:
        return 0
    exact = value.get("exit_code")
    if type(exact) is int and 1 <= exact <= 125:
        return exact
    return 3 if str(value.get("status", "")).startswith("blocked") else 2


if __name__ == "__main__":
    raise SystemExit(main())

