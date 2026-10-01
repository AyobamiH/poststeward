"""Pre-K installation admission: capability proof and existing-install classification."""
from __future__ import annotations

import errno
import json
import os
from pathlib import Path
import shutil
import socket
import sqlite3
import stat
import subprocess
import sys
import tempfile
from typing import Any, Callable

from ocpf_post import automation_authority
from ocpf_post.setup_store import DB_NAME, SetupStore, SetupStoreError, resolve_plain_path


Runner = Callable[..., subprocess.CompletedProcess[str]]


def _run(
    args: list[str],
    *,
    timeout: int = 10,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(args, text=True, capture_output=True, timeout=timeout, check=False)


def _probe_root(workspace: Path) -> Path:
    probe = workspace.parent
    while not probe.exists() and probe != probe.parent:
        probe = probe.parent
    if not probe.is_dir():
        raise SetupStoreError("setup.host_probe.unavailable", "No existing directory is available for host capability proof")
    return probe


def _check(code: str, *, ready: bool, required: bool, evidence: dict[str, Any]) -> dict[str, Any]:
    return {
        "code": code,
        "status": "ready" if ready else "blocked" if required else "attention",
        "required": required,
        "evidence": evidence,
    }


def prove_host_capabilities(
    workspace: str | Path,
    *,
    production: bool = False,
    runner: Runner = _run,
) -> dict[str, Any]:
    """Prove concrete local capabilities rather than trusting version strings."""
    target = resolve_plain_path(
        workspace,
        symlink_code="setup.workspace.symlink",
        label="Bootstrap setup workspace",
    )
    checks: list[dict[str, Any]] = []

    python_ready = sys.version_info >= (3, 10)
    linux_ready = sys.platform.startswith("linux")
    posix_ready = linux_ready or sys.platform == "darwin"
    checks.append(_check(
        "host.python",
        ready=python_ready,
        required=True,
        evidence={"version": ".".join(str(x) for x in sys.version_info[:3])},
    ))
    checks.append(_check(
        "host.macos" if sys.platform == "darwin" else "host.linux",
        ready=posix_ready,
        required=True,
        evidence={"platform": sys.platform},
    ))

    root = _probe_root(target)
    with tempfile.TemporaryDirectory(prefix=".post-once-host-proof-", dir=root) as temp:
        probe = Path(temp)
        probe.chmod(0o700)

        private = probe / "private"
        private.write_text("proof\n", encoding="utf-8")
        private.chmod(0o600)
        mode_dir = stat.S_IMODE(probe.stat().st_mode)
        mode_file = stat.S_IMODE(private.stat().st_mode)
        checks.append(_check(
            "host.private_permissions",
            ready=(mode_dir & 0o077) == 0 and (mode_file & 0o077) == 0,
            required=True,
            evidence={"directory_mode": oct(mode_dir), "file_mode": oct(mode_file)},
        ))

        lock_ready = False
        lock_detail = "unsupported_platform"
        if posix_ready:
            import fcntl

            lock_path = probe / "lock"
            fd1 = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o600)
            fd2 = os.open(lock_path, os.O_RDWR)
            try:
                fcntl.flock(fd1, fcntl.LOCK_EX | fcntl.LOCK_NB)
                try:
                    fcntl.flock(fd2, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except OSError as exc:
                    lock_ready = exc.errno in {errno.EACCES, errno.EAGAIN}
                    lock_detail = "exclusive"
                else:
                    lock_detail = "second_writer_not_blocked"
                    fcntl.flock(fd2, fcntl.LOCK_UN)
            finally:
                try:
                    fcntl.flock(fd1, fcntl.LOCK_UN)
                except OSError:
                    pass
                os.close(fd2)
                os.close(fd1)
        checks.append(_check(
            "host.local_locking",
            ready=lock_ready,
            required=True,
            evidence={"mechanism": "flock", "detail": lock_detail},
        ))

        source = probe / "atomic-source"
        target_file = probe / "atomic-target"
        source.write_bytes(b"candidate")
        with source.open("r+b") as handle:
            os.fsync(handle.fileno())
        os.replace(source, target_file)
        dir_fd = os.open(probe, os.O_RDONLY)
        try:
            os.fsync(dir_fd)
        finally:
            os.close(dir_fd)
        atomic_ready = target_file.read_bytes() == b"candidate" and not source.exists()
        checks.append(_check(
            "host.atomic_replace",
            ready=atomic_ready,
            required=True,
            evidence={"directory_fsync": True},
        ))

        database = probe / "probe.sqlite3"
        connection = sqlite3.connect(database, isolation_level=None)
        sqlite_ready = False
        observed_journal = ""
        observed_sync = -1
        try:
            observed_journal = str(connection.execute("PRAGMA journal_mode=DELETE").fetchone()[0]).lower()
            connection.execute("PRAGMA synchronous=EXTRA")
            observed_sync = int(connection.execute("PRAGMA synchronous").fetchone()[0])
            connection.execute("BEGIN IMMEDIATE")
            connection.execute("CREATE TABLE proof(value TEXT NOT NULL)")
            connection.execute("INSERT INTO proof(value) VALUES('committed')")
            connection.execute("COMMIT")
        finally:
            connection.close()
        reopened = sqlite3.connect(database)
        try:
            row = reopened.execute("SELECT value FROM proof").fetchone()
            sqlite_ready = (
                observed_journal == "delete"
                and observed_sync == 3
                and row is not None
                and row[0] == "committed"
            )
        finally:
            reopened.close()
        checks.append(_check(
            "host.sqlite_durability",
            ready=sqlite_ready,
            required=True,
            evidence={
                "sqlite_version": sqlite3.sqlite_version,
                "journal_mode": observed_journal,
                "synchronous": observed_sync,
            },
        ))

    loopback_ready = False
    selected_port: int | None = None
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        server.bind(("127.0.0.1", 0))
        selected_port = int(server.getsockname()[1])
        loopback_ready = selected_port > 0
    finally:
        server.close()
    checks.append(_check(
        "host.loopback",
        ready=loopback_ready,
        required=True,
        evidence={"host": "127.0.0.1", "ephemeral_port": selected_port},
    ))

    systemctl = shutil.which("systemctl")
    systemd_ready = False
    systemd_detail = "systemctl_missing"
    if systemctl:
        try:
            result = runner([systemctl, "--user", "show-environment"], timeout=10)
        except (OSError, subprocess.SubprocessError):
            systemd_detail = "user_manager_unreachable"
        else:
            systemd_ready = result.returncode == 0
            systemd_detail = "user_manager_reachable" if systemd_ready else "user_manager_unreachable"
    if sys.platform == "darwin":
        try:
            systemd_ready = runner(['launchctl', 'print', f'gui/{os.getuid()}'], timeout=10).returncode == 0
            systemd_detail = 'desktop_user_session_reachable' if systemd_ready else 'desktop_user_session_unreachable'
        except (OSError, subprocess.SubprocessError):
            systemd_ready = False
            systemd_detail = 'launchctl_unavailable'
    checks.append(_check(
        "host.launchd_user" if sys.platform == "darwin" else "host.systemd_user",
        ready=systemd_ready,
        required=production,
        evidence={"systemctl": systemctl or "", "detail": systemd_detail},
    ))

    blockers = [row["code"] for row in checks if row["required"] and row["status"] != "ready"]
    return {
        "schema_version": 1,
        "status": "READY" if not blockers else "BLOCKED",
        "profile": "production" if production else "bootstrap",
        "workspace": str(target),
        "checks": checks,
        "blockers": blockers,
        "publishing_authority": False,
        "provider_consequence_attempted": False,
    }


def _read_release_state(state_root: Path) -> dict[str, Any]:
    path = state_root / "runtime-release.json"
    if not path.is_file() or path.is_symlink():
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return {"status": "invalid"}
    return value if isinstance(value, dict) else {"status": "invalid"}


def _dir_has_state(path: Path) -> bool:
    try:
        return path.is_dir() and any(path.iterdir())
    except OSError:
        return True


def classify_existing_installation(
    workspace: str | Path,
    *,
    state_root: str | Path,
    config_root: str | Path,
) -> dict[str, Any]:
    """Classify before guided setup decides whether to start, resume, verify or refuse."""
    workspace_path = resolve_plain_path(
        workspace,
        symlink_code="setup.workspace.symlink",
        label="Bootstrap setup workspace",
    )
    state = Path(state_root).expanduser().absolute()
    config = Path(config_root).expanduser().absolute()
    release = _read_release_state(state)
    release_status = str(release.get("status") or "")
    interrupted_release = release_status in {"switching", "rollback_failed"}

    store = SetupStore(workspace_path)
    db_exists = (workspace_path / DB_NAME).is_file()
    session: dict[str, Any] | None = None
    operation: dict[str, Any] | None = None
    installation: dict[str, Any] | None = None
    store_error: str | None = None
    if db_exists:
        try:
            session = store.latest_session()
            if session and session.get("operation_id"):
                operation = store.operation(str(session["operation_id"]))
                installation = store.installation_for_operation(str(session["operation_id"]))
        except SetupStoreError as exc:
            store_error = exc.code

    try:
        marker = automation_authority.read(state)
    except automation_authority.AutomationAuthorityError:
        marker = {"status": "invalid", "reason": "authority_marker_invalid"}

    classification = "new_host"
    action = "start"
    reasons: list[str] = []

    if store_error or marker.get("status") == "invalid":
        classification = "inconsistent_authority"
        action = "refuse_and_repair"
        reasons.append(store_error or "authority_marker_invalid")
    elif interrupted_release:
        classification = "interrupted_runtime_change"
        action = "repair_runtime"
        reasons.append(f"runtime_release.{release_status}")
    elif session is None:
        if marker.get("status") == "active":
            classification = "inconsistent_authority"
            action = "refuse_and_repair"
            reasons.append("active_marker_without_setup_store")
        elif _dir_has_state(state) or _dir_has_state(config) or release_status:
            classification = "legacy_existing_installation"
            action = "inspect_or_migrate"
            reasons.append("existing_runtime_state_without_bootstrap_identity")
    elif session["session_status"] == "open":
        classification = "incomplete_bootstrap"
        action = "resume"
        reasons.append(f"setup_stage.{session['stage']}")
    elif session["stage"] == "explore_ready":
        classification = "explore_only"
        action = "verify"
    elif session["stage"] == "aborted":
        classification = "aborted_setup"
        action = "inspect_then_new_workspace"
    elif session["stage"] == "active":
        identities_match = bool(
            operation
            and installation
            and marker.get("status") == "active"
            and marker.get("operation_id") == operation.get("operation_id")
            and marker.get("installation_id") == installation.get("installation_id")
            and marker.get("authority_generation") == operation.get("authority_generation")
            and operation.get("active_installation_id") == installation.get("installation_id")
        )
        if marker.get("status") == "active" and identities_match:
            classification = "active_installation"
            action = "verify"
        elif marker.get("status") == "inactive":
            classification = "configured_inactive"
            action = "verify_or_activate"
        else:
            classification = "inconsistent_authority"
            action = "refuse_and_repair"
            reasons.append("setup_and_marker_authority_disagree")
    else:
        classification = "incomplete_bootstrap"
        action = "resume"
        reasons.append(f"setup_stage.{session['stage']}")

    return {
        "schema_version": 1,
        "classification": classification,
        "recommended_action": action,
        "reasons": reasons,
        "workspace": str(workspace_path),
        "setup": {
            "session_id": session.get("session_id") if session else None,
            "mode": session.get("mode") if session else None,
            "stage": session.get("stage") if session else None,
            "session_status": session.get("session_status") if session else None,
            "operation_id": operation.get("operation_id") if operation else None,
            "installation_id": installation.get("installation_id") if installation else None,
        },
        "automation_marker_status": marker.get("status"),
        "runtime_release_status": release_status or None,
        "publishing_authority": marker.get("status") == "active",
        "provider_consequence_attempted": False,
    }
