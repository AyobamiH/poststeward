"""Healthy same-operator migration primitives for Milestone F.

This module is deliberately local-first and fail-closed.  It can inspect an
explicit Post-Once state/config tree, prove that known writer units are quiescent,
seal a credential-free transfer bundle, and verify/reconcile that bundle on a
new installation.  It never starts/stops services, authorizes providers, promotes
restored files into production roots, or grants publishing authority.
"""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
import os
import re
from pathlib import Path
import shutil
import subprocess
import tempfile
from typing import Any, Iterator, NoReturn

from ocpf_post.ledger import LedgerIntegrityError, iter_jsonl
from ocpf_post.setup_contracts import (
    schedule_disposition,
    validate_installation,
    validate_operation,
)
from ocpf_post.state_registry import SCHEMA_REGISTRY_VERSION
from ocpf_post.transfer_bundle import BundleError, PreparedObject, seal_bundle, verify_to_quarantine

UTC = timezone.utc
SCHEMA_VERSION = 1

WRITER_UNITS = (
    "ocpf-post-run-due.timer",
    "ocpf-post-run-due.service",
    "ocpf-post-portfolio-refill.timer",
    "ocpf-post-portfolio-refill.service",
    "ocpf-post-collection.timer",
    "ocpf-post-collection.service",
    "ocpf-post-replies.timer",
    "ocpf-post-replies.service",
)

CRITICAL_JSONL = {
    "publish-receipts.jsonl": ("campaign", "provider", "status"),
    "schedule-events.jsonl": ("schedule_id", "event", "status", "recorded_at"),
    "performance-snapshots.jsonl": ("campaign", "provider", "captured_at"),
    "portfolio-allocation-events.jsonl": ("event",),
}

_SECRET_KEY_FRAGMENTS = (
    "access_token",
    "refresh_token",
    "client_secret",
    "api_key",
    "apikey",
    "password",
    "private_key",
    "service_role",
    "encryption_key",
    "webhook_secret",
    "bearer",
    "credential",
    "secret",
    "token",
)
_SECRET_PATH_PARTS = {"accounts", "outcome-connectors", "credentials", "keyring"}
_SECRET_NAME_FRAGMENTS = ("token", "credential", "secret", ".env")


_SOURCE_VERSION_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+-]{0,63}$")
_SOURCE_REVISION_RE = re.compile(r"^[0-9a-f]{40}$")


def _validate_source_metadata(
    source_post_once_version: str,
    source_revision: str,
    source_state_registry_version: int,
) -> tuple[str, str, int]:
    version = str(source_post_once_version or "").strip()
    revision = str(source_revision or "").strip()
    if not _SOURCE_VERSION_RE.fullmatch(version):
        _fail("migration.source.version_invalid", "Source Post-Once version is invalid")
    if not _SOURCE_REVISION_RE.fullmatch(revision):
        _fail("migration.source.revision_invalid", "Source Post-Once revision must be an exact 40-character Git SHA")
    if type(source_state_registry_version) is not int or source_state_registry_version < 1:
        _fail("migration.source.state_registry_invalid", "Source state-registry version is invalid")
    if source_state_registry_version != SCHEMA_REGISTRY_VERSION:
        _fail(
            "migration.source.state_registry_unsupported",
            (
                "Healthy migration requires an explicitly supported state-registry version; "
                f"source={source_state_registry_version} target={SCHEMA_REGISTRY_VERSION}"
            ),
        )
    return version, revision, source_state_registry_version


class MigrationError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def _fail(code: str, message: str) -> NoReturn:
    raise MigrationError(code, message)


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return "sha256:" + digest.hexdigest()


def _json_bytes(value: Any) -> bytes:
    return (json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False) + "\n").encode("ascii")


def _write_private_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.parent.chmod(0o700)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "wb") as handle:
        handle.write(_json_bytes(value))
        handle.flush()
        os.fsync(handle.fileno())


def _contains_secret_key(value: Any) -> bool:
    if isinstance(value, dict):
        for key, item in value.items():
            lowered = str(key).lower()
            if any(fragment in lowered for fragment in _SECRET_KEY_FRAGMENTS):
                return True
            if _contains_secret_key(item):
                return True
    elif isinstance(value, list):
        return any(_contains_secret_key(item) for item in value)
    return False


def _config_safe(path: Path, root: Path) -> bool:
    relative = path.relative_to(root)
    lowered_parts = {part.lower() for part in relative.parts[:-1]}
    name = relative.name.lower()
    if lowered_parts & _SECRET_PATH_PARTS:
        return False
    if any(fragment in name for fragment in _SECRET_NAME_FRAGMENTS):
        return False
    if path.suffix.lower() != ".json":
        return False
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        _fail("migration.config.invalid", f"Configuration file is not valid JSON: {relative}")
    if _contains_secret_key(value):
        return False
    return True


def _state_path_safe(path: Path, root: Path) -> bool:
    relative = path.relative_to(root)
    lowered_parts = {part.lower() for part in relative.parts[:-1]}
    name = relative.name.lower()
    if name == "automation-authority.json":
        return False
    if lowered_parts & _SECRET_PATH_PARTS:
        return False
    if any(fragment in name for fragment in _SECRET_NAME_FRAGMENTS):
        return False
    return True


def _regular_files(root: Path, *, config: bool) -> list[Path]:
    if not root.is_dir():
        _fail("migration.source_root.missing", f"Migration source root does not exist: {root}")
    rows: list[Path] = []
    for path in sorted(root.rglob("*")):
        if path.is_symlink():
            _fail("migration.source.symlink", f"Migration source contains a symlink: {path.relative_to(root)}")
        if not path.is_file():
            continue
        if path.name.endswith(".lock") or path.name.endswith(".tmp"):
            continue
        if config and not _config_safe(path, root):
            continue
        if not config and not _state_path_safe(path, root):
            continue
        rows.append(path)
    return rows


def _validate_critical_ledgers(state_root: Path) -> None:
    for name, required in CRITICAL_JSONL.items():
        path = state_root / name
        if not path.exists():
            continue
        try:
            for _ in iter_jsonl(path, required=required):
                pass
        except (OSError, ValueError, LedgerIntegrityError) as exc:
            raise MigrationError("migration.state.integrity", f"Critical ledger is invalid: {name}") from exc


def _schedule_records(state_root: Path) -> list[dict[str, Any]]:
    path = state_root / "schedule-events.jsonl"
    if not path.exists():
        return []
    records: dict[str, dict[str, Any]] = {}
    try:
        events = iter_jsonl(path, required=("schedule_id", "event", "status", "recorded_at"))
        for event in events:
            schedule_id = str(event["schedule_id"])
            kind = event.get("event")
            if kind == "scheduled":
                records[schedule_id] = dict(event)
                records[schedule_id]["last_event"] = "scheduled"
                records[schedule_id]["updated_at"] = event.get("recorded_at")
                continue
            record = records.get(schedule_id)
            if record is None:
                continue
            if event.get("status"):
                record["status"] = event["status"]
            record["last_event"] = kind
            record["updated_at"] = event.get("recorded_at")
            for key in (
                "retry_at", "failure_class", "receipt_status", "readback_verified",
                "provider_http_status", "automatic_retry",
            ):
                if key in event:
                    record[key] = event[key]
    except (OSError, ValueError, LedgerIntegrityError) as exc:
        raise MigrationError("migration.schedule.integrity", "Schedule ledger could not be reconstructed") from exc
    return sorted(records.values(), key=lambda row: (str(row.get("run_at") or ""), str(row.get("schedule_id") or "")))


def observe_writer_units() -> dict[str, Any]:
    """Read systemd user-unit state; never stop/start/enable/disable a unit."""
    observed: list[dict[str, str]] = []
    active: list[str] = []
    for unit in WRITER_UNITS:
        try:
            result = subprocess.run(
                [
                    "systemctl", "--user", "show", unit,
                    "--property=LoadState", "--property=ActiveState", "--property=SubState",
                    "--no-pager",
                ],
                check=False,
                capture_output=True,
                text=True,
                timeout=5,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise MigrationError(
                "migration.source.systemd_unavailable",
                "Healthy migration requires readable systemd user-unit state",
            ) from exc
        values: dict[str, str] = {}
        for line in result.stdout.splitlines():
            key, sep, value = line.partition("=")
            if sep:
                values[key] = value
        load = values.get("LoadState", "unknown")
        state = values.get("ActiveState", "unknown")
        sub = values.get("SubState", "unknown")
        row = {"unit": unit, "load_state": load, "active_state": state, "sub_state": sub}
        observed.append(row)
        if load not in {"not-found", "masked"} and state not in {"inactive", "failed"}:
            active.append(unit)
    return {
        "schema_version": 1,
        "status": "quiescent" if not active else "active",
        "units": observed,
        "active_writer_units": active,
        "mutation_attempted": False,
        "boundary": "Read-only systemd observation. The migration command never stops or starts source automation.",
    }


def _safe_relative(path: Path, root: Path) -> str:
    relative = path.relative_to(root)
    if any(part in {"", ".", ".."} for part in relative.parts):
        _fail("migration.path.invalid", "Invalid migration source path")
    return relative.as_posix()


def _file_rows(state_root: Path, config_root: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for scope, root, config in (
        ("state", state_root, False),
        ("config", config_root, True),
    ):
        for path in _regular_files(root, config=config):
            relative = _safe_relative(path, root)
            rows.append({
                "scope": scope,
                "path": relative,
                "source": str(path),
                "size": path.stat().st_size,
                "sha256": _sha256_file(path),
            })
    return rows


def _expected_identities(
    schedules: list[dict[str, Any]],
    files: list[dict[str, Any]],
) -> list[dict[str, str]]:
    values = {
        (str(row.get("provider") or ""), str(row.get("account_id") or ""))
        for row in schedules
        if row.get("provider") and row.get("account_id")
    }

    def collect(value: Any) -> None:
        if isinstance(value, dict):
            provider = value.get("provider")
            account_id = value.get("account_id")
            if provider and account_id:
                values.add((str(provider), str(account_id)))
            for item in value.values():
                collect(item)
        elif isinstance(value, list):
            for item in value:
                collect(item)

    for row in files:
        if row["scope"] != "config" or not str(row["path"]).endswith(".json"):
            continue
        try:
            collect(json.loads(Path(row["source"]).read_text(encoding="utf-8")))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise MigrationError(
                "migration.config.invalid",
                f"Configuration file changed or became invalid: {row['path']}",
            ) from exc

    return [
        {"provider": provider, "account_id": account_id}
        for provider, account_id in sorted(values)
    ]


def source_preview(
    *,
    operation: dict[str, Any],
    installation: dict[str, Any],
    state_root: str | Path,
    config_root: str | Path,
    output: str | Path,
    source_post_once_version: str,
    source_revision: str,
    source_state_registry_version: int = SCHEMA_REGISTRY_VERSION,
    unit_observation: dict[str, Any] | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    validate_operation(operation)
    validate_installation(installation)
    source_version, source_git_revision, registry_version = _validate_source_metadata(
        source_post_once_version,
        source_revision,
        source_state_registry_version,
    )
    if operation["status"] != "active":
        _fail("migration.source.operation_not_active", "Source operation must be active before healthy handoff")
    if installation["status"] != "active":
        _fail("migration.source.installation_not_active", "Source installation must be active before healthy handoff")
    if operation["active_installation_id"] != installation["installation_id"]:
        _fail("migration.source.authority_mismatch", "Source installation is not the active authority holder")
    if operation["authority_generation"] != installation["authority_generation"]:
        _fail("migration.source.generation_mismatch", "Source operation and installation generations disagree")

    state = Path(state_root).expanduser().resolve()
    config = Path(config_root).expanduser().resolve()
    if state == config or state in config.parents or config in state.parents:
        _fail("migration.source_roots.overlap", "Source state and config roots must be separate")
    _validate_critical_ledgers(state)
    units = unit_observation or observe_writer_units()
    if units.get("status") != "quiescent":
        _fail(
            "migration.source.automation_active",
            "Stop all Post-Once writer timers/services before sealing a healthy migration bundle",
        )

    schedules = _schedule_records(state)
    dispositions = []
    for row in schedules:
        projected = schedule_disposition(row, context="healthy_migration", now=now)
        projected.update({
            "run_at": row.get("run_at"),
            "provider": row.get("provider"),
            "account_id": row.get("account_id"),
        })
        dispositions.append(projected)
    sealing_blockers = [
        row for row in dispositions
        if row["disposition"] in {"block_sealing", "blocked_unknown", "recovery_quarantine"}
    ]
    if sealing_blockers:
        _fail("migration.source.schedule_unresolved", "Healthy migration has unresolved/executing schedule state")

    files = _file_rows(state, config)
    review_body = {
        "schema_version": 1,
        "operation_id": operation["operation_id"],
        "source_installation_id": installation["installation_id"],
        "source_authority_generation": operation["authority_generation"],
        "output": str(Path(output).expanduser().resolve()),
        "source_post_once_version": source_version,
        "source_revision": source_git_revision,
        "source_state_registry_version": registry_version,
        "files": files,
        "schedule_dispositions": dispositions,
        "expected_identities": _expected_identities(schedules, files),
        "writer_units": units,
    }
    review = hashlib.sha256(_json_bytes(review_body)).hexdigest()
    return {
        **review_body,
        "status": "preview",
        "review_sha256": review,
        "file_count": len(files),
        "publishing_authority": False,
        "automation_enabled": False,
        "boundary": (
            "Healthy-migration preview only. Credentials are excluded. No source file, service, schedule, "
            "receipt or provider is mutated."
        ),
    }


@contextmanager
def _prepared_objects(
    *,
    workspace: Path,
    operation: dict[str, Any],
    retired_installation: dict[str, Any],
    preview: dict[str, Any],
) -> Iterator[list[PreparedObject]]:
    root = Path(tempfile.mkdtemp(prefix=".migration-prepare-", dir=workspace))
    root.chmod(0o700)
    try:
        operation_path = root / "operation.json"
        installation_path = root / "source-installation.json"
        handoff_path = root / "source-handoff.json"
        schedules_path = root / "schedule-dispositions.json"
        references_path = root / "credential-references.json"
        index_path = root / "source-index.json"
        _write_private_json(operation_path, operation)
        _write_private_json(installation_path, retired_installation)
        _write_private_json(
            handoff_path,
            {
                "schema_version": 1,
                "source_status": "retired",
                "source_installation_id": retired_installation["installation_id"],
                "source_authority_generation": operation["authority_generation"],
                "writer_units": preview["writer_units"],
                "credentials_included": False,
                "publishing_authority": False,
            },
        )
        _write_private_json(schedules_path, {"schema_version": 1, "rows": preview["schedule_dispositions"]})
        _write_private_json(
            references_path,
            {"schema_version": 1, "expected_identities": preview["expected_identities"], "credentials_included": False},
        )

        objects: list[PreparedObject] = [
            PreparedObject("operation/operation", "operation", "application/json", operation_path, 1),
            PreparedObject("operation/source_installation", "operation", "application/json", installation_path, 1),
            PreparedObject("evidence/source_handoff", "evidence", "application/json", handoff_path, 1),
            PreparedObject("held_authority/schedule_dispositions", "held_authority", "application/json", schedules_path, 1),
            PreparedObject("held_authority/credential_references", "held_authority", "application/json", references_path, 1),
        ]

        digest_to_logical: dict[str, str] = {}
        index_rows: list[dict[str, Any]] = []
        for row in preview["files"]:
            source = Path(row["source"])
            digest = row["sha256"]
            logical = digest_to_logical.get(digest)
            if logical is None:
                logical = f"{row['scope']}/object_{len(digest_to_logical) + 1:06d}"
                digest_to_logical[digest] = logical
                restore_class = "historical" if row["scope"] == "state" else "configuration"
                media = "application/json" if source.suffix.lower() == ".json" else (
                    "application/x-ndjson" if source.suffix.lower() == ".jsonl" else "application/octet-stream"
                )
                objects.append(PreparedObject(logical, restore_class, media, source, 1 if media != "application/octet-stream" else None))
            index_rows.append({
                "scope": row["scope"],
                "path": row["path"],
                "logical_id": logical,
                "sha256": digest,
                "size": row["size"],
            })
        _write_private_json(index_path, {"schema_version": 1, "files": index_rows})
        objects.append(PreparedObject("evidence/source_index", "evidence", "application/json", index_path, 1))
        yield objects
    finally:
        shutil.rmtree(root, ignore_errors=True)


def verify_preview_sources(preview: dict[str, Any]) -> None:
    for row in preview.get("files", []):
        path = Path(str(row.get("source") or "")).expanduser()
        if not path.is_file() or path.is_symlink():
            _fail("migration.source.drift", f"Prepared migration source disappeared or changed type: {row.get('path')}")
        if path.stat().st_size != row.get("size") or _sha256_file(path) != row.get("sha256"):
            _fail("migration.source.drift", f"Prepared migration source changed after review: {row.get('path')}")


def seal_source_bundle(
    *,
    workspace: str | Path,
    operation: dict[str, Any],
    retired_installation: dict[str, Any],
    preview: dict[str, Any],
    output: str | Path,
    source_post_once_version: str,
    source_revision: str,
    source_state_registry_version: int = 1,
) -> dict[str, Any]:
    root = Path(workspace).expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True)
    root.chmod(0o700)
    with _prepared_objects(
        workspace=root,
        operation=operation,
        retired_installation=retired_installation,
        preview=preview,
    ) as objects:
        return seal_bundle(
            output,
            objects,
            operation_id=operation["operation_id"],
            source_installation_id=retired_installation["installation_id"],
            source_authority_generation=int(operation["authority_generation"]),
            source_post_once_version=source_post_once_version,
            source_revision=source_revision,
            source_state_registry_version=source_state_registry_version,
        )


def load_quarantine_object(quarantine: str | Path, logical_id: str) -> Any:
    root = Path(quarantine).expanduser().resolve()
    manifest = json.loads((root / "manifest.json").read_text(encoding="ascii"))
    descriptor = next((row for row in manifest.get("objects", []) if row.get("logical_id") == logical_id), None)
    if descriptor is None:
        _fail("migration.bundle.object_missing", f"Required migration object is missing: {logical_id}")
    path = root / "objects" / "sha256" / str(descriptor["digest"]).removeprefix("sha256:")
    if not path.is_file():
        _fail("migration.bundle.object_missing", f"Verified migration object is missing: {logical_id}")
    if _sha256_file(path) != descriptor["digest"]:
        _fail("migration.bundle.object_drift", f"Verified migration object changed: {logical_id}")
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise MigrationError("migration.bundle.object_invalid", f"Migration object is invalid JSON: {logical_id}") from exc


def verify_healthy_bundle(
    bundle: str | Path,
    quarantine_parent: str | Path,
    *,
    expected_bundle_sha256: str | None = None,
) -> dict[str, Any]:
    try:
        verified = verify_to_quarantine(
            bundle,
            quarantine_parent,
            expected_bundle_sha256=expected_bundle_sha256,
        )
    except BundleError as exc:
        raise MigrationError("migration.bundle." + exc.code, str(exc)) from exc

    quarantine = verified["quarantine"]
    manifest = json.loads((Path(quarantine) / "manifest.json").read_text(encoding="ascii"))
    source_registry_version = manifest.get("source_state_registry_version")
    if source_registry_version != SCHEMA_REGISTRY_VERSION:
        _fail(
            "migration.bundle.state_registry_unsupported",
            (
                "Healthy migration requires an explicitly supported state-registry version; "
                f"bundle={source_registry_version!r} target={SCHEMA_REGISTRY_VERSION}"
            ),
        )
    operation = validate_operation(load_quarantine_object(quarantine, "operation/operation"))
    source_installation = validate_installation(
        load_quarantine_object(quarantine, "operation/source_installation")
    )
    handoff = load_quarantine_object(quarantine, "evidence/source_handoff")
    dispositions = load_quarantine_object(quarantine, "held_authority/schedule_dispositions")
    references = load_quarantine_object(quarantine, "held_authority/credential_references")
    index = load_quarantine_object(quarantine, "evidence/source_index")

    if operation["operation_id"] != verified["operation_id"]:
        _fail("migration.bundle.operation_mismatch", "Bundle operation identity does not match operation object")
    if source_installation["installation_id"] != verified["source_installation_id"]:
        _fail("migration.bundle.installation_mismatch", "Bundle source installation identity does not match")
    if operation["authority_generation"] != verified["source_authority_generation"]:
        _fail("migration.bundle.generation_mismatch", "Bundle authority generation is inconsistent")
    if operation["status"] != "prepared" or operation.get("active_installation_id") is not None:
        _fail("migration.bundle.source_not_retired", "Healthy handoff bundle must carry a prepared operation with no active installation")
    if source_installation["status"] != "retired":
        _fail("migration.bundle.source_not_retired", "Healthy handoff source installation is not retired")
    if handoff.get("source_status") != "retired" or handoff.get("credentials_included") is not False:
        _fail("migration.bundle.handoff_invalid", "Healthy handoff evidence is incomplete")
    if handoff.get("writer_units", {}).get("status") != "quiescent":
        _fail("migration.bundle.source_not_quiescent", "Source automation was not proven quiescent")
    if dispositions.get("schema_version") != 1 or not isinstance(dispositions.get("rows"), list):
        _fail("migration.bundle.schedule_dispositions_invalid", "Schedule disposition evidence is invalid")
    if references.get("schema_version") != 1 or references.get("credentials_included") is not False:
        _fail("migration.bundle.credential_boundary_invalid", "Credential references are invalid")
    if index.get("schema_version") != 1 or not isinstance(index.get("files"), list):
        _fail("migration.bundle.index_invalid", "Source index is invalid")

    return {
        **verified,
        "operation": operation,
        "source_installation": source_installation,
        "source_handoff": handoff,
        "schedule_dispositions": dispositions["rows"],
        "expected_identities": references.get("expected_identities", []),
        "source_index": index["files"],
        "source_state_registry_version": source_registry_version,
        "source_continuity": "retired_and_quiescent",
        "publishing_authority": False,
        "automation_enabled": False,
        "boundary": "Verified healthy-handoff quarantine only. Target production state and services are untouched.",
    }


def validate_provider_readiness(
    expected_identities: list[dict[str, str]],
    readiness: list[dict[str, Any]],
) -> dict[str, Any]:
    expected = {(row["provider"], row["account_id"]) for row in expected_identities}
    observed: dict[tuple[str, str], dict[str, Any]] = {}
    for row in readiness:
        if not isinstance(row, dict):
            _fail("migration.provider_readiness.invalid", "Provider readiness rows must be objects")
        provider = str(row.get("provider") or "")
        identity = str(row.get("expected_identity") or "")
        if not provider or not identity:
            _fail("migration.provider_readiness.invalid", "Provider readiness row lacks expected identity")
        observed[(provider, identity)] = row

    missing = sorted(expected - set(observed))
    blockers: list[str] = []
    if missing:
        blockers.append("provider.identity.evidence_missing")
    for key in sorted(expected & set(observed)):
        row = observed[key]
        if row.get("identity_match") != "match":
            blockers.append("provider.identity.mismatch")
        if row.get("ready_for_write_configuration") is not True:
            blockers.extend(str(code) for code in row.get("blocking_reasons", []) if code)
    return {
        "schema_version": 1,
        "status": "ready" if not blockers else "blocked",
        "expected_count": len(expected),
        "observed_count": len(observed),
        "blockers": sorted(set(blockers)),
        "credentials_stored": False,
        "boundary": (
            "Consumes read-only provider-readiness projections only. Reauthorization and stale-host revocation "
            "are not performed or inferred."
        ),
    }


def reconcile_schedules(
    rows: list[dict[str, Any]],
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    current = (now or datetime.now(UTC)).astimezone(UTC)
    reconciled: list[dict[str, Any]] = []
    blockers: list[str] = []
    for row in rows:
        # Source evidence deliberately retains only disposition output, so the
        # target can never turn a transferred row directly into executable authority.
        disposition = str(row.get("disposition") or "")
        if disposition in {"block_sealing", "recovery_quarantine", "blocked_unknown"}:
            blockers.append("schedule.transfer.unresolved")
        if disposition == "held_future":
            run_at = row.get("run_at")
            if not isinstance(run_at, str):
                blockers.append("schedule.transfer.run_at_missing")
                target = "blocked_unknown"
            else:
                try:
                    parsed = datetime.fromisoformat(run_at.replace("Z", "+00:00")).astimezone(UTC)
                except ValueError:
                    blockers.append("schedule.transfer.run_at_invalid")
                    target = "blocked_unknown"
                else:
                    target = "held_future" if parsed > current else "handoff_expired"
            reconciled.append({**row, "target_disposition": target, "actionable_after_restore": False, "eligible_for_rearm": target == "held_future"})
        elif disposition == "hold_recompute":
            reconciled.append({**row, "target_disposition": "hold_recompute", "actionable_after_restore": False})
        else:
            reconciled.append({**row, "target_disposition": disposition, "actionable_after_restore": False})
    return {
        "schema_version": 1,
        "observed_at": current.replace(microsecond=0).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "status": "ready" if not blockers else "blocked",
        "rows": reconciled,
        "blockers": sorted(set(blockers)),
        "automatic_rearm": False,
        "automatic_catch_up": False,
        "publishing_authority": False,
        "boundary": "Transferred schedules remain non-actionable. Milestone F never creates or re-arms a live schedule.",
    }
