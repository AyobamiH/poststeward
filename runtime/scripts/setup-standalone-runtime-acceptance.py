#!/usr/bin/env python3
"""Milestone K acceptance for standalone Post-Once runtime identity.

This exercises the real product entrypoint against hostile inherited legacy path
variables, then audits every consequence-capable runtime-management surface for the
standalone service namespace. It never calls systemd or a provider.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile

from ocpf_post import runtime_release
from ocpf_post.campaigns import campaign_ids
from ocpf_post.setup_activation import SERVICES, TIMERS

ROOT = Path(__file__).resolve().parents[1]
CRITICAL_RUNTIME_FILES = (
    "scripts/install-user-scheduler-timer",
    "scripts/install-user-portfolio-timer",
    "scripts/uninstall-user-scheduler-timer",
    "scripts/uninstall-user-portfolio-timer",
    "scripts/install-user-console",
    "scripts/restore-local-runtime.py",
    "scripts/safe-runtime-upgrade.py",
    "scripts/run-unattended",
    "scripts/run-portfolio-refill",
    "scripts/run-operating-cycle",
    "scripts/runtime-attestation",
    "scripts/runtime-env",
)


def digest_tree(root: Path) -> str:
    rows: list[tuple[str, str]] = []
    if root.exists():
        for path in sorted(p for p in root.rglob("*") if p.is_file()):
            rows.append((
                path.relative_to(root).as_posix(),
                hashlib.sha256(path.read_bytes()).hexdigest(),
            ))
    return hashlib.sha256(
        json.dumps(rows, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def main() -> None:
    with tempfile.TemporaryDirectory(prefix="post-once-standalone-acceptance-") as temp:
        root = Path(temp)
        packaged_campaigns = campaign_ids()
        if not packaged_campaigns:
            raise RuntimeError("Acceptance fixture expected historical packaged campaigns")
        historical_campaign = packaged_campaigns[0]
        home = root / "home"
        xdg_config = root / "xdg-config"
        xdg_state = root / "xdg-state"
        xdg_data = root / "xdg-data"
        legacy_config = root / "legacy-owner-config"
        legacy_state = root / "legacy-owner-state"
        legacy_releases = root / "legacy-owner-releases"
        legacy_setup = root / "legacy-owner-setup"
        for path in (home, legacy_config, legacy_state, legacy_releases, legacy_setup):
            path.mkdir(parents=True)
        (legacy_config / "sentinel.txt").write_text("owner-config-do-not-touch\n", encoding="utf-8")
        (legacy_state / "sentinel.txt").write_text("owner-state-do-not-touch\n", encoding="utf-8")
        (legacy_releases / "sentinel.txt").write_text("owner-releases-do-not-touch\n", encoding="utf-8")
        (legacy_setup / "sentinel.txt").write_text("owner-setup-do-not-touch\n", encoding="utf-8")
        before = {
            "config": digest_tree(legacy_config),
            "state": digest_tree(legacy_state),
            "releases": digest_tree(legacy_releases),
            "setup": digest_tree(legacy_setup),
        }

        env = {
            **os.environ,
            "HOME": str(home),
            "XDG_CONFIG_HOME": str(xdg_config),
            "XDG_STATE_HOME": str(xdg_state),
            "XDG_DATA_HOME": str(xdg_data),
            # Deliberately hostile inherited owner-runtime paths.
            "OCPF_POST_CONFIG_DIR": str(legacy_config),
            "OCPF_POST_STATE_DIR": str(legacy_state),
            "OCPF_POST_RELEASES_DIR": str(legacy_releases),
            "OCPF_POST_SETUP_STATE_DIR": str(legacy_setup),
        }
        for key in (
            "POST_ONCE_CONFIG_DIR",
            "POST_ONCE_STATE_DIR",
            "POST_ONCE_RELEASES_DIR",
            "POST_ONCE_SETUP_STATE_DIR",
            "POST_ONCE_RUNTIME_ROOT",
        ):
            env.pop(key, None)

        command = [
            "sh",
            str(ROOT / "post-once"),
            "setup",
            "start",
            "--mode",
            "explore",
            "--timezone",
            "UTC",
            "--pace",
            "occasional",
            "--json",
        ]
        result = subprocess.run(
            command,
            cwd=ROOT,
            env=env,
            text=True,
            capture_output=True,
            timeout=60,
            check=False,
        )
        if result.returncode:
            raise RuntimeError("Standalone product entrypoint did not complete isolated explore setup")
        value = json.loads(result.stdout)
        if value["session"]["stage"] != "explore_ready":
            raise RuntimeError("Standalone product did not reach explore_ready")

        registry_result = subprocess.run(
            ["sh", str(ROOT / "post-once"), "registry", "list"],
            cwd=ROOT,
            env=env,
            text=True,
            capture_output=True,
            timeout=30,
            check=False,
        )
        if registry_result.returncode:
            raise RuntimeError("Fresh product registry could not be inspected")
        registry_empty = json.loads(registry_result.stdout) == []

        campaign_result = subprocess.run(
            ["sh", str(ROOT / "post-once"), "campaign", "show", "--campaign", historical_campaign, "--provider", "x"],
            cwd=ROOT,
            env=env,
            text=True,
            capture_output=True,
            timeout=30,
            check=False,
        )
        packaged_owner_campaign_hidden = (
            campaign_result.returncode != 0
            and "No built-in" in campaign_result.stderr
        )

        expected_setup = xdg_state / "post-once-bootstrap" / "setup" / "setup-control.sqlite3"
        if not expected_setup.is_file():
            raise RuntimeError("Standalone product did not use its isolated setup-control root")
        if (legacy_setup / "setup-control.sqlite3").exists():
            raise RuntimeError("Inherited legacy setup root was mutated")

        after = {
            "config": digest_tree(legacy_config),
            "state": digest_tree(legacy_state),
            "releases": digest_tree(legacy_releases),
            "setup": digest_tree(legacy_setup),
        }
        original_paths_unchanged = before == after

        texts = {
            relative: (ROOT / relative).read_text(encoding="utf-8")
            for relative in CRITICAL_RUNTIME_FILES
        }
        forbidden = (
            "ocpf-post-run-due",
            "ocpf-post-portfolio-refill",
            "ocpf-post-collection",
            "ocpf-post-replies",
            "ocpf-post-console",
            "oneclickpostfactory/post-once",
        )
        legacy_namespace_absent = all(
            token not in text
            for text in texts.values()
            for token in forbidden
        )
        unit_namespace_isolated = (
            all(unit.startswith("post-once-") for unit in (*TIMERS, *SERVICES))
            and all(unit.startswith("post-once-") for unit in runtime_release.RUNTIME_UNIT_FILES)
        )
        canonical_cli = (ROOT / "post-once").read_text(encoding="utf-8")
        compat_cli = (ROOT / "ocpf-post").read_text(encoding="utf-8")
        product_entry_shared = (
            "ocpf_post.product_entry" in canonical_cli
            and "ocpf_post.product_entry" in compat_cli
        )

        output = {
            "schema_version": 1,
            "status": "pass" if all((
                original_paths_unchanged,
                legacy_namespace_absent,
                unit_namespace_isolated,
                product_entry_shared,
                registry_empty,
                packaged_owner_campaign_hidden,
            )) else "fail",
            "canonical_command": "post-once",
            "canonical_command_explore_stage": value["session"]["stage"],
            "isolated_setup_store_created": expected_setup.is_file(),
            "inherited_original_runtime_paths_ignored": original_paths_unchanged,
            "legacy_service_namespace_absent": legacy_namespace_absent,
            "standalone_unit_namespace_verified": unit_namespace_isolated,
            "canonical_and_compat_entry_share_product_boundary": product_entry_shared,
            "fresh_registry_empty": registry_empty,
            "packaged_owner_campaign_hidden": packaged_owner_campaign_hidden,
            "standalone_config_root": str(xdg_config / "post-once"),
            "standalone_state_root": str(xdg_state / "post-once"),
            "standalone_release_root": str(xdg_data / "post-once" / "releases"),
            "standalone_setup_root": str(xdg_state / "post-once-bootstrap" / "setup"),
            "provider_consequence_attempted": False,
            "original_post_once_mutated": False,
        }
        print(json.dumps(output, sort_keys=True))


if __name__ == "__main__":
    main()
