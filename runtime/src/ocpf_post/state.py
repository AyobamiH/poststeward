from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Iterable

from ocpf_post.ledger import append_jsonl, iter_jsonl

from ocpf_post.schedule_semantics import TERMINAL_EFFECT_STATUSES


def _xdg_path(env_name: str, fallback: Path) -> Path:
    value = os.environ.get(env_name)
    return Path(value).expanduser() if value else fallback


def config_dir() -> Path:
    override = os.environ.get("OCPF_POST_CONFIG_DIR")
    if override:
        return Path(override).expanduser()
    base = _xdg_path("XDG_CONFIG_HOME", Path.home() / ".config")
    return base / "oneclickpostfactory" / "post-once"


def state_dir() -> Path:
    override = os.environ.get("OCPF_POST_STATE_DIR")
    if override:
        return Path(override).expanduser()
    base = _xdg_path("XDG_STATE_HOME", Path.home() / ".local" / "state")
    return base / "oneclickpostfactory" / "post-once"


def provider_token_file(provider: str) -> Path:
    return config_dir() / f"{provider}-token.json"


def provider_settings_file(provider: str) -> Path:
    return config_dir() / f"{provider}-settings.json"


def receipts_file() -> Path:
    return state_dir() / "publish-receipts.jsonl"


def ensure_private_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    try:
        path.chmod(0o700)
    except OSError:
        pass


def read_json(path: Path) -> dict[str, Any]:
    from ocpf_post.credential_keyring import read_managed
    managed = read_managed(path, config_dir())
    if managed is not None:
        return managed
    if not path.exists():
        return {}
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"Expected JSON object in {path}")
    return value


def write_private_json(path: Path, value: dict[str, Any]) -> None:
    from ocpf_post.credential_keyring import write_managed
    if write_managed(path, value, config_dir()):
        return
    ensure_private_dir(path.parent)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    try:
        tmp.chmod(0o600)
    except OSError:
        pass
    os.replace(tmp, path)
    try:
        path.chmod(0o600)
    except OSError:
        pass


def delete_private_json(path: Path) -> None:
    from ocpf_post.credential_keyring import delete_managed
    if delete_managed(path, config_dir()):
        return
    path.unlink(missing_ok=True)


def append_receipt(value: dict[str, Any]) -> None:
    append_jsonl(receipts_file(), value)


def iter_receipts() -> Iterable[dict[str, Any]]:
    return iter_jsonl(
        receipts_file(),
        required=("campaign", "provider", "status"),
    )


def latest_receipt(campaign: str, provider: str, account_id: str | None = None) -> dict[str, Any] | None:
    values = list(iter_receipts())
    for receipt in reversed(values):
        if receipt.get("campaign") != campaign or receipt.get("provider") != provider:
            continue
        if account_id is not None and receipt.get("account_id") != account_id:
            continue
        return receipt
    return None


def terminal_effect_receipt(campaign: str, provider: str, account_id: str) -> dict[str, Any] | None:
    values = list(iter_receipts())
    for receipt in reversed(values):
        if (
            receipt.get("campaign") == campaign
            and receipt.get("provider") == provider
            and receipt.get("account_id") in {None, account_id}
            and receipt.get("status") in TERMINAL_EFFECT_STATUSES
        ):
            return receipt
    return None
