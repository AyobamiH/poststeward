"""Strict append-only JSONL ledger primitives with bounded segmentation.

Consequential readers must never reinterpret malformed or partially rotated
durable evidence as if the record were absent. Corruption and interrupted
segmentation therefore fail closed and report metadata only, never private rows.
"""
from __future__ import annotations

from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import tempfile
from typing import Any, Iterable, Iterator

MAX_SEGMENTS = 1024


class LedgerIntegrityError(ValueError):
    pass


def _segments_dir(path: Path) -> Path:
    return path.with_name(path.name + ".segments")


def _transaction_file(path: Path) -> Path:
    return _segments_dir(path) / "SEGMENTATION.json"


def _segment_files(path: Path) -> list[Path]:
    root = _segments_dir(path)
    if not root.exists():
        return []
    files = sorted(p for p in root.iterdir() if p.is_file() and p.suffix == ".jsonl")
    if len(files) > MAX_SEGMENTS:
        raise LedgerIntegrityError(f"Ledger has too many segments: {path.name}")
    return files


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


@contextmanager
def ledger_lock(path: Path):
    """Single writer/segmenter lock for a logical ledger."""
    try:
        import fcntl
    except ImportError as exc:
        raise LedgerIntegrityError("Ledger mutation requires POSIX file locks") from exc
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        path.parent.chmod(0o700)
    except OSError:
        pass
    lock = path.with_name(path.name + ".ledger.lock")
    fd = os.open(lock, os.O_CREAT | os.O_RDWR, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        os.close(fd)


def append_jsonl(path: Path, value: dict[str, Any]) -> None:
    """Append one durable JSON object under the shared ledger lock."""
    if not isinstance(value, dict):
        raise TypeError("Ledger row must be an object")
    line = (json.dumps(value, separators=(",", ":"), ensure_ascii=False, allow_nan=False) + "\n").encode()
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        path.parent.chmod(0o700)
    except OSError:
        pass
    with ledger_lock(path):
        if _transaction_file(path).exists():
            raise LedgerIntegrityError(f"Ledger segmentation recovery required: {path.name}")
        fd = os.open(path, os.O_APPEND | os.O_CREAT | os.O_WRONLY, 0o600)
        try:
            os.write(fd, line)
            os.fsync(fd)
        finally:
            os.close(fd)
        try:
            path.chmod(0o600)
        except OSError:
            pass


def _iter_one(path: Path, *, required: tuple[str, ...], max_bytes: int) -> Iterator[dict[str, Any]]:
    if path.stat().st_size > max_bytes:
        raise LedgerIntegrityError(f"Ledger segment exceeds bounded size: {path.name}")
    with path.open("r", encoding="utf-8") as handle:
        for number, raw in enumerate(handle, 1):
            if not raw.strip():
                continue
            try:
                value = json.loads(raw)
            except json.JSONDecodeError as exc:
                raise LedgerIntegrityError(
                    f"Malformed durable ledger {path.name} line {number}"
                ) from exc
            if not isinstance(value, dict):
                raise LedgerIntegrityError(f"Non-object durable ledger {path.name} line {number}")
            missing = [field for field in required if value.get(field) is None]
            if missing:
                raise LedgerIntegrityError(
                    f"Durable ledger {path.name} line {number} missing required fields: {','.join(missing)}"
                )
            yield value


def iter_jsonl(
    path: Path,
    *,
    required: Iterable[str] = (),
    max_bytes: int = 200_000_000,
) -> Iterator[dict[str, Any]]:
    """Stream immutable segments followed by the active tail."""
    transaction = _transaction_file(path)
    if transaction.exists():
        raise LedgerIntegrityError(f"Ledger segmentation recovery required: {path.name}")
    required_tuple = tuple(required)
    for segment in _segment_files(path):
        yield from _iter_one(segment, required=required_tuple, max_bytes=max_bytes)
    if path.exists():
        yield from _iter_one(path, required=required_tuple, max_bytes=max_bytes)


def read_jsonl(
    path: Path,
    *,
    required: Iterable[str] = (),
    max_bytes: int = 200_000_000,
) -> list[dict[str, Any]]:
    return list(iter_jsonl(path, required=required, max_bytes=max_bytes))


def segmentation_status(path: Path) -> dict[str, Any]:
    segments = _segment_files(path)
    transaction = _transaction_file(path)
    return {
        "ledger": path.name,
        "segment_count": len(segments),
        "active_size": path.stat().st_size if path.exists() else 0,
        "transaction_pending": transaction.exists(),
        "segment_bytes": sum(p.stat().st_size for p in segments),
    }


def _next_segment_name(path: Path) -> str:
    existing = _segment_files(path)
    number = 1
    if existing:
        try:
            number = max(int(p.stem) for p in existing) + 1
        except ValueError as exc:
            raise LedgerIntegrityError(f"Unexpected ledger segment name: {path.name}") from exc
    return f"{number:08d}.jsonl"


def _load_transaction(path: Path) -> dict[str, Any]:
    marker = _transaction_file(path)
    if not marker.exists():
        return {}
    try:
        value = json.loads(marker.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise LedgerIntegrityError(f"Invalid segmentation transaction: {path.name}") from exc
    if not isinstance(value, dict) or value.get("schema_version") != 1:
        raise LedgerIntegrityError(f"Invalid segmentation transaction: {path.name}")
    return value


def recover_segmentation(path: Path, *, apply: bool = False) -> dict[str, Any]:
    """Finish or roll back an interrupted two-file segment commit."""
    with ledger_lock(path):
        txn = _load_transaction(path)
        if not txn:
            return {"schema_version": 1, "status": "not_required", "applied": False, "ledger": path.name}
        root = _segments_dir(path)
        pending = root / str(txn.get("pending_name"))
        final = root / str(txn.get("final_name"))
        live_sha = _sha256_file(path) if path.exists() else _sha256_bytes(b"")
        old_sha = txn.get("old_live_sha256")
        new_sha = txn.get("new_live_sha256")
        pending_sha = txn.get("pending_sha256")
        if live_sha == old_sha:
            action = "rollback_pending_segment"
        elif live_sha == new_sha and pending.exists() and _sha256_file(pending) == pending_sha:
            action = "complete_segment_commit"
        elif live_sha == new_sha and final.exists() and _sha256_file(final) == pending_sha:
            action = "clear_completed_marker"
        else:
            return {
                "schema_version": 1,
                "status": "blocked",
                "applied": False,
                "ledger": path.name,
                "reason": "segmentation_state_not_reconcilable",
            }
        if not apply:
            return {
                "schema_version": 1,
                "status": "recovery_preview",
                "applied": False,
                "ledger": path.name,
                "action": action,
            }
        if action == "rollback_pending_segment":
            pending.unlink(missing_ok=True)
        elif action == "complete_segment_commit":
            os.replace(pending, final)
            try:
                final.chmod(0o600)
            except OSError:
                pass
        _transaction_file(path).unlink(missing_ok=True)
        directory = os.open(root, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
        return {
            "schema_version": 1,
            "status": "recovered",
            "applied": True,
            "ledger": path.name,
            "action": action,
        }


def segment_jsonl(
    path: Path,
    *,
    keep_lines: int = 50_000,
    apply: bool = False,
    expected_sha256: str | None = None,
) -> dict[str, Any]:
    """Move an immutable prefix into a numbered segment, keeping a bounded live tail."""
    if type(keep_lines) is not int or not 1_000 <= keep_lines <= 1_000_000:
        raise ValueError("keep_lines must be between 1000 and 1000000")
    with ledger_lock(path):
        if _transaction_file(path).exists():
            return {
                "schema_version": 1,
                "status": "blocked",
                "applied": False,
                "ledger": path.name,
                "reason": "segmentation_recovery_required",
            }
        if not path.exists():
            return {
                "schema_version": 1,
                "status": "not_required",
                "applied": False,
                "ledger": path.name,
                "reason": "ledger_missing",
            }
        raw = path.read_bytes()
        lines = raw.splitlines(keepends=True)
        if len(lines) <= keep_lines:
            return {
                "schema_version": 1,
                "status": "not_required",
                "applied": False,
                "ledger": path.name,
                "records": len(lines),
                "keep_lines": keep_lines,
            }
        split_at = len(lines) - keep_lines
        prefix = b"".join(lines[:split_at])
        tail = b"".join(lines[split_at:])
        final_name = _next_segment_name(path)
        review_payload = {
            "ledger": path.name,
            "old_live_sha256": _sha256_bytes(raw),
            "prefix_sha256": _sha256_bytes(prefix),
            "new_live_sha256": _sha256_bytes(tail),
            "segment_name": final_name,
            "moved_lines": split_at,
            "keep_lines": keep_lines,
        }
        review = hashlib.sha256(
            json.dumps(review_payload, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        if not apply:
            return {
                "schema_version": 1,
                "status": "preview",
                "applied": False,
                "review_sha256": review,
                **review_payload,
            }
        if expected_sha256 != review:
            raise ValueError("Ledger segmentation review changed")

        root = _segments_dir(path)
        root.mkdir(parents=True, exist_ok=True)
        try:
            root.chmod(0o700)
        except OSError:
            pass
        pending_name = final_name + ".pending"
        pending = root / pending_name
        final = root / final_name

        fd, tail_name = tempfile.mkstemp(prefix=path.name + ".", suffix=".tail", dir=path.parent)
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(tail)
                handle.flush()
                os.fsync(handle.fileno())
            Path(tail_name).chmod(0o600)
            pending.write_bytes(prefix)
            pending.chmod(0o600)
            with pending.open("rb") as handle:
                os.fsync(handle.fileno())
            marker = {
                "schema_version": 1,
                "old_live_sha256": review_payload["old_live_sha256"],
                "new_live_sha256": review_payload["new_live_sha256"],
                "pending_sha256": review_payload["prefix_sha256"],
                "pending_name": pending_name,
                "final_name": final_name,
                "review_sha256": review,
            }
            marker_path = _transaction_file(path)
            with marker_path.open("w", encoding="utf-8") as handle:
                handle.write(json.dumps(marker, sort_keys=True, separators=(",", ":")))
                handle.flush()
                os.fsync(handle.fileno())
            marker_path.chmod(0o600)
            root_fd = os.open(root, os.O_RDONLY)
            try:
                os.fsync(root_fd)
            finally:
                os.close(root_fd)

            os.replace(tail_name, path)
            state_fd = os.open(path.parent, os.O_RDONLY)
            try:
                os.fsync(state_fd)
            finally:
                os.close(state_fd)

            os.replace(pending, final)
            final.chmod(0o600)
            root_fd = os.open(root, os.O_RDONLY)
            try:
                os.fsync(root_fd)
            finally:
                os.close(root_fd)

            marker_path.unlink(missing_ok=True)
            root_fd = os.open(root, os.O_RDONLY)
            try:
                os.fsync(root_fd)
            finally:
                os.close(root_fd)
        finally:
            Path(tail_name).unlink(missing_ok=True)
        return {
            "schema_version": 1,
            "status": "segmented",
            "applied": True,
            "review_sha256": review,
            **review_payload,
        }
