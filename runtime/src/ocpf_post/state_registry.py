"""Inventory and verify Post-Once local state without repairing it."""
from __future__ import annotations

import hashlib
import io
import json
import os
from pathlib import Path
import tarfile
from typing import Any

from ocpf_post.ledger import (
    LedgerIntegrityError,
    iter_jsonl,
    recover_segmentation,
    segment_jsonl,
    segmentation_status,
)
from ocpf_post.state import config_dir, state_dir

SCHEMA_REGISTRY_VERSION = 1
JSONL_REQUIRED = {
    "publish-receipts.jsonl": ("campaign", "provider", "status"),
    "schedule-events.jsonl": ("schedule_id", "event", "status", "recorded_at"),
    "performance-snapshots.jsonl": ("campaign", "provider", "captured_at"),
    "portfolio-allocation-events.jsonl": ("event",),
}
SEGMENTABLE = {
    "receipts": "publish-receipts.jsonl",
    "schedules": "schedule-events.jsonl",
    "performance": "performance-snapshots.jsonl",
}


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _relative(path: Path, root: Path) -> str:
    try:
        return str(path.relative_to(root))
    except ValueError:
        return path.name


def _json_status(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(value, dict):
            raise ValueError("top-level value is not an object")
        schema = value.get("schema_version")
        if schema is not None and schema != 1:
            raise ValueError("unsupported schema version")
        return {"status": "ok", "schema_version": schema}
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        return {"status": "invalid", "error_type": type(exc).__name__}


def _jsonl_status(path: Path) -> dict[str, Any]:
    try:
        count = sum(1 for _ in iter_jsonl(path, required=JSONL_REQUIRED.get(path.name, ())))
        return {"status": "ok", "records": count}
    except (OSError, LedgerIntegrityError, ValueError) as exc:
        return {"status": "invalid", "error_type": type(exc).__name__}


def _credential_like(path: Path, root: Path) -> bool:
    try:
        relative = path.relative_to(root)
    except ValueError:
        return False
    lowered = [part.lower() for part in relative.parts]
    name = relative.name.lower()
    if any(word in name for word in ("token", "credential", "secret")):
        return True
    if lowered and lowered[0] in {"accounts", "outcome-connectors"} and name.endswith(".json"):
        return True
    return False


def _scan(root: Path, *, kind: str) -> list[dict[str, Any]]:
    if not root.exists():
        return []
    rows = []
    for path in sorted(p for p in root.rglob("*") if p.is_file() and not p.name.endswith(".lock")):
        suffix = path.suffix.lower()
        row: dict[str, Any] = {
            "scope": kind, "path": _relative(path, root),
            "size": path.stat().st_size, "mode": oct(path.stat().st_mode & 0o777),
        }
        if kind == "config" and _credential_like(path, root):
            row.update(status="opaque", format="credential_opaque")
        elif suffix == ".json":
            row.update(_json_status(path), format="json")
        elif suffix == ".jsonl":
            row.update(_jsonl_status(path), format="jsonl")
        else:
            row.update(status="opaque", format="opaque")
        # Fingerprints allow drift detection without exposing contents.
        row["sha256"] = _sha256(path)
        rows.append(row)
    return rows


def inventory() -> dict[str, Any]:
    state_rows = _scan(state_dir(), kind="state")
    config_rows = _scan(config_dir(), kind="config")
    rows = state_rows + config_rows
    invalid = [row for row in rows if row["status"] == "invalid"]
    return {
        "schema_version": 1,
        "registry_version": SCHEMA_REGISTRY_VERSION,
        "status": "attention" if invalid else "observed",
        "state_file_count": len(state_rows),
        "config_file_count": len(config_rows),
        "invalid_count": len(invalid),
        "files": rows,
        "boundary": "Read-only local file/schema/fingerprint inventory. Opaque credential files are fingerprinted but never decoded or printed.",
    }


def verify_critical() -> dict[str, Any]:
    """Verify authority-bearing ledgers only, without walking/fingerprinting all state.

    This is the bounded integrity projection used by the live console. Full
    forensic inventory remains available through verify()/state verify.
    """
    critical: dict[str, str] = {}
    segment_state: dict[str, Any] = {}
    for name, required in JSONL_REQUIRED.items():
        path = state_dir() / name
        if not path.exists() and not path.with_name(path.name + ".segments").exists():
            continue
        try:
            for _row in iter_jsonl(path, required=required):
                pass
            critical[name] = "observed"
        except (OSError, LedgerIntegrityError, ValueError):
            critical[name] = "invalid"
        if name in SEGMENTABLE.values():
            try:
                segment_state[name] = segmentation_status(path)
            except (OSError, LedgerIntegrityError, ValueError) as exc:
                segment_state[name] = {"status": "invalid", "error_type": type(exc).__name__}
                critical[name] = "invalid"

    invalid_count = sum(value == "invalid" for value in critical.values())
    status = "attention" if invalid_count else "observed"
    return {
        "schema_version": 1,
        "registry_version": SCHEMA_REGISTRY_VERSION,
        "status": status,
        "scope": "critical_ledgers_only",
        "invalid_count": invalid_count,
        "critical_ledgers": critical,
        "ledger_segmentation": segment_state,
        "critical_ledger_status": status,
        "boundary": (
            "Live critical-ledger integrity only. It does not walk or fingerprint the complete state/config tree; "
            "use ocpf-post state verify for the full forensic registry."
        ),
    }


def verify() -> dict[str, Any]:
    value = inventory()
    critical = {}
    segment_state = {}
    for name, required in JSONL_REQUIRED.items():
        path = state_dir() / name
        if not path.exists() and not path.with_name(path.name + ".segments").exists():
            continue
        try:
            for _row in iter_jsonl(path, required=required):
                pass
            critical[name] = "observed"
        except (OSError, LedgerIntegrityError, ValueError):
            critical[name] = "invalid"
        if name in SEGMENTABLE.values():
            try:
                segment_state[name] = segmentation_status(path)
            except (OSError, LedgerIntegrityError, ValueError) as exc:
                segment_state[name] = {"status": "invalid", "error_type": type(exc).__name__}
                critical[name] = "invalid"
    value["critical_ledgers"] = critical
    value["ledger_segmentation"] = segment_state
    value["critical_ledger_status"] = "attention" if any(v == "invalid" for v in critical.values()) else "observed"
    if value["critical_ledger_status"] == "attention":
        value["status"] = "attention"
    return value


def archive(output: str | Path, *, apply: bool = False) -> dict[str, Any]:
    target = Path(output).expanduser().resolve()
    root = state_dir().resolve()
    if target == root or root in target.parents:
        raise ValueError("Evidence archive must be outside the live state directory")
    current = verify()
    if current["status"] == "attention":
        return {"schema_version": 1, "status": "blocked", "applied": False,
                "reason": "state_integrity_required_before_archive"}
    manifest = [
        {key: row.get(key) for key in ("path", "size", "mode", "sha256", "format", "status")}
        for row in current["files"] if row.get("scope") == "state"
    ]
    review = hashlib.sha256(json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    if not apply:
        return {"schema_version": 1, "status": "preview", "applied": False,
                "output": str(target), "review_sha256": review, "file_count": len(manifest),
                "boundary": "State evidence only; provider/config credentials are not included."}
    if target.exists():
        raise ValueError("Archive output already exists")
    target.parent.mkdir(parents=True, exist_ok=True)
    before = {row["path"]: row["sha256"] for row in current["files"] if row.get("scope") == "state"}
    with tarfile.open(target, "x:gz") as archive_file:
        for row in manifest:
            source = root / row["path"]
            if source.exists():
                archive_file.add(source, arcname="state/" + row["path"], recursive=False)
        payload = json.dumps({"schema_version": 1, "review_sha256": review, "files": manifest},
                             indent=2, ensure_ascii=False).encode()
        info = tarfile.TarInfo("MANIFEST.json")
        info.size = len(payload)
        info.mode = 0o600
        archive_file.addfile(info, io.BytesIO(payload))
    os.chmod(target, 0o600)
    after_state = verify()
    after = {row["path"]: row["sha256"] for row in after_state["files"] if row.get("scope") == "state"}
    if before != after:
        target.unlink(missing_ok=True)
        raise ValueError("State changed during archive; no archive retained")
    return {"schema_version": 1, "status": "archived", "applied": True,
            "output": str(target), "review_sha256": review, "file_count": len(manifest),
            "boundary": "Consistent state-evidence archive only. Restoring authority-bearing evidence is intentionally not automated."}


def segment(
    ledger: str,
    *,
    keep_lines: int = 50_000,
    apply: bool = False,
    expected_sha256: str | None = None,
) -> dict[str, Any]:
    if ledger not in SEGMENTABLE:
        raise ValueError("Unknown segmentable ledger")
    current = verify()
    if current["status"] == "attention":
        return {
            "schema_version": 1,
            "status": "blocked",
            "applied": False,
            "ledger": ledger,
            "reason": "state_integrity_required_before_segmentation",
        }
    path = state_dir() / SEGMENTABLE[ledger]
    result = segment_jsonl(
        path,
        keep_lines=keep_lines,
        apply=apply,
        expected_sha256=expected_sha256,
    )
    return {
        **result,
        "ledger_alias": ledger,
        "boundary": (
            "Structural ledger compaction only. Immutable prefix rows move to numbered segments; "
            "logical read order and consequence evidence are preserved."
        ),
    }


def segment_recover(ledger: str, *, apply: bool = False) -> dict[str, Any]:
    if ledger not in SEGMENTABLE:
        raise ValueError("Unknown segmentable ledger")
    result = recover_segmentation(state_dir() / SEGMENTABLE[ledger], apply=apply)
    return {
        **result,
        "ledger_alias": ledger,
        "boundary": (
            "Recovery only completes or rolls back a recorded structural segmentation transaction. "
            "It never edits ledger rows or invents consequence evidence."
        ),
    }


def migrate(*, apply: bool = False) -> dict[str, Any]:
    """Framework for explicit future migrations; there is no implicit repair."""
    current = verify()
    if current["invalid_count"]:
        return {
            "schema_version": 1, "status": "blocked", "applied": False,
            "reason": "state_integrity_required_before_migration",
            "boundary": "Corrupt/unknown state is never rewritten by migration.",
        }
    # All currently registered structured state is schema v1. A future schema
    # change must add an explicit reviewed transformer here before apply can act.
    return {
        "schema_version": 1, "status": "current", "applied": False,
        "target_registry_version": SCHEMA_REGISTRY_VERSION,
        "apply_requested": bool(apply),
        "boundary": "No migration is currently required; --apply cannot manufacture one.",
    }
