"""External runtime identity for the PostSteward local product runtime.

The embedded engine retains historical OCPF_POST_* internals while PostSteward
product-facing paths and environment variables are mapped into those internals before
runtime commands load. This keeps customer state isolated from the owner's original
Post-Once installation and from the earlier post-once-bootstrap incubation identity.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any

PRODUCT_LINEAGE = "poststeward-local-runtime-v1"
PRODUCT_COMMAND = "poststeward"
PRODUCT_REPOSITORY = "AyobamiH/poststeward"
INCUBATION_REPOSITORY = "AyobamiH/post-once-bootstrap"
REFERENCE_REPOSITORY = "AyobamiH/post-once"


def standalone_product_active() -> bool:
    """Return whether the embedded PostSteward product boundary is active.

    POST_ONCE_PRODUCT_LINEAGE is accepted only as a transitional internal signal for
    the adopted A-K engine. Product-facing callers must use POSTSTEWARD_RUNTIME_LINEAGE.
    """
    return (
        os.environ.get("POSTSTEWARD_RUNTIME_LINEAGE") == PRODUCT_LINEAGE
        or os.environ.get("POST_ONCE_PRODUCT_LINEAGE") == PRODUCT_LINEAGE
    )


def _xdg(env_name: str, fallback: Path) -> Path:
    value = os.environ.get(env_name)
    return Path(value).expanduser() if value else fallback


def resolved_paths() -> dict[str, Path]:
    home = Path.home()
    config_home = _xdg("XDG_CONFIG_HOME", home / ".config")
    state_home = _xdg("XDG_STATE_HOME", home / ".local" / "state")
    data_home = _xdg("XDG_DATA_HOME", home / ".local" / "share")
    config = Path(
        os.environ.get("POSTSTEWARD_RUNTIME_CONFIG_DIR")
        or (config_home / "poststeward" / "runtime")
    ).expanduser()
    state = Path(
        os.environ.get("POSTSTEWARD_RUNTIME_STATE_DIR")
        or (state_home / "poststeward" / "runtime")
    ).expanduser()
    releases = Path(
        os.environ.get("POSTSTEWARD_RELEASES_DIR")
        or (data_home / "poststeward" / "releases")
    ).expanduser()
    setup = Path(
        os.environ.get("POSTSTEWARD_SETUP_STATE_DIR")
        or (state_home / "poststeward" / "setup")
    ).expanduser()
    return {
        "config": config,
        "state": state,
        "releases": releases,
        "setup": setup,
    }


def apply_environment() -> dict[str, Any]:
    """Install PostSteward-owned paths before importing inherited runtime commands.

    Historical POST_ONCE_* and OCPF_POST_* path variables are overwritten on the
    canonical PostSteward entrypoint. They are internal compatibility surfaces only
    and must never redirect a customer runtime into the owner's original state.
    """
    paths = resolved_paths()

    # Public PostSteward namespace.
    os.environ["POSTSTEWARD_RUNTIME_CONFIG_DIR"] = str(paths["config"])
    os.environ["POSTSTEWARD_RUNTIME_STATE_DIR"] = str(paths["state"])
    os.environ["POSTSTEWARD_RELEASES_DIR"] = str(paths["releases"])
    os.environ["POSTSTEWARD_SETUP_STATE_DIR"] = str(paths["setup"])
    os.environ["POSTSTEWARD_RUNTIME_LINEAGE"] = PRODUCT_LINEAGE
    # Canonical PostSteward activation/deactivation is always cloud-fenced. Direct
    # module tests may omit this environment because they are not product entrypoints.
    os.environ["POSTSTEWARD_REQUIRE_CLOUD_FENCE"] = "1"

    # Transitional compatibility consumed by copied A-K runtime modules/scripts.
    os.environ["POST_ONCE_CONFIG_DIR"] = str(paths["config"])
    os.environ["POST_ONCE_STATE_DIR"] = str(paths["state"])
    os.environ["POST_ONCE_RELEASES_DIR"] = str(paths["releases"])
    os.environ["POST_ONCE_SETUP_STATE_DIR"] = str(paths["setup"])
    os.environ["POST_ONCE_PRODUCT_LINEAGE"] = PRODUCT_LINEAGE
    os.environ["OCPF_POST_CONFIG_DIR"] = str(paths["config"])
    os.environ["OCPF_POST_STATE_DIR"] = str(paths["state"])
    os.environ["OCPF_POST_RELEASES_DIR"] = str(paths["releases"])
    os.environ["OCPF_POST_SETUP_STATE_DIR"] = str(paths["setup"])

    runtime = os.environ.get("POSTSTEWARD_RUNTIME_ROOT")
    if runtime:
        root = str(Path(runtime).expanduser())
        os.environ["POST_ONCE_RUNTIME_ROOT"] = root
        os.environ["OCPF_POST_RUNTIME_ROOT"] = root

    return {
        "schema_version": 1,
        "product": "poststeward",
        "product_lineage": PRODUCT_LINEAGE,
        "command": PRODUCT_COMMAND,
        "repository": PRODUCT_REPOSITORY,
        "incubation_repository": INCUBATION_REPOSITORY,
        "reference_repository": REFERENCE_REPOSITORY,
        "config_dir": str(paths["config"]),
        "state_dir": str(paths["state"]),
        "releases_dir": str(paths["releases"]),
        "setup_state_dir": str(paths["setup"]),
        "provider_credentials_local": False,
        "original_post_once_mutation_allowed": False,
    }
