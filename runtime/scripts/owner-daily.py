#!/usr/bin/env python3
"""Read local Post-Once publications for a London calendar day. No provider calls.

Repository reporting helper; no provider calls or runtime upgrade.
Uses the existing completed-publication read model and current local manifests.
"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import date, datetime, timezone
import hashlib
import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path
import sys
from typing import Any, Callable
from zoneinfo import ZoneInfo

PROVIDERS = ("x", "threads", "linkedin")


def mapping(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def collect(day: date, now: datetime, publications: dict,
            get_manifest: Callable, get_text: Callable) -> dict:
    zone = ZoneInfo("Europe/London")
    rows: list[dict] = []
    manifests: dict[str, dict] = {}
    for publication in publications.values():
        receipt = mapping(publication.get("receipt"))
        at = publication.get("at")
        provider = receipt.get("provider")
        if provider not in PROVIDERS or not isinstance(at, datetime) or at.tzinfo is None:
            continue
        if at > now or at.astimezone(zone).date() != day:
            continue
        campaign = str(receipt.get("campaign") or "")
        metadata_error = None
        try:
            if campaign not in manifests:
                manifests[campaign] = mapping(get_manifest(campaign))
            manifest = manifests[campaign]
        except (OSError, ValueError, KeyError, TypeError, RuntimeError) as exc:
            manifest = {}
            metadata_error = type(exc).__name__
        vault = mapping(manifest.get("vault"))
        source = mapping(manifest.get("source"))
        brief = mapping(manifest.get("evidence_brief"))
        text = None
        copy_status = "local_copy_unavailable"
        try:
            local_text = get_text(campaign, provider)
            if isinstance(local_text, str):
                digest = hashlib.sha256(local_text.encode("utf-8")).hexdigest()
                if receipt.get("text_sha256") == digest:
                    text, copy_status = local_text, "matches_receipt_sha256"
                else:
                    copy_status = "current_copy_not_proven_to_match_receipt"
        except (OSError, ValueError, KeyError, TypeError, RuntimeError):
            pass
        rows.append({
            "published_at_local": at.astimezone(zone).isoformat(),
            "provider": provider,
            "account_id": receipt.get("account_id"),
            "account_label": receipt.get("username"),
            "campaign": campaign,
            "project_from_current_manifest": manifest.get("project"),
            "title_from_current_manifest": manifest.get("title"),
            "schedule_id": receipt.get("schedule_id"),
            "post_id": receipt.get("post_id"),
            "url": receipt.get("url"),
            "effective_verified": bool(publication.get("effective_verified")),
            "verification_basis": publication.get("verification_basis"),
            "receipt_status": receipt.get("ledger_status") or receipt.get("status"),
            "text_sha256": receipt.get("text_sha256"),
            "vault": {k: vault[k] for k in (
                "id", "document_id", "tab_id", "key", "base_campaign", "revision"
            ) if k in vault},
            "source": {k: source[k] for k in (
                "type", "source_id", "repository", "path", "source_sha", "observed_at"
            ) if k in source},
            "brief_id": brief.get("brief_id"),
            "runtime_generated": manifest.get("runtime_generated"),
            "provenance_basis": "current_local_manifest_not_historical_attestation",
            "metadata_error_type": metadata_error,
            "copy_status": copy_status,
            "published_text_matching_receipt": text,
        })
    rows.sort(key=lambda row: (PROVIDERS.index(row["provider"]), row["published_at_local"], row["campaign"]))
    counts = Counter(row["provider"] for row in rows)
    verified = Counter(row["provider"] for row in rows if row["effective_verified"])
    return {
        "date": day.isoformat(), "timezone": "Europe/London",
        "publication_cutoff_utc": now.astimezone(timezone.utc).isoformat(),
        "scope": "recognised_completed_campaign_publications_not_replies_or_partial_effects",
        "time_basis": "first_local_creation_receipt_not_later_readback",
        "counts": {p: {"published": counts[p], "verified": verified[p],
                        "published_unverified": counts[p] - verified[p]} for p in PROVIDERS},
        "publications": rows,
        "limitations": [
            "Local receipt evidence only; no fresh social-provider or Google Docs readback.",
            "A native multi-part publication is counted as one logical campaign publication.",
            "The existing publications model excludes incomplete identities and invalid timestamps.",
            "Origin metadata is from the current local campaign manifest; historical edits are not reconstructed.",
            "Copy is shown only when its hash matches the publication receipt.",
            "No vault metadata means no vault attribution is established, not proof of non-vault origin.",
        ],
    }


def summary(report: dict) -> str:
    """Render the receipt-backed result without executing any recorded content."""
    lines = [
        "POST-ONCE OWNER PUBLICATION REPORT",
        f"Day: {report['date']} | Europe/London",
        "Receipt cutoff UTC: " + report["publication_cutoff_utc"],
        "Counts: completed logical campaign publications, not replies or thread parts.",
        "",
    ]
    for provider in PROVIDERS:
        counts = report["counts"][provider]
        lines.append(
            f"{provider.upper()}: {counts['published']} published | "
            f"{counts['verified']} verified | {counts['published_unverified']} "
            "published without verified readback"
        )
        selected = [row for row in report["publications"] if row["provider"] == provider]
        if not selected:
            lines.append("  No qualifying completed publication receipts in this local-day window.")
        for index, row in enumerate(selected, 1):
            lines.extend([
                f"\n  {index}. {row['published_at_local']} | {row['campaign']}",
                "     Project: " + str(row["project_from_current_manifest"] or "not established"),
                "     Account: " + str(row["account_label"] or row["account_id"]),
                "     Title: " + str(row["title_from_current_manifest"] or "not recorded"),
                "     Vault: " + str(row["vault"].get("id") or "no vault attribution recorded"),
            ])
            for key in ("vault", "source"):
                if row[key]:
                    lines.append(f"     {key.title()} metadata: " + json.dumps(row[key], ensure_ascii=False, sort_keys=True))
            if row["brief_id"]:
                lines.append("     Brief: " + str(row["brief_id"]))
            lines.extend([
                "     Receipt: " + str(row["receipt_status"]) + " | effective verified: " + str(row["effective_verified"]),
                "     Post ID: " + str(row["post_id"]),
                "     URL: " + str(row["url"] or "not recorded"),
                "     Schedule ID: " + str(row["schedule_id"] or "not recorded"),
            ])
            text = row["published_text_matching_receipt"]
            if text is not None:
                lines.append("     Copy matching receipt hash:")
                lines.extend("       " + line for line in text.splitlines())
            else:
                lines.append("     Exact copy: " + row["copy_status"])
            if row["metadata_error_type"]:
                lines.append("     Metadata warning: " + row["metadata_error_type"])
        lines.append("")
    lines.extend(["EVIDENCE BOUNDARIES", *("- " + note for note in report["limitations"]), "OWNER_REPORT=PASS"])
    # Neutralise terminal control characters in recorded copy/metadata.
    return "".join(char if char in "\n\t" or char.isprintable() else "?" for char in "\n".join(lines)) + "\n"


def save_report(report: dict, root: Path, state_root: Path, config_root: Path) -> Path:
    """Create new private report files outside source and live state/config roots."""
    home = Path.home().resolve()
    for protected in (root.resolve(), state_root.resolve(), config_root.resolve()):
        if home == protected or protected in home.parents:
            raise ValueError("Home report location is inside a protected input directory")
    directory = Path(tempfile.mkdtemp(prefix="post-once-owner-report-", dir=home))
    for name, content in (
        ("publications.json", json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False) + "\n"),
        ("summary.txt", summary(report)),
    ):
        descriptor = os.open(directory / name, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(content)
    return directory


def open_summary(path: Path) -> None:
    """Best-effort WSL convenience; never changes a successful report's result."""
    if not shutil.which("notepad.exe") or not shutil.which("wslpath"):
        return
    try:
        converted = subprocess.run(["wslpath", "-w", str(path)], capture_output=True, text=True, timeout=5, check=True)
        subprocess.Popen(
            ["notepad.exe", converted.stdout.strip()], stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True,
        )
    except (OSError, subprocess.SubprocessError):
        print("NOTEPAD=unavailable; use SUMMARY_SAVED instead", file=sys.stderr)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, default=Path(__file__).resolve().parents[1],
                        help="Repository to read; defaults to the checkout containing this script")
    parser.add_argument("--date", type=date.fromisoformat, help="London calendar date YYYY-MM-DD; default is today")
    parser.add_argument("--save", action="store_true", help="Save a private JSON/text report outside the repository and live state")
    parser.add_argument("--no-open", action="store_true", help="Do not open a saved summary in Notepad")
    args = parser.parse_args(argv)
    root = args.repo.expanduser().resolve()
    if not (root / "src/ocpf_post/performance_review.py").is_file():
        raise ValueError("Post-Once source not found at the selected repository path")
    sys.dont_write_bytecode = True
    sys.path.insert(0, str(root / "src"))
    from ocpf_post.campaigns import builtin_manifest, builtin_text
    from ocpf_post.performance_review import publications
    from ocpf_post.state import config_dir, state_dir
    state_root = state_dir()
    if not (state_root / "publish-receipts.jsonl").exists() and not (state_root / "publish-receipts.jsonl.segments").exists():
        raise ValueError("No local publication ledger found; refusing to report a misleading zero")
    now = datetime.now(timezone.utc)
    day = args.date or now.astimezone(ZoneInfo("Europe/London")).date()
    if day > now.astimezone(ZoneInfo("Europe/London")).date():
        raise ValueError("A future day has no completed daily publication report")
    report = collect(day, now, publications(), builtin_manifest, builtin_text)
    if args.save:
        directory = save_report(report, root, state_root, config_dir())
        print(summary(report), end="")
        print("SUMMARY_SAVED=" + str(directory / "summary.txt"))
        print("JSON_SAVED=" + str(directory / "publications.json"))
        if not args.no_open:
            open_summary(directory / "summary.txt")
    else:
        print(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ValueError, KeyError, TypeError, RuntimeError, ImportError) as exc:
        print("OWNER_REPORT=FAIL error_type=" + type(exc).__name__, file=sys.stderr)
        raise SystemExit(1)
