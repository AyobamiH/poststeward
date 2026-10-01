"""Optional OS keyring/keychain backend for default provider credentials."""
from __future__ import annotations

import hashlib
import importlib
import json
import os
from pathlib import Path
import tempfile
from typing import Any

SERVICE = "oneclickpostfactory/post-once"
PROVIDERS = ("x", "threads", "linkedin")
INDEX_NAME = "keyring-providers.json"


def _service() -> str:
    from ocpf_post.product_runtime import standalone_product_active
    return 'poststeward/local-runtime' if standalone_product_active() else SERVICE


def index_path(config_root: Path) -> Path:
    return config_root / INDEX_NAME


def _module():
    try:
        return importlib.import_module("keyring")
    except ImportError as exc:
        raise ValueError("OS keyring support is not installed; install the 'keyring' extra") from exc


def backend_status() -> dict[str, Any]:
    try:
        module = _module()
        backend = module.get_keyring()
        priority = getattr(backend, "priority", 0)
        available = bool(priority and priority > 0)
        return {
            "available": available,
            "backend": type(backend).__name__,
            "priority": priority,
        }
    except Exception as exc:
        return {"available": False, "error_type": type(exc).__name__}


def _read_index(config_root: Path) -> dict[str, Any]:
    path = index_path(config_root)
    if not path.exists():
        return {"schema_version": 1, "providers": {}}
    value = json.loads(path.read_text(encoding="utf-8"))
    if (
        not isinstance(value, dict)
        or value.get("schema_version") != 1
        or not isinstance(value.get("providers"), dict)
    ):
        raise ValueError("Invalid keyring provider index")
    return value


def _write_index(config_root: Path, value: dict[str, Any]) -> None:
    config_root.mkdir(parents=True, exist_ok=True)
    try:
        config_root.chmod(0o700)
    except OSError:
        pass
    fd, name = tempfile.mkstemp(prefix=INDEX_NAME + ".", dir=config_root)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(value, handle, sort_keys=True, separators=(",", ":"))
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(name, 0o600)
        os.replace(name, index_path(config_root))
        os.chmod(index_path(config_root), 0o600)
        directory = os.open(config_root, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        Path(name).unlink(missing_ok=True)


def username(provider: str) -> str:
    if provider not in PROVIDERS:
        raise ValueError("Unsupported provider keyring identity")
    return "provider:" + provider


def managed_provider_for_path(path: Path, config_root: Path) -> str | None:
    try:
        relative = path.resolve().relative_to(config_root.resolve())
    except (OSError, ValueError):
        return None
    if relative.parent != Path(".") or relative.name not in {f"{p}-token.json" for p in PROVIDERS}:
        return None
    provider = relative.name.removesuffix("-token.json")
    index = _read_index(config_root)
    row = index["providers"].get(provider)
    if not isinstance(row, dict) or row.get("service") != _service() or row.get("username") != username(provider):
        return None
    return provider


def is_managed(provider: str, config_root: Path) -> bool:
    if provider not in PROVIDERS:
        return False
    index = _read_index(config_root)
    row = index["providers"].get(provider)
    return isinstance(row, dict) and row.get("service") == _service() and row.get("username") == username(provider)


def _secret(provider: str) -> str | None:
    module = _module()
    return module.get_password(_service(), username(provider))


def read_managed(path: Path, config_root: Path) -> dict[str, Any] | None:
    provider = managed_provider_for_path(path, config_root)
    if provider is None:
        return None
    raw = _secret(provider)
    if raw is None:
        raise ValueError("Configured OS keyring credential is unavailable")
    value = json.loads(raw)
    if not isinstance(value, dict):
        raise ValueError("OS keyring credential is not a JSON object")
    return value


def write_managed(path: Path, value: dict[str, Any], config_root: Path) -> bool:
    provider = managed_provider_for_path(path, config_root)
    if provider is None:
        return False
    module = _module()
    raw = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    module.set_password(_service(), username(provider), raw)
    observed = module.get_password(_service(), username(provider))
    if observed != raw:
        raise ValueError("OS keyring write could not be verified")
    return True


def delete_managed(path: Path, config_root: Path) -> bool:
    provider = managed_provider_for_path(path, config_root)
    if provider is None:
        return False
    module = _module()
    try:
        module.delete_password(_service(), username(provider))
    except Exception as exc:
        try:
            remaining = module.get_password(_service(), username(provider))
        except Exception:
            remaining = "unknown"
        if remaining is not None:
            raise ValueError("OS keyring credential could not be deleted") from exc
    index = _read_index(config_root)
    index["providers"].pop(provider, None)
    _write_index(config_root, index)
    return True


def _review(provider: str, value: dict[str, Any]) -> str:
    payload = {
        "provider": provider,
        "credential_sha256": hashlib.sha256(
            json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
        ).hexdigest(),
        "service": _service(),
        "username": username(provider),
    }
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def status(config_root: Path) -> dict[str, Any]:
    backend = backend_status()
    providers = []
    for provider in PROVIDERS:
        managed = is_managed(provider, config_root)
        present = None
        if managed and backend.get("available"):
            try:
                present = _secret(provider) is not None
            except Exception:
                present = False
        providers.append({
            "provider": provider,
            "managed": managed,
            "secret_present": present,
        })
    return {
        "schema_version": 1,
        "backend": backend,
        "providers": providers,
        "boundary": "Metadata/presence only. Credential values are never returned.",
    }


def migrate_provider(
    provider: str,
    token_path: Path,
    config_root: Path,
    *,
    apply: bool = False,
    expected_sha256: str | None = None,
) -> dict[str, Any]:
    if provider not in PROVIDERS:
        raise ValueError("Unsupported provider")
    if is_managed(provider, config_root):
        return {"schema_version": 1, "status": "already_managed", "applied": False, "provider": provider}
    if not token_path.exists():
        return {"schema_version": 1, "status": "blocked", "applied": False, "provider": provider,
                "reason": "provider_token_file_missing"}
    if os.name == "posix" and token_path.stat().st_mode & 0o077:
        return {"schema_version": 1, "status": "blocked", "applied": False, "provider": provider,
                "reason": "provider_token_file_not_private"}
    value = json.loads(token_path.read_text(encoding="utf-8"))
    if not isinstance(value, dict) or not value.get("access_token"):
        raise ValueError("Provider token file does not contain an access token")
    review = _review(provider, value)
    if not apply:
        return {
            "schema_version": 1,
            "status": "preview",
            "applied": False,
            "provider": provider,
            "review_sha256": review,
            "backend": backend_status(),
        }
    if expected_sha256 != review:
        raise ValueError("Keyring migration review changed")
    module = _module()
    raw = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    module.set_password(_service(), username(provider), raw)
    if module.get_password(_service(), username(provider)) != raw:
        raise ValueError("OS keyring write could not be verified")
    index = _read_index(config_root)
    index["providers"][provider] = {"service": _service(), "username": username(provider)}
    _write_index(config_root, index)
    token_path.unlink()
    directory = os.open(token_path.parent, os.O_RDONLY)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)
    return {
        "schema_version": 1,
        "status": "migrated",
        "applied": True,
        "provider": provider,
        "review_sha256": review,
        "boundary": "Verified OS keyring copy is authoritative; plaintext token file was removed after verification.",
    }


def restore_provider(
    provider: str,
    token_path: Path,
    config_root: Path,
    *,
    apply: bool = False,
    expected_sha256: str | None = None,
) -> dict[str, Any]:
    if provider not in PROVIDERS:
        raise ValueError("Unsupported provider")
    if not is_managed(provider, config_root):
        return {"schema_version": 1, "status": "not_managed", "applied": False, "provider": provider}
    raw = _secret(provider)
    if raw is None:
        return {"schema_version": 1, "status": "blocked", "applied": False, "provider": provider,
                "reason": "keyring_secret_missing"}
    value = json.loads(raw)
    if not isinstance(value, dict):
        raise ValueError("OS keyring credential is not a JSON object")
    review = _review(provider, value)
    if not apply:
        return {
            "schema_version": 1,
            "status": "preview",
            "applied": False,
            "provider": provider,
            "review_sha256": review,
        }
    if expected_sha256 != review:
        raise ValueError("Keyring restore review changed")
    token_path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=token_path.name + ".", dir=token_path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(value, handle, indent=2, ensure_ascii=False)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(name, 0o600)
        os.replace(name, token_path)
        os.chmod(token_path, 0o600)
    finally:
        Path(name).unlink(missing_ok=True)
    index = _read_index(config_root)
    index["providers"].pop(provider, None)
    _write_index(config_root, index)
    try:
        _module().delete_password(_service(), username(provider))
    except Exception:
        # A duplicate keyring copy is safer than deleting the newly restored file.
        pass
    return {
        "schema_version": 1,
        "status": "restored_to_private_file",
        "applied": True,
        "provider": provider,
        "review_sha256": review,
        "boundary": "Private token file was fsync'd before keyring authority was removed.",
    }
