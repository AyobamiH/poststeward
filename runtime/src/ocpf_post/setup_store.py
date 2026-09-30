"""Crash-consistent bootstrap setup control store.

This store is intentionally separate from Post-Once production state. It contains
setup identities, non-secret staged configuration and setup transition evidence.
It never stores provider credentials or grants publication authority.
"""
from __future__ import annotations

from contextlib import contextmanager
import json
import os
from pathlib import Path
import sqlite3
import stat
import sys
from typing import Any, Iterator

from ocpf_post.setup_contracts import (
    new_installation,
    new_operation,
    new_setup_session,
    transition_setup_session,
    validate_installation,
    validate_operation,
    validate_setup_session,
)
from ocpf_post.state import config_dir as production_config_dir
from ocpf_post.state import state_dir as production_state_dir

STORE_SCHEMA_VERSION = 1
DB_NAME = "setup-control.sqlite3"
UNSAFE_REMOTE_FILESYSTEMS = frozenset(
    {"nfs", "nfs4", "cifs", "smbfs", "smb3", "sshfs", "fuse.sshfs", "9p", "drvfs"}
)


class SetupStoreError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def _fail(code: str, message: str) -> None:
    raise SetupStoreError(code, message)


def _json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)


def _json_object(value: str) -> dict[str, Any]:
    loaded = json.loads(value)
    if not isinstance(loaded, dict):
        _fail("setup.store.corrupt", "Expected an object in setup control state")
    return loaded


def default_workspace() -> Path:
    configured = os.environ.get("OCPF_POST_SETUP_STATE_DIR")
    if configured:
        path = Path(configured).expanduser()
        if not path.is_absolute():
            _fail("setup.workspace.invalid", "OCPF_POST_SETUP_STATE_DIR must be an absolute path")
        return path.resolve()
    xdg = os.environ.get("XDG_STATE_HOME")
    if xdg:
        base = Path(xdg).expanduser()
        if not base.is_absolute():
            base = Path.home() / ".local" / "state"
    else:
        base = Path.home() / ".local" / "state"
    return (base / "oneclickpostfactory" / "post-once-bootstrap" / "setup").resolve()


def resolve_workspace(value: str | Path | None = None) -> Path:
    candidate = default_workspace() if value is None else value
    path = resolve_plain_path(
        candidate,
        symlink_code="setup.workspace.symlink",
        label="Bootstrap setup workspace",
    )
    _reject_production_collision(path)
    return path


def resolve_plain_path(
    value: str | Path,
    *,
    symlink_code: str,
    label: str,
) -> Path:
    """Resolve an authority-sensitive path without erasing symlink provenance.

    Every existing component in the operator-supplied path is inspected with
    lstat before lexical normalisation. This prevents a top-level or ancestor
    symlink from being hidden by Path.resolve().
    """
    raw = Path(value).expanduser()
    absolute = raw if raw.is_absolute() else Path.cwd() / raw
    cursor = Path(absolute.anchor)
    for part in absolute.parts[1:]:
        if part in {"", "."}:
            continue
        if part == "..":
            cursor = cursor.parent
            continue
        cursor = cursor / part
        try:
            mode = cursor.lstat().st_mode
        except FileNotFoundError:
            continue
        except OSError as exc:
            raise SetupStoreError(
                "setup.path.metadata_unavailable",
                f"Could not inspect {label} path component: {cursor}",
            ) from exc
        if stat.S_ISLNK(mode):
            _fail(
                symlink_code,
                f"{label} may not traverse a symbolic link: {cursor}",
            )
    return Path(os.path.abspath(os.fspath(absolute)))


def _resolved_no_create(path: Path) -> Path:
    return path.expanduser().resolve(strict=False)


def _overlaps(left: Path, right: Path) -> bool:
    return left == right or left in right.parents or right in left.parents


def _reject_production_collision(workspace: Path) -> None:
    candidate = _resolved_no_create(workspace)
    for name, root in (
        ("production state", production_state_dir()),
        ("production config", production_config_dir()),
    ):
        production = _resolved_no_create(root)
        if _overlaps(candidate, production):
            _fail(
                "setup.workspace.production_collision",
                f"Bootstrap setup workspace overlaps {name}: {production}",
            )


def _decode_mount_path(value: str) -> str:
    return (
        value.replace("\\040", " ")
        .replace("\\011", "\t")
        .replace("\\012", "\n")
        .replace("\\134", "\\")
    )


def linux_filesystem_type(path: Path) -> str | None:
    mountinfo = Path("/proc/self/mountinfo")
    if not sys.platform.startswith("linux") or not mountinfo.exists():
        return None
    target = path.resolve(strict=False)
    probe = target if target.exists() else target.parent
    while not probe.exists() and probe != probe.parent:
        probe = probe.parent
    best_mount: Path | None = None
    best_type: str | None = None
    try:
        lines = mountinfo.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return None
    for line in lines:
        before, separator, after = line.partition(" - ")
        if not separator:
            continue
        fields = before.split()
        after_fields = after.split()
        if len(fields) < 5 or not after_fields:
            continue
        mount_path = Path(_decode_mount_path(fields[4]))
        try:
            probe.relative_to(mount_path)
        except ValueError:
            continue
        if best_mount is None or len(str(mount_path)) > len(str(best_mount)):
            best_mount = mount_path
            best_type = after_fields[0]
    return best_type


def validate_workspace_filesystem(path: Path) -> str | None:
    filesystem = linux_filesystem_type(path)
    if filesystem in UNSAFE_REMOTE_FILESYSTEMS:
        _fail(
            "setup.workspace.remote_filesystem",
            f"Setup control state requires local host storage; filesystem {filesystem} is unsupported",
        )
    return filesystem


def _ensure_private_workspace(path: Path) -> None:
    _reject_production_collision(path)
    validate_workspace_filesystem(path)
    if path.exists() and path.is_symlink():
        _fail("setup.workspace.symlink", "Setup workspace may not be a symbolic link")
    path.mkdir(parents=True, exist_ok=True)
    try:
        path.chmod(0o700)
    except OSError:
        pass
    if not path.is_dir():
        _fail("setup.workspace.invalid", "Setup workspace is not a directory")
    mode = stat.S_IMODE(path.stat().st_mode)
    if mode & 0o077:
        _fail("setup.workspace.permissions", "Setup workspace must not be accessible to group/other users")


SCHEMA_STATEMENTS = (
    """
    CREATE TABLE operations (
        operation_id TEXT PRIMARY KEY,
        status TEXT NOT NULL,
        payload_json TEXT NOT NULL,
        created_at TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE installations (
        installation_id TEXT PRIMARY KEY,
        operation_id TEXT NOT NULL REFERENCES operations(operation_id) ON DELETE RESTRICT,
        status TEXT NOT NULL,
        payload_json TEXT NOT NULL,
        created_at TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE setup_sessions (
        session_id TEXT PRIMARY KEY,
        mode TEXT NOT NULL,
        operation_id TEXT REFERENCES operations(operation_id) ON DELETE RESTRICT,
        stage TEXT NOT NULL,
        session_status TEXT NOT NULL,
        revision INTEGER NOT NULL CHECK (revision >= 1),
        last_transition_id TEXT,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        payload_json TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE staged_config (
        session_id TEXT PRIMARY KEY REFERENCES setup_sessions(session_id) ON DELETE RESTRICT,
        timezone TEXT NOT NULL,
        pace_profile TEXT NOT NULL,
        daily_originals INTEGER NOT NULL CHECK (daily_originals BETWEEN 1 AND 100),
        hard_ceiling INTEGER NOT NULL CHECK (hard_ceiling = 100),
        updated_at TEXT NOT NULL,
        payload_json TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE setup_events (
        event_id INTEGER PRIMARY KEY AUTOINCREMENT,
        session_id TEXT NOT NULL REFERENCES setup_sessions(session_id) ON DELETE RESTRICT,
        transition_id TEXT UNIQUE,
        event_type TEXT NOT NULL,
        from_stage TEXT,
        to_stage TEXT,
        revision INTEGER NOT NULL CHECK (revision >= 1),
        occurred_at TEXT NOT NULL,
        payload_json TEXT NOT NULL
    )
    """,
    "CREATE INDEX setup_events_session_idx ON setup_events(session_id, event_id)",
    "CREATE INDEX setup_sessions_status_idx ON setup_sessions(session_status, updated_at)",
)


class SetupStore:
    def __init__(self, workspace: str | Path | None = None) -> None:
        self.workspace = resolve_workspace(workspace)
        self.db_path = self.workspace / DB_NAME

    def _configure(self, connection: sqlite3.Connection, *, writable: bool) -> None:
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout=5000")
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA trusted_schema=OFF")
        if writable:
            journal = str(connection.execute("PRAGMA journal_mode=DELETE").fetchone()[0]).lower()
            if journal != "delete":
                _fail("setup.store.journal_mode", f"Expected SQLite DELETE journal mode, observed {journal}")
            connection.execute("PRAGMA synchronous=EXTRA")
            synchronous = int(connection.execute("PRAGMA synchronous").fetchone()[0])
            if synchronous != 3:
                _fail("setup.store.synchronous", f"Expected SQLite synchronous=EXTRA, observed {synchronous}")
        else:
            connection.execute("PRAGMA query_only=ON")

    def _schema_version(self, connection: sqlite3.Connection) -> int:
        return int(connection.execute("PRAGMA user_version").fetchone()[0])

    def _initialize_schema(self, connection: sqlite3.Connection) -> None:
        version = self._schema_version(connection)
        if version > STORE_SCHEMA_VERSION:
            _fail(
                "setup.store.schema_newer",
                f"Setup control store schema {version} is newer than supported {STORE_SCHEMA_VERSION}",
            )
        if version == STORE_SCHEMA_VERSION:
            return
        if version != 0:
            _fail("setup.store.schema_migration_required", f"Unsupported older setup store schema {version}")
        connection.execute("BEGIN IMMEDIATE")
        try:
            for statement in SCHEMA_STATEMENTS:
                connection.execute(statement)
            connection.execute(f"PRAGMA user_version={STORE_SCHEMA_VERSION}")
            connection.execute("COMMIT")
        except Exception:
            connection.execute("ROLLBACK")
            raise

    @contextmanager
    def writable(self) -> Iterator[sqlite3.Connection]:
        _ensure_private_workspace(self.workspace)
        connection = sqlite3.connect(
            self.db_path,
            timeout=5.0,
            isolation_level=None,
        )
        try:
            self._configure(connection, writable=True)
            self._initialize_schema(connection)
            try:
                self.db_path.chmod(0o600)
            except OSError:
                pass
            yield connection
        finally:
            connection.close()

    @contextmanager
    def readonly(self) -> Iterator[sqlite3.Connection]:
        if not self.db_path.is_file():
            _fail("setup.store.missing", "No bootstrap setup control store exists")
        uri = self.db_path.resolve().as_uri() + "?mode=ro"
        connection = sqlite3.connect(uri, uri=True, timeout=5.0, isolation_level=None)
        try:
            self._configure(connection, writable=False)
            version = self._schema_version(connection)
            if version != STORE_SCHEMA_VERSION:
                code = "setup.store.schema_newer" if version > STORE_SCHEMA_VERSION else "setup.store.schema_migration_required"
                _fail(code, f"Unsupported setup control store schema {version}")
            yield connection
        finally:
            connection.close()

    @contextmanager
    def transaction(self, connection: sqlite3.Connection) -> Iterator[sqlite3.Connection]:
        connection.execute("BEGIN IMMEDIATE")
        try:
            yield connection
            connection.execute("COMMIT")
        except Exception:
            if connection.in_transaction:
                connection.execute("ROLLBACK")
            raise

    def _open_session_row(self, connection: sqlite3.Connection) -> sqlite3.Row | None:
        return connection.execute(
            "SELECT * FROM setup_sessions WHERE session_status='open' ORDER BY rowid DESC LIMIT 1"
        ).fetchone()

    def _assert_no_open_session(self, connection: sqlite3.Connection) -> None:
        row = self._open_session_row(connection)
        if row is not None:
            _fail(
                "setup.session.open_exists",
                f"An incomplete setup session already exists: {row['session_id']}",
            )

    def _assert_no_existing_session(self, connection: sqlite3.Connection) -> None:
        row = connection.execute(
            "SELECT * FROM setup_sessions ORDER BY rowid DESC LIMIT 1"
        ).fetchone()
        if row is None:
            return
        if row["session_status"] == "open":
            _fail(
                "setup.session.open_exists",
                f"An incomplete setup session already exists: {row['session_id']}",
            )
        _fail(
            "setup.installation.exists",
            "This bootstrap workspace already has durable installation history; "
            "verify/repair it or use a new reviewed workspace instead of silently starting over",
        )

    def create_fresh(
        self,
        operator_label: str,
        *,
        machine_label: str | None = None,
    ) -> dict[str, Any]:
        operation = new_operation(operator_label)
        installation = new_installation(operation["operation_id"], machine_label=machine_label)
        session = new_setup_session("fresh", operation_id=operation["operation_id"])
        with self.writable() as connection:
            with self.transaction(connection):
                self._assert_no_existing_session(connection)
                connection.execute(
                    "INSERT INTO operations(operation_id,status,payload_json,created_at) VALUES(?,?,?,?)",
                    (operation["operation_id"], operation["status"], _json(operation), session["created_at"]),
                )
                connection.execute(
                    "INSERT INTO installations(installation_id,operation_id,status,payload_json,created_at) VALUES(?,?,?,?,?)",
                    (
                        installation["installation_id"],
                        installation["operation_id"],
                        installation["status"],
                        _json(installation),
                        session["created_at"],
                    ),
                )
                self._insert_session(connection, session)
                self._insert_event(
                    connection,
                    session=session,
                    event_type="setup_started",
                    transition_id=None,
                    from_stage=None,
                    to_stage=session["stage"],
                    payload={"mode": "fresh", "installation_id": installation["installation_id"]},
                )
        return {"operation": operation, "installation": installation, "session": session}

    def create_explore(self) -> dict[str, Any]:
        session = new_setup_session("explore")
        with self.writable() as connection:
            with self.transaction(connection):
                self._assert_no_existing_session(connection)
                self._insert_session(connection, session)
                self._insert_event(
                    connection,
                    session=session,
                    event_type="setup_started",
                    transition_id=None,
                    from_stage=None,
                    to_stage=session["stage"],
                    payload={"mode": "explore"},
                )
        return {"operation": None, "installation": None, "session": session}


    def create_migration_source(
        self,
        operator_label: str,
        *,
        machine_label: str | None = None,
    ) -> dict[str, Any]:
        operation = new_operation(operator_label)
        installation = new_installation(operation["operation_id"], machine_label=machine_label)
        operation["status"] = "active"
        operation["active_installation_id"] = installation["installation_id"]
        installation["status"] = "active"
        installation["authority_generation"] = operation["authority_generation"]
        validate_operation(operation)
        validate_installation(installation)
        session = new_setup_session("migrate", operation_id=operation["operation_id"])
        with self.writable() as connection:
            with self.transaction(connection):
                self._assert_no_existing_session(connection)
                connection.execute(
                    "INSERT INTO operations(operation_id,status,payload_json,created_at) VALUES(?,?,?,?)",
                    (operation["operation_id"], operation["status"], _json(operation), session["created_at"]),
                )
                connection.execute(
                    "INSERT INTO installations(installation_id,operation_id,status,payload_json,created_at) VALUES(?,?,?,?,?)",
                    (
                        installation["installation_id"],
                        installation["operation_id"],
                        installation["status"],
                        _json(installation),
                        session["created_at"],
                    ),
                )
                self._insert_session(connection, session)
                self._insert_event(
                    connection,
                    session=session,
                    event_type="migration_source_started",
                    transition_id=None,
                    from_stage=None,
                    to_stage=session["stage"],
                    payload={
                        "mode": "migrate",
                        "installation_id": installation["installation_id"],
                        "authority_generation": operation["authority_generation"],
                        "publishing_authority": False,
                    },
                )
        return {"operation": operation, "installation": installation, "session": session}

    def retire_migration_source(
        self,
        session_id: str,
        *,
        expected_revision: int,
        event_payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        with self.writable() as connection:
            with self.transaction(connection):
                row = connection.execute(
                    "SELECT * FROM setup_sessions WHERE session_id=?",
                    (session_id,),
                ).fetchone()
                if row is None:
                    _fail("setup.session.missing", f"Unknown setup session: {session_id}")
                current = self._session_from_row(row)
                if current["mode"] != "migrate" or current["stage"] != "preflight_ready":
                    _fail("setup.migration.source_not_ready", "Migration source is not ready to retire")
                if current["revision"] != expected_revision:
                    _fail(
                        "setup.revision.conflict",
                        f"Expected setup revision {expected_revision}, observed {current['revision']}",
                    )
                op_row = connection.execute(
                    "SELECT payload_json FROM operations WHERE operation_id=?",
                    (current["operation_id"],),
                ).fetchone()
                inst_row = connection.execute(
                    "SELECT payload_json FROM installations WHERE operation_id=? ORDER BY rowid DESC LIMIT 1",
                    (current["operation_id"],),
                ).fetchone()
                if op_row is None or inst_row is None:
                    _fail("setup.store.corrupt", "Migration source authority identity is missing")
                operation = validate_operation(_json_object(op_row["payload_json"]))
                installation = validate_installation(_json_object(inst_row["payload_json"]))
                if (
                    operation["status"] != "active"
                    or installation["status"] != "active"
                    or operation["active_installation_id"] != installation["installation_id"]
                    or operation["authority_generation"] != installation["authority_generation"]
                ):
                    _fail("setup.migration.source_authority_invalid", "Migration source is not the active authority holder")

                operation = dict(operation)
                installation = dict(installation)
                operation["status"] = "prepared"
                operation["active_installation_id"] = None
                installation["status"] = "retired"
                validate_operation(operation)
                validate_installation(installation)
                updated = transition_setup_session(current, "source_drained")

                connection.execute(
                    "UPDATE operations SET status=?,payload_json=? WHERE operation_id=?",
                    (operation["status"], _json(operation), operation["operation_id"]),
                )
                connection.execute(
                    "UPDATE installations SET status=?,payload_json=? WHERE installation_id=?",
                    (installation["status"], _json(installation), installation["installation_id"]),
                )
                connection.execute(
                    """
                    UPDATE setup_sessions
                    SET stage=?,session_status=?,revision=?,last_transition_id=?,updated_at=?,payload_json=?
                    WHERE session_id=? AND revision=?
                    """,
                    (
                        updated["stage"],
                        updated["session_status"],
                        updated["revision"],
                        updated.get("last_transition_id"),
                        updated["updated_at"],
                        _json(updated),
                        session_id,
                        expected_revision,
                    ),
                )
                if connection.execute("SELECT changes()").fetchone()[0] != 1:
                    _fail("setup.revision.conflict", "Setup revision changed during source retirement")
                self._insert_event(
                    connection,
                    session=updated,
                    event_type="migration_source_retired",
                    transition_id=updated.get("last_transition_id"),
                    from_stage=current["stage"],
                    to_stage=updated["stage"],
                    payload=event_payload or {},
                )
                return {
                    "operation": operation,
                    "installation": installation,
                    "session": updated,
                }

    def create_migration_target(
        self,
        operation: dict[str, Any],
        source_installation: dict[str, Any],
        *,
        machine_label: str | None = None,
    ) -> dict[str, Any]:
        operation = validate_operation(dict(operation))
        source_installation = validate_installation(dict(source_installation))
        if operation["status"] != "prepared" or operation.get("active_installation_id") is not None:
            _fail("setup.migration.operation_not_transferable", "Transferred operation is not prepared for a new installation")
        if (
            source_installation["operation_id"] != operation["operation_id"]
            or source_installation["status"] != "retired"
            or source_installation.get("authority_generation") != operation["authority_generation"]
        ):
            _fail("setup.migration.source_not_retired", "Transferred source installation is not retired")
        target = new_installation(operation["operation_id"], machine_label=machine_label)
        session = new_setup_session("migrate", operation_id=operation["operation_id"])
        with self.writable() as connection:
            with self.transaction(connection):
                self._assert_no_existing_session(connection)
                connection.execute(
                    "INSERT INTO operations(operation_id,status,payload_json,created_at) VALUES(?,?,?,?)",
                    (operation["operation_id"], operation["status"], _json(operation), session["created_at"]),
                )
                connection.execute(
                    "INSERT INTO installations(installation_id,operation_id,status,payload_json,created_at) VALUES(?,?,?,?,?)",
                    (
                        source_installation["installation_id"],
                        source_installation["operation_id"],
                        source_installation["status"],
                        _json(source_installation),
                        session["created_at"],
                    ),
                )
                connection.execute(
                    "INSERT INTO installations(installation_id,operation_id,status,payload_json,created_at) VALUES(?,?,?,?,?)",
                    (
                        target["installation_id"],
                        target["operation_id"],
                        target["status"],
                        _json(target),
                        session["created_at"],
                    ),
                )
                self._insert_session(connection, session)
                self._insert_event(
                    connection,
                    session=session,
                    event_type="migration_target_started",
                    transition_id=None,
                    from_stage=None,
                    to_stage=session["stage"],
                    payload={
                        "source_installation_id": source_installation["installation_id"],
                        "target_installation_id": target["installation_id"],
                        "source_authority_generation": operation["authority_generation"],
                        "publishing_authority": False,
                    },
                )
        return {
            "operation": operation,
            "source_installation": source_installation,
            "installation": target,
            "session": session,
        }

    def create_recovery_target(
        self,
        operation: dict[str, Any],
        source_installation: dict[str, Any],
        *,
        machine_label: str | None = None,
    ) -> dict[str, Any]:
        operation = validate_operation(dict(operation))
        source_installation = validate_installation(dict(source_installation))
        if (
            operation["status"] != "active"
            or source_installation["status"] != "active"
            or operation["active_installation_id"] != source_installation["installation_id"]
            or source_installation["operation_id"] != operation["operation_id"]
            or operation["authority_generation"] != source_installation["authority_generation"]
        ):
            _fail(
                "setup.recovery.source_snapshot_invalid",
                "Dead-host recovery requires a coherent active source identity from the recovery point",
            )

        recovered_operation = dict(operation)
        recovered_source = dict(source_installation)
        recovered_operation["status"] = "recovery_review"
        recovered_operation["active_installation_id"] = None
        recovered_source["status"] = "recovery_unknown"
        validate_operation(recovered_operation)
        validate_installation(recovered_source)

        target = new_installation(recovered_operation["operation_id"], machine_label=machine_label)
        session = new_setup_session("recover", operation_id=recovered_operation["operation_id"])

        with self.writable() as connection:
            with self.transaction(connection):
                self._assert_no_existing_session(connection)
                connection.execute(
                    "INSERT INTO operations(operation_id,status,payload_json,created_at) VALUES(?,?,?,?)",
                    (
                        recovered_operation["operation_id"],
                        recovered_operation["status"],
                        _json(recovered_operation),
                        session["created_at"],
                    ),
                )
                connection.execute(
                    "INSERT INTO installations(installation_id,operation_id,status,payload_json,created_at) VALUES(?,?,?,?,?)",
                    (
                        recovered_source["installation_id"],
                        recovered_source["operation_id"],
                        recovered_source["status"],
                        _json(recovered_source),
                        session["created_at"],
                    ),
                )
                connection.execute(
                    "INSERT INTO installations(installation_id,operation_id,status,payload_json,created_at) VALUES(?,?,?,?,?)",
                    (
                        target["installation_id"],
                        target["operation_id"],
                        target["status"],
                        _json(target),
                        session["created_at"],
                    ),
                )
                self._insert_session(connection, session)
                self._insert_event(
                    connection,
                    session=session,
                    event_type="recovery_target_started",
                    transition_id=None,
                    from_stage=None,
                    to_stage=session["stage"],
                    payload={
                        "source_installation_id": recovered_source["installation_id"],
                        "target_installation_id": target["installation_id"],
                        "source_authority_generation": recovered_operation["authority_generation"],
                        "source_status": "recovery_unknown",
                        "publishing_authority": False,
                    },
                )
        return {
            "operation": recovered_operation,
            "source_installation": recovered_source,
            "installation": target,
            "session": session,
        }

    def record_evidence_event(
        self,
        session_id: str,
        *,
        event_type: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        with self.writable() as connection:
            with self.transaction(connection):
                row = connection.execute(
                    "SELECT * FROM setup_sessions WHERE session_id=?",
                    (session_id,),
                ).fetchone()
                if row is None:
                    _fail("setup.session.missing", f"Unknown setup session: {session_id}")
                current = self._session_from_row(row)
                self._insert_event(
                    connection,
                    session=current,
                    event_type=event_type,
                    transition_id=None,
                    from_stage=current["stage"],
                    to_stage=current["stage"],
                    payload=payload,
                )
                return current

    def commit_activation_authority(
        self,
        session_id: str,
        *,
        expected_revision: int,
        target_generation: int,
        event_payload: dict[str, Any],
    ) -> dict[str, Any]:
        """Commit local operation/install ownership before the automation marker cutover.

        This transaction has no provider consequence. The unattended runtime remains
        fenced by automation-authority.json until the activation orchestrator performs
        the final atomic marker write.
        """
        if type(target_generation) is not int or target_generation < 1:
            _fail("setup.activation.generation_invalid", "Activation generation must be a positive integer")
        with self.writable() as connection:
            with self.transaction(connection):
                row = connection.execute(
                    "SELECT * FROM setup_sessions WHERE session_id=?",
                    (session_id,),
                ).fetchone()
                if row is None:
                    _fail("setup.session.missing", f"Unknown setup session: {session_id}")
                current = self._session_from_row(row)
                if current["revision"] != expected_revision:
                    _fail(
                        "setup.revision.conflict",
                        f"Expected setup revision {expected_revision}, observed {current['revision']}",
                    )
                if current["mode"] not in {"fresh", "migrate", "recover"}:
                    _fail("setup.activation.mode_invalid", "This setup mode cannot own unattended automation")

                op_row = connection.execute(
                    "SELECT payload_json FROM operations WHERE operation_id=?",
                    (current["operation_id"],),
                ).fetchone()
                inst_row = connection.execute(
                    "SELECT payload_json FROM installations WHERE operation_id=? ORDER BY rowid DESC LIMIT 1",
                    (current["operation_id"],),
                ).fetchone()
                if op_row is None or inst_row is None:
                    _fail("setup.store.corrupt", "Activation authority identity is missing")
                operation = validate_operation(_json_object(op_row["payload_json"]))
                installation = validate_installation(_json_object(inst_row["payload_json"]))

                if current["stage"] == "active":
                    if (
                        operation["status"] != "active"
                        or installation["status"] != "active"
                        or operation["active_installation_id"] != installation["installation_id"]
                        or operation["authority_generation"] != installation["authority_generation"]
                        or operation["authority_generation"] != target_generation
                    ):
                        _fail("setup.activation.authority_drift", "Active setup authority is internally inconsistent")
                    self._insert_event(
                        connection,
                        session=current,
                        event_type="activation_authority_reconfirmed",
                        transition_id=None,
                        from_stage=current["stage"],
                        to_stage=current["stage"],
                        payload=event_payload,
                    )
                    return {
                        "operation": operation,
                        "installation": installation,
                        "session": current,
                    }

                allowed_start = {
                    "fresh": "verification_ready",
                    "migrate": "verification_ready",
                    "recover": "recovery_review_ready",
                }[current["mode"]]
                if current["stage"] != allowed_start:
                    _fail(
                        "setup.activation.stage_invalid",
                        f"Activation requires {allowed_start}; observed {current['stage']}",
                    )

                source_generation = int(operation["authority_generation"])
                expected_generation = (
                    source_generation
                    if current["mode"] == "fresh"
                    else source_generation + 1
                )
                if target_generation != expected_generation:
                    _fail(
                        "setup.activation.generation_mismatch",
                        f"Expected activation generation {expected_generation}, observed {target_generation}",
                    )
                if operation["status"] not in {"prepared", "recovery_review"}:
                    _fail("setup.activation.operation_not_ready", "Operation is not prepared for activation")
                if installation["status"] != "candidate":
                    _fail("setup.activation.installation_not_candidate", "Target installation is not a candidate")

                updated = current
                while updated["stage"] != "active":
                    previous = updated
                    next_stage = {
                        "recovery_review_ready": "verification_ready",
                        "verification_ready": "dry_accepted",
                        "dry_accepted": "activation_ready",
                        "activation_ready": "active",
                    }.get(updated["stage"])
                    if next_stage is None:
                        _fail(
                            "setup.activation.stage_invalid",
                            f"Cannot advance activation from {updated['stage']}",
                        )
                    updated = transition_setup_session(updated, next_stage)
                    self._insert_event(
                        connection,
                        session=updated,
                        event_type="activation_stage_committed",
                        transition_id=updated.get("last_transition_id"),
                        from_stage=previous["stage"],
                        to_stage=updated["stage"],
                        payload=event_payload,
                    )

                operation = dict(operation)
                installation = dict(installation)
                operation["status"] = "active"
                operation["active_installation_id"] = installation["installation_id"]
                operation["authority_generation"] = target_generation
                installation["status"] = "active"
                installation["authority_generation"] = target_generation
                validate_operation(operation)
                validate_installation(installation)

                connection.execute(
                    "UPDATE operations SET status=?,payload_json=? WHERE operation_id=?",
                    (operation["status"], _json(operation), operation["operation_id"]),
                )
                connection.execute(
                    "UPDATE installations SET status=?,payload_json=? WHERE installation_id=?",
                    (installation["status"], _json(installation), installation["installation_id"]),
                )
                connection.execute(
                    """
                    UPDATE setup_sessions
                    SET stage=?,session_status=?,revision=?,last_transition_id=?,updated_at=?,payload_json=?
                    WHERE session_id=? AND revision=?
                    """,
                    (
                        updated["stage"],
                        updated["session_status"],
                        updated["revision"],
                        updated.get("last_transition_id"),
                        updated["updated_at"],
                        _json(updated),
                        session_id,
                        expected_revision,
                    ),
                )
                if connection.execute("SELECT changes()").fetchone()[0] != 1:
                    _fail("setup.revision.conflict", "Setup revision changed during activation commit")
                return {
                    "operation": operation,
                    "installation": installation,
                    "session": updated,
                }

    def _insert_session(self, connection: sqlite3.Connection, session: dict[str, Any]) -> None:
        validate_setup_session(session)
        connection.execute(
            """
            INSERT INTO setup_sessions(
                session_id,mode,operation_id,stage,session_status,revision,last_transition_id,
                created_at,updated_at,payload_json
            ) VALUES(?,?,?,?,?,?,?,?,?,?)
            """,
            (
                session["session_id"],
                session["mode"],
                session.get("operation_id"),
                session["stage"],
                session["session_status"],
                session["revision"],
                session.get("last_transition_id"),
                session["created_at"],
                session["updated_at"],
                _json(session),
            ),
        )

    def _insert_event(
        self,
        connection: sqlite3.Connection,
        *,
        session: dict[str, Any],
        event_type: str,
        transition_id: str | None,
        from_stage: str | None,
        to_stage: str | None,
        payload: dict[str, Any],
    ) -> None:
        connection.execute(
            """
            INSERT INTO setup_events(
                session_id,transition_id,event_type,from_stage,to_stage,revision,occurred_at,payload_json
            ) VALUES(?,?,?,?,?,?,?,?)
            """,
            (
                session["session_id"],
                transition_id,
                event_type,
                from_stage,
                to_stage,
                session["revision"],
                session["updated_at"],
                _json(payload),
            ),
        )

    def _session_from_row(self, row: sqlite3.Row) -> dict[str, Any]:
        session = _json_object(row["payload_json"])
        validate_setup_session(session)
        if (
            session["session_id"] != row["session_id"]
            or session["stage"] != row["stage"]
            or session["revision"] != row["revision"]
            or session["session_status"] != row["session_status"]
        ):
            _fail("setup.store.corrupt", "Setup session indexed fields disagree with payload")
        return session

    def latest_session(self, *, readonly: bool = True) -> dict[str, Any] | None:
        context = self.readonly() if readonly else self.writable()
        with context as connection:
            row = connection.execute("SELECT * FROM setup_sessions ORDER BY rowid DESC LIMIT 1").fetchone()
            return self._session_from_row(row) if row is not None else None

    def session(self, session_id: str, *, readonly: bool = True) -> dict[str, Any]:
        context = self.readonly() if readonly else self.writable()
        with context as connection:
            row = connection.execute(
                "SELECT * FROM setup_sessions WHERE session_id=?",
                (session_id,),
            ).fetchone()
            if row is None:
                _fail("setup.session.missing", f"Unknown setup session: {session_id}")
            return self._session_from_row(row)

    def operation(self, operation_id: str) -> dict[str, Any]:
        with self.readonly() as connection:
            row = connection.execute(
                "SELECT payload_json FROM operations WHERE operation_id=?",
                (operation_id,),
            ).fetchone()
            if row is None:
                _fail("setup.operation.missing", "Setup operation is missing")
            value = _json_object(row["payload_json"])
            return validate_operation(value)

    def installation_for_operation(self, operation_id: str) -> dict[str, Any]:
        with self.readonly() as connection:
            row = connection.execute(
                "SELECT payload_json FROM installations WHERE operation_id=? ORDER BY rowid DESC LIMIT 1",
                (operation_id,),
            ).fetchone()
            if row is None:
                _fail("setup.installation.missing", "Setup installation is missing")
            value = _json_object(row["payload_json"])
            return validate_installation(value)

    def transition(
        self,
        session_id: str,
        *,
        expected_revision: int,
        next_stage: str,
        event_payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        with self.writable() as connection:
            with self.transaction(connection):
                row = connection.execute(
                    "SELECT * FROM setup_sessions WHERE session_id=?",
                    (session_id,),
                ).fetchone()
                if row is None:
                    _fail("setup.session.missing", f"Unknown setup session: {session_id}")
                current = self._session_from_row(row)
                if current["revision"] != expected_revision:
                    _fail(
                        "setup.revision.conflict",
                        f"Expected setup revision {expected_revision}, observed {current['revision']}",
                    )
                updated = transition_setup_session(current, next_stage)
                connection.execute(
                    """
                    UPDATE setup_sessions
                    SET stage=?,session_status=?,revision=?,last_transition_id=?,updated_at=?,payload_json=?
                    WHERE session_id=? AND revision=?
                    """,
                    (
                        updated["stage"],
                        updated["session_status"],
                        updated["revision"],
                        updated.get("last_transition_id"),
                        updated["updated_at"],
                        _json(updated),
                        session_id,
                        expected_revision,
                    ),
                )
                if connection.execute("SELECT changes()").fetchone()[0] != 1:
                    _fail("setup.revision.conflict", "Setup revision changed during transition")
                self._insert_event(
                    connection,
                    session=updated,
                    event_type="stage_transition",
                    transition_id=updated.get("last_transition_id"),
                    from_stage=current["stage"],
                    to_stage=updated["stage"],
                    payload=event_payload or {},
                )
                return updated

    def configure_and_transition(
        self,
        session_id: str,
        *,
        expected_revision: int,
        timezone: str,
        pace_profile: str,
        daily_originals: int,
        hard_ceiling: int = 100,
    ) -> dict[str, Any]:
        payload = {
            "schema_version": 1,
            "timezone": timezone,
            "pace_profile": pace_profile,
            "daily_originals": daily_originals,
            "hard_ceiling": hard_ceiling,
            "publishing_authority": False,
        }
        with self.writable() as connection:
            with self.transaction(connection):
                row = connection.execute(
                    "SELECT * FROM setup_sessions WHERE session_id=?",
                    (session_id,),
                ).fetchone()
                if row is None:
                    _fail("setup.session.missing", f"Unknown setup session: {session_id}")
                current = self._session_from_row(row)
                if current["revision"] != expected_revision:
                    _fail(
                        "setup.revision.conflict",
                        f"Expected setup revision {expected_revision}, observed {current['revision']}",
                    )
                updated = transition_setup_session(current, "configuration_ready")
                connection.execute(
                    """
                    INSERT INTO staged_config(session_id,timezone,pace_profile,daily_originals,hard_ceiling,updated_at,payload_json)
                    VALUES(?,?,?,?,?,?,?)
                    ON CONFLICT(session_id) DO UPDATE SET
                        timezone=excluded.timezone,
                        pace_profile=excluded.pace_profile,
                        daily_originals=excluded.daily_originals,
                        hard_ceiling=excluded.hard_ceiling,
                        updated_at=excluded.updated_at,
                        payload_json=excluded.payload_json
                    """,
                    (
                        session_id,
                        timezone,
                        pace_profile,
                        daily_originals,
                        hard_ceiling,
                        updated["updated_at"],
                        _json(payload),
                    ),
                )
                connection.execute(
                    """
                    UPDATE setup_sessions
                    SET stage=?,session_status=?,revision=?,last_transition_id=?,updated_at=?,payload_json=?
                    WHERE session_id=? AND revision=?
                    """,
                    (
                        updated["stage"],
                        updated["session_status"],
                        updated["revision"],
                        updated.get("last_transition_id"),
                        updated["updated_at"],
                        _json(updated),
                        session_id,
                        expected_revision,
                    ),
                )
                if connection.execute("SELECT changes()").fetchone()[0] != 1:
                    _fail("setup.revision.conflict", "Setup revision changed during configuration")
                self._insert_event(
                    connection,
                    session=updated,
                    event_type="configuration_saved",
                    transition_id=updated.get("last_transition_id"),
                    from_stage=current["stage"],
                    to_stage=updated["stage"],
                    payload={
                        "timezone": timezone,
                        "pace_profile": pace_profile,
                        "daily_originals": daily_originals,
                        "hard_ceiling": hard_ceiling,
                    },
                )
                return updated

    def config(self, session_id: str) -> dict[str, Any] | None:
        with self.readonly() as connection:
            row = connection.execute(
                "SELECT payload_json FROM staged_config WHERE session_id=?",
                (session_id,),
            ).fetchone()
            return _json_object(row["payload_json"]) if row is not None else None

    def events(self, session_id: str) -> list[dict[str, Any]]:
        with self.readonly() as connection:
            rows = connection.execute(
                """
                SELECT event_id,transition_id,event_type,from_stage,to_stage,revision,occurred_at,payload_json
                FROM setup_events WHERE session_id=? ORDER BY event_id
                """,
                (session_id,),
            ).fetchall()
            return [
                {
                    "event_id": row["event_id"],
                    "transition_id": row["transition_id"],
                    "event_type": row["event_type"],
                    "from_stage": row["from_stage"],
                    "to_stage": row["to_stage"],
                    "revision": row["revision"],
                    "occurred_at": row["occurred_at"],
                    "evidence": _json_object(row["payload_json"]),
                }
                for row in rows
            ]

    def health(self) -> dict[str, Any]:
        with self.readonly() as connection:
            quick = [str(row[0]) for row in connection.execute("PRAGMA quick_check").fetchall()]
            foreign = connection.execute("PRAGMA foreign_key_check").fetchall()
            journal = str(connection.execute("PRAGMA journal_mode").fetchone()[0]).lower()
            version = self._schema_version(connection)
        mode = stat.S_IMODE(self.db_path.stat().st_mode)
        return {
            "schema_version": 1,
            "status": "ok" if quick == ["ok"] and not foreign and journal == "delete" and mode & 0o077 == 0 else "attention",
            "user_version": version,
            "quick_check": quick,
            "foreign_key_violation_count": len(foreign),
            "journal_mode": journal,
            "write_profile": {
                "journal_mode": "delete",
                "synchronous": "extra",
                "foreign_keys": True,
                "trusted_schema": False,
                "busy_timeout_ms": 5000,
            },
            "database_mode": oct(mode),
            "filesystem_type": linux_filesystem_type(self.workspace),
            "boundary": "Local bootstrap setup control store only; no production Post-Once state or provider authority.",
        }
