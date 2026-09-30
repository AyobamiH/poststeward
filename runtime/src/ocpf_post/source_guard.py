"""Recheck accepted generated copy against source observations at consequence."""
from datetime import datetime, timezone
from functools import lru_cache

from ocpf_post import source_observations


@lru_cache(maxsize=2)
def _snapshot(path, inode, size, modified):
    # Atomic replacement changes the cache key. Callers never mutate this view.
    return source_observations.load()["projects"]


def guard(manifest, *, now=None):
    if manifest.get("runtime_generated") is not True:
        return None
    source = manifest.get("source") or {}
    if source.get("type") not in {"repository_product_truth", "repository_change_event", "evidence_grounded_generation"}:
        return None
    now = now or datetime.now(timezone.utc)
    try:
        allocation = manifest.get("allocation") or {}
        expires = allocation.get("expires_at")
        if expires and datetime.fromisoformat(expires.replace("Z", "+00:00")) <= now:
            return "Generated campaign freshness expired before publication"
        if allocation.get("superseded_by"):
            return "Generated campaign source was superseded"
        path = source_observations.path()
        if not path.exists():
            return "Generated source observation is unavailable" if manifest.get("payload_frozen") else None
        info = path.stat()
        row = _snapshot(str(path), info.st_ino, info.st_size, info.st_mtime_ns).get(manifest.get("project"), {})
        if manifest.get("payload_frozen") and not row:
            return "Generated source observation is unavailable"
        if source.get("type") in {"repository_product_truth", "evidence_grounded_generation"} and row:
            if row.get("repository") != source.get("repository") or row.get("source_ok") is False:
                return "Generated source identity or README guard changed"
            if row.get("readme_sha") and row["readme_sha"] != source.get("source_sha"):
                return "Generated campaign README revision was superseded"
    except (OSError, ValueError, TypeError, KeyError):
        return "Generated source observation could not be verified"
    return None
