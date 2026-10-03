"""Bounded Cloudflare Workers AI transport for editorial fallback only.

This module owns no editorial policy, approval, scheduling or publication authority.
It provides one JSON-only text inference transport with a separate credential from
Cloudflare deployment credentials and from the legacy OpenAI API author.
"""
from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from typing import Any

from ocpf_post import local_store
from ocpf_post.state import config_dir, state_dir

DEFAULT_MODEL = "@cf/meta/llama-3.3-70b-instruct-fp8-fast"
DEFAULT_DAILY_LIMIT = 24
MAX_DAILY_LIMIT = 100
MAX_REQUEST_BYTES = 250_000
MAX_RESPONSE_BYTES = 500_000


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        raise ValueError("Workers AI redirect refused")


def _saved() -> dict[str, Any]:
    value = local_store.read(config_dir() / "workers-ai.json") or {}
    return value if isinstance(value, dict) else {}


def editorial_policy(env: dict[str, str] | os._Environ[str] = os.environ) -> dict[str, Any]:
    """Resolve persistent editorial-fallback policy with explicit env overrides."""
    saved = _saved()
    raw_enabled = env.get("OCPF_POST_GENERATIVE_SUPPLY_ENABLED")
    if raw_enabled is None:
        enabled = saved.get("fallback_enabled") is True
    else:
        enabled = str(raw_enabled).strip().lower() in {"1", "true", "yes"}

    raw_mode = env.get("OCPF_POST_GENERATIVE_SUPPLY_MODE")
    mode = str(raw_mode if raw_mode is not None else saved.get("producer_mode") or "work").strip().lower()
    if mode not in {"work", "work-workers-ai", "workers-ai", "api"}:
        mode = "work"
    raw_limit = env.get("OCPF_POST_WORKERS_AI_DAILY_LIMIT")
    source_limit = raw_limit if raw_limit is not None else saved.get("daily_limit", DEFAULT_DAILY_LIMIT)
    try:
        daily_limit = int(source_limit)
    except (TypeError, ValueError):
        daily_limit = DEFAULT_DAILY_LIMIT
    daily_limit = max(1, min(MAX_DAILY_LIMIT, daily_limit))
    return {
        "enabled": enabled,
        "mode": mode,
        "daily_limit": daily_limit,
        "persistent_fallback_enabled": saved.get("fallback_enabled") is True,
    }


def configuration(env: dict[str, str] | os._Environ[str] = os.environ) -> dict[str, Any]:
    saved = _saved()
    account_id = str(env.get("CLOUDFLARE_ACCOUNT_ID") or saved.get("account_id") or "").strip()
    token = str(env.get("CLOUDFLARE_WORKERS_AI_TOKEN") or saved.get("api_token") or "").strip()
    model = str(env.get("OCPF_POST_WORKERS_AI_MODEL") or saved.get("model") or DEFAULT_MODEL).strip()

    if account_id and not re.fullmatch(r"[A-Za-z0-9_-]{8,128}", account_id):
        raise ValueError("workers_ai_account_id_invalid")
    if token and (len(token) > 5000 or any(ch.isspace() for ch in token)):
        raise ValueError("workers_ai_token_invalid")
    if model and not re.fullmatch(r"@cf/[A-Za-z0-9._-]+/[A-Za-z0-9._-]+", model):
        raise ValueError("workers_ai_model_invalid")

    return {
        "available": bool(account_id and token and model),
        "account_id": account_id,
        "api_token": token,
        "model": model,
    }


def probe_status(*, now: datetime | None = None, max_age_hours: int = 24) -> dict[str, Any]:
    """Read non-secret live inference acceptance bound to the current model."""
    if type(max_age_hours) is not int or not 1 <= max_age_hours <= 168:
        raise ValueError("workers_ai_probe_age_invalid")
    now = now or datetime.now(timezone.utc)
    evidence = local_store.read(state_dir() / "workers-ai-probe.json") or {}
    current = configuration()
    observed_at = evidence.get("observed_at")
    try:
        observed = datetime.fromisoformat(str(observed_at).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        observed = None
    fresh = bool(
        observed
        and observed.tzinfo is not None
        and timedelta(0) <= now - observed.astimezone(timezone.utc) <= timedelta(hours=max_age_hours)
    )
    same_model = bool(current.get("model") and evidence.get("model") == current.get("model"))
    accepted = (
        current.get("available") is True
        and evidence.get("status") == "accepted"
        and fresh
        and same_model
    )
    return {
        "schema_version": 1,
        "status": "accepted" if accepted else "unobserved",
        "observed_at": observed_at if isinstance(observed_at, str) else None,
        "model_match": same_model,
        "fresh": fresh,
        "max_age_hours": max_age_hours,
        "boundary": (
            "Live Workers AI inference/structured-output acceptance only. "
            "No token, generated copy or social-provider authority is exposed."
        ),
    }


def status() -> dict[str, Any]:
    try:
        cfg = configuration()
    except ValueError as exc:
        return {
            "schema_version": 1,
            "status": "invalid",
            "reason": str(exc),
            "credential_present": False,
            "boundary": "Workers AI inference configuration only; no model call was attempted.",
        }
    return {
        "schema_version": 1,
        "status": "configured" if cfg["available"] else "unavailable",
        "credential_present": bool(cfg["api_token"]),
        "account_id_present": bool(cfg["account_id"]),
        "model": cfg["model"] or None,
        "editorial_enabled": editorial_policy()["enabled"],
        "fallback_enabled": editorial_policy()["persistent_fallback_enabled"],
        "producer_mode": editorial_policy()["mode"],
        "daily_limit": editorial_policy()["daily_limit"],
        "boundary": (
            "Workers AI editorial-fallback configuration only. The API token is never returned; "
            "configuration grants no campaign, scheduling or publication authority."
        ),
    }


def chat_json(
    *,
    messages: list[dict[str, str]],
    schema: dict[str, Any],
    max_tokens: int,
    temperature: float,
    timeout: int = 45,
    opener=None,
) -> tuple[dict[str, Any], str]:
    """Run one bounded JSON-mode Workers AI request.

    The caller remains responsible for semantic validation. JSON mode narrows
    transport shape but is never treated as approval.
    """
    cfg = configuration()
    if not cfg["available"]:
        raise ValueError("workers_ai_configuration_missing")
    if not isinstance(messages, list) or not messages:
        raise ValueError("workers_ai_messages_invalid")
    if type(max_tokens) is not int or not 1 <= max_tokens <= 16_000:
        raise ValueError("workers_ai_max_tokens_invalid")
    if not isinstance(temperature, (int, float)) or not 0 <= float(temperature) <= 1:
        raise ValueError("workers_ai_temperature_invalid")
    if type(timeout) is not int or not 1 <= timeout <= 120:
        raise ValueError("workers_ai_timeout_invalid")

    payload = {
        "model": cfg["model"],
        "messages": messages,
        "temperature": float(temperature),
        "max_tokens": max_tokens,
        "response_format": {
            "type": "json_schema",
            "json_schema": schema,
        },
        "options": {"rejectIfBusy": True},
    }
    raw_request = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode()
    if len(raw_request) > MAX_REQUEST_BYTES:
        raise ValueError("workers_ai_request_oversized")

    endpoint = (
        "https://api.cloudflare.com/client/v4/accounts/"
        + cfg["account_id"]
        + "/ai/v1/chat/completions"
    )
    request = urllib.request.Request(
        endpoint,
        data=raw_request,
        headers={
            "Authorization": "Bearer " + cfg["api_token"],
            "Content-Type": "application/json",
            "User-Agent": "OneClickPostFactory-post-once/workers-ai-fallback",
        },
        method="POST",
    )
    opener = opener or urllib.request.build_opener(NoRedirect())
    try:
        with opener.open(request, timeout=timeout) as response:
            raw = response.read(MAX_RESPONSE_BYTES + 1)
    except urllib.error.HTTPError as exc:
        if exc.code in {401, 403}:
            raise ValueError("workers_ai_auth_rejected") from None
        if exc.code == 429:
            raise ValueError("workers_ai_busy_or_rate_limited") from None
        raise ValueError("workers_ai_request_unavailable") from None
    except (urllib.error.URLError, TimeoutError, OSError):
        raise ValueError("workers_ai_request_unavailable") from None

    if len(raw) > MAX_RESPONSE_BYTES:
        raise ValueError("workers_ai_response_oversized")
    try:
        result = json.loads(raw)
        choice = result["choices"][0]
        message = choice["message"]
        finish = choice.get("finish_reason")
        if finish not in {None, "stop"}:
            raise ValueError
        content = message.get("content")
        if not isinstance(content, str) or not content.strip():
            raise ValueError
        value = json.loads(content)
    except (json.JSONDecodeError, KeyError, IndexError, TypeError, ValueError):
        raise ValueError("workers_ai_response_invalid") from None
    if not isinstance(value, dict):
        raise ValueError("workers_ai_response_invalid")
    return value, str(cfg["model"])

