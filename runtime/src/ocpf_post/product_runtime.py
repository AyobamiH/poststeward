"""External runtime identity for the standalone Post-Once product lineage.

The copied engine still uses historical OCPF_POST_* internals in many modules. This
boundary maps the public POST_ONCE_* environment onto isolated paths before the engine
starts, so the product can coexist with the owner's original Post-Once installation.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any

PRODUCT_LINEAGE = "post-once-bootstrap-runtime-v1"
PRODUCT_COMMAND = "post-once"
REFERENCE_REPOSITORY = "AyobamiH/post-once"
PRODUCT_REPOSITORY = "AyobamiH/post-once-bootstrap"


def standalone_product_active() -> bool:
    return os.environ.get("POST_ONCE_PRODUCT_LINEAGE") == PRODUCT_LINEAGE


def _xdg(env_name: str, fallback: Path) -> Path:
    value = os.environ.get(env_name)
    return Path(value).expanduser() if value else fallback


def resolved_paths() -> dict[str, Path]:
    home = Path.home()
    config_home = _xdg("XDG_CONFIG_HOME", home / ".config")
    state_home = _xdg("XDG_STATE_HOME", home / ".local" / "state")
    data_home = _xdg("XDG_DATA_HOME", home / ".local" / "share")
    config = Path(os.environ.get("POST_ONCE_CONFIG_DIR") or (config_home / "post-once")).expanduser()
    state = Path(os.environ.get("POST_ONCE_STATE_DIR") or (state_home / "post-once")).expanduser()
    releases = Path(
        os.environ.get("POST_ONCE_RELEASES_DIR") or (data_home / "post-once" / "releases")
    ).expanduser()
    setup = Path(
        os.environ.get("POST_ONCE_SETUP_STATE_DIR") or (state_home / "post-once-bootstrap" / "setup")
    ).expanduser()
    return {
        "config": config,
        "state": state,
        "releases": releases,
        "setup": setup,
    }


def apply_environment() -> dict[str, Any]:
    """Install the product-owned path namespace before importing runtime commands.

    Historical OCPF_POST_* path values are intentionally overwritten. They belong to
    the owner's legacy/runtime lineage and must never redirect this standalone product.
    """
    paths = resolved_paths()
    os.environ["OCPF_POST_CONFIG_DIR"] = str(paths["config"])
    os.environ["OCPF_POST_STATE_DIR"] = str(paths["state"])
    os.environ["OCPF_POST_RELEASES_DIR"] = str(paths["releases"])
    os.environ["OCPF_POST_SETUP_STATE_DIR"] = str(paths["setup"])
    runtime = os.environ.get("POST_ONCE_RUNTIME_ROOT")
    if runtime:
        os.environ["OCPF_POST_RUNTIME_ROOT"] = str(Path(runtime).expanduser())
    os.environ["POST_ONCE_PRODUCT_LINEAGE"] = PRODUCT_LINEAGE
    return {
        "schema_version": 1,
        "product_lineage": PRODUCT_LINEAGE,
        "command": PRODUCT_COMMAND,
        "repository": PRODUCT_REPOSITORY,
        "reference_repository": REFERENCE_REPOSITORY,
        "config_dir": str(paths["config"]),
        "state_dir": str(paths["state"]),
        "releases_dir": str(paths["releases"]),
        "setup_state_dir": str(paths["setup"]),
        "original_post_once_mutation_allowed": False,
    }
