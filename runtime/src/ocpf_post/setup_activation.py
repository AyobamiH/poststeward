"""Milestone H activation/deactivation orchestration.

Activation is a staged local cutover:

  prepare/preview -> exact review digest -> stage unit files -> promote reviewed
  portable state while unattended execution is fenced -> commit local owner
  identity -> enable timers behind the inactive fence -> attest -> atomically
  flip automation-authority.json active.

Deactivation flips the marker inactive first and only then disables timers.
No state/receipt rollback is attempted after cutover because provider effects may
have occurred.
"""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
import errno
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
from typing import Any, Iterable, Iterator, NoReturn, Protocol
import uuid

from ocpf_post import automation_authority
from ocpf_post.setup_contracts import validate_installation, validate_operation
from ocpf_post.setup_migration import load_quarantine_object
from ocpf_post.setup_store import SetupStore, SetupStoreError, resolve_plain_path

UTC = timezone.utc
SCHEMA_VERSION = 1
TIMERS = (
    "post-once-run-due.timer",
    "post-once-portfolio-refill.timer",
    "post-once-collection.timer",
    "post-once-replies.timer",
)
SERVICES = (
    "post-once-run-due.service",
    "post-once-portfolio-refill.service",
    "post-once-collection.service",
    "post-once-replies.service",
)
_SHA40_RE = re.compile(r"^[0-9a-f]{40}$")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


class ActivationError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def _fail(code: str, message: str) -> NoReturn:
    raise ActivationError(code, message)


def _now() -> str:
    return datetime.now(UTC).replace(microsecond=0).strftime("%Y-%m-%dT%H:%M:%SZ")


@contextmanager
def _exclusive_activation_lock(workspace: Path, *, action: str) -> Iterator[None]:
    """Serialize local activation/deactivation mutations across processes.

    SQLite revision checks protect the setup control transaction, but activation
    also stages filesystem roots and systemd units before that transaction commits.
    A kernel-managed flock closes that race for one local bootstrap workspace and
    is automatically released if the process exits or crashes.
    """
    if os.name != "posix":
        _fail("activation.lock.unsupported", "Activation serialization requires a POSIX host")
    try:
        import fcntl
    except ImportError as exc:
        raise ActivationError(
            "activation.lock.unsupported",
            "Activation serialization is unavailable on this host",
        ) from exc

    root = workspace.expanduser().resolve()
    if not root.is_dir() or root.is_symlink():
        _fail("activation.lock.workspace_invalid", "Activation workspace must be an existing plain directory")
    lock_path = root / ".activation-operation.lock"
    flags = os.O_RDWR | os.O_CREAT | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        fd = os.open(lock_path, flags, 0o600)
    except OSError as exc:
        raise ActivationError("activation.lock.unavailable", "Could not open the local activation lock") from exc

    acquired = False
    try:
        try:
            os.fchmod(fd, 0o600)
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            acquired = True
        except OSError as exc:
            if exc.errno in {errno.EACCES, errno.EAGAIN}:
                _fail(
                    "activation.concurrent_operation",
                    "Another activation/deactivation operation is already in progress for this setup workspace",
                )
            raise ActivationError("activation.lock.unavailable", "Could not acquire the local activation lock") from exc

        metadata = {
            "schema_version": 1,
            "action": action,
            "pid": os.getpid(),
            "acquired_at": _now(),
        }
        payload = (
            json.dumps(metadata, sort_keys=True, separators=(",", ":"), ensure_ascii=True) + "\n"
        ).encode("ascii")
        os.ftruncate(fd, 0)
        os.lseek(fd, 0, os.SEEK_SET)
        os.write(fd, payload)
        os.fsync(fd)
        yield
    finally:
        if acquired:
            try:
                fcntl.flock(fd, fcntl.LOCK_UN)
            except OSError:
                pass
        os.close(fd)


def _parse_time(value: Any, field: str) -> datetime:
    if not isinstance(value, str) or not value:
        _fail("activation.evidence.invalid", f"Missing {field}")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ActivationError("activation.evidence.invalid", f"Invalid {field}") from exc
    if parsed.tzinfo is None:
        _fail("activation.evidence.invalid", f"{field} must include timezone")
    return parsed.astimezone(UTC)


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _json_sha(value: Any) -> str:
    raw = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False).encode("ascii")
    return hashlib.sha256(raw).hexdigest()


def _safe_relative(value: Any) -> Path:
    if not isinstance(value, str) or not value:
        _fail("activation.restore.path_invalid", "Restore path is missing")
    path = Path(value)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        _fail("activation.restore.path_invalid", f"Unsafe restore path: {value}")
    return path


def _scan_regular_tree(root: Path) -> list[dict[str, Any]]:
    if not root.exists():
        return []
    if root.is_symlink() or not root.is_dir():
        _fail("activation.target.invalid", f"Target root is not a plain directory: {root}")
    rows: list[dict[str, Any]] = []
    for path in sorted(root.rglob("*")):
        if path.is_symlink():
            _fail("activation.target.symlink", f"Target root contains a symlink: {path}")
        if path.is_dir():
            continue
        if not path.is_file():
            _fail("activation.target.non_regular", f"Target root contains a non-regular file: {path}")
        relative = path.relative_to(root).as_posix()
        rows.append({"path": relative, "size": path.stat().st_size, "sha256": _sha256_file(path)})
    return rows


def _tree_fingerprint(root: Path) -> str:
    return _json_sha(_scan_regular_tree(root))


def _load_json(path: Path) -> dict[str, Any]:
    if not path.is_file() or path.is_symlink():
        _fail("activation.input.missing", f"Required JSON evidence is missing: {path}")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ActivationError("activation.input.invalid_json", f"Invalid JSON evidence: {path}") from exc
    if not isinstance(value, dict):
        _fail("activation.input.invalid_json", f"Expected an object: {path}")
    return value


def _manifest_object_path(quarantine: Path, logical_id: str) -> Path:
    manifest = _load_json(quarantine / "manifest.json")
    rows = manifest.get("objects")
    if not isinstance(rows, list):
        _fail("activation.restore.manifest_invalid", "Transfer manifest has no object list")
    descriptor = next((row for row in rows if isinstance(row, dict) and row.get("logical_id") == logical_id), None)
    if descriptor is None:
        _fail("activation.restore.object_missing", f"Transfer object missing: {logical_id}")
    digest = str(descriptor.get("digest") or "")
    if not re.fullmatch(r"sha256:[0-9a-f]{64}", digest):
        _fail("activation.restore.manifest_invalid", "Transfer object digest is invalid")
    path = quarantine / "objects" / "sha256" / digest.removeprefix("sha256:")
    if not path.is_file() or path.is_symlink():
        _fail("activation.restore.object_missing", f"Quarantine object missing: {logical_id}")
    if _sha256_file(path) != digest.removeprefix("sha256:"):
        _fail("activation.restore.object_drift", f"Quarantine object changed: {logical_id}")
    return path


def _restore_index_from_events(events: list[dict[str, Any]]) -> tuple[Path | None, list[dict[str, Any]]]:
    restore = next(
        (row["evidence"] for row in reversed(events) if row["to_stage"] == "restored_quarantined" and row["evidence"].get("quarantine")),
        None,
    )
    if restore is None:
        return None, []
    quarantine = Path(str(restore["quarantine"])).expanduser().resolve()
    index = load_quarantine_object(quarantine, "evidence/source_index")
    rows = index.get("files", []) if isinstance(index, dict) else []
    if not isinstance(rows, list):
        _fail("activation.restore.index_invalid", "Restore source index is invalid")
    return quarantine, rows


def _schedule_activation_plan(
    quarantine: Path | None,
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    if quarantine is None:
        return {"rows": [], "rearm_count": 0, "hold_count": 0, "expire_count": 0}
    value = load_quarantine_object(quarantine, "held_authority/schedule_dispositions")
    source_rows = value.get("rows", []) if isinstance(value, dict) else []
    if not isinstance(source_rows, list):
        _fail("activation.schedule_dispositions.invalid", "Transferred schedule dispositions are invalid")
    current = (now or datetime.now(UTC)).astimezone(UTC)
    rows: list[dict[str, Any]] = []
    for source in source_rows:
        if not isinstance(source, dict):
            _fail("activation.schedule_dispositions.invalid", "Schedule disposition row must be an object")
        schedule_id = str(source.get("schedule_id") or "")
        if not schedule_id:
            _fail("activation.schedule_dispositions.invalid", "Schedule disposition is missing schedule_id")
        disposition = str(source.get("disposition") or "")
        source_status = str(source.get("source_status") or "")
        run_at = source.get("run_at")
        action = "preserve"
        if source_status == "scheduled" and disposition == "held_future":
            try:
                future = _parse_time(run_at, f"{schedule_id}.run_at")
            except ActivationError:
                action = "hold"
            else:
                action = "rearm_future" if future > current else "expire"
        elif source_status == "scheduled" and disposition == "hold_recompute":
            action = "hold"
        elif source_status == "scheduled" and disposition == "handoff_expired":
            action = "expire"
        elif disposition in {"blocked_unknown", "block_sealing"}:
            _fail(
                "activation.schedule_dispositions.blocked",
                f"Transferred schedule is not safe to activate: {schedule_id}",
            )
        rows.append({
            "schedule_id": schedule_id,
            "source_status": source_status,
            "source_disposition": disposition,
            "run_at": run_at,
            "action": action,
        })
    return {
        "rows": rows,
        "rearm_count": sum(row["action"] == "rearm_future" for row in rows),
        "hold_count": sum(row["action"] == "hold" for row in rows),
        "expire_count": sum(row["action"] == "expire" for row in rows),
        "boundary": (
            "Safely-future schedules are explicitly re-armed by the reviewed activation. "
            "Overdue schedules expire; recompute-required schedules remain held; no automatic catch-up occurs."
        ),
    }


def _append_schedule_activation_events(state_root: Path, plan: dict[str, Any]) -> None:
    rows = plan.get("rows", [])
    if not isinstance(rows, list) or not rows:
        return
    ledger = state_root / "schedule-events.jsonl"
    if not ledger.exists():
        # No source schedule ledger means there is nothing for the sidecar plan
        # to mutate; fail rather than fabricating schedule history.
        if any(row.get("action") in {"hold", "expire"} for row in rows if isinstance(row, dict)):
            _fail("activation.schedule_ledger.missing", "Schedule hold/expiry plan has no restored ledger")
        return
    now_text = _now()
    with ledger.open("ab") as handle:
        for row in rows:
            if not isinstance(row, dict):
                continue
            action = row.get("action")
            if action not in {"hold", "expire"}:
                continue
            status = "held" if action == "hold" else "handoff_expired"
            event = {
                "schedule_id": row["schedule_id"],
                "event": "activation_hold" if action == "hold" else "handoff_expired",
                "status": status,
                "recorded_at": now_text,
                "detail": (
                    "Held for explicit recompute after transfer"
                    if action == "hold"
                    else "Expired during handoff; automatic catch-up is not authorised"
                ),
            }
            handle.write(
                (json.dumps(event, separators=(",", ":"), ensure_ascii=False, allow_nan=False) + "\n").encode()
            )
        handle.flush()
        os.fsync(handle.fileno())
    try:
        ledger.chmod(0o600)
    except OSError:
        pass


def _restore_plan(
    *, quarantine: Path | None, rows: list[dict[str, Any]], state_root: Path, config_root: Path
) -> dict[str, Any]:
    actions: list[dict[str, Any]] = []
    collisions: list[str] = []
    for row in rows:
        if not isinstance(row, dict) or row.get("scope") not in {"state", "config"}:
            _fail("activation.restore.index_invalid", "Restore index row is invalid")
        relative = _safe_relative(row.get("path"))
        logical_id = str(row.get("logical_id") or "")
        expected = str(row.get("sha256") or "").removeprefix("sha256:")
        if not _SHA256_RE.fullmatch(expected):
            _fail("activation.restore.index_invalid", "Restore index digest is invalid")
        if quarantine is None:
            _fail("activation.restore.quarantine_missing", "Restore index exists without quarantine")
        source = _manifest_object_path(quarantine, logical_id)
        if _sha256_file(source) != expected:
            _fail("activation.restore.index_mismatch", f"Restore object/index digest mismatch: {relative}")
        target_root = state_root if row["scope"] == "state" else config_root
        target = target_root / relative
        if target.exists():
            if target.is_symlink() or not target.is_file():
                collisions.append(f"{row['scope']}:{relative.as_posix()}:non_regular")
                action = "collision"
            elif _sha256_file(target) == expected:
                action = "already_identical"
            else:
                collisions.append(f"{row['scope']}:{relative.as_posix()}:content_differs")
                action = "collision"
        else:
            action = "restore"
        actions.append({
            "scope": row["scope"],
            "path": relative.as_posix(),
            "logical_id": logical_id,
            "sha256": expected,
            "size": int(row.get("size") or source.stat().st_size),
            "action": action,
        })
    if collisions:
        _fail(
            "activation.restore.collision",
            "Reviewed portable state conflicts with existing target files: " + ", ".join(collisions[:8]),
        )
    return {
        "rows": actions,
        "restore_count": sum(row["action"] == "restore" for row in actions),
        "identical_count": sum(row["action"] == "already_identical" for row in actions),
    }


_RECOVERY_ALLOWED_METHODS = {
    "authority.source_host.unknown": {"all_provider_authority_fenced", "provider_control_plane_fenced"},
    "authority.recovery_gap.unreconciled": {"provider_effects_reconciled"},
    "bundle.authenticity.not_provided": {"trusted_bundle_digest_verified"},
}


def _allowed_recovery_methods(code: str) -> set[str]:
    if code.startswith("provider.stale_authority."):
        return {"provider_credential_revoked", "provider_credential_rotated", "provider_session_revoked"}
    if code.startswith(("schedule.ambiguous_effect.", "schedule.partial_effect.", "schedule.executing.")):
        return {"provider_effect_reconciled"}
    return _RECOVERY_ALLOWED_METHODS.get(code, set())


def validate_recovery_resolution(
    value: dict[str, Any],
    *, operation_id: str, source_installation_id: str, required_codes: Iterable[str], source_lost_at: str | None,
) -> dict[str, Any]:
    required = sorted(set(str(code) for code in required_codes))
    if value.get("schema_version") != 1:
        _fail("activation.recovery_resolution.invalid", "Unsupported recovery-resolution schema")
    if value.get("operation_id") != operation_id or value.get("source_installation_id") != source_installation_id:
        _fail("activation.recovery_resolution.identity_mismatch", "Recovery resolution is for a different operation/source")
    rows = value.get("resolutions")
    if not isinstance(rows, list):
        _fail("activation.recovery_resolution.invalid", "Recovery resolutions must be an array")
    normalized: list[dict[str, str]] = []
    seen: set[str] = set()
    loss = _parse_time(source_lost_at, "source_lost_at") if source_lost_at else None
    for row in rows:
        if not isinstance(row, dict):
            _fail("activation.recovery_resolution.invalid", "Recovery resolution row must be an object")
        code = str(row.get("code") or "")
        if code in seen:
            _fail("activation.recovery_resolution.duplicate", f"Duplicate recovery resolution: {code}")
        seen.add(code)
        if code not in required:
            _fail("activation.recovery_resolution.unexpected", f"Unexpected recovery resolution code: {code}")
        if row.get("status") != "resolved":
            _fail("activation.recovery_resolution.unresolved", f"Recovery requirement remains unresolved: {code}")
        method = str(row.get("method") or "")
        if method not in _allowed_recovery_methods(code):
            _fail("activation.recovery_resolution.method_invalid", f"Unsupported resolution method for {code}")
        observed_at = str(row.get("observed_at") or "")
        observed = _parse_time(observed_at, f"{code}.observed_at")
        if loss is not None and observed < loss:
            _fail("activation.recovery_resolution.too_old", f"Resolution predates declared host loss: {code}")
        evidence_ref = str(row.get("evidence_ref") or "").strip()
        if not evidence_ref or len(evidence_ref) > 500:
            _fail("activation.recovery_resolution.evidence_missing", f"Evidence reference required for {code}")
        normalized.append({
            "code": code, "status": "resolved", "method": method,
            "observed_at": observed.astimezone(UTC).replace(microsecond=0).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "evidence_ref": evidence_ref,
        })
    missing = sorted(set(required) - seen)
    if missing:
        _fail("activation.recovery_resolution.missing", "Missing required recovery resolutions: " + ", ".join(missing))
    return {
        "schema_version": 1,
        "operation_id": operation_id,
        "source_installation_id": source_installation_id,
        "resolutions": sorted(normalized, key=lambda row: row["code"]),
    }


class ServiceController(Protocol):
    def inspect(self) -> dict[str, Any]: ...
    def stage(self, *, runtime_root: Path, state_root: Path, config_root: Path) -> dict[str, Any]: ...
    def preflight(self, *, runtime_root: Path, state_root: Path, config_root: Path) -> dict[str, Any]: ...
    def arm(self) -> dict[str, Any]: ...
    def disarm(self) -> dict[str, Any]: ...


class SystemdServiceController:
    def __init__(self) -> None:
        if os.name != "posix":
            _fail("activation.systemd.unsupported", "Milestone H production activation requires Linux/systemd")

    @staticmethod
    def _run(args: list[str], *, cwd: Path | None = None, env: dict[str, str] | None = None, timeout: int = 120) -> subprocess.CompletedProcess[str]:
        return subprocess.run(args, cwd=cwd, env=env, text=True, capture_output=True, timeout=timeout, check=False)

    def inspect(self) -> dict[str, Any]:
        rows = []
        for unit in TIMERS:
            enabled = self._run(["systemctl", "--user", "is-enabled", unit], timeout=10)
            active = self._run(["systemctl", "--user", "is-active", unit], timeout=10)
            rows.append({
                "unit": unit,
                "enabled": enabled.returncode == 0 and enabled.stdout.strip() in {"enabled", "static"},
                "active": active.returncode == 0 and active.stdout.strip() == "active",
            })
        return {
            "schema_version": 1, "timers": rows,
            "all_enabled": all(row["enabled"] for row in rows),
            "all_active": all(row["active"] for row in rows),
        }

    def stage(self, *, runtime_root: Path, state_root: Path, config_root: Path) -> dict[str, Any]:
        env = dict(os.environ)
        env.update({
            "POST_ONCE_STATE_DIR": str(state_root),
            "POST_ONCE_CONFIG_DIR": str(config_root),
            "OCPF_POST_STAGE_ONLY": "1",
        })
        script = runtime_root / "scripts" / "install-user-portfolio-timer"
        result = self._run(["sh", str(script)], cwd=runtime_root, env=env, timeout=180)
        if result.returncode:
            _fail("activation.systemd.stage_failed", "Could not stage Post-Once user units")
        return {"status": "staged", "provider_consequence": False}

    def preflight(self, *, runtime_root: Path, state_root: Path, config_root: Path) -> dict[str, Any]:
        env = dict(os.environ)
        env.update({"POST_ONCE_STATE_DIR": str(state_root), "POST_ONCE_CONFIG_DIR": str(config_root)})
        checks = [
            ["sh", str(runtime_root / "post-once"), "state", "verify"],
            ["sh", str(runtime_root / "post-once"), "run-due", "--check"],
            ["sh", str(runtime_root / "post-once"), "portfolio", "status", "--json"],
        ]
        results = []
        for args in checks:
            result = self._run(args, cwd=runtime_root, env=env, timeout=120)
            results.append({"command": " ".join(args[2:]), "exit_code": result.returncode})
            if result.returncode != 0:
                _fail("activation.preflight.failed", f"Activation preflight failed: {args[2:]}")
        return {"status": "passed", "checks": results, "provider_consequence": False}

    def arm(self) -> dict[str, Any]:
        result = self._run(["systemctl", "--user", "enable", "--now", *TIMERS], timeout=120)
        if result.returncode:
            _fail("activation.systemd.arm_failed", "Could not enable/start all Post-Once timers")
        observed = self.inspect()
        if not observed["all_enabled"] or not observed["all_active"]:
            _fail("activation.systemd.attestation_failed", "Post-Once timers are not fully enabled/active")
        return observed

    def disarm(self) -> dict[str, Any]:
        self._run(["systemctl", "--user", "disable", "--now", *TIMERS], timeout=120)
        self._run(["systemctl", "--user", "stop", *SERVICES], timeout=120)
        observed = self.inspect()
        if any(
            bool(row.get("active")) or bool(row.get("enabled"))
            for row in observed.get("timers", [])
            if isinstance(row, dict)
        ):
            _fail("activation.systemd.disarm_failed", "One or more Post-Once timers remained enabled or active")
        return observed


def _runtime_revision(runtime_root: Path) -> str:
    result = subprocess.run(
        ["git", "-C", str(runtime_root), "rev-parse", "--verify", "HEAD^{commit}"],
        text=True, capture_output=True, timeout=20, check=False,
    )
    value = result.stdout.strip()
    if result.returncode or not _SHA40_RE.fullmatch(value):
        _fail("activation.runtime_revision.unavailable", "Could not resolve exact runtime Git revision")
    dirty = subprocess.run(
        ["git", "-C", str(runtime_root), "status", "--porcelain=v1", "--untracked-files=all"],
        text=True, capture_output=True, timeout=20, check=False,
    )
    if dirty.returncode:
        _fail("activation.runtime_status.unavailable", "Could not verify runtime checkout cleanliness")
    if dirty.stdout.strip():
        _fail(
            "activation.runtime.dirty",
            "Activation requires a clean checkout so the reviewed revision exactly matches executable code",
        )
    return value


def _roots_overlap(left: Path, right: Path) -> bool:
    return left == right or left in right.parents or right in left.parents


@dataclass
class RootSwap:
    target: Path
    staged: Path
    backup: Path
    had_original: bool
    swapped: bool = False


class RestoreTransaction:
    def __init__(
        self,
        *,
        state_root: Path,
        config_root: Path,
        quarantine: Path | None,
        rows: list[dict[str, Any]],
        schedule_plan: dict[str, Any],
    ) -> None:
        self.id = str(uuid.uuid4())
        self.state_root = state_root
        self.config_root = config_root
        self.quarantine = quarantine
        self.rows = rows
        self.schedule_plan = schedule_plan
        self.swaps: list[RootSwap] = []

    def _prepare_root(self, target: Path) -> RootSwap:
        target.parent.mkdir(parents=True, exist_ok=True)
        staged = target.parent / f".{target.name}.activation-{self.id}.new"
        backup = target.parent / f".{target.name}.activation-{self.id}.old"
        if staged.exists() or backup.exists():
            _fail("activation.restore.transaction_collision", "Activation staging path already exists")
        if target.exists():
            _scan_regular_tree(target)
            shutil.copytree(target, staged, copy_function=shutil.copy2)
            had_original = True
        else:
            staged.mkdir(mode=0o700)
            had_original = False
        try:
            staged.chmod(0o700)
        except OSError:
            pass
        return RootSwap(target=target, staged=staged, backup=backup, had_original=had_original)

    def prepare(self) -> None:
        state_swap = self._prepare_root(self.state_root)
        config_swap = self._prepare_root(self.config_root)
        by_scope = {"state": state_swap, "config": config_swap}
        try:
            for row in self.rows:
                if row["action"] == "already_identical":
                    continue
                scope = row["scope"]
                relative = _safe_relative(row["path"])
                if self.quarantine is None:
                    _fail("activation.restore.quarantine_missing", "Cannot restore without verified quarantine")
                source = _manifest_object_path(self.quarantine, row["logical_id"])
                destination = by_scope[scope].staged / relative
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, destination)
                try:
                    destination.chmod(0o600)
                except OSError:
                    pass
                if _sha256_file(destination) != row["sha256"]:
                    _fail("activation.restore.copy_failed", f"Restored file digest mismatch: {relative}")
            _append_schedule_activation_events(state_swap.staged, self.schedule_plan)
            self.swaps = [state_swap, config_swap]
        except Exception:
            shutil.rmtree(state_swap.staged, ignore_errors=True)
            shutil.rmtree(config_swap.staged, ignore_errors=True)
            raise

    def swap(self) -> None:
        for item in self.swaps:
            # Mark the root touched before the first rename so rollback also
            # repairs a crash/failure between target->backup and staged->target.
            item.swapped = True
            if item.had_original:
                os.replace(item.target, item.backup)
            os.replace(item.staged, item.target)

    def rollback(self) -> None:
        for item in reversed(self.swaps):
            if not item.swapped:
                shutil.rmtree(item.staged, ignore_errors=True)
                continue
            shutil.rmtree(item.target, ignore_errors=True)
            if item.had_original and item.backup.exists():
                os.replace(item.backup, item.target)
            elif item.backup.exists():
                shutil.rmtree(item.backup, ignore_errors=True)
            item.swapped = False

    def finalize(self) -> None:
        for item in self.swaps:
            shutil.rmtree(item.backup, ignore_errors=True)
            shutil.rmtree(item.staged, ignore_errors=True)


class ActivationManager:
    def __init__(
        self, *, store: SetupStore, workspace: Path, service_controller: ServiceController | None = None
    ) -> None:
        self.store = store
        self.workspace = workspace
        self.services = service_controller or SystemdServiceController()

    def _session_context(self, session_id: str | None) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any], list[dict[str, Any]]]:
        try:
            session = self.store.session(session_id) if session_id else self.store.latest_session()
            if session is None:
                _fail("activation.session.missing", "No setup session exists")
            if session.get("operation_id") is None:
                _fail("activation.mode.invalid", "Explore-only setup cannot activate unattended publishing")
            operation = validate_operation(self.store.operation(session["operation_id"]))
            installation = validate_installation(self.store.installation_for_operation(session["operation_id"]))
            events = self.store.events(session["session_id"])
        except SetupStoreError as exc:
            raise ActivationError(exc.code, str(exc)) from exc
        return session, operation, installation, events

    def preview(
        self, *, runtime_root: str | Path, state_root: str | Path, config_root: str | Path,
        session_id: str | None = None, recovery_resolution: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        session, operation, installation, events = self._session_context(session_id)
        try:
            runtime = resolve_plain_path(
                runtime_root,
                symlink_code="activation.runtime.symlink",
                label="Activation runtime root",
            )
            state = resolve_plain_path(
                state_root,
                symlink_code="activation.target.symlink",
                label="Activation state root",
            )
            config = resolve_plain_path(
                config_root,
                symlink_code="activation.target.symlink",
                label="Activation config root",
            )
        except SetupStoreError as exc:
            raise ActivationError(exc.code, str(exc)) from exc
        for left, right, code in (
            (state, config, "activation.roots.overlap"),
            (self.workspace.resolve(), state, "activation.workspace.overlap"),
            (self.workspace.resolve(), config, "activation.workspace.overlap"),
        ):
            if _roots_overlap(left, right):
                _fail(code, "Activation roots must be isolated from each other and the setup workspace")
        if not runtime.is_dir() or runtime.is_symlink():
            _fail("activation.runtime.invalid", "Runtime root must be a plain checkout directory")
        runtime_revision = _runtime_revision(runtime)
        ever_activated = any(row["event_type"] == "automation_activated" for row in events)
        if session["stage"] == "active" and ever_activated:
            # Reactivation after a deliberate deactivation uses current durable
            # production state. Replaying the original migration/recovery bundle
            # would roll history backwards or collide with newer receipts.
            quarantine = None
            source_rows: list[dict[str, Any]] = []
            schedule_plan = _schedule_activation_plan(None)
        else:
            quarantine, source_rows = _restore_index_from_events(events)
            if quarantine is not None and _roots_overlap(self.workspace.resolve(), quarantine):
                # Quarantine is intentionally inside the setup workspace; this is expected.
                pass
            schedule_plan = _schedule_activation_plan(quarantine)
        restore = _restore_plan(
            quarantine=quarantine, rows=source_rows, state_root=state, config_root=config
        )

        gate = automation_authority.read(state)
        if gate.get("status") == "active":
            if (
                gate.get("operation_id") != operation["operation_id"]
                or gate.get("installation_id") != installation["installation_id"]
            ):
                _fail("activation.authority.conflict", "Another operation/installation already owns this automation gate")

        if session["stage"] == "active":
            if operation["status"] != "active" or installation["status"] != "active":
                _fail("activation.authority.drift", "Active setup identity is inconsistent")
            target_generation = int(operation["authority_generation"])
        else:
            required_stage = {
                "fresh": "verification_ready",
                "migrate": "verification_ready",
                "recover": "recovery_review_ready",
            }.get(session["mode"])
            if required_stage is None or session["stage"] != required_stage:
                _fail(
                    "activation.stage.not_ready",
                    f"{session['mode']} activation requires {required_stage}; observed {session['stage']}",
                )
            target_generation = int(operation["authority_generation"]) + (0 if session["mode"] == "fresh" else 1)

        resolution_normalized = None
        recovery_required_codes: list[str] = []
        if session["mode"] == "recover":
            review_event: dict[str, Any] | None = next(
                (row["evidence"] for row in reversed(events) if row["to_stage"] == "recovery_review_ready"),
                None,
            )
            if review_event is None or not review_event.get("review"):
                _fail("activation.recovery_review.missing", "Recovery review evidence is missing")
            review = _load_json(Path(str(review_event["review"])))
            raw_summary = review.get("summary")
            summary: dict[str, Any] = raw_summary if isinstance(raw_summary, dict) else {}
            recovery_required_codes = sorted(str(code) for code in summary.get("required_blocker_codes", []))
            restore_event: dict[str, Any] = next(
                (row["evidence"] for row in reversed(events) if row["to_stage"] == "restored_quarantined"),
                {},
            )
            raw_point = restore_event.get("recovery_point")
            point: dict[str, Any] = raw_point if isinstance(raw_point, dict) else {}
            source_lost_at = str(point.get("source_lost_at") or "") or None
            source_installation = str(review.get("source_installation_id") or "")
            if recovery_resolution is None:
                _fail(
                    "activation.recovery_resolution.required",
                    "Dead-host activation requires structured resolution of every recovery-review blocker",
                )
            resolution_normalized = validate_recovery_resolution(
                recovery_resolution, operation_id=operation["operation_id"],
                source_installation_id=source_installation, required_codes=recovery_required_codes,
                source_lost_at=source_lost_at,
            )

        service_before = self.services.inspect()
        if gate.get("status") != "active" and any(
            bool(row.get("active"))
            for row in service_before.get("timers", [])
            if isinstance(row, dict)
        ):
            _fail(
                "activation.unfenced_automation_active",
                "One or more unattended timers are already active while the local automation gate is inactive",
            )
        review_body = {
            "schema_version": 1,
            "mode": session["mode"],
            "session_id": session["session_id"],
            "session_revision": session["revision"],
            "operation_id": operation["operation_id"],
            "installation_id": installation["installation_id"],
            "target_authority_generation": target_generation,
            "runtime_root": str(runtime),
            "runtime_revision": runtime_revision,
            "state_root": str(state),
            "config_root": str(config),
            "state_fingerprint": _tree_fingerprint(state),
            "config_fingerprint": _tree_fingerprint(config),
            "restore": restore,
            "schedule_activation": schedule_plan,
            "prior_successful_activation": ever_activated,
            "service_before": service_before,
            "recovery_resolution": resolution_normalized,
            "recovery_required_codes": recovery_required_codes,
            "gate_before": {k: gate.get(k) for k in ("status", "operation_id", "installation_id", "authority_generation", "runtime_revision")},
        }
        review_sha = _json_sha(review_body)
        return {
            **review_body,
            "status": "already_active" if gate.get("status") == "active" else "preview",
            "review_sha256": review_sha,
            "publishing_authority": gate.get("status") == "active",
            "automation_enabled": bool(service_before.get("all_enabled") and gate.get("status") == "active"),
            "boundary": (
                "Activation preview only. It verifies exact target roots, portable-state collision policy, "
                "runtime revision, local service state and recovery fencing evidence. No provider effect or service mutation occurs."
            ),
        }

    def apply(
        self, *, runtime_root: str | Path, state_root: str | Path, config_root: str | Path,
        expected_sha256: str, session_id: str | None = None, recovery_resolution: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        with _exclusive_activation_lock(self.workspace, action="activate"):
            return self._apply_locked(
                runtime_root=runtime_root,
                state_root=state_root,
                config_root=config_root,
                expected_sha256=expected_sha256,
                session_id=session_id,
                recovery_resolution=recovery_resolution,
            )

    def _apply_locked(
        self, *, runtime_root: str | Path, state_root: str | Path, config_root: str | Path,
        expected_sha256: str, session_id: str | None = None, recovery_resolution: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        review = self.preview(
            runtime_root=runtime_root, state_root=state_root, config_root=config_root,
            session_id=session_id, recovery_resolution=recovery_resolution,
        )
        if review["review_sha256"] != expected_sha256:
            _fail("activation.review.changed", "Activation review changed")
        if review["status"] == "already_active":
            return review
        runtime = Path(review["runtime_root"])
        state = Path(review["state_root"])
        config = Path(review["config_root"])
        session, operation, installation, events = self._session_context(review["session_id"])
        quarantine, _source_rows = _restore_index_from_events(events)
        transaction = RestoreTransaction(
            state_root=state,
            config_root=config,
            quarantine=quarantine,
            rows=review["restore"]["rows"],
            schedule_plan=review["schedule_activation"],
        )
        cutover = False
        committed = False
        try:
            # An inactive/missing marker remains the actual consequence fence while
            # we stage files, user units, and validate the restored state.
            transaction.prepare()
            self.services.stage(runtime_root=runtime, state_root=state, config_root=config)
            transaction.swap()
            self.services.preflight(runtime_root=runtime, state_root=state, config_root=config)

            committed_value = self.store.commit_activation_authority(
                session["session_id"], expected_revision=session["revision"],
                target_generation=int(review["target_authority_generation"]),
                event_payload={
                    "review_sha256": review["review_sha256"],
                    "runtime_revision": review["runtime_revision"],
                    "state_root": str(state),
                    "config_root": str(config),
                    "publishing_authority": False,
                },
            )
            committed = True
            operation = committed_value["operation"]
            installation = committed_value["installation"]

            # Timers may now be enabled/start, but every unattended entrypoint is
            # still fenced by the inactive automation marker.
            armed = self.services.arm()
            if not armed.get("all_enabled") or not armed.get("all_active"):
                _fail("activation.systemd.attestation_failed", "Timer arming did not attest cleanly")

            marker = automation_authority.activate(
                root=state, operation_id=operation["operation_id"],
                installation_id=installation["installation_id"],
                authority_generation=int(operation["authority_generation"]),
                review_sha256=review["review_sha256"], runtime_revision=review["runtime_revision"],
            )
            cutover = True
            post = self.services.inspect()
            if not post.get("all_enabled") or not post.get("all_active"):
                _fail("activation.post_cutover.attestation_failed", "Timer health changed immediately after activation")
            observed_marker = automation_authority.require_active(state)
            if observed_marker["review_sha256"] != review["review_sha256"]:
                _fail("activation.post_cutover.marker_drift", "Automation marker changed after cutover")

            transaction.finalize()
            self.store.record_evidence_event(
                committed_value["session"]["session_id"], event_type="automation_activated",
                payload={
                    "review_sha256": review["review_sha256"], "runtime_revision": review["runtime_revision"],
                    "authority_generation": operation["authority_generation"],
                    "state_root": str(state), "config_root": str(config),
                    "service_attestation": post, "cutover": "automation_authority_marker",
                },
            )
            return {
                "schema_version": 1, "status": "active",
                "session": committed_value["session"], "operation": operation, "installation": installation,
                "authority": marker, "services": post,
                "publishing_authority": True, "automation_enabled": True,
                "boundary": (
                    "Unattended automation is active on this local installation. "
                    "This does not itself claim that a different host is fenced; recovery mode required external fencing evidence before cutover."
                ),
            }
        except Exception as exc:
            try:
                if cutover:
                    # Cut off future unattended effects first. Never roll durable
                    # state backwards after a provider-effect window has opened.
                    try:
                        automation_authority.deactivate(
                            root=state, review_sha256=review["review_sha256"],
                            reason="post-cutover activation attestation failed",
                        )
                    finally:
                        self.services.disarm()
                        # Once the cutover window opened, the promoted durable
                        # state is never rolled backwards. Remove pre-cutover
                        # backups instead of preserving a tempting stale rollback.
                        transaction.finalize()
                else:
                    try:
                        self.services.disarm()
                    finally:
                        transaction.rollback()
            finally:
                if committed:
                    try:
                        self.store.record_evidence_event(
                            session["session_id"], event_type="automation_activation_failed",
                            payload={
                                "review_sha256": review["review_sha256"],
                                "cutover_happened": cutover,
                                "state_rollback_attempted": not cutover,
                                "provider_replay_attempted": False,
                            },
                        )
                    except Exception:
                        pass
            if isinstance(exc, ActivationError):
                raise
            raise ActivationError("activation.apply.failed", "Activation failed closed") from exc

    def deactivate_preview(
        self, *, state_root: str | Path, reason: str, runtime_root: str | Path, session_id: str | None = None
    ) -> dict[str, Any]:
        session, operation, installation, _events = self._session_context(session_id)
        try:
            state = resolve_plain_path(
                state_root,
                symlink_code="deactivation.target.symlink",
                label="Deactivation state root",
            )
            runtime = resolve_plain_path(
                runtime_root,
                symlink_code="deactivation.runtime.symlink",
                label="Deactivation runtime root",
            )
        except SetupStoreError as exc:
            raise ActivationError(exc.code, str(exc)) from exc
        marker = automation_authority.read(state)
        if marker.get("status") != "active":
            return {
                "schema_version": 1, "status": "already_inactive", "session_id": session["session_id"],
                "publishing_authority": False, "automation_enabled": False,
            }
        if (
            marker.get("operation_id") != operation["operation_id"]
            or marker.get("installation_id") != installation["installation_id"]
            or marker.get("authority_generation") != operation["authority_generation"]
        ):
            _fail("deactivation.authority.mismatch", "Automation marker does not match setup authority")
        reason_text = str(reason or "").strip()
        if not reason_text or len(reason_text) > 500:
            _fail("deactivation.reason.required", "A bounded deactivation reason is required")
        body = {
            "schema_version": 1, "session_id": session["session_id"],
            "operation_id": operation["operation_id"], "installation_id": installation["installation_id"],
            "authority_generation": operation["authority_generation"],
            "runtime_revision": _runtime_revision(runtime), "state_root": str(state),
            "reason": reason_text, "marker_sha256": _json_sha({k:v for k,v in marker.items() if k != "path"}),
            "service_before": self.services.inspect(),
        }
        return {
            **body, "status": "preview", "review_sha256": _json_sha(body),
            "publishing_authority": True, "automation_enabled": True,
            "boundary": "Deactivation preview only. Apply will atomically close the local unattended gate before stopping timers.",
        }

    def deactivate(
        self, *, state_root: str | Path, reason: str, runtime_root: str | Path,
        expected_sha256: str, session_id: str | None = None
    ) -> dict[str, Any]:
        with _exclusive_activation_lock(self.workspace, action="deactivate"):
            return self._deactivate_locked(
                state_root=state_root,
                reason=reason,
                runtime_root=runtime_root,
                expected_sha256=expected_sha256,
                session_id=session_id,
            )

    def _deactivate_locked(
        self, *, state_root: str | Path, reason: str, runtime_root: str | Path,
        expected_sha256: str, session_id: str | None = None
    ) -> dict[str, Any]:
        review = self.deactivate_preview(
            state_root=state_root, reason=reason, runtime_root=runtime_root, session_id=session_id
        )
        if review.get("status") == "already_inactive":
            return review
        if review["review_sha256"] != expected_sha256:
            _fail("deactivation.review.changed", "Deactivation review changed")
        state = Path(review["state_root"])
        marker = automation_authority.deactivate(
            root=state, review_sha256=review["review_sha256"], reason=review["reason"]
        )
        # The marker is closed before touching systemd: even a timer wakeup racing
        # with disable will fail the unattended wrapper check.
        observed = self.services.disarm()
        session, _operation, _installation, _events = self._session_context(review["session_id"])
        self.store.record_evidence_event(
            session["session_id"], event_type="automation_deactivated",
            payload={
                "review_sha256": review["review_sha256"], "reason": review["reason"],
                "state_root": str(state),
                "marker_cutoff_first": True, "provider_replay_attempted": False,
                "service_attestation": observed,
            },
        )
        return {
            "schema_version": 1, "status": "inactive", "authority": marker, "services": observed,
            "session": session, "publishing_authority": False, "automation_enabled": False,
            "boundary": (
                "Unattended automation is deactivated. Operation identity/history and durable receipts remain current; "
                "no provider effect or state rollback was attempted."
            ),
        }
