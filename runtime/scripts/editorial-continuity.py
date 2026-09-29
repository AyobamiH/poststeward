#!/usr/bin/env python3
"""Maintain local editorial demand continuity; never publish or approve copy."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import importlib.util
import json
from pathlib import Path
import runpy
import sys

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))


def supply_module():
    spec = importlib.util.spec_from_file_location("post_once_editorial_supply", ROOT / "scripts/editorial-supply.py")
    if spec is None or spec.loader is None:
        raise RuntimeError("Editorial supply loader unavailable")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--batch", type=Path)
    parser.add_argument("--expected-sha256")
    args = parser.parse_args()
    now = datetime.now(timezone.utc)
    supply = supply_module()
    from ocpf_post import editorial_continuity as continuity
    try:
        if args.batch:
            packet = supply.load_report(args.batch)
            reviewed = supply.review_batch(packet)
            result = continuity.record_assessments(
                packet, reviewed, apply=args.apply, expected_sha256=args.expected_sha256, now=now,
            )
        else:
            coverage = runpy.run_path(str(ROOT / "scripts/audit-portfolio-coverage.py"))["collect"](now)
            workpack = supply.build_workpack(coverage, now)
            result = continuity.reconcile(workpack, now=now, apply=args.apply)
            result["audience_evidence"] = continuity.audience_evidence(now=now)
        code = 0
    except (OSError, ValueError, KeyError, TypeError, AttributeError, RuntimeError) as exc:
        result = {"schema_version": 1, "status": "unavailable", "error_type": type(exc).__name__,
                  "publishing_unchanged": True,
                  "boundary": "Editorial continuity failed closed. No publication, scheduling, approval, account, quota or provider state was changed."}
        code = 2
    print(json.dumps(result, indent=2, ensure_ascii=False, allow_nan=False))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
