"""Read-only, revision-scoped GitHub observations. Never publishing authority."""
from __future__ import annotations

import re
from urllib.parse import quote

from ocpf_post import replenisher as github
from ocpf_post.portfolio_source_loader import packaged_source_profiles
from ocpf_post.runtime_sources import SourceError, _load, _sha

REPOSITORY = re.compile(r"[A-Za-z0-9][A-Za-z0-9-]{0,38}/[A-Za-z0-9_][A-Za-z0-9_.-]{0,99}")


def _configured(project):
    runtime = _load()["projects"].get(project)
    if runtime:
        return runtime["profile"], (runtime.get("activation") or {}).get("repository_id")
    profile = packaged_source_profiles()["projects"].get(project)
    if profile is None:
        raise SourceError("No configured source for this project")
    return profile, None


def _items(data, key=None):
    values = data.get(key) if key and isinstance(data, dict) else data if key is None else None
    if not isinstance(values, list) or any(not isinstance(item, dict) for item in values):
        raise SourceError("GitHub returned incomplete evidence metadata")
    return values


def inspect_source(project, *, sha=None, fetch=None, token=None):
    """No log/patch/URL execution, local writes, generation or authority changes."""
    if sha is not None:
        _sha(sha)
    profile, pinned_id = _configured(project)
    repository = profile["repository"]
    if not isinstance(repository, str) or not REPOSITORY.fullmatch(repository):
        raise SourceError("Invalid configured repository")
    fetch = fetch or github._github_json
    token = token if token is not None else github._github_token()
    root = f"https://api.github.com/repos/{repository}"
    def get(path):
        return fetch(root + path, token=token)
    metadata = get("")
    if (not isinstance(metadata, dict)
            or str(metadata.get("full_name", "")).lower() != repository.lower()
            or type(metadata.get("id")) is not int
            or type(metadata.get("private")) is not bool
            or (pinned_id is not None and metadata["id"] != pinned_id)):
        raise SourceError("GitHub repository identity or visibility does not match configuration")
    ref = sha or metadata.get("default_branch")
    if not isinstance(ref, str) or not ref:
        raise SourceError("GitHub default branch is unavailable")
    commit = get(f"/commits/{quote(ref, safe='')}?per_page=100")
    if not isinstance(commit, dict):
        raise SourceError("GitHub commit metadata is unavailable")
    observed_sha = _sha(commit.get("sha"))
    if sha is not None and observed_sha != sha:
        raise SourceError("GitHub returned evidence for a different commit")
    source_url = f"https://github.com/{repository}/commit/{observed_sha}"
    # Deliberately omit commit messages, filenames, descriptions and deployment
    # URLs: private-source inspection does not authorise disclosing their text.
    result = {
        "schema_version": 1, "project": project, "observed_at": github._iso(github._utc_now()),
        "repository": {"full_name": repository, "id": metadata["id"], "private": metadata["private"]},
        "source": {"status": "observed", "sha": observed_sha, "url": source_url,
                   "selection": "explicit_commit" if sha else "default_branch_at_observation",
                   "implementation_review": "not_performed"},
        "workflow_runs": {}, "deployments": {},
        "runtime_verification": {"status": "not_observed", "reason": "No deployed application behaviour was exercised"},
        "business_outcomes": {"status": "not_observed", "reason": "No adoption, customer or revenue evidence source is connected"},
        "publishing_authority_changed": False,
        "boundary": "Point-in-time GitHub records for one commit. Workflow success does not establish test coverage or production behaviour. Deployment status is a report, not independent runtime verification. This command does not change copy, source cursors, schedules or publishing authority.",
    }
    try:
        data = get(f"/actions/runs?head_sha={observed_sha}&per_page=100")
        runs = _items(data, "workflow_runs")
        if any(run.get("head_sha") != observed_sha for run in runs):
            raise SourceError("Workflow response contained a different commit")
        rows = []
        for run in runs[:100]:
            if type(run.get("id")) is not int:
                raise SourceError("Workflow run identity was unavailable")
            rows.append({key: run.get(key) for key in (
                "id", "workflow_id", "run_attempt", "event", "status", "conclusion", "head_sha", "created_at", "updated_at")})
            rows[-1]["url"] = f"https://github.com/{repository}/actions/runs/{run['id']}"
        total = data.get("total_count")
        result["workflow_runs"] = {
            "status": "observed" if rows else "none_observed", "records": rows,
            "possibly_truncated": type(total) is not int or total > len(rows) or len(rows) >= 100,
            "scope": "GitHub Actions only; records are not a required-check or feature-correctness verdict",
        }
    except (github.ReplenisherError, SourceError):
        result["workflow_runs"] = {"status": "unavailable", "records": [], "reason": "GitHub Actions evidence could not be read or validated"}
    try:
        deployments = _items(get(f"/deployments?sha={observed_sha}&per_page=10"))
        if any(row.get("sha") != observed_sha for row in deployments):
            raise SourceError("Deployment response contained a different commit")
        rows = []
        for deployment in deployments[:10]:
            identity = deployment.get("id")
            if type(identity) is not int:
                raise SourceError("Deployment identity was unavailable")
            row = {key: deployment.get(key) for key in (
                "id", "sha", "environment", "production_environment", "transient_environment", "created_at")}
            row["url"] = f"{root}/deployments/{identity}"
            try:
                statuses = _items(get(f"/deployments/{identity}/statuses?per_page=1"))
                row["latest_status"] = ({key: statuses[0].get(key) for key in ("id", "state", "created_at", "updated_at")}
                                        if statuses else None)
                row["status_observation"] = "observed" if statuses else "none_observed"
            except (github.ReplenisherError, SourceError):
                row["latest_status"] = None
                row["status_observation"] = "unavailable"
            rows.append(row)
        result["deployments"] = {"status": "observed" if rows else "none_observed", "records": rows,
                                 "possibly_truncated": len(deployments) >= 10,
                                 "scope": "GitHub deployment records only; absent records do not establish that nothing is deployed"}
    except (github.ReplenisherError, SourceError):
        result["deployments"] = {"status": "unavailable", "records": [], "reason": "GitHub deployment evidence could not be read or validated"}
    return result
