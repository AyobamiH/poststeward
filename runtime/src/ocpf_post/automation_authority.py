"""Local unattended-automation authority marker.

The marker is deliberately simple: it is the last local cutover step.  Systemd
units may be installed and even enabled before activation, but unattended
wrappers must refuse to execute unless this exact marker is active.

This is a *local* fence only.  It does not fence another host.  Dead-host
recovery therefore requires external provider-authority fencing evidence before
the marker may be activated.
"""
from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import stat
from typing import Any
import uuid

from ocpf_post.state import state_dir

UTC = timezone.utc
SCHEMA_VERSION = 1
AUTHORITY_FILE = "automation-authority.json"
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


class AutomationAuthorityError(RuntimeError):
    pass


def path(root: str | Path | None = None) -> Path:
    base = Path(root).expanduser().resolve() if root is not None else state_dir()
    return base / AUTHORITY_FILE


def _uuid4(value: Any, field: str) -> str:
    try:
        parsed = uuid.UUID(str(value))
    except (TypeError, ValueError, AttributeError) as exc:
        raise AutomationAuthorityError(f"Invalid {field}") from exc
    if parsed.version != 4 or str(parsed) != str(value):
        raise AutomationAuthorityError(f"{field} must be canonical UUID4")
    return str(parsed)


def _time(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value:
        raise AutomationAuthorityError(f"Missing {field}")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise AutomationAuthorityError(f"Invalid {field}") from exc
    if parsed.tzinfo is None:
        raise AutomationAuthorityError(f"{field} must include timezone")
    return parsed.astimezone(UTC).replace(microsecond=0).strftime("%Y-%m-%dT%H:%M:%SZ")


def _now() -> str:
    return datetime.now(UTC).replace(microsecond=0).strftime("%Y-%m-%dT%H:%M:%SZ")


def validate(value: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(value, dict) or value.get("schema_version") != SCHEMA_VERSION:
        raise AutomationAuthorityError("Unsupported automation-authority schema")
    status = value.get("status")
    if status not in {"active", "inactive"}:
        raise AutomationAuthorityError("Invalid automation-authority status")
    active_fields = {
        "schema_version", "status", "operation_id", "installation_id",
        "authority_generation", "review_sha256", "runtime_revision",
        "activated_at", "updated_at",
    }
    inactive_fields = active_fields | {"deactivated_at", "reason"}
    expected_fields = active_fields if status == "active" else inactive_fields
    if set(value) != expected_fields:
        raise AutomationAuthorityError("Automation-authority fields do not match the stored status")
    _uuid4(value.get("operation_id"), "operation_id")
    _uuid4(value.get("installation_id"), "installation_id")
    generation = value.get("authority_generation")
    if type(generation) is not int or generation < 1:
        raise AutomationAuthorityError("Invalid authority_generation")
    review = value.get("review_sha256")
    if not isinstance(review, str) or not _SHA256_RE.fullmatch(review):
        raise AutomationAuthorityError("Invalid review_sha256")
    _time(value.get("updated_at"), "updated_at")
    _time(value.get("activated_at"), "activated_at")
    if status == "inactive":
        _time(value.get("deactivated_at"), "deactivated_at")
        reason = value.get("reason")
        if not isinstance(reason, str) or not reason.strip() or len(reason) > 500:
            raise AutomationAuthorityError("Invalid deactivation reason")
    runtime_revision = value.get("runtime_revision")
    if not isinstance(runtime_revision, str) or not re.fullmatch(r"[0-9a-f]{40}", runtime_revision):
        raise AutomationAuthorityError("Invalid runtime_revision")
    return value


def read(root: str | Path | None = None) -> dict[str, Any]:
    file = path(root)
    if not file.is_file() or file.is_symlink():
        return {
            "schema_version": SCHEMA_VERSION,
            "status": "inactive",
            "reason": "authority_marker_missing",
            "path": str(file),
        }
    try:
        mode = stat.S_IMODE(file.stat().st_mode)
    except OSError as exc:
        raise AutomationAuthorityError("Automation-authority marker metadata is unreadable") from exc
    if mode & 0o077:
        raise AutomationAuthorityError("Automation-authority marker permissions must be private")
    try:
        value = json.loads(file.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise AutomationAuthorityError("Automation-authority marker is unreadable") from exc
    if not isinstance(value, dict):
        raise AutomationAuthorityError("Automation-authority marker must be an object")
    validate(value)
    return {**value, "path": str(file)}


def _atomic_write(file: Path, value: dict[str, Any]) -> None:
    validate(value)
    file.parent.mkdir(parents=True, exist_ok=True)
    try:
        file.parent.chmod(0o700)
    except OSError:
        pass
    temp = file.with_name(f".{file.name}.{os.getpid()}.tmp")
    fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(value, handle, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp, file)
        try:
            dir_fd = os.open(file.parent, os.O_RDONLY)
        except OSError:
            return
        try:
            os.fsync(dir_fd)
        finally:
            os.close(dir_fd)
    finally:
        temp.unlink(missing_ok=True)


def activate(
    *,
    root: str | Path,
    operation_id: str,
    installation_id: str,
    authority_generation: int,
    review_sha256: str,
    runtime_revision: str,
) -> dict[str, Any]:
    value = {
        "schema_version": SCHEMA_VERSION,
        "status": "active",
        "operation_id": operation_id,
        "installation_id": installation_id,
        "authority_generation": authority_generation,
        "review_sha256": review_sha256,
        "runtime_revision": runtime_revision,
        "activated_at": _now(),
        "updated_at": _now(),
    }
    _atomic_write(path(root), value)
    return read(root)


def deactivate(
    *,
    root: str | Path,
    review_sha256: str,
    reason: str,
) -> dict[str, Any]:
    current = read(root)
    if current.get("status") != "active":
        return current
    reason_text = str(reason or "").strip()
    if not reason_text or len(reason_text) > 500:
        raise AutomationAuthorityError("A bounded deactivation reason is required")
    value = {
        "schema_version": SCHEMA_VERSION,
        "status": "inactive",
        "operation_id": current["operation_id"],
        "installation_id": current["installation_id"],
        "authority_generation": current["authority_generation"],
        "review_sha256": review_sha256,
        "runtime_revision": current["runtime_revision"],
        "activated_at": current["activated_at"],
        "deactivated_at": _now(),
        "updated_at": _now(),
        "reason": reason_text,
    }
    _atomic_write(path(root), value)
    return read(root)


def require_active(root: str | Path | None = None) -> dict[str, Any]:
    value = read(root)
    if value.get("status") != "active":
        raise AutomationAuthorityError(
            "Unattended Post-Once automation is not active on this installation"
        )
    return value


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description="Inspect the local unattended-automation gate")
    parser.add_argument("action", choices=("check", "status"))
    parser.add_argument("--state-dir")
    args = parser.parse_args()
    try:
        value = read(args.state_dir)
        if args.action == "check" and value.get("status") != "active":
            print(json.dumps(value, sort_keys=True))
            raise SystemExit(3)
        print(json.dumps(value, sort_keys=True))
    except AutomationAuthorityError as exc:
        print(json.dumps({"schema_version": 1, "status": "invalid", "error": str(exc)}))
        raise SystemExit(3)


if __name__ == "__main__":
    main()
