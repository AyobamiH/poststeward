from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from ocpf_post.campaigns import runtime_campaign_root
from ocpf_post.replenisher import source_state_file
from ocpf_post.state import read_json

UTC = timezone.utc


def _iso_now() -> str:
    return datetime.now(tz=UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def reconcile_runtime_sources() -> list[dict[str, Any]]:
    state = read_json(source_state_file())
    repositories = state.get("repositories") if isinstance(state.get("repositories"), dict) else {}
    from ocpf_post.source_observations import repository_observations
    repositories = repository_observations(repositories)
    root = runtime_campaign_root()
    if not root.exists():
        return []
    changed: list[dict[str, Any]] = []
    for campaign_dir in sorted(root.iterdir()):
        if not campaign_dir.is_dir():
            continue
        path = campaign_dir / "manifest.json"
        try:
            manifest = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(manifest, dict):
            continue
        source = manifest.get("source") if isinstance(manifest.get("source"), dict) else {}
        allocation = manifest.get("allocation") if isinstance(manifest.get("allocation"), dict) else {}
        if source.get("type") not in {"repository_product_truth", "evidence_grounded_generation"} or allocation.get("enabled") is not True:
            continue
        repository = str(source.get("repository") or "")
        observed_sha = str(source.get("source_sha") or "")
        repo_state = repositories.get(repository) if isinstance(repositories.get(repository), dict) else {}
        current_sha = str(repo_state.get("readme_sha") or "")
        if not repository or not observed_sha or not current_sha or observed_sha == current_sha:
            continue
        manifest = dict(manifest)
        manifest["allocation"] = dict(allocation)
        manifest["allocation"]["enabled"] = False
        manifest["allocation"]["superseded_by"] = f"README:{current_sha}"
        manifest["allocation"]["superseded_at"] = _iso_now()
        from ocpf_post.local_store import write
        write(path, manifest)
        try:
            path.chmod(0o600)
        except OSError:
            pass
        changed.append({
            "campaign": manifest.get("campaign") or campaign_dir.name,
            "repository": repository,
            "previous_readme_sha": observed_sha,
            "current_readme_sha": current_sha,
        })
    return changed
