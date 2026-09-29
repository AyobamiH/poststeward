#!/usr/bin/env python3
"""Milestone K acceptance for progressive beta evidence and runtime adoption review.

This is deliberately synthetic acceptance. It proves the ring/evidence machinery
without claiming that CI is a real owner-canary field beta.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import subprocess
import tempfile
from unittest.mock import patch

from ocpf_post import automation_authority, setup_beta
from ocpf_post.setup_engine import SetupEngine

UTC = timezone.utc


def make_runtime(root: Path) -> tuple[Path, str]:
    repo = root / "runtime"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.name", "K Fixture"], check=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.email", "k@example.invalid"], check=True)
    subprocess.run(["git", "-C", str(repo), "config", "commit.gpgsign", "false"], check=True)
    (repo / "fixture.txt").write_text("candidate\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(repo), "add", "."], check=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-qm", "candidate"], check=True)
    revision = subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    return repo, revision


def ready_host(*_args, **_kwargs) -> dict:
    return {
        "schema_version": 1,
        "status": "READY",
        "profile": "production",
        "workspace": "/synthetic-k",
        "checks": [],
        "blockers": [],
        "publishing_authority": False,
        "provider_consequence_attempted": False,
    }


def active_fixture(
    workspace: Path,
    state: Path,
    revision: str,
) -> SetupEngine:
    engine = SetupEngine(workspace)
    value = engine.start(
        "fresh",
        operator_label="K Owner",
        timezone="UTC",
        pace="occasional",
    )
    session = value["session"]
    session = engine.store.transition(
        session["session_id"],
        expected_revision=session["revision"],
        next_stage="verification_ready",
        event_payload={"fixture": "k-owner-canary"},
    )
    committed = engine.store.commit_activation_authority(
        session["session_id"],
        expected_revision=session["revision"],
        target_generation=1,
        event_payload={"fixture": "k-owner-canary"},
    )
    automation_authority.activate(
        root=state,
        operation_id=committed["operation"]["operation_id"],
        installation_id=committed["installation"]["installation_id"],
        authority_generation=1,
        review_sha256="a" * 64,
        runtime_revision=revision,
    )
    return engine


def main() -> None:
    with tempfile.TemporaryDirectory(prefix="post-once-k-beta-") as temp:
        root = Path(temp)
        runtime, revision = make_runtime(root)
        start = datetime(2026, 9, 27, 9, 0, tzinfo=UTC)

        # Ring 0: real setup semantics, no publishing authority.
        rehearsal_workspace = root / "rehearsal-bootstrap"
        rehearsal_state = root / "rehearsal-state"
        rehearsal_config = root / "rehearsal-config"
        rehearsal_engine = SetupEngine(rehearsal_workspace)
        rehearsal_engine.start(
            "explore",
            operator_label=None,
            timezone="UTC",
            pace="occasional",
        )
        rehearsal = setup_beta.enroll(
            workspace=rehearsal_workspace,
            ring="rehearsal",
            target_revision=revision,
            state_root=rehearsal_state,
            config_root=rehearsal_config,
            runtime_root=runtime,
            now=start,
        )
        for offset in (0, 1):
            setup_beta.observe(
                workspace=rehearsal_workspace,
                beta_id=rehearsal["beta_id"],
                state_root=rehearsal_state,
                config_root=rehearsal_config,
                runtime_root=runtime,
                operator_outcome="healthy",
                now=start + timedelta(hours=offset),
            )
        rehearsal_decision = setup_beta.decide(
            rehearsal_workspace,
            beta_id=rehearsal["beta_id"],
            now=start + timedelta(hours=1),
        )
        rehearsal_review = setup_beta.adoption_review(
            rehearsal_workspace,
            beta_id=rehearsal["beta_id"],
        )

        # Ring 1: synthetic owner-canary state. This proves gates/ledger only.
        owner_workspace = root / "owner-bootstrap"
        owner_state = root / "owner-state"
        owner_config = root / "owner-config"
        owner_engine = active_fixture(owner_workspace, owner_state, revision)
        owner_before = owner_engine.status()
        owner_marker_before = automation_authority.read(owner_state)
        with patch("ocpf_post.setup_engine.prove_host_capabilities", side_effect=ready_host):
            owner_preflight = setup_beta.preflight(
                workspace=owner_workspace,
                ring="owner-canary",
                target_revision=revision,
                state_root=owner_state,
                config_root=owner_config,
                runtime_root=runtime,
            )
            if owner_preflight["status"] != "READY" or owner_preflight["beta_evidence_written"]:
                raise RuntimeError(f"synthetic owner-canary preflight failed: {owner_preflight}")
            owner = setup_beta.enroll(
                workspace=owner_workspace,
                ring="owner-canary",
                target_revision=revision,
                state_root=owner_state,
                config_root=owner_config,
                runtime_root=runtime,
                now=start,
            )
            owner_observations = []
            for offset in (0, 12, 24):
                observed = setup_beta.observe(
                    workspace=owner_workspace,
                    beta_id=owner["beta_id"],
                    state_root=owner_state,
                    config_root=owner_config,
                    runtime_root=runtime,
                    operator_outcome="healthy",
                    now=start + timedelta(hours=offset),
                )
                if observed["status"] != "HEALTHY":
                    raise RuntimeError(f"synthetic owner-canary observation failed: {observed}")
                owner_observations.append(observed)
        owner_decision = setup_beta.decide(
            owner_workspace,
            beta_id=owner["beta_id"],
            now=start + timedelta(hours=24),
        )
        owner_review = setup_beta.adoption_review(
            owner_workspace,
            beta_id=owner["beta_id"],
        )
        owner_after = owner_engine.status()
        owner_marker_after = automation_authority.read(owner_state)

        result = {
            "schema_version": 1,
            "status": "pass",
            "rehearsal_decision": rehearsal_decision["decision"],
            "rehearsal_adoption_review_status": rehearsal_review["status"],
            "rehearsal_authority_inactive": automation_authority.read(rehearsal_state)["status"] == "inactive",
            "owner_canary_decision": owner_decision["decision"],
            "owner_canary_adoption_review_status": owner_review["status"],
            "owner_canary_preflight_ready": owner_preflight["status"] == "READY",
            "owner_canary_preflight_wrote_evidence": owner_preflight["beta_evidence_written"],
            "owner_canary_consequence_quiet_verified": owner["consequence_quiet_verified"],
            "owner_canary_quiet_evidence_sha256": owner["quiet_evidence_sha256"],
            "owner_canary_quiet_baseline_stable": all(
                row["status"] == "HEALTHY" and not row["blockers"]
                for row in owner_observations
            ),
            "owner_canary_observation_count": owner_decision["observation_count"],
            "owner_canary_window_hours": owner_decision["observation_window_hours"],
            "owner_authority_unchanged": (
                owner_before["operation"]["status"] == "active"
                and owner_before["installation"]["status"] == "active"
                and owner_after["operation"]["status"] == "active"
                and owner_after["installation"]["status"] == "active"
                and owner_marker_before["status"] == "active"
                and owner_marker_after["status"] == "active"
                and owner_marker_before["operation_id"] == owner_marker_after["operation_id"]
                and owner_marker_before["installation_id"] == owner_marker_after["installation_id"]
                and owner_marker_before["authority_generation"] == owner_marker_after["authority_generation"]
                and owner_marker_after["operation_id"] == owner_after["operation"]["operation_id"]
                and owner_marker_after["installation_id"] == owner_after["installation"]["installation_id"]
                and owner_marker_after["authority_generation"] == owner_after["operation"]["authority_generation"]
            ),
            "adoption_package_has_core_components": bool(owner_review["runtime_adoption_components"]),
            "adoption_package_incubates_ui_helpers": "src/ocpf_post/setup_browser.py" in owner_review["incubate_longer"],
            "automatic_external_mutation": owner_review["automatic_external_mutation"],
            "field_beta_evidence_satisfied": False,
            "provider_consequence_attempted": False,
            "production_post_once_mutated": False,
        }
        print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()
