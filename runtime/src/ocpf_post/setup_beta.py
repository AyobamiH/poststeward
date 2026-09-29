"""Milestone K limited-beta evidence and bootstrap-owned runtime adoption gating.

K is intentionally local and opt-in. It records allowlisted health evidence only;
it never changes provider credentials, publishing authority, schedules, the original
AyobamiH/post-once repository, or the operator's existing local Post-Once runtime.
"""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
from typing import Any
import uuid

from ocpf_post import automation_authority, local_store
from ocpf_post.setup_engine import SetupEngine, SetupEngineError
from ocpf_post.setup_store import SetupStoreError, resolve_workspace

UTC = timezone.utc
SCHEMA_VERSION = 1
RINGS = frozenset({"rehearsal", "owner-canary"})
SHA_RE = re.compile(r"^[0-9a-f]{40}$")
LEDGER_NAME = "beta-evidence.jsonl"
MAX_LEDGER_BYTES = 5_000_000
MAX_LEDGER_EVENTS = 10_000
MAX_CANARY_EVIDENCE_BYTES = 50_000_000


class BetaError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def _fail(code: str, message: str) -> None:
    raise BetaError(code, message)


def _now(value: datetime | None = None) -> str:
    current = (value or datetime.now(UTC)).astimezone(UTC).replace(microsecond=0)
    return current.isoformat().replace("+00:00", "Z")


def _parse_time(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise BetaError("beta.time.invalid", "Beta evidence timestamp is invalid") from exc
    if parsed.tzinfo is None:
        _fail("beta.time.invalid", "Beta evidence timestamp must include timezone")
    return parsed.astimezone(UTC)


def _canonical(value: Any) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    ).encode("ascii")


def _event_hash(value: dict[str, Any]) -> str:
    body = {key: item for key, item in value.items() if key != "event_sha256"}
    return hashlib.sha256(_canonical(body)).hexdigest()


def _beta_root(workspace: str | Path | None, *, create: bool) -> Path:
    root = resolve_workspace(workspace) / "beta"
    if create:
        root.mkdir(parents=True, exist_ok=True)
        try:
            root.chmod(0o700)
        except OSError:
            pass
    return root


def ledger_path(workspace: str | Path | None, *, create: bool = False) -> Path:
    return _beta_root(workspace, create=create) / LEDGER_NAME


def _read_events(workspace: str | Path | None) -> list[dict[str, Any]]:
    path = ledger_path(workspace)
    if not path.exists():
        return []
    if path.is_symlink() or not path.is_file():
        _fail("beta.ledger.invalid", "Beta evidence ledger must be a regular file")
    try:
        mode = path.stat().st_mode & 0o777
    except OSError as exc:
        raise BetaError("beta.ledger.unreadable", "Beta evidence ledger metadata is unreadable") from exc
    if mode & 0o077:
        _fail("beta.ledger.permissions", "Beta evidence ledger permissions must be private")
    try:
        size = path.stat().st_size
    except OSError as exc:
        raise BetaError("beta.ledger.unreadable", "Beta evidence ledger size is unreadable") from exc
    if size > MAX_LEDGER_BYTES:
        _fail("beta.ledger.too_large", "Beta evidence ledger exceeds its bounded size")
    rows: list[dict[str, Any]] = []
    previous = ""
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeDecodeError) as exc:
        raise BetaError("beta.ledger.unreadable", "Beta evidence ledger is unreadable") from exc
    if len(lines) > MAX_LEDGER_EVENTS:
        _fail("beta.ledger.too_many_events", "Beta evidence ledger exceeds its bounded event count")
    for index, line in enumerate(lines, start=1):
        if not line.strip():
            _fail("beta.ledger.invalid", "Beta evidence ledger contains an empty record")
        try:
            row = json.loads(line)
        except json.JSONDecodeError as exc:
            raise BetaError("beta.ledger.invalid", "Beta evidence ledger contains invalid JSON") from exc
        if not isinstance(row, dict) or row.get("schema_version") != SCHEMA_VERSION:
            _fail("beta.ledger.invalid", "Beta evidence ledger has unsupported schema")
        if row.get("sequence") != index:
            _fail("beta.ledger.sequence", "Beta evidence sequence is not contiguous")
        if row.get("previous_sha256", "") != previous:
            _fail("beta.ledger.chain", "Beta evidence hash chain is broken")
        observed_hash = str(row.get("event_sha256") or "")
        if observed_hash != _event_hash(row):
            _fail("beta.ledger.hash", "Beta evidence record hash does not match its content")
        previous = observed_hash
        rows.append(row)
    return rows


def _append_event(workspace: str | Path | None, value: dict[str, Any]) -> dict[str, Any]:
    path = ledger_path(workspace, create=True)
    try:
        with local_store.locked(path):
            rows = _read_events(workspace)
            if len(rows) >= MAX_LEDGER_EVENTS:
                _fail("beta.ledger.too_many_events", "Beta evidence ledger cannot accept more events")
            row = {
                "schema_version": SCHEMA_VERSION,
                "sequence": len(rows) + 1,
                "previous_sha256": rows[-1]["event_sha256"] if rows else "",
                **value,
            }
            row["event_sha256"] = _event_hash(row)
            raw = _canonical(row) + b"\n"
            current_size = path.stat().st_size if path.exists() else 0
            if current_size + len(raw) > MAX_LEDGER_BYTES:
                _fail("beta.ledger.too_large", "Beta evidence ledger cannot accept another event")
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
            try:
                offset = 0
                while offset < len(raw):
                    written = os.write(fd, raw[offset:])
                    if written <= 0:
                        _fail("beta.ledger.write_failed", "Beta evidence ledger write did not make progress")
                    offset += written
                os.fsync(fd)
            finally:
                os.close(fd)
            try:
                path.chmod(0o600)
            except OSError:
                pass
            dir_fd = os.open(path.parent, os.O_RDONLY)
            try:
                os.fsync(dir_fd)
            finally:
                os.close(dir_fd)
            return row
    except BlockingIOError as exc:
        raise BetaError(
            "beta.ledger.concurrent_writer",
            "Another beta evidence writer currently owns the local ledger",
        ) from exc


def _git_revision(runtime_root: str | Path) -> str:
    root = Path(runtime_root).expanduser()
    result = subprocess.run(
        ["git", "-C", str(root), "rev-parse", "--verify", "HEAD^{commit}"],
        text=True,
        capture_output=True,
        timeout=15,
        check=False,
    )
    revision = result.stdout.strip().lower()
    if result.returncode or not SHA_RE.fullmatch(revision):
        _fail("beta.runtime_revision.unavailable", "Could not prove one exact runtime Git revision")
    dirty = subprocess.run(
        ["git", "-C", str(root), "status", "--porcelain=v1", "--untracked-files=all"],
        text=True,
        capture_output=True,
        timeout=15,
        check=False,
    )
    if dirty.returncode or dirty.stdout.strip():
        _fail("beta.runtime_checkout.dirty", "Beta runtime checkout must be clean")
    return revision


def _bounded_file_digest(path: Path) -> tuple[str | None, int]:
    if not path.exists():
        return None, 0
    if path.is_symlink() or not path.is_file():
        _fail("beta.owner_canary.evidence_invalid", f"Canary evidence path is not a regular file: {path.name}")
    try:
        size = path.stat().st_size
    except OSError as exc:
        raise BetaError("beta.owner_canary.evidence_unreadable", "Canary evidence metadata is unreadable") from exc
    if size > MAX_CANARY_EVIDENCE_BYTES:
        _fail("beta.owner_canary.evidence_too_large", "Canary evidence exceeds its bounded size")
    digest = hashlib.sha256()
    count = 0
    try:
        with path.open("rb") as handle:
            for line in handle:
                digest.update(line)
                if line.strip():
                    count += 1
    except OSError as exc:
        raise BetaError("beta.owner_canary.evidence_unreadable", "Canary evidence is unreadable") from exc
    return digest.hexdigest(), count


def _json_object(path: Path, *, code: str) -> dict[str, Any]:
    if not path.exists():
        return {}
    if path.is_symlink() or not path.is_file():
        _fail(code, f"Canary JSON evidence path is not a regular file: {path.name}")
    try:
        if path.stat().st_size > MAX_CANARY_EVIDENCE_BYTES:
            _fail("beta.owner_canary.evidence_too_large", "Canary JSON evidence exceeds its bounded size")
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise BetaError(code, "Canary JSON evidence is invalid or unreadable") from exc
    if not isinstance(value, dict):
        _fail(code, "Canary JSON evidence must be an object")
    return value


def _runtime_campaign_snapshot(state_root: Path) -> dict[str, Any]:
    root = state_root / "runtime-campaigns"
    if not root.exists():
        empty = hashlib.sha256(_canonical([])).hexdigest()
        return {
            "runtime_campaign_count": 0,
            "allocator_enabled_campaign_count": 0,
            "runtime_campaign_tree_sha256": empty,
        }
    if root.is_symlink() or not root.is_dir():
        _fail("beta.owner_canary.campaign_tree_invalid", "Runtime campaign root must be a plain directory")
    rows: list[dict[str, Any]] = []
    campaigns: set[str] = set()
    allocator_enabled = 0
    total_bytes = 0
    for item in sorted(root.rglob("*")):
        if item.is_symlink():
            _fail("beta.owner_canary.campaign_tree_invalid", "Runtime campaign tree contains a symbolic link")
        if item.is_dir():
            continue
        if not item.is_file():
            _fail("beta.owner_canary.campaign_tree_invalid", "Runtime campaign tree contains a non-regular file")
        relative = item.relative_to(root).as_posix()
        try:
            size = item.stat().st_size
        except OSError as exc:
            raise BetaError("beta.owner_canary.evidence_unreadable", "Runtime campaign metadata is unreadable") from exc
        total_bytes += size
        if total_bytes > MAX_CANARY_EVIDENCE_BYTES:
            _fail("beta.owner_canary.evidence_too_large", "Runtime campaign evidence exceeds its bounded size")
        try:
            raw = item.read_bytes()
        except OSError as exc:
            raise BetaError("beta.owner_canary.evidence_unreadable", "Runtime campaign evidence is unreadable") from exc
        rows.append({"path": relative, "size": size, "sha256": hashlib.sha256(raw).hexdigest()})
        parts = Path(relative).parts
        if parts:
            campaigns.add(parts[0])
        if item.name == "manifest.json":
            try:
                value = json.loads(raw.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise BetaError(
                    "beta.owner_canary.campaign_manifest_invalid",
                    "Runtime campaign manifest is invalid",
                ) from exc
            if not isinstance(value, dict):
                _fail("beta.owner_canary.campaign_manifest_invalid", "Runtime campaign manifest must be an object")
            allocation = value.get("allocation")
            if isinstance(allocation, dict) and allocation.get("enabled") is True:
                allocator_enabled += 1
    return {
        "runtime_campaign_count": len(campaigns),
        "allocator_enabled_campaign_count": allocator_enabled,
        "runtime_campaign_tree_sha256": hashlib.sha256(_canonical(rows)).hexdigest(),
    }


def _owner_canary_quiet_snapshot(
    *, state_root: str | Path, config_root: str | Path
) -> dict[str, Any]:
    state = Path(state_root).expanduser()
    config = Path(config_root).expanduser()

    source_path = config / "runtime-sources.json"
    source_data = _json_object(
        source_path,
        code="beta.owner_canary.runtime_sources_invalid",
    )
    raw_projects = source_data.get("projects", {}) if source_data else {}
    if not isinstance(raw_projects, dict):
        _fail("beta.owner_canary.runtime_sources_invalid", "Runtime source projects must be an object")
    enabled_sources = sorted(
        str(project_id)
        for project_id, entry in raw_projects.items()
        if isinstance(entry, dict) and entry.get("enabled") is True
    )

    project_sha, _project_lines = _bounded_file_digest(config / "runtime-projects.json")
    source_sha, _source_lines = _bounded_file_digest(source_path)
    schedule_sha, schedule_events = _bounded_file_digest(state / "schedule-events.jsonl")
    receipt_sha, receipt_events = _bounded_file_digest(state / "publish-receipts.jsonl")
    campaigns = _runtime_campaign_snapshot(state)

    blockers: list[str] = []
    if enabled_sources:
        blockers.append("beta.owner_canary.enabled_source_present")
    if campaigns["allocator_enabled_campaign_count"]:
        blockers.append("beta.owner_canary.allocator_enabled_campaign_present")
    if schedule_events:
        blockers.append("beta.owner_canary.schedule_history_present")
    if receipt_events:
        blockers.append("beta.owner_canary.provider_effect_history_present")

    evidence = {
        "schema_version": 1,
        "enabled_runtime_source_count": len(enabled_sources),
        "allocator_enabled_campaign_count": campaigns["allocator_enabled_campaign_count"],
        "runtime_campaign_count": campaigns["runtime_campaign_count"],
        "schedule_event_count": schedule_events,
        "provider_effect_receipt_count": receipt_events,
        "project_registry_sha256": project_sha,
        "runtime_sources_sha256": source_sha,
        "runtime_campaign_tree_sha256": campaigns["runtime_campaign_tree_sha256"],
        "schedule_ledger_sha256": schedule_sha,
        "provider_effect_ledger_sha256": receipt_sha,
        "blockers": sorted(blockers),
    }
    return {
        **evidence,
        "quiet": not blockers,
        "quiet_evidence_sha256": hashlib.sha256(_canonical(evidence)).hexdigest(),
        "boundary": (
            "Read-only consequence-quiet proof. No provider call, schedule mutation, "
            "source activation, campaign allocation or authority change is attempted."
        ),
    }


def _latest_enrollment(events: list[dict[str, Any]], beta_id: str | None = None) -> dict[str, Any]:
    rows = [
        row for row in events
        if row.get("event") == "enrolled" and (beta_id is None or row.get("beta_id") == beta_id)
    ]
    if not rows:
        _fail("beta.enrollment.missing", "No matching beta enrollment exists")
    return rows[-1]


def _status_snapshot(
    *,
    workspace: str | Path | None,
    state_root: str | Path,
    config_root: str | Path,
    runtime_root: str | Path,
    production: bool,
) -> dict[str, Any]:
    engine = SetupEngine(workspace)
    admission = engine.installation_admission(
        state_root=state_root,
        config_root=config_root,
        production=production,
    )
    try:
        setup = engine.status()
    except (SetupEngineError, SetupStoreError) as exc:
        if getattr(exc, "code", "") in {"setup.store.missing", "setup.session.missing"}:
            setup = None
        else:
            raise
    try:
        marker = automation_authority.read(state_root)
    except automation_authority.AutomationAuthorityError:
        marker = {"status": "invalid"}
    setup_identity = admission["installation"].get("setup") or {}
    return {
        "runtime_revision": _git_revision(runtime_root),
        "admission_status": admission["status"],
        "classification": admission["installation"]["classification"],
        "recommended_action": admission["installation"]["recommended_action"],
        "operation_id": setup_identity.get("operation_id"),
        "installation_id": setup_identity.get("installation_id"),
        "authority_generation": marker.get("authority_generation"),
        "publishing_authority": bool(admission["publishing_authority"]),
        "host_proof_status": admission["host_capability_proof"]["status"],
        "host_blockers": list(admission["host_capability_proof"]["blockers"]),
        "automation_marker_status": marker.get("status"),
        "setup_stage": setup["session"]["stage"] if setup else None,
        "setup_store_health": (
            setup.get("store", {}).get("status")
            if setup and isinstance(setup.get("store"), dict)
            else None
        ),
        "owner_canary_quiet": _owner_canary_quiet_snapshot(
            state_root=state_root,
            config_root=config_root,
        ),
    }


def preflight(
    *,
    workspace: str | Path | None,
    ring: str,
    target_revision: str,
    state_root: str | Path,
    config_root: str | Path,
    runtime_root: str | Path,
) -> dict[str, Any]:
    """Read-only beta admission proof. No evidence record or authority mutation."""
    selected = str(ring or "").strip().lower()
    if selected not in RINGS:
        _fail("beta.ring.invalid", "Beta ring must be rehearsal or owner-canary")
    revision = str(target_revision or "").strip().lower()
    if not SHA_RE.fullmatch(revision):
        _fail("beta.target_revision.invalid", "Beta target must be one exact 40-character Git revision")
    snapshot = _status_snapshot(
        workspace=workspace,
        state_root=state_root,
        config_root=config_root,
        runtime_root=runtime_root,
        production=selected == "owner-canary",
    )
    blockers: list[str] = []
    if snapshot["runtime_revision"] != revision:
        blockers.append("beta.target_revision.mismatch")
    if snapshot["admission_status"] != "READY":
        blockers.append("beta.admission.blocked")
    if snapshot["host_proof_status"] != "READY":
        blockers.append("beta.host.blocked")
    if selected == "rehearsal":
        if snapshot["publishing_authority"] or snapshot["automation_marker_status"] == "active":
            blockers.append("beta.rehearsal.authority_open")
    else:
        if snapshot["classification"] != "active_installation":
            blockers.append("beta.owner_canary.not_active")
        if not snapshot["publishing_authority"] or snapshot["automation_marker_status"] != "active":
            blockers.append("beta.owner_canary.authority_closed")
        if not snapshot["owner_canary_quiet"].get("quiet"):
            blockers.append("beta.owner_canary.consequence_quiet_required")
    return {
        "schema_version": SCHEMA_VERSION,
        "status": "READY" if not blockers else "BLOCKED",
        "ring": selected,
        "target_revision": revision,
        "runtime_revision": snapshot["runtime_revision"],
        "classification": snapshot["classification"],
        "operation_id": snapshot["operation_id"],
        "installation_id": snapshot["installation_id"],
        "authority_generation": snapshot["authority_generation"],
        "publishing_authority": snapshot["publishing_authority"],
        "automation_marker_status": snapshot["automation_marker_status"],
        "host_proof_status": snapshot["host_proof_status"],
        "owner_canary_quiet": snapshot["owner_canary_quiet"] if selected == "owner-canary" else None,
        "blockers": sorted(set(blockers)),
        "provider_consequence_attempted": False,
        "beta_evidence_written": False,
        "boundary": (
            "Read-only beta admission proof. No beta ledger event, provider call, "
            "schedule mutation, credential change or publishing-authority change is attempted."
        ),
    }


def enroll(
    *,
    workspace: str | Path | None,
    ring: str,
    target_revision: str,
    state_root: str | Path,
    config_root: str | Path,
    runtime_root: str | Path,
    now: datetime | None = None,
) -> dict[str, Any]:
    selected = str(ring or "").strip().lower()
    if selected not in RINGS:
        _fail("beta.ring.invalid", "Beta ring must be rehearsal or owner-canary")
    revision = str(target_revision or "").strip().lower()
    if not SHA_RE.fullmatch(revision):
        _fail("beta.target_revision.invalid", "Beta target must be one exact 40-character Git revision")
    snapshot = _status_snapshot(
        workspace=workspace,
        state_root=state_root,
        config_root=config_root,
        runtime_root=runtime_root,
        production=selected == "owner-canary",
    )
    if snapshot["runtime_revision"] != revision:
        _fail("beta.target_revision.mismatch", "Running checkout does not match the beta target revision")
    if snapshot["admission_status"] != "READY":
        _fail("beta.admission.blocked", "Installation admission is blocked")
    if selected == "rehearsal" and snapshot["publishing_authority"]:
        _fail("beta.rehearsal.authority_open", "Rehearsal ring requires publishing authority to remain off")
    if selected == "owner-canary":
        if snapshot["classification"] != "active_installation":
            _fail("beta.owner_canary.not_active", "Owner canary requires one active classified installation")
        if not snapshot["publishing_authority"] or snapshot["automation_marker_status"] != "active":
            _fail("beta.owner_canary.authority_closed", "Owner canary requires the already-reviewed active authority state")
        quiet = snapshot["owner_canary_quiet"]
        if not quiet.get("quiet"):
            _fail(
                "beta.owner_canary.consequence_quiet_required",
                "Owner canary requires zero eligible publishing work and pristine schedule/effect ledgers",
            )
    beta_id = str(uuid.uuid4())
    event = _append_event(workspace, {
        "event": "enrolled",
        "beta_id": beta_id,
        "ring": selected,
        "target_revision": revision,
        "observed_at": _now(now),
        "classification": snapshot["classification"],
        "operation_id": snapshot["operation_id"],
        "installation_id": snapshot["installation_id"],
        "authority_generation": snapshot["authority_generation"],
        "publishing_authority": snapshot["publishing_authority"],
        "owner_canary_quiet_evidence_sha256": (
            snapshot["owner_canary_quiet"]["quiet_evidence_sha256"]
            if selected == "owner-canary"
            else None
        ),
        "owner_canary_quiet_counts": (
            {
                key: snapshot["owner_canary_quiet"][key]
                for key in (
                    "enabled_runtime_source_count",
                    "allocator_enabled_campaign_count",
                    "runtime_campaign_count",
                    "schedule_event_count",
                    "provider_effect_receipt_count",
                )
            }
            if selected == "owner-canary"
            else None
        ),
        "provider_consequence_attempted": False,
    })
    return {
        "schema_version": SCHEMA_VERSION,
        "status": "ENROLLED",
        "beta_id": beta_id,
        "ring": selected,
        "target_revision": revision,
        "event_sha256": event["event_sha256"],
        "consequence_quiet_verified": (
            snapshot["owner_canary_quiet"]["quiet"] if selected == "owner-canary" else None
        ),
        "quiet_evidence_sha256": (
            snapshot["owner_canary_quiet"]["quiet_evidence_sha256"]
            if selected == "owner-canary"
            else None
        ),
        "boundary": "Enrollment records local beta intent only; it does not enable publishing or mutate provider state.",
    }


def observe(
    *,
    workspace: str | Path | None,
    beta_id: str | None,
    state_root: str | Path,
    config_root: str | Path,
    runtime_root: str | Path,
    operator_outcome: str,
    now: datetime | None = None,
) -> dict[str, Any]:
    events = _read_events(workspace)
    enrollment = _latest_enrollment(events, beta_id)
    outcome = str(operator_outcome or "").strip().lower()
    if outcome not in {"healthy", "issue", "rollback"}:
        _fail("beta.outcome.invalid", "Operator outcome must be healthy, issue, or rollback")
    snapshot = _status_snapshot(
        workspace=workspace,
        state_root=state_root,
        config_root=config_root,
        runtime_root=runtime_root,
        production=enrollment["ring"] == "owner-canary",
    )
    observed_at = _now(now)
    observed_time = _parse_time(observed_at)
    beta_rows = [
        row for row in events
        if row.get("beta_id") == enrollment["beta_id"] and isinstance(row.get("observed_at"), str)
    ]
    last_time = _parse_time(beta_rows[-1]["observed_at"]) if beta_rows else _parse_time(enrollment["observed_at"])
    if observed_time < last_time:
        _fail("beta.time.regressed", "Beta observation time cannot move backwards")
    blockers: list[str] = []
    if snapshot["runtime_revision"] != enrollment["target_revision"]:
        blockers.append("beta.runtime_revision.drift")
    if snapshot["operation_id"] != enrollment.get("operation_id"):
        blockers.append("beta.operation.drift")
    if snapshot["installation_id"] != enrollment.get("installation_id"):
        blockers.append("beta.installation.drift")
    if snapshot["authority_generation"] != enrollment.get("authority_generation"):
        blockers.append("beta.authority_generation.drift")
    if snapshot["admission_status"] != "READY":
        blockers.append("beta.admission.blocked")
    if snapshot["host_proof_status"] != "READY":
        blockers.append("beta.host.blocked")
    if enrollment["ring"] == "rehearsal":
        if snapshot["publishing_authority"] or snapshot["automation_marker_status"] == "active":
            blockers.append("beta.rehearsal.authority_open")
    else:
        if snapshot["classification"] != "active_installation":
            blockers.append("beta.owner_canary.not_active")
        if not snapshot["publishing_authority"] or snapshot["automation_marker_status"] != "active":
            blockers.append("beta.owner_canary.authority_closed")
        quiet = snapshot["owner_canary_quiet"]
        if not quiet.get("quiet"):
            blockers.append("beta.owner_canary.consequence_eligible_work")
        baseline = str(enrollment.get("owner_canary_quiet_evidence_sha256") or "")
        if not baseline:
            blockers.append("beta.owner_canary.quiet_baseline_missing")
        elif quiet.get("quiet_evidence_sha256") != baseline:
            blockers.append("beta.owner_canary.quiet_baseline_drift")
    if outcome == "issue":
        blockers.append("beta.operator.issue")
    if outcome == "rollback":
        blockers.append("beta.operator.rollback")
    event = _append_event(workspace, {
        "event": "observation",
        "beta_id": enrollment["beta_id"],
        "ring": enrollment["ring"],
        "target_revision": enrollment["target_revision"],
        "observed_at": observed_at,
        "operator_outcome": outcome,
        "snapshot": snapshot,
        "blockers": sorted(set(blockers)),
        "quiet_evidence_sha256": (
            snapshot["owner_canary_quiet"]["quiet_evidence_sha256"]
            if enrollment["ring"] == "owner-canary"
            else None
        ),
        "provider_consequence_attempted": False,
    })
    return {
        "schema_version": SCHEMA_VERSION,
        "status": "HEALTHY" if not blockers else "ATTENTION",
        "beta_id": enrollment["beta_id"],
        "ring": enrollment["ring"],
        "blockers": sorted(set(blockers)),
        "event_sha256": event["event_sha256"],
    }


def status(workspace: str | Path | None, *, beta_id: str | None = None) -> dict[str, Any]:
    events = _read_events(workspace)
    enrollment = _latest_enrollment(events, beta_id)
    observations = [
        row for row in events
        if row.get("event") == "observation" and row.get("beta_id") == enrollment["beta_id"]
    ]
    return {
        "schema_version": SCHEMA_VERSION,
        "beta_id": enrollment["beta_id"],
        "ring": enrollment["ring"],
        "target_revision": enrollment["target_revision"],
        "enrolled_at": enrollment["observed_at"],
        "observation_count": len(observations),
        "healthy_observation_count": sum(not row.get("blockers") for row in observations),
        "latest_observation": observations[-1] if observations else None,
        "ledger_event_count": len(events),
        "ledger_head_sha256": events[-1]["event_sha256"] if events else None,
    }


def decide(
    workspace: str | Path | None,
    *,
    beta_id: str | None = None,
    minimum_observations: int | None = None,
    minimum_hours: int | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    if minimum_observations is not None and minimum_observations < 1:
        _fail("beta.minimum_observations.invalid", "Minimum observations must be positive")
    if minimum_hours is not None and minimum_hours < 0:
        _fail("beta.minimum_hours.invalid", "Minimum hours cannot be negative")
    events = _read_events(workspace)
    enrollment = _latest_enrollment(events, beta_id)
    observations = [
        row for row in events
        if row.get("event") == "observation" and row.get("beta_id") == enrollment["beta_id"]
    ]
    default_observations = 2 if enrollment["ring"] == "rehearsal" else 3
    default_hours = 1 if enrollment["ring"] == "rehearsal" else 24
    required_observations = minimum_observations or default_observations
    required_hours = default_hours if minimum_hours is None else minimum_hours
    blockers: list[str] = []
    if len(observations) < required_observations:
        blockers.append("beta.observation_count.insufficient")
    if observations:
        first = _parse_time(observations[0]["observed_at"])
        last = _parse_time(observations[-1]["observed_at"])
        span_hours = (last - first).total_seconds() / 3600
    else:
        span_hours = 0.0
    if span_hours < required_hours:
        blockers.append("beta.observation_window.insufficient")
    for row in observations:
        blockers.extend(str(item) for item in row.get("blockers", []))
        if row.get("target_revision") != enrollment["target_revision"]:
            blockers.append("beta.target_revision.mixed")
    blockers = sorted(set(blockers))
    if any(code in blockers for code in {
        "beta.operator.rollback",
        "beta.runtime_revision.drift",
        "beta.owner_canary.authority_closed",
        "beta.rehearsal.authority_open",
        "beta.operation.drift",
        "beta.installation.drift",
        "beta.authority_generation.drift",
        "beta.owner_canary.consequence_eligible_work",
        "beta.owner_canary.quiet_baseline_missing",
        "beta.owner_canary.quiet_baseline_drift",
    }):
        decision = "ROLLBACK_REQUIRED"
    elif blockers:
        decision = "HOLD"
    else:
        decision = (
            "OWNER_CANARY_READY"
            if enrollment["ring"] == "rehearsal"
            else "BOOTSTRAP_RUNTIME_ADOPTION_READY"
        )
    decided_at = _now(now)
    digest = hashlib.sha256(_canonical({
        "beta_id": enrollment["beta_id"],
        "ring": enrollment["ring"],
        "target_revision": enrollment["target_revision"],
        "decision": decision,
        "blockers": blockers,
        "observation_count": len(observations),
        "span_hours": round(span_hours, 6),
        "ledger_head_sha256": events[-1]["event_sha256"],
    })).hexdigest()
    return {
        "schema_version": SCHEMA_VERSION,
        "beta_id": enrollment["beta_id"],
        "ring": enrollment["ring"],
        "target_revision": enrollment["target_revision"],
        "decision": decision,
        "blockers": blockers,
        "observation_count": len(observations),
        "required_observation_count": required_observations,
        "observation_window_hours": round(span_hours, 6),
        "required_observation_window_hours": required_hours,
        "decided_at": decided_at,
        "ledger_head_sha256": events[-1]["event_sha256"],
        "review_sha256": digest,
        "automatic_external_mutation": False,
    }


CORE_RUNTIME_ADOPTION_COMPONENTS = (
    "src/ocpf_post/setup_contracts.py",
    "src/ocpf_post/automation_authority.py",
    "src/ocpf_post/provider_readiness.py",
    "src/ocpf_post/transfer_bundle.py",
    "src/ocpf_post/setup_migration.py",
    "src/ocpf_post/setup_recovery.py",
    "src/ocpf_post/setup_activation.py",
)
INCUBATE_LONGER = (
    "src/ocpf_post/setup_cli.py",
    "src/ocpf_post/setup_browser.py",
    "src/ocpf_post/setup_admission.py",
    "scripts/install-user-scheduler-timer",
    "scripts/install-user-portfolio-timer",
)


def adoption_review(
    workspace: str | Path | None,
    *,
    beta_id: str | None = None,
    minimum_observations: int | None = None,
    minimum_hours: int | None = None,
) -> dict[str, Any]:
    decision = decide(
        workspace,
        beta_id=beta_id,
        minimum_observations=minimum_observations,
        minimum_hours=minimum_hours,
    )
    ready = (
        decision["ring"] == "owner-canary"
        and decision["decision"] == "BOOTSTRAP_RUNTIME_ADOPTION_READY"
    )
    return {
        "schema_version": SCHEMA_VERSION,
        "status": "ADOPTION_REVIEW_READY" if ready else "BLOCKED",
        "beta_decision": decision,
        "runtime_adoption_components": list(CORE_RUNTIME_ADOPTION_COMPONENTS),
        "incubate_longer": list(INCUBATE_LONGER),
        "runtime_lineage_repository": "AyobamiH/post-once-bootstrap",
        "original_post_once_repository": "AyobamiH/post-once",
        "original_post_once_mutation_allowed": False,
        "automatic_external_mutation": False,
        "required_human_gate": (
            "Explicit owner acceptance is required before this project adopts the candidate "
            "into its own Post-Once-derived runtime lineage."
        ),
        "boundary": (
            "This package evaluates adoption into the bootstrap project's own runtime lineage. "
            "It never creates a branch, commit, PR, release or runtime mutation in AyobamiH/post-once "
            "and never changes the operator's existing local Post-Once installation."
        ),
    }
