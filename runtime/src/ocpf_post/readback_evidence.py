"""Project separate provider readback evidence without rewriting durable ledgers."""
from __future__ import annotations

from typing import Any

from ocpf_post import local_store
from ocpf_post.state import state_dir
from ocpf_post.publication_readback_contract import complete_observation

IDENTITY_FIELDS = ('schedule_id', 'campaign', 'provider', 'account_id', 'post_id', 'text_sha256')


def observations(data: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    """Return safe local readback observations; corrupt sidecars fail closed."""
    if data is None:
        try:
            data = local_store.read(state_dir() / 'publication-readbacks.json')
        except (OSError, ValueError, TypeError):
            return []
    if not isinstance(data, dict) or not isinstance(data.get('observations'), dict):
        return []
    if any(not complete_observation(key, row) for key, row in data['observations'].items()):
        return []
    return list(data['observations'].values())


def verified(record: dict[str, Any], *, data: dict[str, Any] | None = None) -> bool:
    """Match one verified readback to the exact immutable publication identity."""
    if not isinstance(record, dict) or any(not record.get(key) for key in IDENTITY_FIELDS):
        return False
    return any(
        row.get('status') == 'verified'
        and all(row.get(key) == record.get(key) for key in IDENTITY_FIELDS)
        for row in observations(data)
    )
