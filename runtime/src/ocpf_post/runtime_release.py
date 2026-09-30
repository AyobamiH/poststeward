"""Versioned software-release switching without rolling durable evidence backwards."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
from typing import Any

from ocpf_post import local_store
from ocpf_post.runtime_attestation import runtime_root
from ocpf_post.state import state_dir
from ocpf_post.state_registry import SCHEMA_REGISTRY_VERSION, verify as verify_state

SHA_RE = re.compile(r"[0-9a-f]{40}")
RELEASE_SCHEMA = 1


def metadata_path() -> Path:
    return state_dir() / "runtime-release.json"


def releases_root() -> Path:
    override = os.environ.get("OCPF_POST_RELEASES_DIR")
    if override:
        return Path(override).expanduser().resolve()
    base = Path(os.environ.get("XDG_DATA_HOME") or (Path.home() / ".local" / "share"))
    return (base / "oneclickpostfactory" / "post-once" / "releases").resolve()


def _run(args: list[str], *, cwd: Path | None = None, timeout: int = 60) -> subprocess.CompletedProcess[str]:
    return subprocess.run(args, cwd=cwd, text=True, capture_output=True, timeout=timeout, check=False)


def _git(root: Path, *args: str, timeout: int = 30) -> str:
    result = _run(["git", "-C", str(root), *args], timeout=timeout)
    if result.returncode:
        raise ValueError("Git operation failed; output withheld")
    return result.stdout.strip()


def _resolve(root: Path, revision: str) -> str:
    value = _git(root, "rev-parse", "--verify", revision + "^{commit}")
    if not SHA_RE.fullmatch(value):
        raise ValueError("Revision did not resolve to one exact commit")
    return value


def _clean(root: Path) -> bool:
    installed = str(os.environ.get("POSTSTEWARD_RUNTIME_RELEASE_SHA") or "").strip().lower()
    if SHA_RE.fullmatch(installed):
        return True
    top = Path(_git(root, "rev-parse", "--show-toplevel")).resolve()
    relative = root.resolve().relative_to(top)
    return not bool(_git(top, "status", "--porcelain=v1", "--untracked-files=all", "--", str(relative)))


def _compatibility(root: Path, revision: str) -> int:
    result = _run(["git", "-C", str(root), "show", f"{revision}:src/ocpf_post/__init__.py"])
    if result.returncode:
        raise ValueError("Target revision has no readable Post-Once package identity")
    match = re.search(r"RUNTIME_STATE_COMPATIBILITY\s*=\s*([0-9]+)", result.stdout)
    # Pre-0.26 Post-Once state is the original v1 generation.
    return int(match.group(1)) if match else 1


def _state_digest() -> str:
    value = verify_state()
    files = [
        (row.get("path"), row.get("sha256"))
        for row in value.get("files", [])
        if row.get("scope") == "state"
    ]
    return hashlib.sha256(json.dumps(files, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _review(current: str, target: str, state_sha: str, compatibility: int) -> str:
    return hashlib.sha256(
        json.dumps(
            {
                "current_revision": current,
                "target_revision": target,
                "state_sha256": state_sha,
                "state_compatibility": compatibility,
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
    ).hexdigest()


def _active_revision(root: Path) -> str:
    saved = local_store.read(metadata_path())
    recorded = saved.get("current_revision")
    if saved.get("status") in {"switched", "rollback_restored"} and isinstance(recorded, str) and SHA_RE.fullmatch(recorded):
        return recorded
    return _resolve(root, "HEAD")


def status() -> dict[str, Any]:
    root = runtime_root()
    checkout = _resolve(root, "HEAD")
    active = _active_revision(root)
    saved = local_store.read(metadata_path())
    return {
        "schema_version": RELEASE_SCHEMA,
        "control_checkout_revision": checkout,
        "active_runtime_revision": active,
        "release_state": saved,
        "releases_root": str(releases_root()),
        "state_registry_version": SCHEMA_REGISTRY_VERSION,
        "boundary": "Software-release metadata only. Durable publication/schedule evidence is never rolled backwards.",
    }


def preview(revision: str) -> dict[str, Any]:
    root = runtime_root()
    if not _clean(root):
        raise ValueError("Tracked/untracked checkout changes must be resolved before release switching")
    state = verify_state()
    if state.get("status") == "attention":
        return {
            "schema_version": RELEASE_SCHEMA,
            "status": "blocked",
            "reason": "state_integrity_required_before_release_switch",
            "applied": False,
        }
    current = _active_revision(root)
    target = _resolve(root, revision)
    compatibility = _compatibility(root, target)
    if compatibility != SCHEMA_REGISTRY_VERSION:
        return {
            "schema_version": RELEASE_SCHEMA,
            "status": "blocked",
            "reason": "state_generation_incompatible",
            "current_state_generation": SCHEMA_REGISTRY_VERSION,
            "target_state_generation": compatibility,
            "target_revision": target,
            "applied": False,
        }
    state_sha = _state_digest()
    return {
        "schema_version": RELEASE_SCHEMA,
        "status": "preview",
        "applied": False,
        "current_revision": current,
        "target_revision": target,
        "state_sha256": state_sha,
        "state_compatibility": compatibility,
        "review_sha256": _review(current, target, state_sha, compatibility),
        "release_directory": str(releases_root() / target),
        "boundary": (
            "Switches software/user-unit paths only. State, receipts, schedules and ambiguous effects remain in place. "
            "No Git fetch, provider call or state rollback occurs."
        ),
    }


RUNTIME_UNIT_ROUTES = {
    "poststeward-run-due.service": "run-due",
    "poststeward-portfolio-refill.service": "refill",
    "poststeward-collection.service": "collect",
    "poststeward-replies.service": "respond",
}
RUNTIME_UNIT_FILES = tuple(RUNTIME_UNIT_ROUTES)


def _prove_candidate_runtime(release: Path, target: str) -> dict[str, Any]:
    if _resolve(release, "HEAD") != target or not _clean(release):
        raise ValueError("Candidate release identity changed before proof")
    cli = release / "poststeward"
    if not cli.is_file() or cli.is_symlink():
        raise ValueError("Target release has no plain PostSteward launcher")
    version = _run(["sh", str(cli), "--version"], cwd=release, timeout=30)
    if version.returncode:
        raise ValueError("Target release CLI did not execute")
    help_result = _run(["sh", str(cli), "help", "--json"], cwd=release, timeout=60)
    if help_result.returncode:
        raise ValueError("Target release CLI discovery failed")
    try:
        discovered = json.loads(help_result.stdout)
    except json.JSONDecodeError as exc:
        raise ValueError("Target release CLI discovery returned invalid JSON") from exc
    if not isinstance(discovered, dict) or discovered.get("schema_version") != 1 or not discovered.get("commands"):
        raise ValueError("Target release CLI discovery contract is incomplete")
    return {
        "target_revision": target,
        "version_output": version.stdout.strip()[:200],
        "help_schema_version": discovered["schema_version"],
        "command_count": len(discovered["commands"]),
    }


def _runtime_directory_for_revision(control_root: Path, revision: str) -> Path:
    if _resolve(control_root, "HEAD") == revision and _clean(control_root):
        return control_root
    candidate = releases_root() / revision
    if candidate.is_dir() and _resolve(candidate, "HEAD") == revision and _clean(candidate):
        return candidate
    raise ValueError("Previous working runtime is not locally available for rollback")


def _unit_directory() -> Path:
    config_home = Path(os.environ.get("XDG_CONFIG_HOME") or (Path.home() / ".config"))
    return config_home / "systemd" / "user"


def _prove_persisted_runtime_paths(release: Path, *, console_expected: bool) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    for name, route in RUNTIME_UNIT_ROUTES.items():
        unit = _unit_directory() / name
        if not unit.is_file() or unit.is_symlink():
            raise ValueError(f"Runtime unit is missing after reconciliation: {name}")
        text = unit.read_text(encoding="utf-8")
        working = f"WorkingDirectory={release}"
        expected_exec = f"ExecStart=/bin/sh {release}/scripts/run-unattended {route}"
        if working not in text or expected_exec not in text:
            raise ValueError(f"Runtime unit does not read back the promoted release route: {name}")
        rows.append({"unit": name, "route": route, "release_path_verified": True})
    if console_expected:
        unit = _unit_directory() / "poststeward-console.service"
        if not unit.is_file() or unit.is_symlink():
            raise ValueError("Installed console unit disappeared during release switch")
        text = unit.read_text(encoding="utf-8")
        if (
            f"WorkingDirectory={release}" not in text
            or f"ExecStart=/bin/sh {release}/post-once console --port " not in text
        ):
            raise ValueError("Console unit does not read back the promoted release route")
        rows.append({"unit": "poststeward-console.service", "release_path_verified": True})
    return {"units": rows, "release_directory": str(release)}


def _reconcile_runtime(release: Path, *, console_loaded: bool) -> dict[str, Any]:
    script = release / "scripts" / "restore-local-runtime.py"
    if not script.exists():
        raise ValueError("Release has no safe runtime reconciliation script")
    restored = _run([sys.executable, str(script)], cwd=release, timeout=900)
    if restored.returncode:
        raise ValueError("Release could not safely reconcile runtime units")
    if console_loaded:
        console = release / "scripts" / "install-user-console"
        if not console.exists():
            raise ValueError("Release cannot preserve the installed console")
        console_result = _run(["sh", str(console)], cwd=release, timeout=120)
        if console_result.returncode:
            raise ValueError("Release could not preserve the installed console")
    return _prove_persisted_runtime_paths(release, console_expected=console_loaded)


def _ensure_worktree(root: Path, target: str) -> Path:
    destination = releases_root() / target
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        observed = _resolve(destination, "HEAD")
        if observed != target or not _clean(destination):
            raise ValueError("Existing release worktree does not match the reviewed revision")
        return destination
    result = _run(["git", "-C", str(root), "worktree", "add", "--detach", str(destination), target], timeout=120)
    if result.returncode:
        raise ValueError("Could not create reviewed release worktree")
    if _resolve(destination, "HEAD") != target:
        raise ValueError("Created release worktree revision mismatch")
    return destination


def switch(revision: str, *, apply: bool = False, expected_sha256: str | None = None) -> dict[str, Any]:
    review = preview(revision)
    if review.get("status") == "blocked" or not apply:
        return review
    if expected_sha256 != review["review_sha256"]:
        raise ValueError("Release switch review changed")
    root = runtime_root()
    target = str(review["target_revision"])
    release = _ensure_worktree(root, target)

    # PREPARE -> PROVE. No user units are touched until the exact candidate
    # compiles and its own CLI executes/discovers successfully.
    compiled = _run([sys.executable, "-m", "compileall", "-q", "src"], cwd=release, timeout=120)
    if compiled.returncode:
        raise ValueError("Target release failed Python compilation")
    candidate_proof = _prove_candidate_runtime(release, target)

    previous = str(review["current_revision"])
    previous_runtime = _runtime_directory_for_revision(root, previous)
    _prove_candidate_runtime(previous_runtime, previous)

    console_probe = _run([
        "systemctl", "--user", "show", "poststeward-console.service",
        "--property=LoadState", "--value",
    ])
    console_loaded = console_probe.returncode == 0 and console_probe.stdout.strip() == "loaded"

    pending = {
        "schema_version": RELEASE_SCHEMA,
        "status": "switching",
        "current_revision": previous,
        "target_revision": target,
        "previous_revision": previous,
        "previous_release_directory": str(previous_runtime),
        "state_compatibility": SCHEMA_REGISTRY_VERSION,
        "release_directory": str(release),
        "review_sha256": review["review_sha256"],
        "candidate_proof": candidate_proof,
    }
    local_store.write(metadata_path(), pending)

    try:
        readback = _reconcile_runtime(release, console_loaded=console_loaded)
        post_proof = _prove_candidate_runtime(release, target)
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        rollback_error: str | None = None
        try:
            rollback_readback = _reconcile_runtime(previous_runtime, console_loaded=console_loaded)
        except (OSError, ValueError, subprocess.SubprocessError) as rollback_exc:
            rollback_error = type(rollback_exc).__name__
            local_store.write(metadata_path(), {
                **pending,
                "status": "rollback_failed",
                "last_known_revision": previous,
                "failure_type": type(exc).__name__,
                "rollback_failure_type": rollback_error,
            })
            raise ValueError(
                "Target release failed and the previous runtime could not be restored; "
                "runtime repair is required before further release changes"
            ) from exc
        local_store.write(metadata_path(), {
            "schema_version": RELEASE_SCHEMA,
            "status": "rollback_restored",
            "current_revision": previous,
            "failed_target_revision": target,
            "previous_revision": previous,
            "release_directory": str(previous_runtime),
            "failed_release_directory": str(release),
            "state_compatibility": SCHEMA_REGISTRY_VERSION,
            "review_sha256": review["review_sha256"],
            "failure_type": type(exc).__name__,
            "rollback_readback": rollback_readback,
        })
        raise ValueError("Target release failed; previous working runtime was restored") from exc

    value = {
        "schema_version": RELEASE_SCHEMA,
        "status": "switched",
        "current_revision": target,
        "previous_revision": previous,
        "state_compatibility": SCHEMA_REGISTRY_VERSION,
        "release_directory": str(release),
        "review_sha256": review["review_sha256"],
        "console_preserved": console_loaded,
        "candidate_proof": candidate_proof,
        "post_switch_proof": post_proof,
        "runtime_readback": readback,
    }
    local_store.write(metadata_path(), value)
    return {
        **value,
        "applied": True,
        "boundary": (
            "Candidate compiled and executed before promotion; persisted unit paths were read back after "
            "reconciliation. On failure, the previous locally-proven runtime is restored before returning."
        ),
    }


def rollback(*, apply: bool = False, expected_sha256: str | None = None) -> dict[str, Any]:
    saved = local_store.read(metadata_path())
    target = saved.get("previous_revision")
    if not isinstance(target, str) or not SHA_RE.fullmatch(target):
        return {
            "schema_version": RELEASE_SCHEMA,
            "status": "blocked",
            "reason": "no_reviewed_previous_revision",
            "applied": False,
        }
    return switch(target, apply=apply, expected_sha256=expected_sha256)
