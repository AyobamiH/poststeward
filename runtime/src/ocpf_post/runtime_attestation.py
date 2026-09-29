"""Read-only runtime attestation for the local post-once checkout.

Attestation is descriptive evidence only. It never authorises a schedule, refreshes
provider credentials or calls a social provider.
"""
from __future__ import annotations

import hashlib
import json
import os
import socket
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ocpf_post import __version__

UTC = timezone.utc
RUNTIME_STATE_VERSION = "post-once-state-v1"


def _run(args: list[str], *, cwd: Path | None = None, timeout: int = 5) -> subprocess.CompletedProcess[str] | None:
    try:
        return subprocess.run(args, cwd=cwd, capture_output=True, text=True, timeout=timeout, check=False)
    except (OSError, subprocess.SubprocessError):
        return None


def _git(root: Path, *args: str, timeout: int = 5) -> str | None:
    result = _run(["git", "-C", str(root), *args], timeout=timeout)
    if not result or result.returncode:
        return None
    return result.stdout.strip()


def runtime_root() -> Path:
    override = os.environ.get("OCPF_POST_RUNTIME_ROOT")
    return Path(override).expanduser().resolve() if override else Path(__file__).resolve().parents[2]


def _canonical_digest(value: Any) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
                         allow_nan=False).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def policy_digest() -> tuple[str | None, str]:
    """Hash policy/source authority without returning their contents."""
    try:
        from ocpf_post.portfolio import load_policy
        from ocpf_post.admission import load_policy as load_admission_policy
        from ocpf_post.portfolio_source_loader import merged_source_profiles
        value = {
            "portfolio": load_policy(effective=False),
            "admission": load_admission_policy(),
            "source_profiles": merged_source_profiles(),
        }
        return _canonical_digest(value), "observed"
    except (OSError, ValueError, KeyError, TypeError):
        return None, "unavailable"


def _instance_identity() -> str:
    # No raw hostname, username, home path or machine id leaves the process.
    material = f"post-once-runtime-v1\0{socket.gethostname()}\0{os.name}".encode("utf-8")
    return hashlib.sha256(material).hexdigest()[:20]


def _source_root() -> Path | None:
    value = os.environ.get("OCPF_POST_SOURCE_ROOT")
    if value:
        return Path(value).expanduser()
    root = runtime_root()
    remote = _git(root, "remote", "get-url", "origin")
    return root if remote else None


def _repository_revision(source: Path | None) -> tuple[str | None, str]:
    if source is None or not source.exists():
        return None, "source_unavailable"
    remote = _git(source, "ls-remote", "origin", "refs/heads/main", timeout=5)
    if remote:
        first = remote.splitlines()[0].split()[0] if remote.splitlines() else ""
        if len(first) == 40:
            return first.lower(), "remote_observed"
    local = _git(source, "rev-parse", "refs/remotes/origin/main")
    if local and len(local) == 40:
        return local.lower(), "local_tracking_ref"
    return None, "repository_unavailable"


def _repository_has_newer_revision(root: Path, running: str | None, repository_sha: str | None) -> bool | None:
    if not running or not repository_sha:
        return None
    if running == repository_sha:
        return False
    relation = _run(["git", "-C", str(root), "merge-base", "--is-ancestor", running, repository_sha], timeout=5)
    if relation and relation.returncode == 0:
        return True
    reverse = _run(["git", "-C", str(root), "merge-base", "--is-ancestor", repository_sha, running], timeout=5)
    if reverse and reverse.returncode == 0:
        return False
    return None


def _state_schemas() -> dict[str, Any]:
    from ocpf_post.state import state_dir
    result: dict[str, Any] = {
        "runtime": RUNTIME_STATE_VERSION,
        "schedule_events": 1,
        "publish_receipts": 1,
        "queue_watch": None,
        "admission": None,
    }
    for key, name in (("queue_watch", "queue-watch.json"), ("admission", "admission-state.json")):
        path = state_dir() / name
        if not path.exists():
            continue
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
            result[key] = value.get("schema_version") if isinstance(value, dict) else "invalid"
        except (OSError, json.JSONDecodeError):
            result[key] = "unreadable"
    return result


def attest(*, now: datetime | None = None) -> dict[str, Any]:
    now = (now or datetime.now(UTC)).astimezone(UTC)
    root = runtime_root()
    running = _git(root, "rev-parse", "HEAD")
    status = _git(root, "status", "--porcelain=v1", "--untracked-files=all")
    clean = None if status is None else not bool(status)
    source = _source_root()
    repository_sha, repository_observation = _repository_revision(source)
    digest, digest_status = policy_digest()
    try:
        from ocpf_post.portfolio_queue import ENGINE
        engine = ENGINE
    except (ImportError, AttributeError):
        engine = "unknown"
    local_matches = None if not running or not repository_sha else running == repository_sha
    newer = _repository_has_newer_revision(root, running, repository_sha)
    return {
        "schema_version": 1,
        "consequence": "READ_ONLY",
        "observed_at": now.replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "instance": _instance_identity(),
        "git_commit_sha": running,
        "cli_version": __version__,
        "allocator_engine": engine,
        "policy_config_sha256": digest,
        "policy_digest_status": digest_status,
        "runtime_state_versions": _state_schemas(),
        "checkout_clean": clean,
        "repository_main_sha": repository_sha,
        "repository_observation": repository_observation,
        "local_matches_repository_main": local_matches,
        "repository_has_newer_revision": newer,
        "publishing_authority": False,
        "boundary": "Diagnostic attestation only. The local checkout is the runtime; this command does not update Git, refresh credentials, mutate schedules, call providers or grant publication authority.",
    }
