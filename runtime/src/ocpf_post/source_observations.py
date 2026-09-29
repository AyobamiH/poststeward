"""Bounded source observations, independent of campaign admission.

The observed head is never the campaign consumption cursor. Gaps are sticky:
neither a later poll nor a full publishing queue can silently baseline lost work.
"""
from __future__ import annotations

import hashlib
import json
import time
from datetime import datetime, timezone

from ocpf_post import local_store, replenisher as r
from ocpf_post.state import state_dir, read_json

UTC = timezone.utc
MAX_PENDING = 500
MAX_PAGES = 5


def path():
    return state_dir() / "source-observations.json"


def fingerprint(profile):
    return hashlib.sha256(json.dumps(profile, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def load():
    value = local_store.read(path()) or {"schema_version": 1, "projects": {}}
    if not isinstance(value.get("projects"), dict):
        raise ValueError("Invalid source observation state")
    for row in value["projects"].values():
        if not isinstance(row, dict) or not isinstance(row.get("pending", []), list) or len(row.get("pending", [])) > MAX_PENDING:
            raise ValueError("Invalid bounded source observation")
        for event in row.get("pending", []):
            if not isinstance(event, dict):
                raise ValueError("Invalid pending source event")
            sha, title = event.get("sha"), event.get("title")
            if not isinstance(sha, str) or len(sha) != 40 or any(c not in "0123456789abcdef" for c in sha):
                raise ValueError("Invalid pending revision")
            if not isinstance(title, str) or not title or len(title) > 160:
                raise ValueError("Invalid pending title")
            at = datetime.fromisoformat(str(event.get("observed_at", "")).replace("Z", "+00:00"))
            if at.tzinfo is None or not isinstance(event.get("completed_providers", []), list) or set(event.get("completed_providers", [])) - r.PROVIDERS:
                raise ValueError("Invalid pending event boundary")
    return value


def _history(repository, head, anchor, token):
    """Walk a pinned head; never combine pages from a moving default branch."""
    commits = []
    for page in range(1, MAX_PAGES + 1):
        rows = r._github_json(f"https://api.github.com/repos/{repository}/commits?sha={head}&per_page=100&page={page}", token=token)
        if not isinstance(rows, list) or len(rows) > 100:
            raise ValueError("Invalid bounded commit page")
        for row in rows:
            if row.get("sha") == anchor:
                return commits, True
            commits.append(row)
        if len(rows) < 100:
            break
    return commits, False


def _observe(profile, previous, legacy, now, token):
    for timestamp in (previous.get("observed_at"), previous.get("last_attempt_at")):
        if timestamp and datetime.fromisoformat(timestamp.replace("Z", "+00:00")) > now:
            raise ValueError("Source observation clock regression")
    repository = profile["repository"]
    metadata = r._github_json(f"https://api.github.com/repos/{repository}", token=token)
    if not isinstance(metadata, dict) or type(metadata.get("private")) is not bool:
        raise ValueError("Repository identity or visibility is unknown")
    expected = (profile.get("runtime_source") or {}).get("repository_id") or previous.get("repository_id")
    if (metadata.get("full_name", "").lower() != repository.lower()
            or type(metadata.get("id")) is not int or (expected and metadata["id"] != expected)):
        raise ValueError("Repository identity changed")
    if metadata.get("private") and profile.get("event_enabled") and profile.get("allow_private_events", "runtime_source" not in profile) is not True:
        raise ValueError("Private source events are not authorised")
    heads = r._repo_commits(repository, token=token, limit=1)
    head = str(heads[0]["sha"]) if heads else ""
    if len(head) != 40 or any(c not in "0123456789abcdef" for c in head):
        raise ValueError("Invalid repository head")
    readme, readme_sha = r._repo_readme(repository, token=token, ref=head)
    if not isinstance(readme_sha, str) or len(readme_sha) != 40 or any(c not in "0123456789abcdef" for c in readme_sha):
        raise ValueError("Invalid README revision")
    ok, reason = r._source_valid(profile, readme)
    stamp = r._iso(now)
    row = {**previous, "profile_sha256": fingerprint(profile), "repository": repository,
           "repository_id": metadata["id"], "head_sha": head, "readme_sha": readme_sha,
           "source_ok": ok, "source_reason": reason, "observed_at": stamp,
           "last_attempt_at": stamp, "pending": list(previous.get("pending", [])),
           "status": previous.get("status", "observed")}
    # A changed approval/profile cannot inherit a pending queue authorised under
    # another profile. Preserve it for review instead of implicitly re-authorising.
    if previous.get("profile_sha256") and previous["profile_sha256"] != fingerprint(profile):
        row["previous_profile_sha256"] = previous["profile_sha256"]
        row["status"] = "profile_changed_review_required"
        return row
    if previous.get("status") in {"history_gap", "pending_overflow", "profile_changed_review_required"}:
        return row
    if previous.get("readme_sha") != readme_sha:
        row["readme_observed_at"] = stamp
    anchor = previous.get("buffered_head_sha") or legacy.get("head_sha") or (profile.get("runtime_source") or {}).get("head_sha")
    if not anchor:
        row["buffered_head_sha"] = head
        row["baseline_only"] = True
        return row
    if head == anchor:
        row["status"] = "observed"
        return row
    commits, found = _history(repository, head, anchor, token)
    if not found:
        row.update(status="history_gap", gap_after_sha=anchor)
        return row
    pending = row["pending"]
    known = {item["sha"] for item in pending}
    additions = []
    for item in reversed(commits):
        sha = str(item.get("sha") or "")
        if len(sha) != 40 or any(c not in "0123456789abcdef" for c in sha):
            raise ValueError("Invalid observed revision")
        commit = item.get("commit")
        if not isinstance(commit, dict) or not isinstance(commit.get("message"), str):
            raise ValueError("Incomplete observed commit metadata")
        title = r._safe_event_title(str(commit.get("message") or "").partition("\n")[0])
        if sha in known or not title or profile.get("event_enabled") is not True:
            continue
        source_at = (commit.get("committer") or {}).get("date")
        if source_at:
            parsed = datetime.fromisoformat(source_at.replace("Z", "+00:00"))
            if parsed.tzinfo is None or parsed > now:
                raise ValueError("Invalid source event timestamp")
        additions.append({"sha": sha, "title": title, "source_event_at": source_at,
                          "observed_at": stamp, "completed_providers": []})
    if len(pending) + len(additions) > MAX_PENDING:
        row.update(status="pending_overflow", gap_after_sha=anchor)
        return row
    row.update(pending=pending + additions, buffered_head_sha=head, status="observed", baseline_only=False)
    return row


def observe(*, apply=False, project=None, now=None, budget_seconds=180):
    from ocpf_post.portfolio_source_loader import merged_source_profiles
    from ocpf_post.runtime_sources import source_lock
    now = now or datetime.now(UTC)
    if not 1 <= budget_seconds <= 600:
        raise ValueError("Observation budget must be 1..600 seconds")
    profiles = merged_source_profiles()["projects"]
    if project and project not in profiles:
        raise ValueError("Unknown source project")
    # Serialise source activation/consumption with observation, never the social
    # publisher lock. Each completed project survives interruption of the cycle.
    with source_lock():
        state = load()
        legacy = read_json(r.source_state_file()).get("repositories", {})
        token = r._github_token()
        deadline = time.monotonic() + budget_seconds
        targets = [project] if project else sorted(profiles, key=lambda p: (state["projects"].get(p, {}).get("last_attempt_at", ""), p))
        results = []
        for name in targets:
            if time.monotonic() >= deadline:
                results.append({"project": name, "status": "deferred_collection_budget"})
                continue
            profile = {**profiles[name], "project": name}
            old = state["projects"].get(name, {})
            try:
                row = _observe(profile, old, legacy.get(profile["repository"], {}), now, token)
            except (r.ReplenisherError, OSError, ValueError, KeyError, TypeError, IndexError, AttributeError) as exc:
                row = {**old, "last_attempt_at": r._iso(now), "last_error_type": type(exc).__name__,
                       "collection_error": True}
            else:
                row.pop("collection_error", None)
                row.pop("last_error_type", None)
            state["projects"][name] = row
            if apply:
                from ocpf_post.scheduler import RunnerLock, RunnerBusy
                try:
                    # Network reads have already finished. Serialise only the
                    # authority observation commit with the publisher's claim.
                    with RunnerLock():
                        local_store.write(path(), state)
                except RunnerBusy:
                    state["projects"][name] = old
                    results.append({"project": name, "status": "deferred_publisher_busy"})
                    continue
            results.append({"project": name, "status": "collection_unavailable" if row.get("collection_error") else "source_guard_failed" if row.get("source_ok") is False else row.get("status"),
                            "head_sha": row.get("head_sha"), "buffered_head_sha": row.get("buffered_head_sha"),
                            "observed_at": row.get("observed_at"), "pending_events": len(row.get("pending", []))})
    return {"schema_version": 1, "apply": apply, "projects": results,
            "boundary": "Source observation only. Buffered evidence is not campaign admission, a reservation or a publication."}


def repository_observations(legacy=None):
    result = dict(legacy or {})
    for row in load()["projects"].values():
        if row.get("repository") and row.get("observed_at"):
            result[row["repository"]] = row
    return result


def acknowledge_gap(*, project, apply=False, expected_sha256=None, now=None):
    """Explicit, hash-reviewed recovery; never erase the pre-recovery evidence."""
    from ocpf_post.portfolio_source_loader import merged_source_profiles
    from ocpf_post.runtime_sources import source_lock
    now = now or datetime.now(UTC)
    with source_lock():
        state = load()
        row = state["projects"].get(project, {})
        profiles = merged_source_profiles()["projects"]
        if project not in profiles or row.get("status") not in {"history_gap", "pending_overflow", "profile_changed_review_required"}:
            raise ValueError("No source gap or authority change to acknowledge")
        if row.get("collection_error") or row.get("source_ok") is not True:
            raise ValueError("A successful guarded observation is required")
        from datetime import timedelta
        if not now - timedelta(hours=1) <= datetime.fromisoformat(row["observed_at"].replace("Z", "+00:00")) <= now:
            raise ValueError("A fresh source observation is required")
        profile = {**profiles[project], "project": project}
        review = {"project": project, "profile": profile, "observation": row,
                  "effect": "Acknowledge the unbuffered revision range and resume from the observed head. Existing pending events are retained unless authority changed; those remain archived and unadmitted. Existing campaigns and schedules are untouched."}
        digest = fingerprint(review)
        if apply:
            if expected_sha256 != digest:
                raise ValueError("Source gap review changed; preview again")
            archive = state_dir() / "source-gap-reviews" / (digest + ".json")
            if not archive.exists():
                local_store.write(archive, {"schema_version": 1, "review": review, "acknowledged_at": r._iso(now)})
            row = dict(row)
            if row["status"] == "profile_changed_review_required":
                row["pending"] = []
            row.update(status="observed", buffered_head_sha=row["head_sha"], profile_sha256=fingerprint(profile),
                       acknowledged_gap_sha256=digest, acknowledged_at=r._iso(now))
            state["projects"][project] = row
            local_store.write(path(), state)
        return {"schema_version": 1, "result": "acknowledged" if apply else "preview", "review_sha256": digest, "review": review}
