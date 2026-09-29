#!/usr/bin/env python3
"""Milestone J adversarial acceptance for Post-Once Setup & Recovery.

Every experiment uses isolated temporary state and either read-only/fake provider
surfaces. Passing means the *durable post-condition* is safe, not merely that an
exception occurred.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from http.client import HTTPConnection
import io
import json
import os
from pathlib import Path
import subprocess
import tarfile
import tempfile
import threading
from typing import Any, Callable
from urllib.parse import urlencode
import uuid
from unittest.mock import patch

from ocpf_post import automation_authority, product_runtime, runtime_release
from ocpf_post.setup_activation import (
    ActivationError,
    _exclusive_activation_lock,
)
from ocpf_post.setup_admission import classify_existing_installation, prove_host_capabilities
from ocpf_post.setup_browser import BIND_HOST, build_server
from ocpf_post.setup_engine import SetupEngine, SetupEngineError
from ocpf_post.setup_store import SetupStore, SetupStoreError

ROOT = Path(__file__).resolve().parents[1]
REVISION = "a" * 40
QUIESCENT = {
    "schema_version": 1,
    "status": "quiescent",
    "units": [],
    "active_writer_units": [],
    "mutation_attempted": False,
    "boundary": "Milestone J adversarial fixture",
}


@dataclass(frozen=True)
class Experiment:
    name: str
    category: str
    invariant: str
    passed: bool
    evidence: dict[str, Any]


def code_of(exc: BaseException) -> str:
    return str(getattr(exc, "code", exc.__class__.__name__))


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(json.dumps(row, separators=(",", ":")) + "\n" for row in rows),
        encoding="utf-8",
    )


def source_tree(root: Path, *, secret: str = "J_SECRET_MUST_NOT_TRANSFER") -> tuple[Path, Path]:
    state = root / "source-state"
    config = root / "source-config"
    state.mkdir(parents=True)
    config.mkdir(parents=True)
    future = (datetime.now(timezone.utc) + timedelta(days=3)).replace(microsecond=0)
    write_jsonl(
        state / "schedule-events.jsonl",
        [{
            "schedule_id": "sch_j_future",
            "event": "scheduled",
            "status": "scheduled",
            "recorded_at": "2026-09-27T09:00:00Z",
            "run_at": future.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "provider": "x",
            "account_id": "123456",
            "campaign": "J-001",
        }],
    )
    write_jsonl(
        state / "publish-receipts.jsonl",
        [{
            "campaign": "J-HISTORICAL",
            "provider": "x",
            "status": "published_verified",
            "recorded_at": "2026-09-26T12:00:00Z",
        }],
    )
    (state / "runtime-campaign.txt").write_text("portable J content\n", encoding="utf-8")
    (config / "portfolio-policy.json").write_text(
        '{"schema_version":1,"daily_target":5}\n',
        encoding="utf-8",
    )
    (config / "account-profiles.json").write_text(
        '{"schema_version":1,"accounts":{"x-main":{"provider":"x","account_id":"123456"}}}\n',
        encoding="utf-8",
    )
    (config / "x-token.json").write_text(
        json.dumps({"access_token": secret}) + "\n",
        encoding="utf-8",
    )
    return state, config


def readiness(*, observed: str = "123456") -> list[dict[str, Any]]:
    return [{
        "provider": "x",
        "expected_identity": "123456",
        "observed_identity": observed,
        "identity_match": "match" if observed == "123456" else "mismatch",
        "ready_for_write_configuration": observed == "123456",
        "blocking_reasons": [] if observed == "123456" else ["identity_mismatch"],
        "recovery_fencing_strength": "assisted",
        "optional_gaps": ["provider.stale_authority.unresolved"],
    }]


class FakeServices:
    def __init__(
        self,
        *,
        fail_preflight: bool = False,
        fail_post_cutover: bool = False,
    ) -> None:
        self.enabled = False
        self.active = False
        self.staged = False
        self.fail_preflight = fail_preflight
        self.fail_post_cutover = fail_post_cutover
        self.inspect_calls = 0

    def inspect(self) -> dict[str, Any]:
        self.inspect_calls += 1
        active = self.active
        if self.fail_post_cutover and self.inspect_calls >= 3 and self.active:
            active = False
        return {
            "schema_version": 1,
            "timers": [{"unit": "fixture.timer", "enabled": self.enabled, "active": active}],
            "all_enabled": self.enabled,
            "all_active": active,
        }

    def stage(self, **_kwargs: Any) -> dict[str, Any]:
        self.staged = True
        return {"status": "staged", "provider_consequence": False}

    def preflight(self, **_kwargs: Any) -> dict[str, Any]:
        if self.fail_preflight:
            raise ActivationError("activation.preflight.failed", "J injected pre-cutover failure")
        return {"status": "passed", "checks": [], "provider_consequence": False}

    def arm(self) -> dict[str, Any]:
        self.enabled = True
        self.active = True
        # Arming itself attests the timers. The injected post-cutover failure is
        # reserved for the explicit inspect performed after the authority marker
        # opens, matching the H acceptance fixture.
        return {
            "schema_version": 1,
            "timers": [{"unit": "fixture.timer", "enabled": True, "active": True}],
            "all_enabled": True,
            "all_active": True,
        }

    def disarm(self) -> dict[str, Any]:
        self.enabled = False
        self.active = False
        return self.inspect()


class BrowserClient:
    def __init__(self, port: int, app: Any) -> None:
        self.port = port
        self.app = app
        self.origin = f"http://{BIND_HOST}:{port}"
        self.cookie = ""

    def request(
        self,
        method: str,
        path: str,
        *,
        values: dict[str, str] | None = None,
        origin: str | None = None,
        cookie: str | None = None,
    ) -> tuple[int, dict[str, str], str]:
        conn = HTTPConnection(BIND_HOST, self.port, timeout=5)
        headers: dict[str, str] = {}
        body = None
        use_cookie = self.cookie if cookie is None else cookie
        if use_cookie:
            headers["Cookie"] = use_cookie
        if values is not None:
            body = urlencode(values).encode("utf-8")
            headers["Content-Type"] = "application/x-www-form-urlencoded"
            headers["Origin"] = origin or self.origin
        conn.request(method, path, body=body, headers=headers)
        response = conn.getresponse()
        text = response.read().decode("utf-8")
        result_headers = {key: value for key, value in response.getheaders()}
        status = response.status
        conn.close()
        return status, result_headers, text

    def bootstrap(self) -> tuple[str, dict[str, str]]:
        token = self.app.bootstrap_token
        if token is None:
            raise RuntimeError("browser bootstrap token missing")
        status, headers, _ = self.request("GET", f"/?session={token}")
        if status != 303:
            raise RuntimeError(f"browser bootstrap failed: {status}")
        self.cookie = headers["Set-Cookie"].split(";", 1)[0]
        return token, headers

    def post(
        self,
        path: str,
        values: dict[str, str],
        *,
        origin: str | None = None,
        csrf: str | None = None,
    ) -> tuple[int, dict[str, str], str]:
        return self.request(
            "POST",
            path,
            values={"csrf_token": csrf if csrf is not None else self.app.csrf_token, **values},
            origin=origin,
        )


class Harness:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.results: list[Experiment] = []

    def record(
        self,
        name: str,
        category: str,
        invariant: str,
        passed: bool,
        **evidence: Any,
    ) -> None:
        self.results.append(
            Experiment(
                name=name,
                category=category,
                invariant=invariant,
                passed=bool(passed),
                evidence=evidence,
            )
        )

    def case(self, name: str) -> Path:
        path = self.root / name
        path.mkdir()
        return path

    def migration_target(self, case: Path) -> tuple[SetupEngine, dict[str, Any], Path]:
        state, config = source_tree(case)
        source = SetupEngine(case / "source-bootstrap")
        source.start("migrate", operator_label="J Example", machine_label="old-host")
        preview = source.export_migration(
            state_root=state,
            config_root=config,
            output=case / "transfer.tar.gz",
            source_post_once_version="0.28.29",
            source_revision=REVISION,
            unit_observation=QUIESCENT,
        )
        sealed = source.export_migration(
            state_root=state,
            config_root=config,
            output=case / "transfer.tar.gz",
            source_post_once_version="0.28.29",
            source_revision=REVISION,
            unit_observation=QUIESCENT,
            apply=True,
            expected_sha256=preview["migration_export"]["review_sha256"],
        )
        target = SetupEngine(case / "target-bootstrap")
        target.restore_migration(
            case / "transfer.tar.gz",
            expected_bundle_sha256=sealed["migration_export"]["bundle_sha256"],
            machine_label="new-host",
        )
        verified = target.verify_migration(readiness())
        return target, verified, case / "transfer.tar.gz"

    def recovery_target(
        self,
        case: Path,
    ) -> tuple[SetupEngine, dict[str, Any], datetime]:
        state, config = source_tree(case)
        source = SetupEngine(case / "source-bootstrap")
        source.start("migrate", operator_label="J Recovery", machine_label="lost-host")
        preview = source.recovery_point(
            state_root=state,
            config_root=config,
            output=case / "recovery.tar.gz",
            source_post_once_version="0.28.29",
            source_revision=REVISION,
            unit_observation=QUIESCENT,
        )
        sealed = source.recovery_point(
            state_root=state,
            config_root=config,
            output=case / "recovery.tar.gz",
            source_post_once_version="0.28.29",
            source_revision=REVISION,
            unit_observation=QUIESCENT,
            apply=True,
            expected_sha256=preview["recovery_point_export"]["review_sha256"],
        )
        inspected = SetupEngine(case / "inspect-bootstrap").inspect_bundle(
            case / "recovery.tar.gz",
            expected_bundle_sha256=sealed["recovery_point_export"]["bundle_sha256"],
            recovery=True,
        )
        captured = datetime.fromisoformat(inspected["sealed_at"].replace("Z", "+00:00"))
        lost = captured + timedelta(minutes=2)
        target = SetupEngine(case / "target-bootstrap")
        target.restore_recovery(
            case / "recovery.tar.gz",
            source_lost_at=lost.strftime("%Y-%m-%dT%H:%M:%SZ"),
            max_data_loss_minutes=5,
            expected_bundle_sha256=sealed["recovery_point_export"]["bundle_sha256"],
            machine_label="replacement-host",
        )
        verified = target.verify_recovery(readiness())
        return target, verified, lost

    @staticmethod
    def recovery_resolution(
        verified: dict[str, Any],
        lost: datetime,
    ) -> dict[str, Any]:
        review = json.loads(
            Path(verified["recovery"]["review"]).read_text(encoding="utf-8")
        )
        methods: dict[str, str] = {}
        for code in review["summary"]["required_blocker_codes"]:
            if code == "authority.source_host.unknown":
                methods[code] = "all_provider_authority_fenced"
            elif code == "authority.recovery_gap.unreconciled":
                methods[code] = "provider_effects_reconciled"
            elif code == "bundle.authenticity.not_provided":
                methods[code] = "trusted_bundle_digest_verified"
            elif code.startswith("provider.stale_authority."):
                methods[code] = "provider_credential_revoked"
            elif code.startswith(
                ("schedule.ambiguous_effect.", "schedule.partial_effect.", "schedule.executing.")
            ):
                methods[code] = "provider_effect_reconciled"
            else:
                raise RuntimeError(f"No J resolution method for {code}")
        observed = (lost + timedelta(minutes=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
        return {
            "schema_version": 1,
            "operation_id": verified["operation"]["operation_id"],
            "source_installation_id": review["source_installation_id"],
            "resolutions": [
                {
                    "code": code,
                    "status": "resolved",
                    "method": methods[code],
                    "observed_at": observed,
                    "evidence_ref": f"adversarial:{code}",
                }
                for code in review["summary"]["required_blocker_codes"]
            ],
        }

    def host_capability_proof_blocks_missing_production_systemd(self) -> None:
        case = self.case("host-capability-proof")
        with patch("ocpf_post.setup_admission.shutil.which", return_value=None):
            value = prove_host_capabilities(case / "bootstrap", production=True)
        self.record(
            "production_host_capability_proof_blocks_missing_systemd",
            "host_admission",
            "Production admission proves the service-manager capability it depends on instead of trusting platform/version strings.",
            value["status"] == "BLOCKED" and "host.systemd_user" in value["blockers"],
            blockers=value["blockers"],
        )

    def completed_install_is_not_reinterpreted_as_fresh(self) -> None:
        case = self.case("existing-install-classification")
        workspace = case / "bootstrap"
        state = case / "state"
        config = case / "config"
        engine = SetupEngine(workspace)
        completed = engine.start(
            "explore",
            operator_label=None,
            timezone="UTC",
            pace="occasional",
        )
        classified = classify_existing_installation(
            workspace,
            state_root=state,
            config_root=config,
        )
        code = ""
        try:
            engine.start(
                "explore",
                operator_label=None,
                timezone="UTC",
                pace="occasional",
            )
        except SetupEngineError as exc:
            code = exc.code
        self.record(
            "completed_install_is_classified_before_guided_restart",
            "guided_bootstrap",
            "Durable completed setup is verification/inspection state, never implicit authority to start a second installation in the same workspace.",
            (
                completed["session"]["stage"] == "explore_ready"
                and classified["classification"] == "explore_only"
                and classified["recommended_action"] == "verify"
                and code == "setup.installation.exists"
            ),
            classification=classified["classification"],
            restart_code=code,
        )

    def invalid_candidate_cli_is_rejected_before_promotion(self) -> None:
        case = self.case("invalid-candidate-cli")
        release = case / "release"
        release.mkdir()
        subprocess.run(["git", "init", str(release)], check=True, capture_output=True, text=True)
        subprocess.run(["git", "-C", str(release), "config", "user.name", "J"], check=True)
        subprocess.run(["git", "-C", str(release), "config", "user.email", "j@example.test"], check=True)
        cli = release / "post-once"
        cli.write_text(
            "#!/bin/sh\n"
            "if [ \"$1\" = \"--version\" ]; then echo 0.0.0; exit 0; fi\n"
            "if [ \"$1\" = \"help\" ]; then echo not-json; exit 0; fi\n"
            "exit 0\n",
            encoding="utf-8",
        )
        cli.chmod(0o755)
        subprocess.run(["git", "-C", str(release), "add", "post-once"], check=True)
        subprocess.run(
            ["git", "-C", str(release), "commit", "-m", "candidate"],
            check=True,
            capture_output=True,
            text=True,
        )
        target = subprocess.run(
            ["git", "-C", str(release), "rev-parse", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        rejected = False
        message = ""
        try:
            runtime_release._prove_candidate_runtime(release, target)
        except ValueError as exc:
            rejected = True
            message = str(exc)
        self.record(
            "invalid_candidate_cli_is_rejected_before_promotion",
            "runtime_promotion",
            "A copied/compiled release is not promotable until its own CLI executes and returns the expected discovery contract.",
            rejected and "invalid JSON" in message,
            error=message,
        )

    def failed_runtime_promotion_restores_previous_runtime(self) -> None:
        case = self.case("runtime-promotion-rollback")
        previous = "a" * 40
        target = "b" * 40
        review = {
            "schema_version": 1,
            "status": "preview",
            "current_revision": previous,
            "target_revision": target,
            "review_sha256": "c" * 64,
        }
        candidate = case / "candidate"
        prior = case / "previous"
        control = case / "control"
        writes: list[dict[str, Any]] = []
        ok = subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")
        console_missing = subprocess.CompletedProcess(args=[], returncode=1, stdout="", stderr="")
        message = ""
        with (
            patch.object(runtime_release, "preview", return_value=review),
            patch.object(runtime_release, "runtime_root", return_value=control),
            patch.object(runtime_release, "_ensure_worktree", return_value=candidate),
            patch.object(runtime_release, "_runtime_directory_for_revision", return_value=prior),
            patch.object(
                runtime_release,
                "_prove_candidate_runtime",
                side_effect=[{"target_revision": target}, {"target_revision": previous}],
            ),
            patch.object(
                runtime_release,
                "_reconcile_runtime",
                side_effect=[
                    ValueError("injected candidate handoff failure"),
                    {"release_directory": str(prior), "units": []},
                ],
            ),
            patch.object(runtime_release, "_run", side_effect=[ok, console_missing]),
            patch.object(
                runtime_release.local_store,
                "write",
                side_effect=lambda _path, value: writes.append(value.copy()),
            ),
        ):
            try:
                runtime_release.switch(
                    target,
                    apply=True,
                    expected_sha256=review["review_sha256"],
                )
            except ValueError as exc:
                message = str(exc)
        self.record(
            "failed_runtime_promotion_restores_previous_proven_runtime",
            "runtime_promotion",
            "A failed candidate handoff restores the previously proven runtime and records the failed promotion instead of leaving ambiguous ownership.",
            (
                len(writes) >= 2
                and writes[0]["status"] == "switching"
                and writes[-1]["status"] == "rollback_restored"
                and writes[-1]["current_revision"] == previous
                and writes[-1]["failed_target_revision"] == target
                and "previous working runtime was restored" in message
            ),
            final_status=writes[-1]["status"] if writes else None,
            error=message,
        )

    def fresh_wrong_provider_identity_never_verifies(self) -> None:
        case = self.case("fresh-wrong-provider-identity")
        config = case / "config"
        state = case / "state"
        workspace = case / "bootstrap"
        env = {
            "POST_ONCE_CONFIG_DIR": str(config),
            "POST_ONCE_STATE_DIR": str(state),
            "POST_ONCE_SETUP_STATE_DIR": str(workspace),
            "POST_ONCE_RELEASES_DIR": str(case / "releases"),
            "POST_ONCE_PRODUCT_LINEAGE": product_runtime.PRODUCT_LINEAGE,
        }
        with patch.dict(os.environ, env, clear=False):
            product_runtime.apply_environment()
            engine = SetupEngine(workspace)
            engine.start(
                "fresh",
                operator_label="J Fresh",
                timezone="UTC",
                pace="occasional",
            )
            config.mkdir(parents=True, exist_ok=True)
            (config / "runtime-projects.json").write_text(
                json.dumps({
                    "schema_version": 1,
                    "projects": {
                        "example": {
                            "schema_version": 1,
                            "project": "example",
                            "label": "Example",
                            "campaign_prefixes": ["EX-"],
                            "accounts": {
                                "primary": {
                                    "provider": "x",
                                    "account_id": "123456789",
                                }
                            },
                            "default_accounts": {"x": "primary"},
                        }
                    },
                }) + "\n",
                encoding="utf-8",
            )
            campaign = state / "runtime-campaigns" / "EX-001"
            campaign.mkdir(parents=True, exist_ok=True)
            (campaign / "manifest.json").write_text(
                json.dumps({
                    "campaign": "EX-001",
                    "project": "example",
                    "title": "Example",
                    "status": "COPY-READY",
                    "providers": ["x"],
                    "destinations": {"x": "primary"},
                    "source": {"type": "owner_approved", "source_id": "j"},
                    "allocation": {"enabled": False},
                    "runtime_imported": True,
                    "payload_sha256": {},
                }) + "\n",
                encoding="utf-8",
            )
            before = engine.status()
            code = ""
            try:
                engine.verify_fresh(
                    [{
                        "schema_version": 1,
                        "provider": "x",
                        "expected_identity": "123456789",
                        "observed_identity": "999999",
                        "identity_match": "mismatch",
                        "write_scope_state": "granted",
                        "ready_for_write_configuration": False,
                        "blocking_reasons": ["provider.identity.mismatch"],
                    }],
                    [{"provider": "x", "account_id": "123456789"}],
                )
            except SetupEngineError as exc:
                code = exc.code
            after = engine.status()
        self.record(
            "fresh_wrong_provider_identity_never_reaches_verification_ready",
            "spoofing",
            "A fresh connected provider with the wrong immutable account identity cannot advance setup or gain authority.",
            (
                code == "setup.fresh.provider_authority_blocked"
                and after["session"]["stage"] == "configuration_ready"
                and after["session"]["revision"] == before["session"]["revision"]
                and after["publishing_authority"] is False
            ),
            code=code,
            stage=after["session"]["stage"],
            revision=after["session"]["revision"],
        )

    def guided_resume_and_rerun(self) -> None:
        case = self.case("guided-resume")
        engine = SetupEngine(case / "bootstrap")
        started = engine.start(
            "fresh",
            operator_label="Guided Example",
            timezone="UTC",
            pace="occasional",
            machine_label="guided-host",
        )
        before = engine.status()
        resumed = engine.resume(session_id=before["session"]["session_id"])
        rerun_code = ""
        try:
            engine.start(
                "fresh",
                operator_label="Replacement",
                timezone="UTC",
                pace="regular",
            )
        except (SetupEngineError, SetupStoreError) as exc:
            rerun_code = code_of(exc)
        after = engine.status()
        app_server, app = build_server(case / "bootstrap", port=0)
        browser_seen = app.status()
        app_server.server_close()
        self.record(
            "guided_resume_preserves_persisted_operation",
            "guided_bootstrap",
            "Interrupted/repeated guided setup resumes the existing durable session and does not replace it.",
            (
                resumed["session"]["session_id"] == before["session"]["session_id"]
                and after["session"]["session_id"] == before["session"]["session_id"]
                and after["operation"]["operation_id"] == before["operation"]["operation_id"]
                and rerun_code == "setup.session.open_exists"
                and browser_seen is not None
                and browser_seen["session"]["session_id"] == before["session"]["session_id"]
                and started["publishing_authority"] is False
            ),
            rerun_code=rerun_code,
            session_id=before["session"]["session_id"],
            revision_before=before["session"]["revision"],
            revision_after=after["session"]["revision"],
        )

    def stale_revision(self) -> None:
        case = self.case("stale-revision")
        store = SetupStore(case / "bootstrap")
        created = store.create_explore()
        session = created["session"]
        advanced = store.transition(
            session["session_id"],
            expected_revision=session["revision"],
            next_stage="preflight_ready",
        )
        code = ""
        try:
            store.transition(
                session["session_id"],
                expected_revision=session["revision"],
                next_stage="preflight_ready",
            )
        except SetupStoreError as exc:
            code = exc.code
        current = store.session(session["session_id"])
        self.record(
            "stale_revision_cannot_overwrite_newer_setup_state",
            "concurrency",
            "Optimistic setup revisions reject stale writers without rewriting the winner.",
            (
                code == "setup.revision.conflict"
                and current is not None
                and current["revision"] == advanced["revision"]
                and current["stage"] == "preflight_ready"
            ),
            code=code,
            observed_revision=current["revision"] if current else None,
        )

    def production_workspace_collision(self) -> None:
        case = self.case("production-collision")
        production = case / "production-state"
        old = os.environ.get("OCPF_POST_STATE_DIR")
        os.environ["OCPF_POST_STATE_DIR"] = str(production)
        code = ""
        try:
            try:
                SetupEngine(production)
            except SetupStoreError as exc:
                code = exc.code
        finally:
            if old is None:
                os.environ.pop("OCPF_POST_STATE_DIR", None)
            else:
                os.environ["OCPF_POST_STATE_DIR"] = old
        self.record(
            "bootstrap_workspace_cannot_overlap_production_state",
            "filesystem",
            "Bootstrap control state never shares the production state root.",
            code == "setup.workspace.production_collision",
            code=code,
        )

    def workspace_symlink(self) -> None:
        case = self.case("workspace-symlink")
        real = case / "real-workspace"
        real.mkdir()
        link = case / "workspace-link"
        link.symlink_to(real, target_is_directory=True)
        code = ""
        rejected = False
        try:
            engine = SetupEngine(link)
            engine.start(
                "explore",
                operator_label=None,
                timezone="UTC",
                pace="occasional",
            )
        except (SetupEngineError, SetupStoreError) as exc:
            rejected = True
            code = code_of(exc)
        self.record(
            "symlinked_bootstrap_workspace_is_rejected",
            "filesystem",
            "A supplied bootstrap workspace may not redirect through a symbolic link.",
            rejected,
            code=code,
            lexical_path=str(link),
            resolved_path=str(link.resolve()),
        )

    def malicious_archive_path(self) -> None:
        case = self.case("archive-traversal")
        bundle = case / "evil.tar.gz"
        with tarfile.open(bundle, "w:gz") as archive:
            payload = b"escape"
            info = tarfile.TarInfo("../escaped.txt")
            info.size = len(payload)
            archive.addfile(info, io.BytesIO(payload))
        code = ""
        rejected = False
        try:
            SetupEngine(case / "bootstrap").inspect_bundle(bundle)
        except (SetupEngineError, ValueError) as exc:
            rejected = True
            code = code_of(exc)
        self.record(
            "archive_path_traversal_is_rejected_before_materialisation",
            "tampering",
            "A transfer archive cannot escape quarantine through ../ paths.",
            rejected and not (case / "escaped.txt").exists(),
            code=code,
            escaped_created=(case / "escaped.txt").exists(),
        )

    def bundle_tamper_and_secret_exclusion(self) -> None:
        case = self.case("bundle-tamper")
        state, config = source_tree(case, secret="J_TAMPER_SECRET")
        source = SetupEngine(case / "source-bootstrap")
        source.start("migrate", operator_label="Tamper Example", machine_label="old")
        preview = source.export_migration(
            state_root=state,
            config_root=config,
            output=case / "transfer.tar.gz",
            source_post_once_version="0.28.29",
            source_revision=REVISION,
            unit_observation=QUIESCENT,
        )
        sealed = source.export_migration(
            state_root=state,
            config_root=config,
            output=case / "transfer.tar.gz",
            source_post_once_version="0.28.29",
            source_revision=REVISION,
            unit_observation=QUIESCENT,
            apply=True,
            expected_sha256=preview["migration_export"]["review_sha256"],
        )
        raw = (case / "transfer.tar.gz").read_bytes()
        tampered = case / "tampered.tar.gz"
        altered = bytearray(raw)
        altered[max(10, len(altered) // 2)] ^= 0x01
        tampered.write_bytes(altered)
        code = ""
        rejected = False
        try:
            SetupEngine(case / "target-bootstrap").restore_migration(
                tampered,
                expected_bundle_sha256=sealed["migration_export"]["bundle_sha256"],
                machine_label="new",
            )
        except (SetupEngineError, ValueError) as exc:
            rejected = True
            code = code_of(exc)
        self.record(
            "tampered_bundle_and_secret_exclusion",
            "tampering",
            "Bundle tampering blocks restore and credential bytes never cross the transfer boundary.",
            rejected and b"J_TAMPER_SECRET" not in raw,
            code=code,
            credential_bytes_absent=b"J_TAMPER_SECRET" not in raw,
        )

    def wrong_provider_identity(self) -> None:
        case = self.case("wrong-provider-identity")
        state, config = source_tree(case)
        source = SetupEngine(case / "source-bootstrap")
        source.start("migrate", operator_label="Identity Example", machine_label="old")
        preview = source.export_migration(
            state_root=state,
            config_root=config,
            output=case / "transfer.tar.gz",
            source_post_once_version="0.28.29",
            source_revision=REVISION,
            unit_observation=QUIESCENT,
        )
        sealed = source.export_migration(
            state_root=state,
            config_root=config,
            output=case / "transfer.tar.gz",
            source_post_once_version="0.28.29",
            source_revision=REVISION,
            unit_observation=QUIESCENT,
            apply=True,
            expected_sha256=preview["migration_export"]["review_sha256"],
        )
        target = SetupEngine(case / "target-bootstrap")
        restored = target.restore_migration(
            case / "transfer.tar.gz",
            expected_bundle_sha256=sealed["migration_export"]["bundle_sha256"],
            machine_label="new",
        )
        code = ""
        blocked = False
        try:
            value = target.verify_migration(readiness(observed="999999"))
            blocked = (
                value["publishing_authority"] is False
                and value["session"]["stage"] != "verification_ready"
            )
        except (SetupEngineError, ValueError) as exc:
            blocked = True
            code = code_of(exc)
        current = target.status()
        self.record(
            "wrong_provider_identity_never_reaches_activation_ready_state",
            "spoofing",
            "A mismatched immutable provider identity cannot grant target authority.",
            (
                blocked
                and current["publishing_authority"] is False
                and current["automation_enabled"] is False
                and current["session"]["stage"] == restored["session"]["stage"]
            ),
            code=code,
            stage=current["session"]["stage"],
        )

    def activation_review_drift(self) -> None:
        case = self.case("activation-review-drift")
        target, _, _ = self.migration_target(case)
        state = case / "target-state"
        config = case / "target-config"
        services = FakeServices()
        preview = target.activation_preview(
            runtime_root=ROOT,
            state_root=state,
            config_root=config,
            service_controller=services,
        )
        config.mkdir(parents=True, exist_ok=True)
        (config / "drift.json").write_text('{"changed":true}\n', encoding="utf-8")
        code = ""
        try:
            target.activate(
                runtime_root=ROOT,
                state_root=state,
                config_root=config,
                expected_sha256=preview["review_sha256"],
                service_controller=services,
            )
        except SetupEngineError as exc:
            code = exc.code
        self.record(
            "stale_activation_review_cannot_apply_after_target_drift",
            "tampering",
            "The exact H review digest is invalidated by target drift before any consequence.",
            (
                code == "activation.review.changed"
                and automation_authority.read(state)["status"] == "inactive"
                and not services.staged
            ),
            code=code,
            staged=services.staged,
        )

    def activation_symlink_root(self) -> None:
        case = self.case("activation-symlink-root")
        target, _, _ = self.migration_target(case)
        real_state = case / "real-target-state"
        real_state.mkdir()
        state_link = case / "target-state-link"
        state_link.symlink_to(real_state, target_is_directory=True)
        config = case / "target-config"
        services = FakeServices()
        code = ""
        rejected = False
        try:
            target.activation_preview(
                runtime_root=ROOT,
                state_root=state_link,
                config_root=config,
                service_controller=services,
            )
        except SetupEngineError as exc:
            rejected = True
            code = exc.code
        self.record(
            "symlinked_activation_root_is_rejected",
            "filesystem",
            "Activation roots must be the exact plain directories the operator reviewed, not symlink aliases.",
            rejected,
            code=code,
            lexical_path=str(state_link),
            resolved_path=str(state_link.resolve()),
        )

    def activation_runtime_symlink(self) -> None:
        case = self.case("activation-runtime-symlink")
        target, _, _ = self.migration_target(case)
        real_runtime = case / "runtime"
        real_runtime.mkdir()
        # A valid-looking checkout identity is not required: the symlink must be
        # rejected before runtime Git inspection.
        runtime_link = case / "runtime-link"
        runtime_link.symlink_to(real_runtime, target_is_directory=True)
        code = ""
        rejected = False
        try:
            target.activation_preview(
                runtime_root=runtime_link,
                state_root=case / "target-state",
                config_root=case / "target-config",
                service_controller=FakeServices(),
            )
        except SetupEngineError as exc:
            rejected = True
            code = exc.code
        self.record(
            "symlinked_activation_runtime_is_rejected_before_checkout_inspection",
            "filesystem",
            "The reviewed runtime root must be the exact plain checkout path, not a symlink alias.",
            rejected and code == "activation.runtime.symlink",
            code=code,
        )

    def activation_concurrency(self) -> None:
        case = self.case("activation-concurrency")
        target, _, _ = self.migration_target(case)
        state = case / "target-state"
        config = case / "target-config"
        services = FakeServices()
        preview = target.activation_preview(
            runtime_root=ROOT,
            state_root=state,
            config_root=config,
            service_controller=services,
        )
        code = ""
        with _exclusive_activation_lock(target.workspace, action="J-holder"):
            try:
                target.activate(
                    runtime_root=ROOT,
                    state_root=state,
                    config_root=config,
                    expected_sha256=preview["review_sha256"],
                    service_controller=services,
                )
            except SetupEngineError as exc:
                code = exc.code
        self.record(
            "concurrent_activation_loser_never_stages",
            "concurrency",
            "Only one local activation/deactivation apply can mutate roots and services.",
            (
                code == "activation.concurrent_operation"
                and not services.staged
                and automation_authority.read(state)["status"] == "inactive"
            ),
            code=code,
        )

    def activation_failure_boundaries(self) -> None:
        pre = self.case("precutover-failure")
        target, _, _ = self.migration_target(pre)
        state = pre / "target-state"
        config = pre / "target-config"
        state.mkdir()
        (state / "local-marker.txt").write_text("before\n", encoding="utf-8")
        services = FakeServices(fail_preflight=True)
        preview = target.activation_preview(
            runtime_root=ROOT,
            state_root=state,
            config_root=config,
            service_controller=services,
        )
        pre_code = ""
        try:
            target.activate(
                runtime_root=ROOT,
                state_root=state,
                config_root=config,
                expected_sha256=preview["review_sha256"],
                service_controller=services,
            )
        except SetupEngineError as exc:
            pre_code = exc.code
        pre_safe = (
            pre_code == "activation.preflight.failed"
            and automation_authority.read(state)["status"] == "inactive"
            and (state / "local-marker.txt").read_text(encoding="utf-8") == "before\n"
            and not (state / "publish-receipts.jsonl").exists()
        )

        post = self.case("postcutover-failure")
        target2, _, _ = self.migration_target(post)
        state2 = post / "target-state"
        config2 = post / "target-config"
        services2 = FakeServices(fail_post_cutover=True)
        preview2 = target2.activation_preview(
            runtime_root=ROOT,
            state_root=state2,
            config_root=config2,
            service_controller=services2,
        )
        post_code = ""
        try:
            target2.activate(
                runtime_root=ROOT,
                state_root=state2,
                config_root=config2,
                expected_sha256=preview2["review_sha256"],
                service_controller=services2,
            )
        except SetupEngineError as exc:
            post_code = exc.code
        current2 = target2.status()
        post_safe = (
            post_code == "activation.post_cutover.attestation_failed"
            and automation_authority.read(state2)["status"] == "inactive"
            and (state2 / "publish-receipts.jsonl").is_file()
            and current2["session"]["stage"] == "active"
        )
        self.record(
            "activation_failure_boundaries_preserve_correct_history",
            "fault_injection",
            "Pre-cutover failures roll local promotion back; post-cutover failures close the gate without rewinding durable history.",
            pre_safe and post_safe,
            pre_code=pre_code,
            post_code=post_code,
            post_stage=current2["session"]["stage"],
        )

    def marker_tamper(self) -> None:
        case = self.case("marker-tamper")
        state = case / "state"
        marker = automation_authority.activate(
            root=state,
            operation_id=str(uuid.uuid4()),
            installation_id=str(uuid.uuid4()),
            authority_generation=1,
            review_sha256="a" * 64,
            runtime_revision="b" * 40,
        )
        path = Path(marker["path"])
        path.chmod(0o644)
        rejected = False
        message = ""
        try:
            automation_authority.read(state)
        except automation_authority.AutomationAuthorityError as exc:
            rejected = True
            message = str(exc)
        self.record(
            "authority_marker_permission_tamper_fails_closed",
            "tampering",
            "A marker that becomes readable by group/other cannot grant unattended authority.",
            rejected,
            error=message,
        )

    def recovery_fencing(self) -> None:
        case = self.case("recovery-fencing")
        target, verified, lost = self.recovery_target(case)
        state = case / "target-state"
        config = case / "target-config"
        services = FakeServices()
        missing_code = ""
        try:
            target.activation_preview(
                runtime_root=ROOT,
                state_root=state,
                config_root=config,
                service_controller=services,
            )
        except SetupEngineError as exc:
            missing_code = exc.code
        replay = self.recovery_resolution(verified, lost)
        replay["operation_id"] = str(uuid.uuid4())
        replay_code = ""
        try:
            target.activation_preview(
                runtime_root=ROOT,
                state_root=state,
                config_root=config,
                recovery_resolution=replay,
                service_controller=services,
            )
        except SetupEngineError as exc:
            replay_code = exc.code
        self.record(
            "dead_host_recovery_requires_bound_external_fencing_evidence",
            "recovery",
            "Missing fencing evidence and evidence replayed from another operation both block activation.",
            (
                missing_code == "activation.recovery_resolution.required"
                and replay_code == "activation.recovery_resolution.identity_mismatch"
                and automation_authority.read(state)["status"] == "inactive"
            ),
            missing_code=missing_code,
            replay_code=replay_code,
        )

    def browser_attacks(self) -> None:
        case = self.case("browser-attacks")
        workspace = case / "bootstrap"
        engine = SetupEngine(workspace)
        engine.start(
            "fresh",
            operator_label="Browser J",
            timezone="UTC",
            pace="occasional",
        )
        before = engine.status()
        server, app = build_server(workspace, port=0)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        client = BrowserClient(server.server_port, app)
        token, _ = client.bootstrap()

        replay = BrowserClient(server.server_port, app)
        replay_status, _, _ = replay.request("GET", f"/?session={token}")

        cross_status, _, _ = client.post(
            "/action/resume",
            {"session_id": before["session"]["session_id"]},
            origin="https://attacker.example",
        )
        csrf_status, _, _ = client.post(
            "/action/resume",
            {"session_id": before["session"]["session_id"]},
            csrf="wrong",
        )
        after = engine.status()
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)
        self.record(
            "browser_replay_cross_origin_and_csrf_attacks_do_not_mutate_setup",
            "browser",
            "Public-web or replayed browser requests cannot mutate the guided setup surface.",
            (
                replay_status == 403
                and cross_status == 403
                and csrf_status == 403
                and after["session"]["revision"] == before["session"]["revision"]
                and after["publishing_authority"] is False
            ),
            replay_status=replay_status,
            cross_origin_status=cross_status,
            csrf_status=csrf_status,
            revision=after["session"]["revision"],
        )

    def run(self) -> dict[str, Any]:
        experiments: list[Callable[[], None]] = [
            self.fresh_wrong_provider_identity_never_verifies,
            self.host_capability_proof_blocks_missing_production_systemd,
            self.completed_install_is_not_reinterpreted_as_fresh,
            self.invalid_candidate_cli_is_rejected_before_promotion,
            self.failed_runtime_promotion_restores_previous_runtime,
            self.guided_resume_and_rerun,
            self.stale_revision,
            self.production_workspace_collision,
            self.workspace_symlink,
            self.malicious_archive_path,
            self.bundle_tamper_and_secret_exclusion,
            self.wrong_provider_identity,
            self.activation_review_drift,
            self.activation_symlink_root,
            self.activation_runtime_symlink,
            self.activation_concurrency,
            self.activation_failure_boundaries,
            self.marker_tamper,
            self.recovery_fencing,
            self.browser_attacks,
        ]
        for experiment in experiments:
            try:
                experiment()
            except Exception as exc:
                self.record(
                    f"harness_error_{experiment.__name__}",
                    "harness",
                    "The adversarial experiment itself must complete deterministically.",
                    False,
                    error=repr(exc),
                )
        failures = [result.name for result in self.results if not result.passed]
        return {
            "schema_version": 1,
            "milestone": "J",
            "status": "pass" if not failures else "fail",
            "experiment_count": len(self.results),
            "pass_count": len(self.results) - len(failures),
            "failure_count": len(failures),
            "failures": failures,
            "provider_consequence_attempted": False,
            "production_post_once_mutated": False,
            "experiments": [asdict(result) for result in self.results],
        }


def main() -> None:
    with tempfile.TemporaryDirectory(prefix="post-once-j-adversarial-") as temp:
        result = Harness(Path(temp)).run()
        print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()
