"""Add-only source policies, reviewed activation and bounded GitHub observation."""
from __future__ import annotations

import hashlib
import json
import re
import uuid

from ocpf_post.onboarding import (
    OnboardingError, PROVIDERS, TEXT_LIMITS, _decode, _import_lock, _object,
    _slug, _text, read_input,
)
from ocpf_post.portfolio_source_loader import PortfolioSourceError, packaged_source_profiles
from ocpf_post.registry import project, resolve_account
from ocpf_post.state import config_dir, write_private_json


class SourceError(PortfolioSourceError):
    pass


def source_lock(*, operation: str | None = None, timeout_seconds: float = 0.0):
    return _import_lock(
        "source-onboarding", operation=operation, timeout_seconds=timeout_seconds,
    )


def source_file():
    return config_dir() / "runtime-sources.json"


def _hash(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()).hexdigest()


def _number(value, name, low, high):
    if type(value) is not int or not low <= value <= high:
        raise SourceError(f"{name} must be an integer from {low} to {high}")


def validate_source(value):
    required = {"schema_version", "project", "repository", "label", "campaign_prefix",
                "providers", "destinations", "required_phrases_any", "event_enabled",
                "allow_private_events", "claim_boundary", "inventory"}
    optional = {"event_problem", "event_cta", "default_cta", "event_priority", "event_ttl_hours"}
    _object(value, required | optional, required, "source policy")
    if type(value["schema_version"]) is not int or value["schema_version"] != 1:
        raise SourceError("Unsupported source schema version")
    pid = _slug(value["project"], "project")
    registered = project(pid)
    repo = value["repository"]
    if not isinstance(repo, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9-]{0,38}/[A-Za-z0-9_][A-Za-z0-9_.-]{0,99}", repo):
        raise SourceError("Repository must be a GitHub owner/name, without a URL, query or credentials")
    prefix = _text(value["campaign_prefix"], "campaign prefix", 30)
    if not re.fullmatch(r"[A-Z][A-Z0-9-]{1,29}", prefix) or prefix + "-" not in registered.get("campaign_prefixes", []):
        raise SourceError("Source prefix must exactly match a registered project prefix without its final hyphen")
    _text(value["label"], "source label", 80)
    _text(value["claim_boundary"], "claim boundary", 500)
    providers = value["providers"]
    if not isinstance(providers, list) or not providers or any(not isinstance(p, str) or p not in PROVIDERS for p in providers) or len(set(providers)) != len(providers):
        raise SourceError("Providers must be a nonempty unique supported list")
    destinations = value["destinations"]
    if not isinstance(destinations, dict) or set(destinations) != set(providers):
        raise SourceError("Exactly one registered destination alias is required per provider")
    for provider, alias in destinations.items():
        _slug(alias, "destination alias")
        resolve_account(pid, alias, expected_provider=provider)
    guards = value["required_phrases_any"]
    if not isinstance(guards, list) or not 1 <= len(guards) <= 16:
        raise SourceError("Provide 1 to 16 nonempty README guard phrases")
    for phrase in guards:
        _text(phrase, "README guard", 200)
        if len(phrase) < 8:
            raise SourceError("README guards must contain at least eight characters")
    for field in ("event_enabled", "allow_private_events"):
        if type(value[field]) is not bool:
            raise SourceError(f"{field} must be boolean")
    for field in ("event_problem", "event_cta", "default_cta"):
        if field in value:
            _text(value[field], field, 300)
    _number(value.get("event_priority", 98), "event_priority", 0, 100)
    _number(value.get("event_ttl_hours", 30), "event_ttl_hours", 1, 168)
    inventory = value["inventory"]
    if not isinstance(inventory, list) or len(inventory) > 16 or not (inventory or value["event_enabled"]):
        raise SourceError("Provide up to 16 inventory items or explicitly enable events")
    for item in inventory:
        fields = {"title", "lane", "priority", "hook", "body", "cta", "ttl_hours"}
        _object(item, fields, {"title", "lane", "priority", "hook", "body"}, "inventory item")
        if item["lane"] not in ("commercial", "evergreen"):
            raise SourceError("Inventory lane must be commercial or evergreen")
        for field in ("title", "hook", "body", "cta"):
            if field in item:
                _text(item[field], field, 500 if field == "body" else 200)
        _number(item["priority"], "priority", 0, 100)
        _number(item.get("ttl_hours", 336), "ttl_hours", 1, 720)
    # Validate every static variant using the same renderer used for publication.
    from ocpf_post.replenisher import STATIC_VARIANTS, _render_static, _render_event
    for item in inventory:
        for variant in STATIC_VARIANTS:
            effective_texts(value, {p: _render_static(value, item, p, variant) for p in providers})
    if value["event_enabled"]:
        # The event filter emits at most 140 title characters. Check this bound
        # even when the observed repository has no eligible example title yet.
        effective_texts(value, {p: _render_event(value, "x" * 140, p) for p in providers})
    return value


def effective_texts(profile, texts):
    """Preserve approved copy; publication segmentation happens after admission."""
    result = {}
    for p, text in texts.items():
        text = text.strip()
        if not text or len(text) > TEXT_LIMITS[p]:
            raise SourceError(f"Rendered {p} copy exceeds the local whole-publication safety envelope")
        result[p] = text
    return result


def _conflicts(profile, existing):
    pid, repo = profile["project"], profile["repository"].lower()
    if pid in existing:
        raise SourceError("Project already has a source policy; source imports cannot replace it")
    if any(str(p.get("repository", "")).lower() == repo for p in existing.values()):
        raise SourceError("Repository already belongs to another source policy")


def _load():
    try:
        data = _decode(source_file().read_bytes()) if source_file().exists() else {"schema_version": 1, "projects": {}}
        _object(data, {"schema_version", "projects"}, {"schema_version", "projects"}, "runtime sources")
        if type(data["schema_version"]) is not int or data["schema_version"] != 1 or not isinstance(data["projects"], dict):
            raise SourceError("Invalid runtime sources")
        existing = dict(packaged_source_profiles()["projects"])
        for pid, entry in data["projects"].items():
            _object(entry, {"profile", "enabled", "activation"}, {"profile", "enabled"}, "stored source")
            profile = validate_source(entry["profile"])
            if pid != profile["project"] or type(entry["enabled"]) is not bool:
                raise SourceError("Runtime source identity or enabled state is invalid")
            _conflicts(profile, existing)
            existing[pid] = profile
            activation = entry.get("activation")
            if entry["enabled"] or activation is not None:
                _object(activation, {"id", "head_sha", "repository_id", "review_sha256"},
                        {"id", "head_sha", "repository_id", "review_sha256"}, "activation")
                if not re.fullmatch(r"[a-f0-9]{32}", str(activation["id"])) or not re.fullmatch(r"[a-f0-9]{64}", str(activation["review_sha256"])):
                    raise SourceError("Invalid activation identity")
                _sha(activation["head_sha"])
                _number(activation["repository_id"], "repository_id", 1, 2**63 - 1)
        return data
    except (ValueError, OSError, TypeError) as exc:
        raise SourceError("Runtime source authority is invalid; inspect local configuration") from exc


def import_source(path, *, apply=False, expected_sha256=None):
    value, digest = read_input(path, apply=apply, expected_sha256=expected_sha256)
    profile = validate_source(value)
    def check():
        data = _load()
        old = data["projects"].get(profile["project"])
        if old and old["profile"] == profile:
            return data, True
        existing = {**packaged_source_profiles()["projects"], **{k: v["profile"] for k, v in data["projects"].items()}}
        _conflicts(profile, existing)
        return data, False
    if apply:
        with source_lock():
            data, exists = check()
            if not exists:
                data["projects"][profile["project"]] = {"profile": profile, "enabled": False}
                write_private_json(source_file(), data)
    else:
        data, exists = check()
    return {"schema_version": 1, "input_sha256": digest, "profile": profile,
            "result": "already_present" if exists else "imported" if apply else "preview",
            "enabled": data["projects"].get(profile["project"], {}).get("enabled", False),
            "boundary": "Local source registration only. New sources are inactive until explicitly enabled."}


def merge_runtime_sources(packaged):
    projects = dict(packaged["projects"])
    for pid, entry in _load()["projects"].items():
        if entry["enabled"]:
            projects[pid] = {**entry["profile"], "runtime_source": entry["activation"]}
    return {**packaged, "projects": projects}


def _sha(value):
    if not isinstance(value, str) or not re.fullmatch(r"[a-f0-9]{40}", value):
        raise SourceError("GitHub returned an invalid source SHA")
    return value


def observe(profile, *, token, repository_id=None):
    from ocpf_post import replenisher as r
    repo = profile["repository"]
    metadata = r._github_json(f"https://api.github.com/repos/{repo}", token=token)
    if not isinstance(metadata, dict) or str(metadata.get("full_name", "")).lower() != repo.lower():
        raise SourceError("GitHub repository identity changed")
    identity = metadata.get("id")
    _number(identity, "repository_id", 1, 2**63 - 1)
    if repository_id is not None and repository_id != identity:
        raise SourceError("GitHub repository was replaced; activation identity no longer matches")
    if type(metadata.get("private")) is not bool:
        raise SourceError("GitHub repository visibility is unknown")
    if metadata["private"] and profile["event_enabled"] and not profile["allow_private_events"]:
        raise SourceError("Private commit titles require explicit allow_private_events permission")
    commits = r._repo_commits(repo, token=token, limit=100)
    if not commits:
        raise SourceError("Repository has no observable commits")
    for item in commits:
        _sha(item.get("sha"))
        if not isinstance(item.get("commit"), dict) or not isinstance(item["commit"].get("message"), str):
            raise SourceError("GitHub commit metadata is incomplete")
    head = commits[0]["sha"]
    readme, readme_sha = r._repo_readme(repo, token=token, ref=head)
    _sha(readme_sha)
    valid, reason = r._source_valid(profile, readme)
    return {"repository": {"id": identity, "full_name": metadata["full_name"], "private": metadata["private"]},
            "head_sha": head, "readme_sha": readme_sha, "source_ok": valid, "source_reason": reason}, commits


def _entry(pid):
    _slug(pid, "project")
    data = _load()
    if pid not in data["projects"]:
        raise SourceError("No runtime source is registered for this project")
    return data, data["projects"][pid]


def preview_source(pid):
    from ocpf_post import __version__
    from ocpf_post import replenisher as r
    _, entry = _entry(pid)
    profile = entry["profile"]
    observation, commits = observe(profile, token=r._github_token(),
                                  repository_id=entry.get("activation", {}).get("repository_id"))
    if not observation["source_ok"]:
        raise SourceError(observation["source_reason"])
    candidates = []
    for index, item in enumerate(profile["inventory"], 1):
        for variant in r.STATIC_VARIANTS:
            code = {"insight": "I", "question": "Q", "practical": "P"}[variant]
            texts = effective_texts(profile, {p: r._render_static(profile, item, p, variant) for p in profile["providers"]})
            candidates.append({"campaign": f"{profile['campaign_prefix']}-AUTO-{index:02d}{code}-{observation['readme_sha'][:7].upper()}",
                               "variant": variant, "lane": item["lane"], "texts": texts, "payload_sha256": r._payload_hashes(texts)})
    example = None
    if profile["event_enabled"]:
        for commit in commits:
            title = r._safe_event_title(commit["commit"]["message"].partition("\n")[0])
            if title:
                example = {"source_sha": commit["sha"], "sample_only": True,
                           "texts": effective_texts(profile, {p: r._render_event(profile, title, p) for p in profile["providers"]})}
                break
    review = {"cli_version": __version__, "profile": profile, **observation,
              "accounts": {p: resolve_account(pid, a, expected_provider=p) for p, a in profile["destinations"].items()},
              "static_candidates": candidates, "historical_event_example": example}
    return {"schema_version": 1, "review_sha256": _hash(review), "review": review,
            "boundary": "Preview only. Enable authorises future template-derived campaigns and optional filtered commit titles. Existing commits are baselined, not queued."}


def enable_source(pid, *, expected_sha256):
    if not isinstance(expected_sha256, str) or not re.fullmatch(r"[a-f0-9]{64}", expected_sha256):
        raise SourceError("Enable requires the review_sha256 from a source preview")
    with source_lock():
        preview = preview_source(pid)
        if preview["review_sha256"] != expected_sha256:
            raise SourceError("Source, copy or authority changed since preview; review a fresh preview")
        data, entry = _entry(pid)
        exists = entry["enabled"]
        if not exists:
            review = preview["review"]
            entry["enabled"] = True
            entry["activation"] = {"id": uuid.uuid4().hex, "head_sha": review["head_sha"],
                                   "repository_id": review["repository"]["id"], "review_sha256": expected_sha256}
            write_private_json(source_file(), data)
    return {"schema_version": 1, "project": pid, "result": "already_enabled" if exists else "enabled",
            "activation": entry["activation"],
            "boundary": "Future replenishment and allocator selection are authorised. No campaigns, schedules or social posts were created by enable."}


def disable_source(pid):
    with source_lock():
        data, entry = _entry(pid)
        exists = not entry["enabled"]
        if not exists:
            entry["enabled"] = False
            write_private_json(source_file(), data)
    return {"schema_version": 1, "project": pid, "result": "already_disabled" if exists else "disabled",
            "boundary": "Stops future replenishment only. Existing campaigns and reservations retain their prior authority; cancel unwanted schedules separately."}


def list_sources():
    data = _load()
    return {"schema_version": 1, "runtime_sources": data["projects"],
            "packaged_projects": sorted(packaged_source_profiles()["projects"])}
