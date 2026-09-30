"""Dead-host recovery-point and quarantined recovery primitives for Milestone G.

Milestone G deliberately does not activate a recovered installation.  It creates
credential-free point-in-time recovery artifacts while the source is healthy,
then reconstructs a dead/unknown source into a quarantined recovery review on a
new installation.  Stale-host authority is never inferred to be fenced.
"""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
import os
import shutil
from pathlib import Path
import tempfile
from typing import Any, Iterator, NoReturn

from ocpf_post.setup_contracts import (
    aggregate_readiness,
    readiness_check,
    validate_installation,
    validate_operation,
)
from ocpf_post.setup_migration import (
    MigrationError,
    load_quarantine_object,
    source_preview,
    verify_preview_sources,
)
from ocpf_post.state_registry import SCHEMA_REGISTRY_VERSION
from ocpf_post.transfer_bundle import BundleError, PreparedObject, seal_bundle, verify_to_quarantine

UTC = timezone.utc
RECOVERY_POINT_KIND = "dead_host_recovery_point_v1"


class RecoveryError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def _fail(code: str, message: str) -> NoReturn:
    raise RecoveryError(code, message)


def _utc_iso(value: datetime | None = None) -> str:
    dt = (value or datetime.now(UTC)).astimezone(UTC).replace(microsecond=0)
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse_time(value: Any, *, field: str) -> datetime:
    if not isinstance(value, str) or not value:
        _fail("recovery.time.invalid", f"{field} is required")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise RecoveryError("recovery.time.invalid", f"{field} is invalid") from exc
    if parsed.tzinfo is None:
        _fail("recovery.time.invalid", f"{field} must include a timezone")
    return parsed.astimezone(UTC)


def _json_bytes(value: Any) -> bytes:
    return (
        json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False)
        + "\n"
    ).encode("ascii")


def _write_private_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.parent.chmod(0o700)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "wb") as handle:
        handle.write(_json_bytes(value))
        handle.flush()
        os.fsync(handle.fileno())


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return "sha256:" + digest.hexdigest()


def recovery_point_preview(
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
) -> dict[str, Any]:
    """Review one future recovery point without mutating the source authority."""
    try:
        base = source_preview(
            operation=operation,
            installation=installation,
            state_root=state_root,
            config_root=config_root,
            output=output,
            source_post_once_version=source_post_once_version,
            source_revision=source_revision,
            source_state_registry_version=source_state_registry_version,
            unit_observation=unit_observation,
        )
    except MigrationError as exc:
        raise RecoveryError("recovery." + exc.code.removeprefix("migration."), str(exc)) from exc

    review_body = {
        "schema_version": 1,
        "recovery_point_kind": RECOVERY_POINT_KIND,
        "operation_id": base["operation_id"],
        "source_installation_id": base["source_installation_id"],
        "source_authority_generation": base["source_authority_generation"],
        "output": base["output"],
        "source_post_once_version": base["source_post_once_version"],
        "source_revision": base["source_revision"],
        "source_state_registry_version": base["source_state_registry_version"],
        "files": base["files"],
        "schedule_dispositions": base["schedule_dispositions"],
        "expected_identities": base["expected_identities"],
        "writer_units": base["writer_units"],
    }
    return {
        **review_body,
        "status": "preview",
        "review_sha256": hashlib.sha256(_json_bytes(review_body)).hexdigest(),
        "file_count": len(base["files"]),
        "source_remains_active": True,
        "credentials_included": False,
        "publishing_authority": False,
        "automation_enabled": False,
        "boundary": (
            "Recovery-point preview only. The source must be quiescent while the immutable point is sealed, "
            "but the source operation is not retired and no provider/service mutation is attempted."
        ),
    }


@contextmanager
def _prepared_recovery_objects(
    *,
    workspace: Path,
    operation: dict[str, Any],
    installation: dict[str, Any],
    preview: dict[str, Any],
    captured_at: str,
) -> Iterator[list[PreparedObject]]:
    root = Path(tempfile.mkdtemp(prefix=".recovery-point-prepare-", dir=workspace))
    root.chmod(0o700)
    try:
        operation_path = root / "operation.json"
        installation_path = root / "source-installation.json"
        point_path = root / "recovery-point.json"
        schedules_path = root / "schedule-dispositions.json"
        references_path = root / "credential-references.json"
        index_path = root / "source-index.json"

        _write_private_json(operation_path, operation)
        _write_private_json(installation_path, installation)
        _write_private_json(
            point_path,
            {
                "schema_version": 1,
                "kind": RECOVERY_POINT_KIND,
                "captured_at": captured_at,
                "source_status_at_capture": "active",
                "source_installation_id": installation["installation_id"],
                "source_authority_generation": operation["authority_generation"],
                "writer_units": preview["writer_units"],
                "credentials_included": False,
                "publishing_authority": False,
                "continuity_after_capture": "unknown",
            },
        )
        _write_private_json(
            schedules_path,
            {"schema_version": 1, "rows": preview["schedule_dispositions"]},
        )
        _write_private_json(
            references_path,
            {
                "schema_version": 1,
                "expected_identities": preview["expected_identities"],
                "credentials_included": False,
            },
        )

        objects: list[PreparedObject] = [
            PreparedObject("operation/operation", "operation", "application/json", operation_path, 1),
            PreparedObject("operation/source_installation", "operation", "application/json", installation_path, 1),
            PreparedObject("evidence/recovery_point", "evidence", "application/json", point_path, 1),
            PreparedObject(
                "held_authority/schedule_dispositions",
                "held_authority",
                "application/json",
                schedules_path,
                1,
            ),
            PreparedObject(
                "held_authority/credential_references",
                "held_authority",
                "application/json",
                references_path,
                1,
            ),
        ]

        digest_to_logical: dict[str, str] = {}
        index_rows: list[dict[str, Any]] = []
        for row in preview["files"]:
            source = Path(row["source"])
            digest = str(row["sha256"])
            logical = digest_to_logical.get(digest)
            if logical is None:
                logical = f"{row['scope']}/object_{len(digest_to_logical) + 1:06d}"
                digest_to_logical[digest] = logical
                restore_class = "historical" if row["scope"] == "state" else "configuration"
                media = (
                    "application/json"
                    if source.suffix.lower() == ".json"
                    else "application/x-ndjson"
                    if source.suffix.lower() == ".jsonl"
                    else "application/octet-stream"
                )
                objects.append(
                    PreparedObject(
                        logical,
                        restore_class,
                        media,
                        source,
                        1 if media != "application/octet-stream" else None,
                    )
                )
            index_rows.append(
                {
                    "scope": row["scope"],
                    "path": row["path"],
                    "logical_id": logical,
                    "sha256": digest,
                    "size": row["size"],
                }
            )

        _write_private_json(index_path, {"schema_version": 1, "files": index_rows})
        objects.append(
            PreparedObject("evidence/source_index", "evidence", "application/json", index_path, 1)
        )
        yield objects
    finally:
        shutil.rmtree(root, ignore_errors=True)


def seal_recovery_point(
    *,
    workspace: str | Path,
    operation: dict[str, Any],
    installation: dict[str, Any],
    preview: dict[str, Any],
    output: str | Path,
) -> dict[str, Any]:
    validate_operation(operation)
    validate_installation(installation)
    if (
        operation["status"] != "active"
        or installation["status"] != "active"
        or operation["active_installation_id"] != installation["installation_id"]
        or operation["authority_generation"] != installation["authority_generation"]
    ):
        _fail("recovery.source.authority_invalid", "Recovery point requires the active source authority holder")

    expected = (
        preview.get("operation_id"),
        preview.get("source_installation_id"),
        preview.get("source_authority_generation"),
    )
    observed = (
        operation["operation_id"],
        installation["installation_id"],
        operation["authority_generation"],
    )
    if expected != observed:
        _fail("recovery.source.identity_changed", "Recovery point source authority changed after review")

    try:
        verify_preview_sources(preview)
    except MigrationError as exc:
        raise RecoveryError("recovery.source.drift", str(exc)) from exc

    captured_at = _utc_iso()
    root = Path(workspace).expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True)
    root.chmod(0o700)
    with _prepared_recovery_objects(
        workspace=root,
        operation=operation,
        installation=installation,
        preview=preview,
        captured_at=captured_at,
    ) as objects:
        try:
            sealed = seal_bundle(
                output,
                objects,
                operation_id=operation["operation_id"],
                source_installation_id=installation["installation_id"],
                source_authority_generation=int(operation["authority_generation"]),
                source_post_once_version=str(preview["source_post_once_version"]),
                source_revision=str(preview["source_revision"]),
                source_state_registry_version=int(preview["source_state_registry_version"]),
                sealed_at=_parse_time(captured_at, field="captured_at"),
            )
        except BundleError as exc:
            raise RecoveryError("recovery.bundle." + exc.code, str(exc)) from exc

    return {
        **sealed,
        "recovery_point_kind": RECOVERY_POINT_KIND,
        "captured_at": captured_at,
        "source_remains_active": True,
        "credentials_included": False,
        "boundary": (
            "Immutable recovery point only. The source operation remains active after sealing; "
            "no provider credentials, service mutation, schedule mutation or publishing authority changed."
        ),
    }


def verify_recovery_point_bundle(
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
        raise RecoveryError("recovery.bundle." + exc.code, str(exc)) from exc

    quarantine = Path(verified["quarantine"])
    manifest = json.loads((quarantine / "manifest.json").read_text(encoding="ascii"))
    if manifest.get("source_state_registry_version") != SCHEMA_REGISTRY_VERSION:
        _fail(
            "recovery.bundle.state_registry_unsupported",
            (
                "Recovery requires an explicitly supported state-registry version; "
                f"bundle={manifest.get('source_state_registry_version')!r} "
                f"target={SCHEMA_REGISTRY_VERSION}"
            ),
        )

    operation = validate_operation(load_quarantine_object(quarantine, "operation/operation"))
    source_installation = validate_installation(
        load_quarantine_object(quarantine, "operation/source_installation")
    )
    point = load_quarantine_object(quarantine, "evidence/recovery_point")
    schedules = load_quarantine_object(quarantine, "held_authority/schedule_dispositions")
    references = load_quarantine_object(quarantine, "held_authority/credential_references")
    index = load_quarantine_object(quarantine, "evidence/source_index")

    if operation["status"] != "active":
        _fail("recovery.bundle.operation_not_active_at_capture", "Recovery point must capture an active operation")
    if source_installation["status"] != "active":
        _fail("recovery.bundle.source_not_active_at_capture", "Recovery point must capture an active source installation")
    if (
        operation["operation_id"] != verified["operation_id"]
        or source_installation["installation_id"] != verified["source_installation_id"]
        or source_installation["operation_id"] != operation["operation_id"]
        or operation["active_installation_id"] != source_installation["installation_id"]
        or operation["authority_generation"] != verified["source_authority_generation"]
        or source_installation["authority_generation"] != operation["authority_generation"]
    ):
        _fail("recovery.bundle.authority_mismatch", "Recovery point authority identity is inconsistent")

    if (
        point.get("schema_version") != 1
        or point.get("kind") != RECOVERY_POINT_KIND
        or point.get("source_status_at_capture") != "active"
        or point.get("credentials_included") is not False
        or point.get("captured_at") != verified["sealed_at"]
        or point.get("source_installation_id") != source_installation["installation_id"]
        or point.get("source_authority_generation") != operation["authority_generation"]
        or point.get("writer_units", {}).get("status") != "quiescent"
    ):
        _fail("recovery.bundle.recovery_point_invalid", "Recovery-point evidence is inconsistent")

    if schedules.get("schema_version") != 1 or not isinstance(schedules.get("rows"), list):
        _fail("recovery.bundle.schedule_dispositions_invalid", "Recovery schedule evidence is invalid")
    if references.get("schema_version") != 1 or references.get("credentials_included") is not False:
        _fail("recovery.bundle.credential_boundary_invalid", "Recovery credential references are invalid")
    if index.get("schema_version") != 1 or not isinstance(index.get("files"), list):
        _fail("recovery.bundle.index_invalid", "Recovery source index is invalid")

    return {
        **verified,
        "operation": operation,
        "source_installation": source_installation,
        "recovery_point": point,
        "schedule_dispositions": schedules["rows"],
        "expected_identities": references.get("expected_identities", []),
        "source_index": index["files"],
        "source_continuity": "unknown_after_recovery_point",
        "authenticity": "digest_pinned" if expected_bundle_sha256 is not None else "not_provided",
        "publishing_authority": False,
        "automation_enabled": False,
        "boundary": (
            "Verified recovery-point quarantine only. The old host is assumed unknown, not retired or fenced. "
            "No production state or provider authority was changed."
        ),
    }


def assess_recovery_point(
    verified: dict[str, Any],
    *,
    source_lost_at: str,
    max_data_loss_minutes: int,
) -> dict[str, Any]:
    captured = _parse_time(verified.get("sealed_at"), field="recovery point time")
    lost = _parse_time(source_lost_at, field="source_lost_at")
    if lost < captured:
        _fail("recovery.point.loss_before_capture", "Source loss time cannot precede the recovery point")
    if type(max_data_loss_minutes) is not int or max_data_loss_minutes < 0:
        _fail("recovery.point.rpo_invalid", "Maximum acceptable data loss must be a non-negative whole number of minutes")

    gap_seconds = int((lost - captured).total_seconds())
    allowed_seconds = max_data_loss_minutes * 60
    if gap_seconds > allowed_seconds:
        _fail(
            "recovery.point.rpo_exceeded",
            (
                f"Recovery point is {gap_seconds} seconds before the declared source loss, "
                f"exceeding the accepted {allowed_seconds}-second loss window"
            ),
        )
    return {
        "schema_version": 1,
        "status": "within_rpo",
        "captured_at": _utc_iso(captured),
        "source_lost_at": _utc_iso(lost),
        "recovery_point_gap_seconds": gap_seconds,
        "max_data_loss_seconds": allowed_seconds,
        "rpo_accepted": True,
        "boundary": (
            "Recovery-point age assessment only. It does not prove the old host stopped, "
            "does not fence provider credentials and does not grant replay authority."
        ),
    }


def reconcile_recovery_schedules(
    rows: list[dict[str, Any]],
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    current = (now or datetime.now(UTC)).astimezone(UTC)
    reconciled: list[dict[str, Any]] = []
    blockers: list[str] = []
    review_reasons: list[str] = []

    for row in rows:
        disposition = str(row.get("disposition") or "")
        target = disposition
        eligible = False
        run_at = row.get("run_at")

        if disposition == "held_future":
            if not isinstance(run_at, str):
                blockers.append("schedule.recovery.run_at_missing")
                target = "blocked_unknown"
            else:
                try:
                    parsed = datetime.fromisoformat(run_at.replace("Z", "+00:00")).astimezone(UTC)
                except ValueError:
                    blockers.append("schedule.recovery.run_at_invalid")
                    target = "blocked_unknown"
                else:
                    if parsed > current:
                        target = "held_future"
                        eligible = True
                    else:
                        target = "handoff_expired"
        elif disposition == "hold_recompute":
            target = "hold_recompute"
            eligible = True
        elif disposition in {"terminal_review", "recovery_quarantine"}:
            review_reasons.append(disposition)
        elif disposition == "blocked_unknown":
            blockers.append("schedule.recovery.unknown_state")
        elif disposition not in {"historical_only", "handoff_expired"}:
            blockers.append("schedule.recovery.unknown_disposition")
            target = "blocked_unknown"

        reconciled.append(
            {
                **row,
                "target_disposition": target,
                "eligible_for_rearm_after_future_activation_review": eligible,
                "actionable_after_restore": False,
                "automatic_retry_safe": False,
            }
        )

    status = "blocked" if blockers else "review_required" if review_reasons else "ready"
    return {
        "schema_version": 1,
        "observed_at": _utc_iso(current),
        "status": status,
        "rows": reconciled,
        "blockers": sorted(set(blockers)),
        "review_reasons": sorted(set(review_reasons)),
        "automatic_rearm": False,
        "automatic_catch_up": False,
        "publishing_authority": False,
        "boundary": (
            "Dead-host recovery reconciliation only. Future work remains held; overdue work does not catch up; "
            "ambiguous/partial/executing evidence remains review-only and never grants automatic retry."
        ),
    }


def build_recovery_review(
    *,
    verified: dict[str, Any],
    assessment: dict[str, Any],
    provider_readiness: list[dict[str, Any]],
    provider_validation: dict[str, Any],
    reconciliation: dict[str, Any],
) -> dict[str, Any]:
    if provider_validation.get("status") != "ready":
        _fail("recovery.provider_authority_blocked", "Recovered provider identity/write-authority evidence is incomplete")
    if reconciliation.get("status") == "blocked":
        _fail("recovery.schedule_blocked", "Recovered schedule state contains an unknown/unusable state")

    checks = [
        readiness_check(
            code="bundle.integrity.verified",
            status="ready",
            severity="info",
            required=True,
            scope="bundle",
            evidence_method="sha256_and_bounded_archive_verification",
            summary="Recovery bundle integrity and structure verified",
            consequence_boundary="No restore promotion or provider consequence",
            evidence={"bundle_sha256": str(verified["bundle_sha256"])},
        ),
        readiness_check(
            code="bundle.recovery_point.within_rpo",
            status="ready",
            severity="info",
            required=True,
            scope="bundle",
            evidence_method="declared_loss_time_vs_recovery_point",
            summary="Recovery point is within the operator-declared data-loss window",
            consequence_boundary="No replay or activation authority",
            evidence={
                "gap_seconds": int(assessment["recovery_point_gap_seconds"]),
                "max_seconds": int(assessment["max_data_loss_seconds"]),
            },
        ),
        readiness_check(
            code="authority.source_host.unknown",
            status="unknown",
            severity="blocker",
            required=True,
            scope="authority",
            evidence_method="dead_host_recovery",
            summary="The old installation is unavailable and cannot be proven retired locally",
            consequence_boundary="Unattended provider effects remain blocked",
            remediation_action_id="fence_or_revoke_old_provider_authority",
            evidence={"source_installation_id": str(verified["source_installation_id"])},
        ),
        readiness_check(
            code="authority.recovery_gap.unreconciled",
            status="unknown",
            severity="blocker",
            required=True,
            scope="authority",
            evidence_method="recovery_point_to_declared_loss_window",
            summary=(
                "Provider effects may have occurred after the recovery point and before host loss; "
                "the accepted RPO does not make that consequence window safe to replay"
            ),
            consequence_boundary="No automatic replay, catch-up or schedule re-arm",
            remediation_action_id="forensic_reconcile_recovery_gap",
            evidence={
                "gap_seconds": int(assessment["recovery_point_gap_seconds"]),
            },
        ),
    ]

    if verified.get("authenticity") == "digest_pinned":
        checks.append(
            readiness_check(
                code="bundle.authenticity.digest_pinned",
                status="ready",
                severity="info",
                required=True,
                scope="bundle",
                evidence_method="operator_supplied_exact_bundle_digest",
                summary="Recovery artifact matched the separately supplied exact digest",
                consequence_boundary="Digest pin proves artifact continuity, not stale-host fencing",
            )
        )
    else:
        checks.append(
            readiness_check(
                code="bundle.authenticity.not_provided",
                status="unknown",
                severity="blocker",
                required=True,
                scope="bundle",
                evidence_method="no_external_authenticity_anchor",
                summary="No independent trusted digest/signature was supplied for the recovery artifact",
                consequence_boundary="Recovery remains review-gated",
                remediation_action_id="supply_trusted_recovery_artifact_digest",
            )
        )

    readiness_by_key = {
        (str(row.get("provider") or ""), str(row.get("expected_identity") or "")): row
        for row in provider_readiness
        if isinstance(row, dict)
    }
    for expected in verified.get("expected_identities", []):
        provider = str(expected.get("provider") or "")
        account_id = str(expected.get("account_id") or "")
        row = readiness_by_key.get((provider, account_id), {})
        checks.append(
            readiness_check(
                code=f"provider.identity.{provider}_verified",
                status="ready",
                severity="info",
                required=True,
                scope=f"provider:{provider}",
                evidence_method="read_only_provider_readiness",
                summary=f"Recovered {provider} target identity matches the expected immutable account ID",
                consequence_boundary="Identity observation only; no provider write attempted",
                evidence={"account_id": account_id},
            )
        )
        checks.append(
            readiness_check(
                code=f"provider.stale_authority.{provider}_unresolved",
                status="unknown",
                severity="blocker",
                required=True,
                scope=f"provider:{provider}",
                evidence_method=str(row.get("recovery_fencing_strength") or "not_proven"),
                summary=f"The dead host's prior {provider} credential family is not proven fenced",
                consequence_boundary="Unattended provider effects remain blocked",
                remediation_action_id=f"fence_stale_{provider}_authority",
                evidence={"account_id": account_id},
            )
        )

    source_statuses = {str(row.get("source_status") or "") for row in reconciliation.get("rows", [])}
    if "ambiguous_effect" in source_statuses:
        checks.append(
            readiness_check(
                code="schedule.ambiguous_effect.present",
                status="unknown",
                severity="blocker",
                required=True,
                scope="schedules",
                evidence_method="restored_schedule_ledger",
                summary="At least one restored effect is ambiguous and cannot be replayed automatically",
                consequence_boundary="No automatic retry or re-arm",
                remediation_action_id="review_ambiguous_effects",
            )
        )
    if "partial_effect" in source_statuses:
        checks.append(
            readiness_check(
                code="schedule.partial_effect.present",
                status="unknown",
                severity="blocker",
                required=True,
                scope="schedules",
                evidence_method="restored_schedule_ledger",
                summary="At least one restored effect is partial and cannot be replayed automatically",
                consequence_boundary="No automatic retry or re-arm",
                remediation_action_id="review_partial_effects",
            )
        )
    if "executing" in source_statuses:
        checks.append(
            readiness_check(
                code="schedule.executing.present",
                status="unknown",
                severity="blocker",
                required=True,
                scope="schedules",
                evidence_method="restored_schedule_ledger",
                summary="The recovery point contains an execution that had not reached a terminal state",
                consequence_boundary="No automatic retry or re-arm",
                remediation_action_id="reconcile_executing_effects",
            )
        )

    summary = aggregate_readiness(checks, mode="recover")
    if summary["status"] != "RECOVERY_REVIEW_REQUIRED":
        _fail("recovery.review.invariant", "Dead-host recovery must remain review-gated before activation")

    return {
        "schema_version": 1,
        "status": "recovery_review_required",
        "summary": summary,
        "checks": checks,
        "source_installation_id": verified["source_installation_id"],
        "source_authority_generation": verified["source_authority_generation"],
        "target_publishing_authority": False,
        "target_automation_enabled": False,
        "boundary": (
            "Milestone G recovery review only. This report deliberately preserves stale-host, authenticity "
            "and uncertain-effect questions for a later explicit activation/fencing decision."
        ),
    }
