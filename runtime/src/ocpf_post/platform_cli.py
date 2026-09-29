"""Platform-level capability, state and runtime lifecycle commands."""
from __future__ import annotations

import json
import os
import subprocess
import sys

from ocpf_post.runtime_attestation import attest, runtime_root


def _print(value):
    print(json.dumps(value, indent=2, ensure_ascii=False, allow_nan=False))


def _run_script(name: str) -> dict:
    root = runtime_root()
    path = root / "scripts" / name
    if not path.exists():
        return {"status": "unavailable", "script": name, "error_type": "MissingScript"}
    command = [sys.executable, str(path)] if path.suffix == ".py" else ["sh", str(path)]
    try:
        result = subprocess.run(command, cwd=root, capture_output=True, text=True, timeout=900, check=False)
    except (OSError, subprocess.SubprocessError) as exc:
        return {"status": "unavailable", "script": name, "error_type": type(exc).__name__}
    return {"status": "completed" if result.returncode == 0 else "attention", "script": name, "exit_code": result.returncode}


def cmd_capabilities(_args):
    from ocpf_post.capabilities import report
    _print(report())


def cmd_work_status(args):
    from ocpf_post.work_status import build, render_text
    value = build()
    if getattr(args, "json", False):
        _print(value)
    else:
        print(render_text(value))


def _origin(env_names, file_path, *, provider=None):
    if any(os.environ.get(name) for name in env_names):
        return "environment"
    if provider is not None:
        from ocpf_post.credential_keyring import is_managed
        from ocpf_post.state import config_dir
        if is_managed(provider, config_dir()):
            return "os_keyring"
    if file_path.exists():
        return "private_file"
    return "missing"


def cmd_config_show(_args):
    from ocpf_post.state import config_dir, provider_token_file, state_dir
    from ocpf_post.portfolio import policy_file
    from ocpf_post.admission import policy_file as admission_policy_file
    value = {
        "schema_version": 1,
        "config_dir": str(config_dir()),
        "state_dir": str(state_dir()),
        "overrides": {
            "config_dir": "environment" if os.environ.get("OCPF_POST_CONFIG_DIR") else "xdg_default",
            "state_dir": "environment" if os.environ.get("OCPF_POST_STATE_DIR") else "xdg_default",
        },
        "providers": {
            "x": {"credential_origin": _origin(("X_USER_ACCESS_TOKEN", "X_OAUTH2_ACCESS_TOKEN"), provider_token_file("x"), provider="x")},
            "threads": {"credential_origin": _origin(("THREADS_ACCESS_TOKEN",), provider_token_file("threads"), provider="threads")},
            "linkedin": {"credential_origin": _origin(("LINKEDIN_ACCESS_TOKEN", "LINKEDIN_TOKEN"), provider_token_file("linkedin"), provider="linkedin")},
        },
        "policies": {
            "portfolio": "private_file" if policy_file().exists() else "built_in_default",
            "admission": "private_file" if admission_policy_file().exists() else "built_in_default",
            "accounts": "private_file" if (config_dir() / "account-profiles.json").exists() else "not_configured",
            "vaults": "private_file" if (config_dir() / "vaults.json").exists() else "not_configured",
            "reply_worker": "private_file" if (config_dir() / "reply-worker-policy.json").exists() else "built_in_default",
            "outcome_connectors": "private_file" if (config_dir() / "outcome-connectors.json").exists() else "not_configured",
            "alert_delivery": "private_file" if (config_dir() / "alert-delivery.json").exists() else "not_configured",
        },
        "boundary": "Configuration provenance only. Secret values, token scopes and credential contents are never returned.",
    }
    _print(value)


def cmd_state_inventory(_args):
    from ocpf_post.state_registry import inventory
    _print(inventory())


def cmd_state_verify(_args):
    from ocpf_post.state_registry import verify
    value = verify()
    _print(value)
    if value["status"] == "attention":
        raise SystemExit(3)


def cmd_state_archive(args):
    from ocpf_post.state_registry import archive
    value = archive(args.output, apply=args.apply)
    _print(value)
    if value["status"] == "blocked":
        raise SystemExit(3)


def cmd_state_migrate(args):
    from ocpf_post.state_registry import migrate
    value = migrate(apply=args.apply)
    _print(value)
    if value["status"] == "blocked":
        raise SystemExit(3)


def cmd_state_segment(args):
    from ocpf_post.state_registry import segment
    value = segment(
        args.ledger,
        keep_lines=args.keep_lines,
        apply=args.apply,
        expected_sha256=args.expected_sha256,
    )
    _print(value)
    if value.get("status") == "blocked":
        raise SystemExit(3)


def cmd_state_segment_recover(args):
    from ocpf_post.state_registry import segment_recover
    value = segment_recover(args.ledger, apply=args.apply)
    _print(value)
    if value.get("status") == "blocked":
        raise SystemExit(3)


def cmd_keyring_status(_args):
    from ocpf_post.credential_keyring import status
    from ocpf_post.state import config_dir
    _print(status(config_dir()))


def cmd_keyring_migrate(args):
    from ocpf_post.credential_keyring import migrate_provider
    from ocpf_post.state import config_dir, provider_token_file
    value = migrate_provider(
        args.provider,
        provider_token_file(args.provider),
        config_dir(),
        apply=args.apply,
        expected_sha256=args.expected_sha256,
    )
    _print(value)
    if value.get("status") == "blocked":
        raise SystemExit(3)


def cmd_keyring_restore(args):
    from ocpf_post.credential_keyring import restore_provider
    from ocpf_post.state import config_dir, provider_token_file
    value = restore_provider(
        args.provider,
        provider_token_file(args.provider),
        config_dir(),
        apply=args.apply,
        expected_sha256=args.expected_sha256,
    )
    _print(value)
    if value.get("status") == "blocked":
        raise SystemExit(3)


def cmd_runtime_status(_args):
    from ocpf_post.capabilities import report as capability_report
    from ocpf_post.runtime_release import status as release_status
    from ocpf_post.state_registry import verify
    _print({
        "schema_version": 1,
        "attestation": attest(),
        "release": release_status(),
        "state": verify(),
        "capabilities": capability_report(),
        "boundary": "Read-only runtime status. No Git update, timer change, provider call or credential refresh.",
    })


def _mutation(args, scripts):
    preview = {
        "schema_version": 1, "status": "preview", "scripts": list(scripts),
        "boundary": "Runtime unit mutation only; no social publish or external-effect retry.",
    }
    if not args.apply:
        _print(preview)
        return
    results = [_run_script(name) for name in scripts]
    value = {**preview, "status": "completed" if all(row["status"] == "completed" for row in results) else "attention", "results": results}
    _print(value)
    if value["status"] == "attention":
        raise SystemExit(3)


def cmd_runtime_install(args):
    _mutation(args, ("install-user-portfolio-timer", "install-user-console"))


def cmd_runtime_uninstall(args):
    _mutation(args, ("uninstall-user-console", "uninstall-user-portfolio-timer", "uninstall-user-scheduler-timer"))


def cmd_runtime_reconcile(args):
    _mutation(args, ("restore-local-runtime.py",))


def cmd_runtime_upgrade(args):
    from ocpf_post.runtime_release import switch
    value = switch(args.revision, apply=args.apply, expected_sha256=args.expected_sha256)
    _print(value)
    if value.get("status") == "blocked":
        raise SystemExit(3)


def cmd_runtime_rollback(args):
    from ocpf_post.runtime_release import rollback
    value = rollback(apply=args.apply, expected_sha256=args.expected_sha256)
    _print(value)
    if value.get("status") == "blocked":
        raise SystemExit(3)


def add_parsers(sub):
    capabilities = sub.add_parser("capabilities", help="Show implementation/configuration/authority/evidence/acceptance state")
    capabilities.set_defaults(func=cmd_capabilities)

    work = sub.add_parser("work", help="Inspect the generated current-work backlog")
    work_sub = work.add_subparsers(dest="work_command", required=True)
    work_status = work_sub.add_parser("status", help="Show current blockers, deadlines, dependencies and safe next actions")
    work_status.add_argument("--json", action="store_true")
    work_status.set_defaults(func=cmd_work_status)

    config = sub.add_parser("config", help="Inspect effective configuration provenance without secret values")
    config_sub = config.add_subparsers(dest="config_command", required=True)
    config_show = config_sub.add_parser("show", help="Show effective config origins and policy presence")
    config_show.set_defaults(func=cmd_config_show)

    credentials = sub.add_parser("credentials", help="Inspect or migrate provider credential storage backends")
    credentials_sub = credentials.add_subparsers(dest="credentials_command", required=True)
    keyring = credentials_sub.add_parser("keyring", help="Manage optional OS keyring/keychain storage for provider tokens")
    keyring_sub = keyring.add_subparsers(dest="keyring_command", required=True)
    keyring_status = keyring_sub.add_parser("status", help="Inspect keyring backend and provider presence without secret values")
    keyring_status.set_defaults(func=cmd_keyring_status)
    keyring_migrate = keyring_sub.add_parser("migrate", help="Preview or migrate one provider token from private file to OS keyring")
    keyring_migrate.add_argument("--provider", required=True, choices=("x", "threads", "linkedin"))
    keyring_migrate.add_argument("--expected-sha256")
    keyring_migrate.add_argument("--apply", action="store_true")
    keyring_migrate.set_defaults(func=cmd_keyring_migrate)
    keyring_restore = keyring_sub.add_parser("restore", help="Preview or restore one OS-keyring token to a private file")
    keyring_restore.add_argument("--provider", required=True, choices=("x", "threads", "linkedin"))
    keyring_restore.add_argument("--expected-sha256")
    keyring_restore.add_argument("--apply", action="store_true")
    keyring_restore.set_defaults(func=cmd_keyring_restore)

    state = sub.add_parser("state", help="Inventory, verify and explicitly migrate local state")
    state_sub = state.add_subparsers(dest="state_command", required=True)
    inventory = state_sub.add_parser("inventory", help="Fingerprint structured and opaque local files without exposing contents")
    inventory.set_defaults(func=cmd_state_inventory)
    verify = state_sub.add_parser("verify", help="Validate structured state and fail closed on corrupt durable ledgers")
    verify.set_defaults(func=cmd_state_verify)
    archive_cmd = state_sub.add_parser("archive", help="Create a consistent state-evidence archive; credentials are excluded")
    archive_cmd.add_argument("--output", required=True)
    archive_cmd.add_argument("--apply", action="store_true")
    archive_cmd.set_defaults(func=cmd_state_archive)
    migrate = state_sub.add_parser("migrate", help="Preview explicit schema migrations; never repairs corrupt evidence")
    migrate.add_argument("--apply", action="store_true")
    migrate.set_defaults(func=cmd_state_migrate)
    segment = state_sub.add_parser("segment", help="Preview or segment a large critical ledger while preserving logical order")
    segment.add_argument("--ledger", required=True, choices=("receipts", "schedules", "performance"))
    segment.add_argument("--keep-lines", type=int, default=50000)
    segment.add_argument("--expected-sha256")
    segment.add_argument("--apply", action="store_true")
    segment.set_defaults(func=cmd_state_segment)
    recover_segment = state_sub.add_parser("recover-segment", help="Preview or recover an interrupted ledger segmentation transaction")
    recover_segment.add_argument("--ledger", required=True, choices=("receipts", "schedules", "performance"))
    recover_segment.add_argument("--apply", action="store_true")
    recover_segment.set_defaults(func=cmd_state_segment_recover)

    runtime = sub.add_parser("runtime", help="Inspect or reconcile the local Post-Once runtime lifecycle")
    runtime_sub = runtime.add_subparsers(dest="runtime_command", required=True)
    status = runtime_sub.add_parser("status", help="Attest checkout, state and capability readiness")
    status.set_defaults(func=cmd_runtime_status)
    install = runtime_sub.add_parser("install", help="Install/reconcile automation and read-only console user units")
    install.add_argument("--apply", action="store_true")
    install.set_defaults(func=cmd_runtime_install)
    uninstall = runtime_sub.add_parser("uninstall", help="Remove Post-Once user units without deleting evidence")
    uninstall.add_argument("--apply", action="store_true")
    uninstall.set_defaults(func=cmd_runtime_uninstall)
    reconcile = runtime_sub.add_parser("reconcile", help="Safely restore unit paths to the current clean checkout")
    reconcile.add_argument("--apply", action="store_true")
    reconcile.set_defaults(func=cmd_runtime_reconcile)
    upgrade = runtime_sub.add_parser("upgrade", help="Preview or switch to one exact locally available Git revision")
    upgrade.add_argument("--revision", required=True, help="Exact/ref-resolvable commit already present in the local repository")
    upgrade.add_argument("--expected-sha256")
    upgrade.add_argument("--apply", action="store_true")
    upgrade.set_defaults(func=cmd_runtime_upgrade)
    rollback = runtime_sub.add_parser("rollback", help="Preview or switch software back to the recorded previous revision")
    rollback.add_argument("--expected-sha256")
    rollback.add_argument("--apply", action="store_true")
    rollback.set_defaults(func=cmd_runtime_rollback)
