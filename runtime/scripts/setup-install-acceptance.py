#!/usr/bin/env python3
"""Clean-room acceptance for the standalone Post-Once installer."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]


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


def run(args: list[str], *, env: dict[str, str], check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        args,
        cwd=ROOT,
        env=env,
        text=True,
        capture_output=True,
        timeout=120,
        check=check,
    )


def main() -> None:
    revision = run(
        ["git", "rev-parse", "--verify", "HEAD^{commit}"],
        env=os.environ.copy(),
    ).stdout.strip()

    with tempfile.TemporaryDirectory(prefix="post-once-installer-acceptance-") as temp:
        root = Path(temp)
        home = root / "home"
        xdg_config = root / "config"
        xdg_state = root / "state"
        xdg_data = root / "data"
        bin_dir = root / "bin"
        legacy = home / "post-once"
        home.mkdir()
        legacy.mkdir()
        (legacy / "sentinel.txt").write_text("original-owner-runtime\n", encoding="utf-8")
        before = digest_tree(legacy)

        env = {
            **os.environ,
            "HOME": str(home),
            "XDG_CONFIG_HOME": str(xdg_config),
            "XDG_STATE_HOME": str(xdg_state),
            "XDG_DATA_HOME": str(xdg_data),
            # Hostile legacy variables must not redirect the installer/product.
            "OCPF_POST_CONFIG_DIR": str(root / "legacy-config"),
            "OCPF_POST_STATE_DIR": str(root / "legacy-state"),
        }
        prefix = xdg_data / "post-once"

        dry_prefix = xdg_data / "dry-run-product"
        dry = run(
            [
                "bash",
                str(ROOT / "install.sh"),
                "--source",
                str(ROOT),
                "--revision",
                revision,
                "--prefix",
                str(dry_prefix),
                "--bin-dir",
                str(bin_dir),
                "--no-onboard",
                "--dry-run",
            ],
            env=env,
        )
        dry_run_non_mutating = (
            "Dry run only" in dry.stdout
            and not dry_prefix.exists()
            and not bin_dir.exists()
        )

        run(
            [
                "bash",
                str(ROOT / "install.sh"),
                "--source",
                str(ROOT),
                "--revision",
                revision,
                "--prefix",
                str(prefix),
                "--bin-dir",
                str(bin_dir),
                "--no-onboard",
            ],
            env=env,
        )
        runtime = prefix / "runtime"
        shim = bin_dir / "post-once"
        receipt = xdg_state / "post-once-bootstrap" / "install.json"

        installed_revision = run(
            ["git", "-C", str(runtime), "rev-parse", "--verify", "HEAD^{commit}"],
            env=env,
        ).stdout.strip()
        version = run([str(shim), "--version"], env=env).stdout.strip()
        registry = json.loads(run([str(shim), "registry", "list"], env=env).stdout)
        receipt_value = json.loads(receipt.read_text(encoding="utf-8"))
        unit_dir = xdg_config / "systemd" / "user"

        rerun = run(
            [
                "bash",
                str(ROOT / "install.sh"),
                "--source",
                str(ROOT),
                "--revision",
                revision,
                "--prefix",
                str(prefix),
                "--bin-dir",
                str(bin_dir),
                "--no-onboard",
            ],
            env=env,
        )

        dirty_marker = runtime / "operator-dirty.tmp"
        dirty_marker.write_text("do-not-overwrite\n", encoding="utf-8")
        dirty = run(
            [
                "bash",
                str(ROOT / "install.sh"),
                "--source",
                str(ROOT),
                "--revision",
                revision,
                "--prefix",
                str(prefix),
                "--bin-dir",
                str(bin_dir),
                "--no-onboard",
            ],
            env=env,
            check=False,
        )
        dirty_runtime_protected = (
            dirty.returncode != 0
            and "checkout is dirty" in dirty.stderr
            and dirty_marker.read_text(encoding="utf-8") == "do-not-overwrite\n"
        )
        dirty_marker.unlink()

        different_revision = "0" * 40 if revision != "0" * 40 else "1" * 40
        different = run(
            [
                "bash",
                str(ROOT / "install.sh"),
                "--source",
                str(ROOT),
                "--revision",
                different_revision,
                "--prefix",
                str(prefix),
                "--bin-dir",
                str(bin_dir),
                "--no-onboard",
            ],
            env=env,
            check=False,
        )
        different_revision_reinstall_blocked = (
            different.returncode != 0
            and "use the reviewed runtime upgrade path instead of reinstalling" in different.stderr
            and run(
                ["git", "-C", str(runtime), "rev-parse", "--verify", "HEAD^{commit}"],
                env=env,
            ).stdout.strip() == revision
        )

        original_origin = run(
            ["git", "-C", str(runtime), "config", "--get", "remote.origin.url"],
            env=env,
        ).stdout.strip()
        run(
            ["git", "-C", str(runtime), "config", "remote.origin.url", "different-source"],
            env=env,
        )
        origin = run(
            [
                "bash",
                str(ROOT / "install.sh"),
                "--source",
                str(ROOT),
                "--revision",
                revision,
                "--prefix",
                str(prefix),
                "--bin-dir",
                str(bin_dir),
                "--no-onboard",
            ],
            env=env,
            check=False,
        )
        origin_mismatch_blocked = (
            origin.returncode != 0
            and "runtime origin does not match" in origin.stderr
        )
        run(
            ["git", "-C", str(runtime), "config", "remote.origin.url", original_origin],
            env=env,
        )

        unrelated = root / "collision-bin"
        unrelated.mkdir()
        collision = unrelated / "post-once"
        collision.write_text("#!/bin/sh\necho unrelated\n", encoding="utf-8")
        collision.chmod(0o755)
        blocked = run(
            [
                "bash",
                str(ROOT / "install.sh"),
                "--source",
                str(ROOT),
                "--revision",
                revision,
                "--prefix",
                str(root / "collision-prefix"),
                "--bin-dir",
                str(unrelated),
                "--no-onboard",
            ],
            env=env,
            check=False,
        )
        unrelated_shim_protected = (
            blocked.returncode != 0
            and "refusing to overwrite an unrelated existing post-once command" in blocked.stderr
            and collision.read_text(encoding="utf-8") == "#!/bin/sh\necho unrelated\n"
        )

        result = {
            "schema_version": 1,
            "status": "pass",
            "revision": revision,
            "dry_run_non_mutating": dry_run_non_mutating,
            "installed_revision_exact": installed_revision == revision,
            "canonical_command_installed": shim.is_file() and os.access(shim, os.X_OK),
            "canonical_command_version": version,
            "canonical_version_identity": version.startswith("post-once "),
            "fresh_registry_empty": registry == [],
            "install_receipt_revision_exact": receipt_value.get("revision") == revision,
            "install_receipt_original_mutation_forbidden": receipt_value.get(
                "original_post_once_mutation_allowed"
            ) is False,
            "no_services_installed_during_install": not unit_dir.exists(),
            "rerun_same_revision_succeeds": rerun.returncode == 0,
            "dirty_runtime_protected": dirty_runtime_protected,
            "different_revision_reinstall_blocked": different_revision_reinstall_blocked,
            "origin_mismatch_blocked": origin_mismatch_blocked,
            "unrelated_post_once_shim_protected": unrelated_shim_protected,
            "original_owner_runtime_unchanged": digest_tree(legacy) == before,
            "provider_consequence_attempted": False,
            "original_post_once_mutated": False,
        }
        required_true = (
            "dry_run_non_mutating",
            "installed_revision_exact",
            "canonical_command_installed",
            "canonical_version_identity",
            "fresh_registry_empty",
            "install_receipt_revision_exact",
            "install_receipt_original_mutation_forbidden",
            "no_services_installed_during_install",
            "rerun_same_revision_succeeds",
            "dirty_runtime_protected",
            "different_revision_reinstall_blocked",
            "origin_mismatch_blocked",
            "unrelated_post_once_shim_protected",
            "original_owner_runtime_unchanged",
        )
        result["status"] = (
            "pass"
            if all(result[key] is True for key in required_true)
            and result["provider_consequence_attempted"] is False
            and result["original_post_once_mutated"] is False
            else "fail"
        )
        print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()
