"""Pure durable-publication readback validation shared by all projections.

A status string is never independent provider evidence. Invalid sidecars are
preserved for recovery instead of silently becoming empty pending work.
"""
from __future__ import annotations

from datetime import datetime
import hashlib
import json
from typing import Any

IDENTITY_FIELDS = ('schedule_id', 'campaign', 'provider', 'account_id', 'post_id', 'text_sha256')
READBACK_MATCH_FIELDS = ('post_id', 'author', 'text')


def identity_digest(row: dict[str, Any]) -> str:
    payload = [row.get(field) for field in IDENTITY_FIELDS]
    encoded = json.dumps(payload, sort_keys=True, separators=(',', ':'), ensure_ascii=False)
    return hashlib.sha256(encoded.encode()).hexdigest()


def _timestamp(value: Any) -> bool:
    if type(value) is not str or not value:
        return False
    try:
        parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
    except ValueError:
        return False
    return parsed.tzinfo is not None and parsed.utcoffset() is not None


def complete_observation(key: Any, row: Any) -> bool:
    """Accept persisted rows only at their immutable digest and evidence level."""
    if type(key) is not str or not isinstance(row, dict):
        return False
    if any(field not in row for field in IDENTITY_FIELDS):
        return False
    for field in ('schedule_id', 'campaign', 'provider', 'account_id', 'text_sha256'):
        if type(row[field]) is not str or not row[field]:
            return False
    if row['post_id'] is not None and type(row['post_id']) is not str:
        return False
    if type(row.get('status')) is not str or not row['status']:
        return False
    try:
        if identity_digest(row) != key:
            return False
    except (TypeError, ValueError):
        return False

    # A terminal claim must contain the recorded provider GET/response and its
    # full expected/observed comparison. Reconciliation creates these fields;
    # accepting status='verified' alone would silently suppress unresolved work.
    status = row['status']
    if status in ('verified', 'verified_discovered'):
        if (type(row.get('http_status')) is not int
                or not 200 <= row['http_status'] < 300
                or row.get('original_status') not in ('published_unverified', 'ambiguous_effect', 'executing')
                or not _timestamp(row.get('observed_at'))
                or row.get('mismatch_fields') != []
                or type(row.get('mismatch_fields')) is not list):
            return False
        matches = row.get('matches')
        required = set(READBACK_MATCH_FIELDS)
        if row['provider'] == 'linkedin':
            required.add('lifecycle_state')
        if not isinstance(matches, dict) or not required.issubset(matches):
            return False
        if any(value is not True for value in matches.values()):
            return False
        if status == 'verified' and not row['post_id']:
            return False
        if status == 'verified_discovered' and (
                row['provider'] != 'threads' or not row.get('discovered_post_id')):
            return False
    return True
