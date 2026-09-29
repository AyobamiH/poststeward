#!/usr/bin/env python3
"""PostSteward installable client for humans and agents.

The client is intentionally thin: provider OAuth, workspace identity, scheduling,
publication effects and durable receipts remain authoritative in the hosted
PostSteward control plane. The CLI stores only a scoped agent token and origin.

Human flow:
    poststeward onboard

Agent flow:
    poststeward help --json
    poststeward invoke workspace_status '{}'
"""
from __future__ import annotations

import argparse
import getpass
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
from typing import Any

VERSION = "0.1.0"
DEFAULT_ORIGIN = os.environ.get("POSTSTEWARD_DEFAULT_ORIGIN", "https://poststeward.com")
PROVIDERS = ("x", "threads", "linkedin")


class CliError(RuntimeError):
    def __init__(self, code: str, message: str, status: int = 1) -> None:
        super().__init__(message)
        self.code = code
        self.status = status


def config_dir() -> Path:
    return Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "poststeward"


def config_path() -> Path:
    return config_dir() / "client.json"


def _private_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    try:
        path.chmod(0o700)
    except OSError:
        pass


def _read_config() -> dict[str, Any]:
    path = config_path()
    if not path.exists():
        return {"schema_version": 1, "origin": DEFAULT_ORIGIN}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise CliError("CONFIG_INVALID", f"Cannot read PostSteward client config: {exc}") from exc
    if not isinstance(value, dict) or value.get("schema_version") != 1:
        raise CliError("CONFIG_INVALID", "Unsupported PostSteward client config.")
    return value


def _write_config(value: dict[str, Any]) -> None:
    root = config_dir()
    _private_dir(root)
    target = config_path()
    tmp = target.with_name(target.name + ".tmp")
    data = json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    os.fchmod(fd, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        handle.write(data)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(tmp, target)
    try:
        target.chmod(0o600)
    except OSError:
        pass


def _origin(value: str | None, config: dict[str, Any]) -> str:
    candidate = str(value or config.get("origin") or DEFAULT_ORIGIN).strip().rstrip("/")
    try:
        parsed = urllib.parse.urlsplit(candidate)
    except ValueError as exc:
        raise CliError("ORIGIN_INVALID", "PostSteward origin is invalid.") from exc
    if parsed.scheme != "https" or not parsed.netloc or parsed.username or parsed.password:
        raise CliError("ORIGIN_INVALID", "PostSteward origin must be an HTTPS origin.")
    return urllib.parse.urlunsplit((parsed.scheme, parsed.netloc, "", "", ""))


def _token(config: dict[str, Any]) -> str:
    value = str(os.environ.get("POSTSTEWARD_TOKEN") or config.get("token") or "").strip()
    if not value:
        raise CliError(
            "AUTH_REQUIRED",
            "No scoped PostSteward agent token is configured. Run 'poststeward onboard' or 'poststeward login'.",
            3,
        )
    return value


def _request(
    origin: str,
    path: str,
    *,
    token: str | None = None,
    method: str = "GET",
    payload: Any | None = None,
    timeout: int = 30,
) -> Any:
    body = None
    headers = {
        "Accept": "application/json",
        "User-Agent": f"poststeward-cli/{VERSION}",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"
    if payload is not None:
        body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(origin + path, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read()
            content_type = response.headers.get("Content-Type", "")
            if "application/json" not in content_type:
                raise CliError("RESPONSE_INVALID", f"Expected JSON from {path}.")
            return json.loads(raw.decode("utf-8"))
    except urllib.error.HTTPError as exc:
        raw = exc.read()
        try:
            value = json.loads(raw.decode("utf-8"))
            error = value.get("error") if isinstance(value, dict) else None
            code = str((error or {}).get("code") or f"HTTP_{exc.code}")
            message = str((error or {}).get("message") or f"PostSteward returned HTTP {exc.code}.")
        except Exception:
            code = f"HTTP_{exc.code}"
            message = f"PostSteward returned HTTP {exc.code}."
        raise CliError(code, message, 4) from exc
    except urllib.error.URLError as exc:
        raise CliError("NETWORK_ERROR", f"Could not reach PostSteward: {exc.reason}", 5) from exc


def _invoke(origin: str, token: str, operation: str, payload: dict[str, Any]) -> Any:
    if not operation or "/" in operation or operation.startswith("."):
        raise CliError("OPERATION_INVALID", "Operation name is invalid.")
    return _request(
        origin,
        f"/api/operations/{urllib.parse.quote(operation, safe='_-')}",
        token=token,
        method="POST",
        payload=payload,
    )


def _print(value: Any, as_json: bool = True) -> None:
    if as_json:
        print(json.dumps(value, indent=2, ensure_ascii=False, sort_keys=True))
    else:
        print(value)


def _open(url: str) -> bool:
    try:
        return bool(webbrowser.open(url, new=2))
    except Exception:
        return False


def cmd_login(args: argparse.Namespace) -> None:
    config = _read_config()
    origin = _origin(args.origin, config)
    token = ""
    if args.token_file:
        source = Path(args.token_file).expanduser()
        if not source.is_file() or source.is_symlink():
            raise CliError("TOKEN_FILE_INVALID", "Token file must be a regular file.")
        token = source.read_text(encoding="utf-8").strip()
    elif args.token:
        token = args.token.strip()
    else:
        token = getpass.getpass("Scoped PostSteward agent token (hidden): ").strip()
    if len(token) < 16 or len(token) > 8192:
        raise CliError("TOKEN_INVALID", "Token is empty or outside the accepted size.")
    workspace = _invoke(origin, token, "workspace_status", {})
    _write_config({"schema_version": 1, "origin": origin, "token": token})
    print("PostSteward token verified and stored with user-only permissions.")
    if isinstance(workspace, dict):
        print(json.dumps(workspace, indent=2, ensure_ascii=False, sort_keys=True))


def cmd_logout(_args: argparse.Namespace) -> None:
    value = _read_config()
    value.pop("token", None)
    _write_config({"schema_version": 1, "origin": value.get("origin") or DEFAULT_ORIGIN})
    print("Local PostSteward token removed. Remote grants remain under owner control in the workspace.")


def cmd_onboard(args: argparse.Namespace) -> None:
    config = _read_config()
    origin = _origin(args.origin, config)
    app = origin + "/app?source=cli"
    print("PostSteward onboarding")
    print("")
    print("1. Sign in as the workspace owner.")
    print("2. Connect the social destinations you want: X, Threads and/or LinkedIn.")
    print("3. Verify the exact stable account/Page identity shown by PostSteward.")
    print("4. Create the smallest useful scoped, expiring agent grant.")
    print("5. Return here and paste the token when prompted.")
    print("")
    print(f"Workspace: {app}")
    opened = False if args.no_open else _open(app)
    if args.no_open or not opened:
        print("Open the workspace URL above in your browser.")
    if args.configure_only:
        return
    token = getpass.getpass("Scoped agent token (hidden; Ctrl+C to finish later): ").strip()
    if not token:
        raise CliError("AUTH_REQUIRED", "No token supplied. Run 'poststeward login' after creating a grant.", 3)
    verify_args = argparse.Namespace(origin=origin, token=token, token_file=None)
    cmd_login(verify_args)


def _auth_context(args: argparse.Namespace) -> tuple[str, str]:
    config = _read_config()
    return _origin(getattr(args, "origin", None), config), _token(config)


def cmd_status(args: argparse.Namespace) -> None:
    origin, token = _auth_context(args)
    workspace = _invoke(origin, token, "workspace_status", {})
    capabilities = _invoke(origin, token, "publishing_capabilities", {})
    accounts = _invoke(origin, token, "accounts_list", {})
    _print(
        {
            "schema_version": 1,
            "origin": origin,
            "workspace": workspace,
            "publishing": capabilities,
            "accounts": accounts,
        }
    )


def cmd_providers(args: argparse.Namespace) -> None:
    origin, token = _auth_context(args)
    accounts = _invoke(origin, token, "accounts_list", {})
    capabilities = _invoke(origin, token, "publishing_capabilities", {})
    rows = accounts if isinstance(accounts, list) else accounts.get("accounts", []) if isinstance(accounts, dict) else []
    by_provider = {provider: [] for provider in PROVIDERS}
    for row in rows:
        if isinstance(row, dict) and row.get("provider") in by_provider:
            by_provider[str(row["provider"])].append(
                {
                    "alias": row.get("alias"),
                    "active": row.get("active"),
                    "identity": row.get("identity"),
                    "capabilities": row.get("capabilities"),
                }
            )
    _print(
        {
            "schema_version": 1,
            "origin": origin,
            "providers": by_provider,
            "publishing_capabilities": capabilities,
        }
    )


def cmd_receipts(args: argparse.Namespace) -> None:
    origin, token = _auth_context(args)
    payload: dict[str, Any] = {"limit": args.limit}
    if args.before is not None:
        payload["before"] = args.before
    _print(_invoke(origin, token, "receipts_list", payload))


def _read_payload(args: argparse.Namespace) -> dict[str, Any]:
    sources = sum(bool(value) for value in (args.input, args.file, args.stdin))
    if sources > 1:
        raise CliError("INPUT_CONFLICT", "Use only one of --input, --file or --stdin.")
    raw = "{}"
    if args.input:
        raw = args.input
    elif args.file:
        raw = Path(args.file).expanduser().read_text(encoding="utf-8")
    elif args.stdin:
        raw = sys.stdin.read()
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise CliError("INPUT_INVALID_JSON", f"Operation input is not valid JSON: {exc}") from exc
    if not isinstance(value, dict):
        raise CliError("INPUT_INVALID", "Operation input must be one JSON object.")
    return value


def cmd_invoke(args: argparse.Namespace) -> None:
    origin, token = _auth_context(args)
    _print(_invoke(origin, token, args.operation, _read_payload(args)))


def cmd_help_json(_args: argparse.Namespace) -> None:
    print(
        json.dumps(
            {
                "schema_version": 1,
                "product": "poststeward",
                "version": VERSION,
                "authority_model": "human-owned provider OAuth and grants; agent-operable scoped client",
                "commands": {
                    "onboard": "Open the owner workspace, connect X/Threads/LinkedIn, then save a scoped agent token.",
                    "login": "Verify and store an existing scoped token.",
                    "status": "Inspect workspace, publishing capabilities and connected accounts.",
                    "providers": "Inspect X, Threads and LinkedIn connection evidence.",
                    "receipts": "Inspect durable delivery history.",
                    "invoke": "Call one operation-catalogue operation with a JSON object.",
                    "mcp": "Print remote MCP connection information without printing the stored token.",
                    "logout": "Remove the local token only.",
                },
                "safe_first_steps": [
                    "poststeward status",
                    "poststeward providers",
                    "poststeward receipts",
                    "poststeward invoke workspace_status '{}'",
                ],
                "external_effect_rule": "Inspect operation metadata and preserve idempotency keys before any consequential call.",
            },
            indent=2,
            ensure_ascii=False,
            sort_keys=True,
        )
    )


def cmd_mcp(args: argparse.Namespace) -> None:
    config = _read_config()
    origin = _origin(args.origin, config)
    configured = bool(str(os.environ.get("POSTSTEWARD_TOKEN") or config.get("token") or "").strip())
    _print(
        {
            "schema_version": 1,
            "url": origin + "/mcp",
            "authorization": "Bearer <scoped-agent-token>",
            "token_configured_locally": configured,
            "note": "The token is intentionally not printed. Revoke or replace it from the owner workspace.",
        }
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="poststeward",
        description="Installable PostSteward client for human-governed, agent-operable social publishing.",
    )
    parser.add_argument("--version", action="version", version=f"poststeward {VERSION}")
    sub = parser.add_subparsers(dest="command", required=True)

    onboard = sub.add_parser("onboard", help="Open guided owner onboarding for X, Threads and LinkedIn.")
    onboard.add_argument("--origin")
    onboard.add_argument("--no-open", action="store_true")
    onboard.add_argument("--configure-only", action="store_true")
    onboard.set_defaults(func=cmd_onboard)

    login = sub.add_parser("login", help="Verify and store a scoped PostSteward agent token.")
    login.add_argument("--origin")
    secret = login.add_mutually_exclusive_group()
    secret.add_argument("--token", help="Token value. Prefer the hidden prompt or a private token file.")
    secret.add_argument("--token-file")
    login.set_defaults(func=cmd_login)

    logout = sub.add_parser("logout", help="Remove the local token without changing the remote grant.")
    logout.set_defaults(func=cmd_logout)

    status = sub.add_parser("status", help="Inspect workspace and provider capability state.")
    status.add_argument("--origin")
    status.set_defaults(func=cmd_status)

    providers = sub.add_parser("providers", help="Inspect X, Threads and LinkedIn connection evidence.")
    providers.add_argument("--origin")
    providers.set_defaults(func=cmd_providers)

    receipts = sub.add_parser("receipts", help="Inspect durable publication/schedule receipts.")
    receipts.add_argument("--origin")
    receipts.add_argument("--limit", type=int, default=50)
    receipts.add_argument("--before", type=float)
    receipts.set_defaults(func=cmd_receipts)

    invoke = sub.add_parser("invoke", help="Invoke one documented PostSteward operation.")
    invoke.add_argument("operation")
    invoke.add_argument("input", nargs="?")
    source = invoke.add_mutually_exclusive_group()
    source.add_argument("--file")
    source.add_argument("--stdin", action="store_true")
    invoke.add_argument("--origin")
    invoke.set_defaults(func=cmd_invoke)

    mcp = sub.add_parser("mcp", help="Print remote MCP endpoint information.")
    mcp.add_argument("--origin")
    mcp.set_defaults(func=cmd_mcp)

    help_json = sub.add_parser("help", help="Machine-readable local client contract.")
    help_json.add_argument("--json", action="store_true")
    help_json.set_defaults(func=cmd_help_json)
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    try:
        args.func(args)
        return 0
    except KeyboardInterrupt:
        print("\nPostSteward operation cancelled locally.", file=sys.stderr)
        return 130
    except CliError as exc:
        print(f"PostSteward blocked [{exc.code}]: {exc}", file=sys.stderr)
        return exc.status
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        print(f"PostSteward blocked [LOCAL_IO_ERROR]: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
