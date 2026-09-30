"""Resumable Setup & Recovery orchestration through Milestone H."""
from __future__ import annotations

import json
import os
from pathlib import Path
import platform
import shutil
import sys
import tempfile
from typing import Any, NoReturn
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from ocpf_post.setup_migration import (
    MigrationError,
    reconcile_schedules,
    seal_source_bundle,
    source_preview,
    validate_provider_readiness,
    verify_healthy_bundle,
    verify_preview_sources,
    load_quarantine_object,
)
from ocpf_post.setup_recovery import (
    RecoveryError,
    assess_recovery_point,
    build_recovery_review,
    recovery_point_preview,
    reconcile_recovery_schedules,
    seal_recovery_point,
    verify_recovery_point_bundle,
)
from ocpf_post import automation_authority
from ocpf_post.setup_activation import ActivationError, ActivationManager, ServiceController
from ocpf_post.setup_store import SetupStore, SetupStoreError, resolve_workspace
from ocpf_post.setup_admission import classify_existing_installation, prove_host_capabilities

PACE_PROFILES = {
    "occasional": 3,
    "regular": 5,
    "active": 10,
    "high": 20,
}
HARD_CEILING = 100
IMPLEMENTED_MODES = frozenset({"fresh", "explore", "migrate", "recover"})


class SetupEngineError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def _fail(code: str, message: str) -> NoReturn:
    raise SetupEngineError(code, message)


def _migration_error(exc: MigrationError) -> SetupEngineError:
    return SetupEngineError(exc.code, str(exc))


def _recovery_error(exc: RecoveryError) -> SetupEngineError:
    return SetupEngineError(exc.code, str(exc))


def validate_timezone(value: str) -> str:
    timezone = str(value or "").strip()
    if not timezone:
        _fail("setup.timezone.required", "An IANA timezone is required")
    try:
        ZoneInfo(timezone)
    except ZoneInfoNotFoundError as exc:
        raise SetupEngineError("setup.timezone.invalid", f"Unknown IANA timezone: {timezone}") from exc
    return timezone


def detect_timezone() -> str | None:
    candidates: list[str] = []
    if os.environ.get("TZ"):
        candidates.append(str(os.environ["TZ"]).strip())
    timezone_file = Path("/etc/timezone")
    if timezone_file.is_file():
        try:
            candidates.append(timezone_file.read_text(encoding="utf-8").strip())
        except OSError:
            pass
    localtime = Path("/etc/localtime")
    try:
        resolved = localtime.resolve()
        marker = "/zoneinfo/"
        if marker in str(resolved):
            candidates.append(str(resolved).split(marker, 1)[1])
    except OSError:
        pass
    for candidate in candidates:
        if not candidate:
            continue
        try:
            return validate_timezone(candidate)
        except SetupEngineError:
            continue
    return None


def resolve_pace(profile: str, daily_originals: int | None = None) -> tuple[str, int]:
    pace = str(profile or "").strip().lower()
    if pace in PACE_PROFILES:
        if daily_originals is not None:
            _fail("setup.pace.conflict", "--daily-originals is only accepted with --pace custom")
        return pace, PACE_PROFILES[pace]
    if pace == "custom":
        if type(daily_originals) is not int or not 1 <= daily_originals <= HARD_CEILING:
            _fail(
                "setup.pace.custom_invalid",
                f"Custom normal publishing pace must be between 1 and {HARD_CEILING}",
            )
        return pace, daily_originals
    _fail(
        "setup.pace.required",
        "Choose one publishing pace: occasional, regular, active, high, or custom",
    )


def _platform_evidence() -> dict[str, Any]:
    linux = sys.platform.startswith("linux")
    version_text = ""
    try:
        version_text = Path("/proc/version").read_text(encoding="utf-8", errors="replace")
    except OSError:
        pass
    wsl = linux and ("microsoft" in version_text.lower() or os.environ.get("WSL_DISTRO_NAME") is not None)
    return {
        "python": platform.python_version(),
        "python_supported": sys.version_info >= (3, 10),
        "platform": sys.platform,
        "linux_supported": linux,
        "wsl": bool(wsl),
    }


def _write_private_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        path.parent.chmod(0o700)
    except OSError:
        pass
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        json.dump(value, handle, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())


class SetupEngine:
    def __init__(self, workspace: str | Path | None = None) -> None:
        self.workspace = resolve_workspace(workspace)
        self.store = SetupStore(self.workspace)

    def preflight(self) -> dict[str, Any]:
        evidence = _platform_evidence()
        proof = prove_host_capabilities(self.workspace, production=False)
        blockers = list(proof["blockers"])
        evidence["workspace"] = str(self.workspace)
        evidence["host_capability_proof"] = proof
        evidence["blockers"] = blockers
        evidence["publishing_authority"] = False
        if blockers:
            _fail(blockers[0], "Bootstrap setup preflight is blocked")
        return evidence

    def installation_admission(
        self,
        *,
        state_root: str | Path | None = None,
        config_root: str | Path | None = None,
        production: bool = False,
    ) -> dict[str, Any]:
        from ocpf_post.state import config_dir, state_dir

        state = Path(state_root).expanduser() if state_root is not None else state_dir()
        config = Path(config_root).expanduser() if config_root is not None else config_dir()
        classification = classify_existing_installation(
            self.workspace,
            state_root=state,
            config_root=config,
        )
        proof = prove_host_capabilities(self.workspace, production=production)
        return {
            "schema_version": 1,
            "status": "READY" if proof["status"] == "READY" and classification["classification"] not in {
                "inconsistent_authority",
                "interrupted_runtime_change",
            } else "BLOCKED",
            "host_capability_proof": proof,
            "installation": classification,
            "publishing_authority": bool(classification["publishing_authority"]),
            "provider_consequence_attempted": False,
        }

    def begin(
        self,
        mode: str,
        *,
        operator_label: str | None = None,
        machine_label: str | None = None,
    ) -> dict[str, Any]:
        selected = str(mode or "").strip().lower()
        if selected not in IMPLEMENTED_MODES:
            _fail(
                "setup.mode.not_implemented",
                "Milestone G implements fresh, explore, healthy migration and dead-host recovery",
            )
        if selected == "recover":
            _fail(
                "setup.recovery.bundle_required",
                "Dead-host recovery starts from an existing recovery point; use 'ocpf-post setup restore --recovery'",
            )
        preflight = self.preflight()
        try:
            if selected == "fresh":
                label = str(operator_label or "").strip()
                if not label:
                    _fail("setup.operator.required", "Fresh setup requires an operator/business label")
                created = self.store.create_fresh(label, machine_label=machine_label)
            elif selected == "migrate":
                label = str(operator_label or "").strip()
                if not label:
                    _fail("setup.operator.required", "Healthy migration source setup requires an operator/business label")
                created = self.store.create_migration_source(label, machine_label=machine_label)
            else:
                created = self.store.create_explore()
            session = created["session"]
            session = self.store.transition(
                session["session_id"],
                expected_revision=session["revision"],
                next_stage="preflight_ready",
                event_payload={
                    "python_supported": preflight["python_supported"],
                    "platform": preflight["platform"],
                    "wsl": preflight["wsl"],
                    "host_capabilities_ready": preflight["host_capability_proof"]["status"] == "READY",
                    "publishing_authority": False,
                },
            )
            if selected != "migrate":
                session = self.store.transition(
                    session["session_id"],
                    expected_revision=session["revision"],
                    next_stage="operation_ready",
                    event_payload={
                        "operation_created": selected == "fresh",
                        "publishing_authority": False,
                    },
                )
        except SetupStoreError as exc:
            raise SetupEngineError(exc.code, str(exc)) from exc
        return self.status(session["session_id"])

    def configure(
        self,
        session_id: str,
        *,
        timezone: str,
        pace: str,
        daily_originals: int | None = None,
    ) -> dict[str, Any]:
        zone = validate_timezone(timezone)
        pace_profile, amount = resolve_pace(pace, daily_originals)
        try:
            session = self.store.session(session_id)
            if session["stage"] == "configuration_ready":
                existing = self.store.config(session_id)
                if existing == {
                    "schema_version": 1,
                    "timezone": zone,
                    "pace_profile": pace_profile,
                    "daily_originals": amount,
                    "hard_ceiling": HARD_CEILING,
                    "publishing_authority": False,
                }:
                    return self._finish_after_configuration(session)
                _fail(
                    "setup.configuration.already_committed",
                    "Setup configuration is already committed; changing it requires a later reconfigure workflow",
                )
            if session["stage"] != "operation_ready":
                _fail(
                    "setup.stage.configuration_not_ready",
                    f"Cannot configure setup from stage {session['stage']}",
                )
            session = self.store.configure_and_transition(
                session_id,
                expected_revision=session["revision"],
                timezone=zone,
                pace_profile=pace_profile,
                daily_originals=amount,
                hard_ceiling=HARD_CEILING,
            )
            return self._finish_after_configuration(session)
        except SetupStoreError as exc:
            raise SetupEngineError(exc.code, str(exc)) from exc

    def _finish_after_configuration(self, session: dict[str, Any]) -> dict[str, Any]:
        if session["mode"] != "explore":
            return self.status(session["session_id"])
        current = session
        try:
            if current["stage"] == "configuration_ready":
                current = self.store.transition(
                    current["session_id"],
                    expected_revision=current["revision"],
                    next_stage="verification_ready",
                    event_payload={
                        "explore_isolated": True,
                        "provider_authority": False,
                        "publishing_authority": False,
                    },
                )
            if current["stage"] == "verification_ready":
                current = self.store.transition(
                    current["session_id"],
                    expected_revision=current["revision"],
                    next_stage="explore_ready",
                    event_payload={"explore_only": True, "publishing_authority": False},
                )
        except SetupStoreError as exc:
            raise SetupEngineError(exc.code, str(exc)) from exc
        return self.status(current["session_id"])

    def start(
        self,
        mode: str,
        *,
        operator_label: str | None,
        timezone: str | None = None,
        pace: str | None = None,
        daily_originals: int | None = None,
        machine_label: str | None = None,
    ) -> dict[str, Any]:
        selected = str(mode or "").strip().lower()
        if selected not in IMPLEMENTED_MODES:
            _fail(
                "setup.mode.not_implemented",
                "Milestone F implements fresh, explore and healthy same-operator migration",
            )
        if selected == "recover":
            _fail(
                "setup.recovery.bundle_required",
                "Dead-host recovery starts from an existing recovery point; use 'ocpf-post setup restore --recovery'",
            )
        if selected in {"fresh", "migrate"} and not str(operator_label or "").strip():
            _fail("setup.operator.required", f"{selected} setup requires an operator/business label")
        if selected == "migrate":
            if timezone is not None or pace is not None or daily_originals is not None:
                _fail(
                    "setup.migration.configuration_inherited",
                    "Healthy migration restores reviewed configuration from the bundle; do not choose a new pace here",
                )
            return self.begin(selected, operator_label=operator_label, machine_label=machine_label)

        if timezone is None or pace is None:
            _fail("setup.configuration.required", "Fresh/explore setup requires timezone and publishing pace")
        zone = validate_timezone(timezone)
        pace_profile, amount = resolve_pace(pace, daily_originals)
        begun = self.begin(selected, operator_label=operator_label, machine_label=machine_label)
        return self.configure(
            begun["session"]["session_id"],
            timezone=zone,
            pace=pace_profile,
            daily_originals=amount if pace_profile == "custom" else None,
        )

    def resume(
        self,
        *,
        session_id: str | None = None,
        timezone: str | None = None,
        pace: str | None = None,
        daily_originals: int | None = None,
    ) -> dict[str, Any]:
        try:
            session = self.store.session(session_id) if session_id else self.store.latest_session()
        except SetupStoreError as exc:
            raise SetupEngineError(exc.code, str(exc)) from exc
        if session is None:
            _fail("setup.session.missing", "No setup session exists")
        if session["session_status"] != "open":
            return self.status(session["session_id"])
        if session["mode"] in {"migrate", "recover"}:
            return self.status(session["session_id"])

        current = session
        try:
            if current["stage"] == "created":
                preflight = self.preflight()
                current = self.store.transition(
                    current["session_id"],
                    expected_revision=current["revision"],
                    next_stage="preflight_ready",
                    event_payload={
                        "python_supported": preflight["python_supported"],
                        "platform": preflight["platform"],
                        "wsl": preflight["wsl"],
                        "publishing_authority": False,
                    },
                )
            if current["stage"] == "preflight_ready":
                current = self.store.transition(
                    current["session_id"],
                    expected_revision=current["revision"],
                    next_stage="operation_ready",
                    event_payload={
                        "operation_created": current["mode"] == "fresh",
                        "publishing_authority": False,
                    },
                )
        except SetupStoreError as exc:
            raise SetupEngineError(exc.code, str(exc)) from exc

        if current["stage"] == "operation_ready":
            if timezone is None or pace is None:
                return self.status(current["session_id"])
            return self.configure(
                current["session_id"],
                timezone=timezone,
                pace=pace,
                daily_originals=daily_originals,
            )
        if current["mode"] == "explore" and current["stage"] in {"configuration_ready", "verification_ready"}:
            return self._finish_after_configuration(current)
        return self.status(current["session_id"])

    def _migration_dir(self, session_id: str) -> Path:
        path = self.workspace / "migrations" / session_id
        path.mkdir(parents=True, exist_ok=True)
        try:
            path.chmod(0o700)
        except OSError:
            pass
        return path

    def export_migration(
        self,
        *,
        state_root: str | Path,
        config_root: str | Path,
        output: str | Path,
        source_post_once_version: str,
        source_revision: str,
        source_state_registry_version: int = 1,
        session_id: str | None = None,
        apply: bool = False,
        expected_sha256: str | None = None,
        unit_observation: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        try:
            session = self.store.session(session_id) if session_id else self.store.latest_session()
            if session is None or session["mode"] != "migrate":
                _fail("setup.migration.session_required", "Start a healthy migration source session first")
            operation = self.store.operation(session["operation_id"])
            installation = self.store.installation_for_operation(session["operation_id"])
        except SetupStoreError as exc:
            raise SetupEngineError(exc.code, str(exc)) from exc

        migration_dir = self._migration_dir(session["session_id"])
        preview_path = migration_dir / "export-preview.json"

        if session["stage"] == "preflight_ready":
            try:
                preview = source_preview(
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
                raise _migration_error(exc) from exc
            if not apply:
                return {**self.status(session["session_id"]), "migration_export": preview}
            if expected_sha256 != preview["review_sha256"]:
                _fail("setup.migration.review_changed", "Migration export review hash is missing or changed")
            _write_private_json(preview_path, preview)
            try:
                retired = self.store.retire_migration_source(
                    session["session_id"],
                    expected_revision=session["revision"],
                    event_payload={
                        "review_sha256": preview["review_sha256"],
                        "writer_units": preview["writer_units"]["status"],
                        "file_count": preview["file_count"],
                        "publishing_authority": False,
                    },
                )
            except SetupStoreError as exc:
                raise SetupEngineError(exc.code, str(exc)) from exc
            session = retired["session"]
            operation = retired["operation"]
            installation = retired["installation"]
        elif session["stage"] == "source_drained":
            if not preview_path.is_file():
                _fail("setup.migration.preview_missing", "Reviewed migration source snapshot is missing")
            preview = json.loads(preview_path.read_text(encoding="utf-8"))
            if expected_sha256 is not None and expected_sha256 != preview.get("review_sha256"):
                _fail("setup.migration.review_changed", "Migration export review hash changed")
        elif session["stage"] == "bundle_verified":
            return self.status(session["session_id"])
        else:
            _fail("setup.migration.source_stage_invalid", f"Cannot export migration bundle from {session['stage']}")

        reviewed_metadata = (
            preview.get("source_post_once_version"),
            preview.get("source_revision"),
            preview.get("source_state_registry_version"),
        )
        requested_metadata = (
            source_post_once_version,
            source_revision,
            source_state_registry_version,
        )
        if requested_metadata != reviewed_metadata:
            _fail(
                "setup.migration.review_changed",
                "Source runtime metadata changed after migration export review",
            )

        try:
            verify_preview_sources(preview)
            output_path = Path(output).expanduser().resolve()
            if output_path.exists():
                verified = verify_healthy_bundle(output_path, migration_dir / "source-verify")
                sealed = {
                    "status": "existing_verified",
                    "bundle_sha256": verified["bundle_sha256"],
                    "manifest_sha256": verified["manifest_sha256"],
                    "output": str(output_path),
                    "operation_id": verified["operation_id"],
                    "source_installation_id": verified["source_installation_id"],
                    "source_authority_generation": verified["source_authority_generation"],
                }
            else:
                sealed = seal_source_bundle(
                    workspace=migration_dir,
                    operation=operation,
                    retired_installation=installation,
                    preview=preview,
                    output=output_path,
                    source_post_once_version=str(preview["source_post_once_version"]),
                    source_revision=str(preview["source_revision"]),
                    source_state_registry_version=int(preview["source_state_registry_version"]),
                )
                verified = verify_healthy_bundle(
                    output_path,
                    migration_dir / "source-verify",
                    expected_bundle_sha256=sealed["bundle_sha256"],
                )
            if (
                verified["operation_id"] != operation["operation_id"]
                or verified["source_installation_id"] != installation["installation_id"]
                or verified["source_authority_generation"] != operation["authority_generation"]
            ):
                _fail("setup.migration.bundle_identity_changed", "Sealed bundle does not match retired source authority")
        except MigrationError as exc:
            raise _migration_error(exc) from exc

        try:
            current = self.store.session(session["session_id"])
            if current["stage"] == "source_drained":
                current = self.store.transition(
                    current["session_id"],
                    expected_revision=current["revision"],
                    next_stage="bundle_verified",
                    event_payload={
                        "bundle_sha256": verified["bundle_sha256"],
                        "manifest_sha256": verified["manifest_sha256"],
                        "output": str(Path(output).expanduser().resolve()),
                        "source_continuity": "retired_and_quiescent",
                        "credentials_included": False,
                        "publishing_authority": False,
                    },
                )
        except SetupStoreError as exc:
            raise SetupEngineError(exc.code, str(exc)) from exc
        return {**self.status(current["session_id"]), "migration_export": sealed}

    def recovery_point(
        self,
        *,
        state_root: str | Path,
        config_root: str | Path,
        output: str | Path,
        source_post_once_version: str,
        source_revision: str,
        source_state_registry_version: int = 1,
        session_id: str | None = None,
        apply: bool = False,
        expected_sha256: str | None = None,
        unit_observation: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Preview or seal an immutable point-in-time artifact for future dead-host recovery."""
        try:
            session = self.store.session(session_id) if session_id else self.store.latest_session()
            if session is None or session["mode"] != "migrate":
                _fail(
                    "setup.recovery.source_session_required",
                    "Create the current source operation identity with setup start --mode migrate before taking a recovery point",
                )
            operation = self.store.operation(session["operation_id"])
            installation = self.store.installation_for_operation(session["operation_id"])
        except SetupStoreError as exc:
            raise SetupEngineError(exc.code, str(exc)) from exc

        if session["stage"] != "preflight_ready":
            _fail(
                "setup.recovery.source_stage_invalid",
                "Recovery points can only be created while the source bootstrap authority remains active",
            )

        try:
            preview = recovery_point_preview(
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
        except RecoveryError as exc:
            raise _recovery_error(exc) from exc

        if not apply:
            return {**self.status(session["session_id"]), "recovery_point_export": preview}
        if expected_sha256 != preview["review_sha256"]:
            _fail(
                "setup.recovery.review_changed",
                "Recovery-point review hash is missing or changed; the source remains active",
            )

        recovery_dir = self.workspace / "recovery-points" / session["session_id"]
        recovery_dir.mkdir(parents=True, exist_ok=True)
        try:
            recovery_dir.chmod(0o700)
        except OSError:
            pass

        output_path = Path(output).expanduser().resolve()
        try:
            if output_path.exists():
                verified = verify_recovery_point_bundle(output_path, recovery_dir / "source-verify")
                if (
                    verified["operation_id"] != operation["operation_id"]
                    or verified["source_installation_id"] != installation["installation_id"]
                    or verified["source_authority_generation"] != operation["authority_generation"]
                ):
                    _fail(
                        "setup.recovery.bundle_identity_changed",
                        "Existing recovery point does not match this source operation",
                    )
                sealed = {
                    "schema_version": 1,
                    "status": "existing_verified",
                    "bundle_sha256": verified["bundle_sha256"],
                    "manifest_sha256": verified["manifest_sha256"],
                    "captured_at": verified["sealed_at"],
                    "output": str(output_path),
                    "source_remains_active": True,
                    "credentials_included": False,
                }
            else:
                sealed = seal_recovery_point(
                    workspace=recovery_dir,
                    operation=operation,
                    installation=installation,
                    preview=preview,
                    output=output_path,
                )
                verified = verify_recovery_point_bundle(
                    output_path,
                    recovery_dir / "source-verify",
                    expected_bundle_sha256=sealed["bundle_sha256"],
                )
        except RecoveryError as exc:
            raise _recovery_error(exc) from exc

        try:
            self.store.record_evidence_event(
                session["session_id"],
                event_type="recovery_point_sealed",
                payload={
                    "bundle_sha256": verified["bundle_sha256"],
                    "manifest_sha256": verified["manifest_sha256"],
                    "captured_at": verified["sealed_at"],
                    "output": str(output_path),
                    "source_remains_active": True,
                    "credentials_included": False,
                    "publishing_authority": False,
                },
            )
        except SetupStoreError as exc:
            raise SetupEngineError(exc.code, str(exc)) from exc
        return {**self.status(session["session_id"]), "recovery_point_export": sealed}

    def restore_recovery(
        self,
        bundle: str | Path,
        *,
        source_lost_at: str,
        max_data_loss_minutes: int,
        expected_bundle_sha256: str | None = None,
        machine_label: str | None = None,
    ) -> dict[str, Any]:
        """Restore a dead/unknown source into quarantined recovery review state."""
        self.preflight()
        quarantine_parent = self.workspace / "quarantine"
        verified: dict[str, Any] | None = None
        try:
            verified = verify_recovery_point_bundle(
                bundle,
                quarantine_parent,
                expected_bundle_sha256=expected_bundle_sha256,
            )
            assessment = assess_recovery_point(
                verified,
                source_lost_at=source_lost_at,
                max_data_loss_minutes=max_data_loss_minutes,
            )

            try:
                existing = self.store.latest_session()
            except SetupStoreError as exc:
                if exc.code == "setup.store.missing":
                    existing = None
                else:
                    raise

            if existing is None:
                created = self.store.create_recovery_target(
                    verified["operation"],
                    verified["source_installation"],
                    machine_label=machine_label,
                )
                current = created["session"]
            else:
                if (
                    existing["mode"] != "recover"
                    or existing["session_status"] != "open"
                    or existing.get("operation_id") != verified["operation_id"]
                ):
                    _fail(
                        "setup.recovery.target_workspace_in_use",
                        "Target bootstrap workspace already contains a different setup/recovery session",
                    )
                current = existing
                prior_events = self.store.events(current["session_id"])
                prior_bundle_event = next(
                    (
                        row["evidence"]
                        for row in reversed(prior_events)
                        if row["to_stage"] == "bundle_verified"
                        and row["evidence"].get("bundle_sha256")
                    ),
                    None,
                )
                if prior_bundle_event is not None:
                    if prior_bundle_event.get("bundle_sha256") != verified["bundle_sha256"]:
                        _fail(
                            "setup.recovery.bundle_changed",
                            "Recovery retry supplied a different bundle than the one already committed",
                        )
                    if prior_bundle_event.get("recovery_point") != assessment:
                        _fail(
                            "setup.recovery.review_changed",
                            "Recovery retry changed the committed source-loss/RPO assessment",
                        )
                    if prior_bundle_event.get("authenticity") != verified["authenticity"]:
                        _fail(
                            "setup.recovery.review_changed",
                            "Recovery retry changed the committed artifact-authenticity evidence",
                        )

            if current["stage"] == "created":
                current = self.store.transition(
                    current["session_id"],
                    expected_revision=current["revision"],
                    next_stage="preflight_ready",
                    event_payload={
                        "target_preflight": "passed",
                        "source_status": "recovery_unknown",
                        "publishing_authority": False,
                    },
                )
            if current["stage"] == "preflight_ready":
                current = self.store.transition(
                    current["session_id"],
                    expected_revision=current["revision"],
                    next_stage="bundle_verified",
                    event_payload={
                        "bundle_sha256": verified["bundle_sha256"],
                        "manifest_sha256": verified["manifest_sha256"],
                        "integrity": verified["integrity"],
                        "authenticity": verified["authenticity"],
                        "source_continuity": verified["source_continuity"],
                        "recovery_point": assessment,
                        "quarantine": verified["quarantine"],
                        "credentials_included": False,
                        "publishing_authority": False,
                    },
                )
            if current["stage"] == "bundle_verified":
                prior_events = self.store.events(current["session_id"])
                prior_bundle_event = next(
                    (
                        row["evidence"]
                        for row in reversed(prior_events)
                        if row["to_stage"] == "bundle_verified"
                        and row["evidence"].get("bundle_sha256")
                    ),
                    None,
                )
                prior_quarantine = (
                    str(prior_bundle_event.get("quarantine") or "")
                    if prior_bundle_event is not None
                    else ""
                )
                if prior_quarantine and prior_quarantine != str(verified["quarantine"]):
                    shutil.rmtree(prior_quarantine, ignore_errors=True)
                current = self.store.transition(
                    current["session_id"],
                    expected_revision=current["revision"],
                    next_stage="restored_quarantined",
                    event_payload={
                        "quarantine": verified["quarantine"],
                        "expected_identities": verified["expected_identities"],
                        "source_index_count": len(verified["source_index"]),
                        "held_schedule_count": len(verified["schedule_dispositions"]),
                        "recovery_point": assessment,
                        "authenticity": verified["authenticity"],
                        "source_installation_id": verified["source_installation_id"],
                        "source_authority_generation": verified["source_authority_generation"],
                        "publishing_authority": False,
                    },
                )
            elif current["stage"] == "restored_quarantined":
                # The current retry reverified into a fresh quarantine, but the
                # committed restore already points at its earlier verified quarantine.
                shutil.rmtree(str(verified["quarantine"]), ignore_errors=True)
            elif current["stage"] not in {
                "provider_authority_ready",
                "schedule_reconciled",
                "recovery_review_ready",
            }:
                _fail(
                    "setup.recovery.restore_stage_invalid",
                    f"Cannot resume recovery restore from stage {current['stage']}",
                )
        except RecoveryError as exc:
            if verified is not None:
                shutil.rmtree(str(verified.get("quarantine") or ""), ignore_errors=True)
            raise _recovery_error(exc) from exc
        except SetupStoreError as exc:
            if verified is not None:
                shutil.rmtree(str(verified.get("quarantine") or ""), ignore_errors=True)
            raise SetupEngineError(exc.code, str(exc)) from exc
        return self.status(current["session_id"])

    def verify_recovery(
        self,
        provider_readiness: list[dict[str, Any]] | dict[str, Any],
        *,
        session_id: str | None = None,
    ) -> dict[str, Any]:
        """Verify a recovered target and stop at explicit RECOVERY_REVIEW_READY."""
        try:
            session = self.store.session(session_id) if session_id else self.store.latest_session()
            if session is None or session["mode"] != "recover":
                _fail("setup.recovery.session_required", "No dead-host recovery target session exists")
            events = self.store.events(session["session_id"])
        except SetupStoreError as exc:
            raise SetupEngineError(exc.code, str(exc)) from exc

        restore_event = next(
            (
                row
                for row in reversed(events)
                if row["to_stage"] == "restored_quarantined" and row["evidence"].get("quarantine")
            ),
            None,
        )
        if restore_event is None:
            _fail("setup.recovery.restore_evidence_missing", "Recovery quarantine evidence is missing")

        quarantine = restore_event["evidence"]["quarantine"]
        expected = restore_event["evidence"].get("expected_identities") or []
        point_assessment = restore_event["evidence"].get("recovery_point") or {}
        rows = provider_readiness.get("providers", []) if isinstance(provider_readiness, dict) else provider_readiness
        if not isinstance(rows, list):
            _fail("setup.recovery.provider_readiness_invalid", "Provider readiness input must be an array")

        current = session
        try:
            if current["stage"] == "restored_quarantined":
                provider_validation = validate_provider_readiness(expected, rows)
                if provider_validation["status"] != "ready":
                    _fail(
                        "setup.recovery.provider_authority_blocked",
                        "Recovered provider identity/write-authority evidence is incomplete or mismatched",
                    )
                current = self.store.transition(
                    current["session_id"],
                    expected_revision=current["revision"],
                    next_stage="provider_authority_ready",
                    event_payload={
                        **provider_validation,
                        "source_stale_authority": "unresolved",
                        "publishing_authority": False,
                    },
                )
            else:
                provider_validation = next(
                    (
                        row["evidence"]
                        for row in reversed(events)
                        if row["to_stage"] == "provider_authority_ready"
                    ),
                    {"status": "ready"},
                )

            if current["stage"] == "provider_authority_ready":
                schedules = load_quarantine_object(
                    quarantine,
                    "held_authority/schedule_dispositions",
                )
                reconciliation = reconcile_recovery_schedules(schedules.get("rows", []))
                if reconciliation["status"] == "blocked":
                    _fail(
                        "setup.recovery.schedule_reconciliation_blocked",
                        "Recovered schedule state contains an unknown state and cannot advance to review",
                    )
                path = self._migration_dir(current["session_id"]) / "recovery-schedule-reconciliation.json"
                _write_private_json(path, reconciliation)
                current = self.store.transition(
                    current["session_id"],
                    expected_revision=current["revision"],
                    next_stage="schedule_reconciled",
                    event_payload={
                        "reconciliation": str(path),
                        "schedule_count": len(reconciliation["rows"]),
                        "review_reasons": reconciliation["review_reasons"],
                        "automatic_rearm": False,
                        "automatic_catch_up": False,
                        "publishing_authority": False,
                    },
                )

            if current["stage"] == "schedule_reconciled":
                verified = {
                    "bundle_sha256": next(
                        (
                            row["evidence"].get("bundle_sha256")
                            for row in reversed(events)
                            if row["evidence"].get("bundle_sha256")
                        ),
                        None,
                    ),
                    "source_installation_id": restore_event["evidence"]["source_installation_id"],
                    "source_authority_generation": restore_event["evidence"]["source_authority_generation"],
                    "expected_identities": expected,
                    "authenticity": restore_event["evidence"].get("authenticity", "not_provided"),
                }
                reconciliation_path = self._migration_dir(current["session_id"]) / "recovery-schedule-reconciliation.json"
                reconciliation = json.loads(reconciliation_path.read_text(encoding="utf-8"))
                review = build_recovery_review(
                    verified=verified,
                    assessment=point_assessment,
                    provider_readiness=rows,
                    provider_validation=provider_validation,
                    reconciliation=reconciliation,
                )
                review_path = self._migration_dir(current["session_id"]) / "recovery-review.json"
                _write_private_json(review_path, review)
                current = self.store.transition(
                    current["session_id"],
                    expected_revision=current["revision"],
                    next_stage="recovery_review_ready",
                    event_payload={
                        "review": str(review_path),
                        "readiness_status": review["summary"]["status"],
                        "required_review_codes": review["summary"]["required_blocker_codes"],
                        "publishing_authority": False,
                        "automation_enabled": False,
                    },
                )
        except (MigrationError, RecoveryError) as exc:
            if isinstance(exc, MigrationError):
                raise _migration_error(exc) from exc
            raise _recovery_error(exc) from exc
        except SetupStoreError as exc:
            raise SetupEngineError(exc.code, str(exc)) from exc
        return self.status(current["session_id"])

    def inspect_bundle(
        self,
        bundle: str | Path,
        *,
        expected_bundle_sha256: str | None = None,
        recovery: bool = False,
    ) -> dict[str, Any]:
        try:
            with tempfile.TemporaryDirectory(prefix="post-once-bundle-inspect-") as temp:
                if recovery:
                    value = verify_recovery_point_bundle(
                        bundle,
                        temp,
                        expected_bundle_sha256=expected_bundle_sha256,
                    )
                else:
                    value = verify_healthy_bundle(
                        bundle,
                        temp,
                        expected_bundle_sha256=expected_bundle_sha256,
                    )
                value.pop("quarantine", None)
                return {
                    **value,
                    "status": "inspected",
                    "bundle_purpose": "dead_host_recovery" if recovery else "healthy_migration",
                }
        except RecoveryError as exc:
            raise _recovery_error(exc) from exc
        except MigrationError as exc:
            raise _migration_error(exc) from exc

    def restore_migration(
        self,
        bundle: str | Path,
        *,
        expected_bundle_sha256: str | None = None,
        machine_label: str | None = None,
    ) -> dict[str, Any]:
        self.preflight()
        quarantine_parent = self.workspace / "quarantine"
        try:
            verified = verify_healthy_bundle(
                bundle,
                quarantine_parent,
                expected_bundle_sha256=expected_bundle_sha256,
            )
            created = self.store.create_migration_target(
                verified["operation"],
                verified["source_installation"],
                machine_label=machine_label,
            )
            current = created["session"]
            current = self.store.transition(
                current["session_id"],
                expected_revision=current["revision"],
                next_stage="preflight_ready",
                event_payload={"target_preflight": "passed", "publishing_authority": False},
            )
            current = self.store.transition(
                current["session_id"],
                expected_revision=current["revision"],
                next_stage="source_drained",
                event_payload={
                    "source_installation_id": verified["source_installation_id"],
                    "source_continuity": verified["source_continuity"],
                    "publishing_authority": False,
                },
            )
            current = self.store.transition(
                current["session_id"],
                expected_revision=current["revision"],
                next_stage="bundle_verified",
                event_payload={
                    "bundle_sha256": verified["bundle_sha256"],
                    "manifest_sha256": verified["manifest_sha256"],
                    "integrity": verified["integrity"],
                    "credentials_included": False,
                    "publishing_authority": False,
                },
            )
            current = self.store.transition(
                current["session_id"],
                expected_revision=current["revision"],
                next_stage="restored_quarantined",
                event_payload={
                    "quarantine": verified["quarantine"],
                    "expected_identities": verified["expected_identities"],
                    "source_index_count": len(verified["source_index"]),
                    "held_schedule_count": len(verified["schedule_dispositions"]),
                    "publishing_authority": False,
                },
            )
        except MigrationError as exc:
            raise _migration_error(exc) from exc
        except SetupStoreError as exc:
            raise SetupEngineError(exc.code, str(exc)) from exc
        return self.status(current["session_id"])

    def verify_migration(
        self,
        provider_readiness: list[dict[str, Any]] | dict[str, Any],
        *,
        session_id: str | None = None,
    ) -> dict[str, Any]:
        try:
            session = self.store.session(session_id) if session_id else self.store.latest_session()
            if session is None or session["mode"] != "migrate":
                _fail("setup.migration.session_required", "No healthy migration target session exists")
            events = self.store.events(session["session_id"])
        except SetupStoreError as exc:
            raise SetupEngineError(exc.code, str(exc)) from exc

        restore_event = next(
            (
                row for row in reversed(events)
                if row["to_stage"] == "restored_quarantined" and row["evidence"].get("quarantine")
            ),
            None,
        )
        if restore_event is None:
            _fail("setup.migration.restore_evidence_missing", "Verified restore quarantine evidence is missing")
        quarantine = restore_event["evidence"]["quarantine"]
        expected = restore_event["evidence"].get("expected_identities") or []
        rows = provider_readiness.get("providers", []) if isinstance(provider_readiness, dict) else provider_readiness
        if not isinstance(rows, list):
            _fail("setup.migration.provider_readiness_invalid", "Provider readiness input must be an array")

        current = session
        try:
            if current["stage"] == "restored_quarantined":
                evidence = validate_provider_readiness(expected, rows)
                if evidence["status"] != "ready":
                    _fail(
                        "setup.migration.provider_authority_blocked",
                        "Target provider identity/write-authority evidence is incomplete or mismatched",
                    )
                current = self.store.transition(
                    current["session_id"],
                    expected_revision=current["revision"],
                    next_stage="provider_authority_ready",
                    event_payload={**evidence, "publishing_authority": False},
                )

            if current["stage"] == "provider_authority_ready":
                schedules = load_quarantine_object(
                    quarantine,
                    "held_authority/schedule_dispositions",
                )
                reconciliation = reconcile_schedules(schedules.get("rows", []))
                if reconciliation["status"] != "ready":
                    _fail(
                        "setup.migration.schedule_reconciliation_blocked",
                        "Transferred schedule state requires manual review before healthy migration can continue",
                    )
                path = self._migration_dir(current["session_id"]) / "schedule-reconciliation.json"
                _write_private_json(path, reconciliation)
                current = self.store.transition(
                    current["session_id"],
                    expected_revision=current["revision"],
                    next_stage="schedule_reconciled",
                    event_payload={
                        "reconciliation": str(path),
                        "schedule_count": len(reconciliation["rows"]),
                        "automatic_rearm": False,
                        "automatic_catch_up": False,
                        "publishing_authority": False,
                    },
                )

            if current["stage"] == "schedule_reconciled":
                current = self.store.transition(
                    current["session_id"],
                    expected_revision=current["revision"],
                    next_stage="verification_ready",
                    event_payload={
                        "healthy_handoff": True,
                        "source_retired": True,
                        "bundle_integrity": "verified",
                        "target_installation": "candidate",
                        "publishing_authority": False,
                        "automation_enabled": False,
                    },
                )
        except MigrationError as exc:
            raise _migration_error(exc) from exc
        except SetupStoreError as exc:
            raise SetupEngineError(exc.code, str(exc)) from exc
        return self.status(current["session_id"])

    def verify_fresh(
        self,
        provider_readiness: list[dict[str, Any]] | dict[str, Any],
        expected_identities: list[dict[str, str]],
        *,
        session_id: str | None = None,
    ) -> dict[str, Any]:
        """Verify one fresh standalone installation without granting authority.

        Provider evidence is read-only and content readiness is derived only from
        runtime/user-owned registry/source/campaign state. Historical packaged owner
        defaults cannot satisfy this gate in standalone product mode.
        """
        from ocpf_post.campaigns import campaign_ids, builtin_manifest
        from ocpf_post.product_runtime import standalone_product_active
        from ocpf_post.registry import load_registry
        from ocpf_post.runtime_sources import list_sources

        if not standalone_product_active():
            _fail(
                "setup.fresh.product_identity_required",
                "Fresh product verification requires the standalone Post-Once runtime identity",
            )
        try:
            session = self.store.session(session_id) if session_id else self.store.latest_session()
        except SetupStoreError as exc:
            raise SetupEngineError(exc.code, str(exc)) from exc
        if session is None or session["mode"] != "fresh":
            _fail("setup.fresh.session_required", "No fresh setup session exists")
        if session["stage"] == "verification_ready":
            return self.status(session["session_id"])
        if session["stage"] != "configuration_ready":
            _fail(
                "setup.fresh.verification_stage_invalid",
                f"Fresh provider/content verification requires configuration_ready; observed {session['stage']}",
            )

        if not isinstance(expected_identities, list) or not expected_identities:
            _fail("setup.fresh.expected_identity_required", "At least one expected provider identity is required")
        expected: list[dict[str, str]] = []
        seen: set[tuple[str, str]] = set()
        for row in expected_identities:
            if not isinstance(row, dict):
                _fail("setup.fresh.expected_identity_invalid", "Expected provider identities must be objects")
            provider = str(row.get("provider") or "").strip().lower()
            account_id = str(row.get("account_id") or "").strip()
            if provider not in {"x", "threads", "linkedin"} or not account_id:
                _fail("setup.fresh.expected_identity_invalid", "Expected provider identity is incomplete")
            key = (provider, account_id)
            if key in seen:
                _fail("setup.fresh.expected_identity_duplicate", "Expected provider identities must be unique")
            seen.add(key)
            expected.append({"provider": provider, "account_id": account_id})

        rows = provider_readiness.get("providers", []) if isinstance(provider_readiness, dict) else provider_readiness
        if not isinstance(rows, list):
            _fail("setup.fresh.provider_readiness_invalid", "Provider readiness input must be an array")
        validation = validate_provider_readiness(expected, rows)
        if validation["status"] != "ready":
            _fail(
                "setup.fresh.provider_authority_blocked",
                "Fresh provider identity/write-authority evidence is incomplete or mismatched",
            )

        registry = load_registry()
        projects = registry.get("projects") if isinstance(registry, dict) else None
        if not isinstance(projects, dict) or not projects:
            _fail(
                "setup.fresh.project_configuration_required",
                "Fresh activation requires at least one user-owned project/account registry",
            )

        sources = list_sources()
        runtime_sources = sources.get("runtime_sources") if isinstance(sources, dict) else None
        enabled_sources = sorted(
            str(project_id)
            for project_id, entry in (runtime_sources.items() if isinstance(runtime_sources, dict) else [])
            if isinstance(entry, dict) and entry.get("enabled") is True
        )
        runtime_campaigns = sorted(
            campaign
            for campaign in campaign_ids()
            if (
                isinstance((manifest := builtin_manifest(campaign)), dict)
                and manifest.get("runtime_imported") is True
            )
        )
        if not enabled_sources and not runtime_campaigns:
            _fail(
                "setup.fresh.content_configuration_required",
                "Fresh activation requires an enabled user-owned source or imported user-owned campaign",
            )

        safe_rows = [
            {
                "provider": str(row.get("provider") or ""),
                "expected_identity": row.get("expected_identity"),
                "observed_identity": row.get("observed_identity"),
                "identity_match": row.get("identity_match"),
                "write_scope_state": row.get("write_scope_state"),
                "ready_for_write_configuration": row.get("ready_for_write_configuration"),
                "blocking_reasons": list(row.get("blocking_reasons", [])),
            }
            for row in rows
            if isinstance(row, dict)
        ]
        try:
            current = self.store.transition(
                session["session_id"],
                expected_revision=session["revision"],
                next_stage="verification_ready",
                event_payload={
                    "fresh_provider_validation": validation,
                    "provider_readiness": safe_rows,
                    "runtime_project_count": len(projects),
                    "enabled_runtime_sources": enabled_sources,
                    "runtime_campaigns": runtime_campaigns,
                    "packaged_owner_defaults_used": False,
                    "provider_consequence_attempted": False,
                    "publishing_authority": False,
                    "automation_enabled": False,
                },
            )
        except SetupStoreError as exc:
            raise SetupEngineError(exc.code, str(exc)) from exc
        return self.status(current["session_id"])

    def activation_preview(
        self,
        *,
        runtime_root: str | Path,
        state_root: str | Path,
        config_root: str | Path,
        session_id: str | None = None,
        recovery_resolution: dict[str, Any] | None = None,
        service_controller: ServiceController | None = None,
    ) -> dict[str, Any]:
        try:
            return ActivationManager(
                store=self.store,
                workspace=self.workspace,
                service_controller=service_controller,
            ).preview(
                runtime_root=runtime_root,
                state_root=state_root,
                config_root=config_root,
                session_id=session_id,
                recovery_resolution=recovery_resolution,
            )
        except ActivationError as exc:
            raise SetupEngineError(exc.code, str(exc)) from exc

    def activate(
        self,
        *,
        runtime_root: str | Path,
        state_root: str | Path,
        config_root: str | Path,
        expected_sha256: str,
        session_id: str | None = None,
        recovery_resolution: dict[str, Any] | None = None,
        service_controller: ServiceController | None = None,
    ) -> dict[str, Any]:
        try:
            return ActivationManager(
                store=self.store,
                workspace=self.workspace,
                service_controller=service_controller,
            ).apply(
                runtime_root=runtime_root,
                state_root=state_root,
                config_root=config_root,
                expected_sha256=expected_sha256,
                session_id=session_id,
                recovery_resolution=recovery_resolution,
            )
        except ActivationError as exc:
            raise SetupEngineError(exc.code, str(exc)) from exc

    def deactivation_preview(
        self,
        *,
        runtime_root: str | Path,
        state_root: str | Path,
        reason: str,
        session_id: str | None = None,
        service_controller: ServiceController | None = None,
    ) -> dict[str, Any]:
        try:
            return ActivationManager(
                store=self.store,
                workspace=self.workspace,
                service_controller=service_controller,
            ).deactivate_preview(
                state_root=state_root,
                reason=reason,
                runtime_root=runtime_root,
                session_id=session_id,
            )
        except ActivationError as exc:
            raise SetupEngineError(exc.code, str(exc)) from exc

    def deactivate(
        self,
        *,
        runtime_root: str | Path,
        state_root: str | Path,
        reason: str,
        expected_sha256: str,
        session_id: str | None = None,
        service_controller: ServiceController | None = None,
    ) -> dict[str, Any]:
        try:
            return ActivationManager(
                store=self.store,
                workspace=self.workspace,
                service_controller=service_controller,
            ).deactivate(
                state_root=state_root,
                reason=reason,
                runtime_root=runtime_root,
                expected_sha256=expected_sha256,
                session_id=session_id,
            )
        except ActivationError as exc:
            raise SetupEngineError(exc.code, str(exc)) from exc

    def status(self, session_id: str | None = None) -> dict[str, Any]:
        try:
            session = self.store.session(session_id) if session_id else self.store.latest_session()
            if session is None:
                _fail("setup.session.missing", "No setup session exists")
            config = self.store.config(session["session_id"])
            events = self.store.events(session["session_id"])
            health = self.store.health()
            operation = None
            installation = None
            if session.get("operation_id"):
                operation = self.store.operation(session["operation_id"])
                installation = self.store.installation_for_operation(session["operation_id"])
        except SetupStoreError as exc:
            raise SetupEngineError(exc.code, str(exc)) from exc

        blockers: list[str] = []
        next_actions: list[str] = []
        if health["status"] != "ok":
            blockers.append("setup.store.integrity_attention")
        if session["session_status"] == "open" and session["stage"] in {"created", "preflight_ready", "operation_ready"}:
            next_actions.append("resume_setup")
        if session["stage"] == "operation_ready":
            blockers.append("setup.configuration.required")
            next_actions.append("choose_timezone_and_publishing_pace")
        if session["mode"] == "fresh" and session["stage"] == "configuration_ready":
            blockers += ["provider.configuration.required", "source.configuration.required"]
            next_actions += [
                "authorize_provider",
                "import_user_project_and_content",
                "verify_fresh_provider_and_content",
            ]
        elif session["mode"] == "fresh" and session["stage"] == "verification_ready":
            next_actions = ["preview_activation_gate"]
        if session["mode"] == "explore" and session["stage"] == "explore_ready":
            next_actions.append("explore_without_publishing")

        migration_role = None
        migration_evidence: dict[str, Any] = {}
        if session["mode"] == "migrate":
            if any(row["event_type"] == "migration_source_started" for row in events):
                migration_role = "source"
            elif any(row["event_type"] == "migration_target_started" for row in events):
                migration_role = "target"
            if session["stage"] == "preflight_ready" and migration_role == "source":
                blockers.append("migration.source.quiescence_and_export_required")
                next_actions = ["preview_and_export_migration_bundle"]
            elif session["stage"] == "source_drained" and migration_role == "source":
                blockers.append("migration.bundle.seal_required")
                next_actions = ["retry_migration_export"]
            elif session["stage"] == "bundle_verified" and migration_role == "source":
                next_actions = ["transfer_verified_bundle_to_target"]
            elif session["stage"] == "restored_quarantined" and migration_role == "target":
                blockers.append("provider.reauthorization_and_identity_verification.required")
                next_actions = ["reauthorize_providers_then_verify_migration"]
            elif session["stage"] in {"provider_authority_ready", "schedule_reconciled"}:
                next_actions = ["resume_migration_verification"]
            elif session["stage"] == "verification_ready":
                next_actions = ["preview_activation_gate"]
            latest_bundle = next(
                (row["evidence"] for row in reversed(events) if row["evidence"].get("bundle_sha256")),
                None,
            )
            if latest_bundle:
                migration_evidence["bundle"] = latest_bundle
            migration_evidence["role"] = migration_role

        recovery_evidence: dict[str, Any] = {}
        if session["mode"] == "recover":
            restore_evidence: dict[str, Any] = next(
                (
                    row["evidence"]
                    for row in reversed(events)
                    if row["to_stage"] == "restored_quarantined"
                ),
                {},
            )
            review_evidence: dict[str, Any] = next(
                (
                    row["evidence"]
                    for row in reversed(events)
                    if row["to_stage"] == "recovery_review_ready"
                ),
                {},
            )
            recovery_evidence = {
                "source_status": "recovery_unknown",
                "recovery_point": restore_evidence.get("recovery_point"),
                "authenticity": restore_evidence.get("authenticity"),
                "readiness_status": review_evidence.get("readiness_status"),
                "required_review_codes": review_evidence.get("required_review_codes", []),
                "review": review_evidence.get("review"),
            }
            if session["stage"] == "restored_quarantined":
                blockers.append("provider.reauthorization_and_identity_verification.required")
                blockers.append("recovery.stale_source_authority.unresolved")
                next_actions = ["reauthorize_providers_then_verify_recovery"]
            elif session["stage"] in {"provider_authority_ready", "schedule_reconciled"}:
                blockers.append("recovery.stale_source_authority.unresolved")
                next_actions = ["resume_recovery_verification"]
            elif session["stage"] == "recovery_review_ready":
                blockers.extend([
                    "recovery.review.required",
                    "recovery.stale_source_authority.unresolved",
                    "activation.recovery_resolution.required",
                ])
                next_actions = [
                    "review_recovery_readiness_report",
                    "fence_or_revoke_old_provider_authority",
                    "prepare_recovery_resolution_then_preview_activation",
                ]

        activation_event = next(
            (
                row
                for row in reversed(events)
                if row["event_type"] in {"automation_activated", "automation_deactivated"}
            ),
            None,
        )
        authority_projection: dict[str, Any] | None = None
        publishing_authority = False
        automation_enabled = False
        if activation_event is not None:
            state_root = activation_event["evidence"].get("state_root")
            if isinstance(state_root, str) and state_root:
                try:
                    authority_projection = automation_authority.read(state_root)
                except Exception:
                    authority_projection = {
                        "schema_version": 1,
                        "status": "invalid",
                        "path": str(Path(state_root) / "automation-authority.json"),
                    }
                publishing_authority = (
                    authority_projection.get("status") == "active"
                    and operation is not None
                    and installation is not None
                    and authority_projection.get("operation_id") == operation.get("operation_id")
                    and authority_projection.get("installation_id") == installation.get("installation_id")
                    and authority_projection.get("authority_generation") == operation.get("authority_generation")
                )
                attestation = activation_event["evidence"].get("service_attestation")
                automation_enabled = bool(
                    publishing_authority
                    and isinstance(attestation, dict)
                    and attestation.get("all_enabled")
                    and attestation.get("all_active")
                )
        if session["stage"] == "active":
            if publishing_authority:
                next_actions = ["monitor_or_deactivate_automation"]
            else:
                blockers.append("automation.deactivated")
                next_actions = ["preview_reactivation"]

        return {
            "schema_version": 1,
            "mode": session["mode"],
            "session": session,
            "operation": operation,
            "installation": installation,
            "configuration": config,
            "store": health,
            "completed_transitions": [
                {
                    "event_type": row["event_type"],
                    "from_stage": row["from_stage"],
                    "to_stage": row["to_stage"],
                    "revision": row["revision"],
                    "occurred_at": row["occurred_at"],
                }
                for row in events
            ],
            **({"migration": migration_evidence} if session["mode"] == "migrate" else {}),
            **({"recovery": recovery_evidence} if session["mode"] == "recover" else {}),
            "activation": {
                "authority": authority_projection,
                "last_event": activation_event["event_type"] if activation_event else None,
            },
            "blockers": sorted(set(blockers)),
            "next_actions": next_actions,
            "publishing_authority": publishing_authority,
            "automation_enabled": automation_enabled,
            "boundary": (
                "Setup & Recovery through Milestone H. Activation is a reviewed local cutover guarded by an "
                "atomic unattended-authority marker; deactivation closes that marker before stopping timers. "
                "Recovery activation still requires explicit resolution of stale-host/recovery-review blockers."
            ),
        }
