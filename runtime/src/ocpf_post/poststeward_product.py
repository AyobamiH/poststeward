"""Product-level lifecycle UX for the installed PostSteward runtime.

These commands compose the proven A-K Setup/Recovery engine with PostSteward Cloud.
They do not create a second provider authority model.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request
from typing import Any

from ocpf_post import __version__, automation_authority
from ocpf_post.poststeward_cloud import CloudError, bindings, runtime_context
from ocpf_post.product_runtime import PRODUCT_LINEAGE, resolved_paths
from ocpf_post.setup_activation import SystemdServiceController
from ocpf_post.setup_engine import SetupEngine, SetupEngineError
from ocpf_post.setup_store import SetupStoreError


class ProductLifecycleError(RuntimeError):
    def __init__(self, code: str, message: str, status: int = 3) -> None:
        super().__init__(message)
        self.code = code
        self.status = status


def _runtime_root() -> Path:
    value = str(os.environ.get("POSTSTEWARD_RUNTIME_ROOT") or "").strip()
    if not value:
        raise ProductLifecycleError(
            "RUNTIME_ROOT_UNAVAILABLE",
            "PostSteward runtime root is unavailable.",
        )
    root = Path(value).expanduser()
    if not root.is_dir() or root.is_symlink():
        raise ProductLifecycleError(
            "RUNTIME_ROOT_INVALID",
            "PostSteward runtime root is not a plain directory.",
        )
    return root


def _provenance(root: Path) -> dict[str, Any] | None:
    path = root / "POSTSTEWARD_RUNTIME_PROVENANCE.json"
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def _services() -> dict[str, Any]:
    from ocpf_post.host_platform import service_controller
    if os.name != "posix":
        return {
            "schema_version": 1,
            "status": "unavailable",
            "reason": "systemd_user_manager_not_available",
        }
    try:
        return {
            "schema_version": 1,
            "status": "observed",
            **service_controller().inspect(),
        }
    except Exception as exc:
        return {
            "schema_version": 1,
            "status": "unavailable",
            "reason": type(exc).__name__,
        }


def _setup_status() -> dict[str, Any] | None:
    try:
        return SetupEngine().status()
    except (SetupEngineError, SetupStoreError):
        return None


def runtime_status() -> dict[str, Any]:
    root = _runtime_root()
    paths = resolved_paths()
    marker = automation_authority.read(paths["state"])
    setup = _setup_status()
    cloud: dict[str, Any]
    try:
        cloud = bindings()
    except CloudError as exc:
        cloud = {
            "status": "unavailable",
            "code": exc.code,
            "message": str(exc),
            "context": runtime_context(),
        }
    except OSError as exc:
        cloud = {
            "status": "unavailable",
            "code": type(exc).__name__,
            "message": "Cloud coordination could not be reached.",
            "context": runtime_context(),
        }

    return {
        "schema_version": 1,
        "product": "poststeward",
        "runtime_version": __version__,
        "product_lineage": PRODUCT_LINEAGE,
        "release_sha": os.environ.get("POSTSTEWARD_RUNTIME_RELEASE_SHA"),
        "runtime_root": str(root),
        "provenance": _provenance(root),
        "paths": {name: str(path) for name, path in paths.items()},
        "automation_authority": {
            key: value for key, value in marker.items() if key != "path"
        },
        "services": _services(),
        "host": __import__('ocpf_post.host_platform', fromlist=['host']).host(),
        "setup": setup,
        "cloud": cloud,
        "provider_credentials_local": False,
        "original_post_once_mutation_allowed": False,
    }


def doctor() -> dict[str, Any]:
    status = runtime_status()
    findings: list[dict[str, str]] = []

    def finding(level: str, code: str, message: str) -> None:
        findings.append({"level": level, "code": code, "message": message})

    if sys.version_info < (3, 10):
        finding("blocker", "python.unsupported", "Python 3.10 or newer is required.")
    if status.get("provenance") is None:
        finding("blocker", "runtime.provenance_missing", "Embedded runtime provenance is unavailable.")

    paths = resolved_paths()
    for name in ("config", "state", "setup"):
        path = paths[name]
        if path.exists() and path.is_symlink():
            finding("blocker", f"path.{name}.symlink", f"{name} path must not be a symlink.")

    cloud = status.get("cloud") if isinstance(status.get("cloud"), dict) else {}
    if cloud.get("status") == "unavailable":
        finding(
            "attention",
            "cloud.unavailable",
            "PostSteward Cloud is unavailable or this runtime is not paired.",
        )
    else:
        executor = cloud.get("executor") if isinstance(cloud.get("executor"), dict) else {}
        marker = status.get("automation_authority") or {}
        if marker.get("status") == "active":
            if not (
                executor.get("executorMode") == "local"
                and executor.get("executorStatus", "active") == "active"
                and executor.get("activeInstallationId")
                == cloud.get("installationId")
            ):
                finding(
                    "blocker",
                    "authority.cloud_local_mismatch",
                    "Local automation is active but cloud executor authority does not match this installation.",
                )
        if (
            marker.get("status") != "active"
            and executor.get("executorMode") == "local"
            and executor.get("executorStatus", "active") == "active"
        ):
            finding(
                "attention",
                "authority.cloud_active_local_marker_inactive",
                "Cloud has handed local executor authority to this machine but local unattended automation is still inactive.",
            )

        accounts = cloud.get("accounts") if isinstance(cloud.get("accounts"), list) else []
        if not accounts:
            finding(
                "attention",
                "providers.none_connected",
                "No hosted X, Threads or LinkedIn destination is connected.",
            )

    services = status.get("services") if isinstance(status.get("services"), dict) else {}
    marker = status.get("automation_authority") or {}
    if marker.get("status") == "active" and services.get("status") == "observed":
        if not services.get("all_enabled") or not services.get("all_active"):
            finding(
                "blocker",
                "automation.service_drift",
                "Local automation marker is active but one or more PostSteward timers are not active/enabled.",
            )

    overall = (
        "BLOCKED"
        if any(row["level"] == "blocker" for row in findings)
        else "ATTENTION"
        if findings
        else "READY"
    )
    return {
        "schema_version": 1,
        "status": overall,
        "findings": findings,
        "runtime": status,
        "boundary":
            "Read-only product diagnosis. No provider, schedule, executor or local authority mutation occurs.",
    }


def activation_preview() -> dict[str, Any]:
    paths = resolved_paths()
    return SetupEngine().activation_preview(
        runtime_root=_runtime_root(),
        state_root=paths["state"],
        config_root=paths["config"],
    )


def activate(expected_sha256: str) -> dict[str, Any]:
    paths = resolved_paths()
    return SetupEngine().activate(
        runtime_root=_runtime_root(),
        state_root=paths["state"],
        config_root=paths["config"],
        expected_sha256=expected_sha256,
    )


def deactivation_preview(reason: str) -> dict[str, Any]:
    return SetupEngine().deactivation_preview(
        runtime_root=_runtime_root(),
        state_root=resolved_paths()["state"],
        reason=reason,
    )


def deactivate(reason: str, expected_sha256: str) -> dict[str, Any]:
    return SetupEngine().deactivate(
        runtime_root=_runtime_root(),
        state_root=resolved_paths()["state"],
        reason=reason,
        expected_sha256=expected_sha256,
    )


def update(channel: str, *, dry_run: bool = False) -> int:
    if channel not in {"stable", "beta"}:
        raise ProductLifecycleError(
            "UPDATE_CHANNEL_INVALID",
            "Update channel must be stable or beta.",
        )
    marker = automation_authority.read(resolved_paths()["state"])
    if marker.get("status") == "active":
        raise ProductLifecycleError(
            "UPDATE_ACTIVE_RUNTIME",
            "Deactivate PostSteward before changing the installed runtime release.",
        )

    origin = str(
        os.environ.get("POSTSTEWARD_ORIGIN") or "https://poststeward.com"
    ).rstrip("/")
    url = origin + "/install.sh"
    request = urllib.request.Request(
        url,
        headers={"User-Agent": f"poststeward-runtime/{__version__}"},
        method="GET",
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            if response.geturl() != url:
                raise ProductLifecycleError(
                    "UPDATE_INSTALLER_REDIRECT",
                    "Installer URL redirected; update refused.",
                )
            payload = response.read(1_048_577)
    except urllib.error.URLError as exc:
        raise ProductLifecycleError(
            "UPDATE_INSTALLER_UNAVAILABLE",
            "Could not retrieve the PostSteward installer.",
        ) from exc
    if len(payload) > 1_048_576:
        raise ProductLifecycleError(
            "UPDATE_INSTALLER_OVERSIZE",
            "Installer exceeded the local size boundary.",
        )
    if not payload.startswith(b"#!/usr/bin/env bash"):
        raise ProductLifecycleError(
            "UPDATE_INSTALLER_INVALID",
            "Downloaded installer did not match the expected script identity.",
        )

    with tempfile.TemporaryDirectory(prefix="poststeward-update-") as temp:
        installer = Path(temp) / "install.sh"
        installer.write_bytes(payload)
        installer.chmod(0o700)
        args = [
            "bash",
            str(installer),
            "--version",
            channel,
            "--no-onboard",
        ]
        if dry_run:
            args.append("--dry-run")
        result = subprocess.run(args, check=False)
        return int(result.returncode)


def _emit(value: dict[str, Any], as_json: bool) -> None:
    if as_json:
        print(json.dumps(value, indent=2, ensure_ascii=False))
        return
    if value.get("product") == "poststeward":
        print("PostSteward runtime")
    print(f"Status: {value.get('status', 'unknown')}")
    if "findings" in value:
        for row in value.get("findings", []):
            print(f"{str(row.get('level')).upper()}: {row.get('code')} — {row.get('message')}")
    elif value.get("review_sha256"):
        print(f"Review SHA-256: {value['review_sha256']}")
        print(value.get("boundary", ""))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="poststeward")
    sub = parser.add_subparsers(dest="command", required=True)

    status_parser = sub.add_parser("status")
    status_parser.add_argument("--json", action="store_true")

    doctor_parser = sub.add_parser("doctor")
    doctor_parser.add_argument("--json", action="store_true")

    activate_parser = sub.add_parser("activate")
    activate_parser.add_argument("--apply", action="store_true")
    activate_parser.add_argument("--expected-sha256")
    activate_parser.add_argument("--json", action="store_true")

    deactivate_parser = sub.add_parser("deactivate")
    deactivate_parser.add_argument("--reason", required=True)
    deactivate_parser.add_argument("--apply", action="store_true")
    deactivate_parser.add_argument("--expected-sha256")
    deactivate_parser.add_argument("--json", action="store_true")

    update_parser = sub.add_parser("update")
    update_parser.add_argument("--channel", choices=["stable", "beta"], default="stable")
    update_parser.add_argument("--dry-run", action="store_true")

    for name in ('rollback', 'uninstall'):
        lifecycle_parser = sub.add_parser(name)
        lifecycle_parser.add_argument('--apply', action='store_true')
        lifecycle_parser.add_argument('--expected-sha256')
        lifecycle_parser.add_argument('--json', action='store_true')
        if name == 'uninstall':
            data = lifecycle_parser.add_mutually_exclusive_group(required=True)
            data.add_argument('--retain-data', action='store_true')
            data.add_argument('--delete-data', action='store_true')

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "status":
            _emit(runtime_status(), args.json)
            return 0
        if args.command == "doctor":
            value = doctor()
            _emit(value, args.json)
            return 0 if value["status"] != "BLOCKED" else 3
        if args.command == "activate":
            if args.apply:
                if not args.expected_sha256:
                    raise ProductLifecycleError(
                        "ACTIVATION_REVIEW_REQUIRED",
                        "Apply requires --expected-sha256 from the exact activation preview.",
                    )
                value = activate(args.expected_sha256)
            else:
                value = activation_preview()
            _emit(value, args.json)
            return 0
        if args.command == "deactivate":
            if args.apply:
                if not args.expected_sha256:
                    raise ProductLifecycleError(
                        "DEACTIVATION_REVIEW_REQUIRED",
                        "Apply requires --expected-sha256 from the exact deactivation preview.",
                    )
                value = deactivate(args.reason, args.expected_sha256)
            else:
                value = deactivation_preview(args.reason)
            _emit(value, args.json)
            return 0
        if args.command == "update":
            return update(args.channel, dry_run=args.dry_run)
        if args.command in {'rollback', 'uninstall'}:
            from ocpf_post.installed_lifecycle import lifecycle
            _emit(lifecycle(args.command, retain_data=not getattr(args, 'delete_data', False),
                            apply=args.apply, expected_sha256=args.expected_sha256), args.json)
            return 0
    except (
        ProductLifecycleError,
        CloudError,
        SetupEngineError,
        SetupStoreError,
        OSError,
        ValueError,
        subprocess.SubprocessError,
    ) as exc:
        code = getattr(exc, "code", "POSTSTEWARD_LIFECYCLE_BLOCKED")
        if getattr(args, "json", False):
            print(
                json.dumps(
                    {
                        "schema_version": 1,
                        "status": "blocked",
                        "code": code,
                        "message": str(exc),
                    }
                )
            )
        else:
            print(f"PostSteward blocked [{code}]: {exc}", file=sys.stderr)
        return int(getattr(exc, "status", 3) or 3)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
