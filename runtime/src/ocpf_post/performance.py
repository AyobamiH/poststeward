from __future__ import annotations

import json
import os
import time
import urllib.parse
from typing import Any, Iterable

from ocpf_post.campaigns import builtin_manifest, campaign_project, destination_binding, normalize_campaign_id
from ocpf_post.ledger import append_jsonl, iter_jsonl
from ocpf_post.providers import get_extended_provider
from ocpf_post.providers.base import ProviderRejected, ProviderUnavailable
from ocpf_post.state import ensure_private_dir, latest_receipt, state_dir


def performance_file():
    return state_dir() / "performance-snapshots.jsonl"


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def append_snapshot(value: dict[str, Any]) -> None:
    append_jsonl(performance_file(), value)


def iter_snapshots() -> Iterable[dict[str, Any]]:
    return iter_jsonl(
        performance_file(),
        required=("campaign", "provider", "captured_at"),
    )


def latest_snapshot(campaign: str, provider: str) -> dict[str, Any] | None:
    campaign = normalize_campaign_id(campaign)
    provider = provider.strip().lower()
    for value in reversed(list(iter_snapshots())):
        if value.get("campaign") == campaign and value.get("provider") == provider:
            return value
    return None


def _x_performance(provider: Any, post_id: str) -> dict[str, Any]:
    url = (
        f"https://api.x.com/2/tweets/{urllib.parse.quote(post_id, safe='')}?"
        + urllib.parse.urlencode({"tweet.fields": "public_metrics"})
    )
    try:
        status, payload = provider._bearer(url)
    except Exception as exc:
        return {"metrics": {}, "availability": {"status": "unavailable", "error_type": type(exc).__name__}}
    if not 200 <= status < 300:
        return {
            "metrics": {},
            "availability": {"status": "unavailable", "http_status": status, "detail": "X metrics request was rejected"},
        }
    data = payload.get("data") if isinstance(payload, dict) else None
    public = data.get("public_metrics") if isinstance(data, dict) else None
    if not isinstance(public, dict):
        return {"metrics": {}, "availability": {"status": "unavailable", "detail": "X returned no public_metrics"}}
    mapping = {
        "likes": "like_count",
        "replies": "reply_count",
        "reposts": "retweet_count",
        "quotes": "quote_count",
        "bookmarks": "bookmark_count",
        "impressions": "impression_count",
    }
    metrics: dict[str, int | None] = {}
    unavailable: list[str] = []
    for target, source in mapping.items():
        if source in public and isinstance(public[source], (int, float)):
            metrics[target] = int(public[source])
        else:
            metrics[target] = None
            unavailable.append(target)
    return {
        "metrics": metrics,
        "availability": {"status": "available", "unavailable_metrics": unavailable},
    }


def _retry_header(headers):
    from email.utils import parsedate_to_datetime
    value = next((v for k, v in (headers or {}).items() if k.lower() == 'retry-after'), None)
    if value is None:
        return {}
    try:
        seconds = int(value)
        return {'retry_after_seconds': max(0, min(seconds, 86400))}
    except (TypeError, ValueError):
        try:
            return {'retry_at': parsedate_to_datetime(value).isoformat()}
        except (TypeError, ValueError):
            return {}


def _threads_performance(provider: Any, post_id: str) -> dict[str, Any]:
    try:
        status, _headers, payload = provider._bearer(
            f"https://graph.threads.net/v1.0/{urllib.parse.quote(post_id, safe='')}/insights",
            query={"metric": "views,likes,replies,reposts,quotes"},
        )
    except Exception as exc:
        return {"metrics": {}, "availability": {"status": "unavailable", "error_type": type(exc).__name__}}
    if not 200 <= status < 300:
        return {
            "metrics": {},
            "availability": {"status": "unavailable", "http_status": status, **_retry_header(_headers), "detail": "Threads insights request was rejected"},
        }
    rows = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(rows, list):
        return {"metrics": {}, "availability": {"status": "unavailable", "detail": "Threads returned no insights data"}}
    raw: dict[str, Any] = {}
    for row in rows:
        if not isinstance(row, dict) or not row.get("name"):
            continue
        value: Any = None
        total = row.get("total_value")
        if isinstance(total, dict):
            value = total.get("value")
        values = row.get("values")
        if value is None and isinstance(values, list) and values and isinstance(values[-1], dict):
            value = values[-1].get("value")
        raw[str(row["name"])] = value
    metrics: dict[str, int | None] = {}
    unavailable: list[str] = []
    for name in ("views", "likes", "replies", "reposts", "quotes"):
        value = raw.get(name)
        if isinstance(value, (int, float)):
            metrics[name] = int(value)
        else:
            metrics[name] = None
            unavailable.append(name)
    return {
        "metrics": metrics,
        "availability": {"status": "available", "unavailable_metrics": unavailable},
    }


def _provider_performance(provider_name: str, provider: Any, post_id: str) -> dict[str, Any]:
    if provider_name == "x":
        return _x_performance(provider, post_id)
    if provider_name == "threads":
        return _threads_performance(provider, post_id)
    if provider_name == "linkedin":
        return {
            "metrics": {},
            "availability": {
                "status": "unavailable",
                "detail": "LinkedIn member-post analytics are not exposed under the current permission boundary; no zero values are inferred.",
            },
        }
    return provider.performance(post_id)


def provider_performance(provider_name: str, post_id: str) -> dict[str, Any]:
    provider_name = provider_name.strip().lower()
    provider = get_extended_provider(provider_name)
    return _provider_performance(provider_name, provider, post_id)


def capture(campaign: str, provider_name: str, *, publication_receipt=None, attempt_id=None,
            target_age_hours=None) -> dict[str, Any]:
    campaign = normalize_campaign_id(campaign)
    provider_name = provider_name.strip().lower()
    binding = ({'account_id': publication_receipt['account_id']} if publication_receipt else destination_binding(campaign, provider_name))
    if not binding:
        raise ValueError(f"No destination binding for {campaign}/{provider_name}")
    receipt = publication_receipt or latest_receipt(campaign, provider_name, binding["account_id"])
    if not receipt or receipt.get("status") not in {"published_verified", "published_unverified"} or not receipt.get("post_id"):
        raise ValueError(f"No published receipt with post id for {campaign}/{provider_name}")

    from ocpf_post.providers import for_account
    provider = for_account(provider_name, binding["account_id"], factory=get_extended_provider, require_enabled=False)
    try:
        account = provider.account()
    except ProviderUnavailable as exc:
        raise ValueError(f"Provider identity check temporarily unavailable for {campaign}/{provider_name}: {exc}") from exc
    except ProviderRejected as exc:
        raise ValueError(f"Provider identity check failed for {campaign}/{provider_name}: {exc}") from exc
    if str(account.account_id) != binding["account_id"]:
        raise ValueError(
            f"Provider identity drift for {campaign}/{provider_name}: expected {binding['account_id']}, got {account.account_id}"
        )

    result = _provider_performance(provider_name, provider, str(receipt["post_id"]))
    try:
        manifest = builtin_manifest(campaign)
    except (ValueError, OSError):
        manifest = {}
    source = manifest.get("source") if isinstance(manifest.get("source"), dict) else {}
    snapshot = {
        "schema_version": 1,
        "capture_attempt_id": attempt_id,
        "target_age_hours": target_age_hours,
        "campaign": campaign,
        "project": manifest.get("project"),
        "source_id": source.get("source_id"),
        "provider": provider_name,
        "account_alias": binding.get("alias"),
        "account_id": binding["account_id"],
        "post_id": str(receipt["post_id"]),
        "url": receipt.get("url"),
        "publication_type": str(receipt.get("publication_type") or "single"),
        "part_count": int(receipt.get("part_count") or 1),
        "metric_scope": (
            "root_post_only"
            if str(receipt.get("publication_type") or "single") == "thread"
            else "logical_publication"
        ),
        "captured_at": _now(),
        "metrics": result.get("metrics", {}),
        "availability": result.get("availability", {"status": "unknown"}),
    }
    from ocpf_post.performance_feedback import metadata
    snapshot['editorial'] = metadata(manifest, receipt)
    append_snapshot(snapshot)
    return snapshot
