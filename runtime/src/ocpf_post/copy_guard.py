"""Exact payload reuse guard for automatic allocation, scoped to destination."""
from __future__ import annotations

import hashlib

from ocpf_post.campaigns import destination_binding
from ocpf_post.registry import RegistryError
from ocpf_post.state import TERMINAL_EFFECT_STATUSES


def payload_key(provider, account_id, digest):
    if not account_id or not digest:
        return None
    return str(provider), str(account_id), str(digest)


def campaign_copy_key(campaign, provider, text):
    try:
        binding = destination_binding(campaign, provider)
    except RegistryError:
        return None  # The existing destination gate handles unbound campaigns.
    return payload_key(provider, (binding or {}).get("account_id"), hashlib.sha256(text.strip().encode()).hexdigest())


def occupied_copies(schedules, receipts, active_statuses):
    occupied = {}
    for row in [r for r in schedules if r.get("status") in active_statuses] + [
        r for r in receipts if r.get("status") in TERMINAL_EFFECT_STATUSES
    ]:
        key = payload_key(row.get("provider"), row.get("account_id"), row.get("text_sha256"))
        if key:
            occupied[key] = {field: row.get(field) for field in ("campaign", "provider", "account_id", "status", "schedule_id")}
    return occupied
