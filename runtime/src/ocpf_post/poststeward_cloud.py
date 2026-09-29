"""PostSteward cloud coordination for the embedded local runtime.

Provider credentials never enter this module. The local runtime stores only its
installation identity and the scoped runtime token returned after explicit owner
pairing.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import platform
import socket
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
import webbrowser
from typing import Any

DEFAULT_ORIGIN = "https://poststeward.com"


class CloudError(RuntimeError):
    def __init__(self, code: str, message: str, status: int = 1) -> None:
        super().__init__(message)
        self.code = code
        self.status = status


def _config_home() -> Path:
    return Path(os.environ.get("XDG_CONFIG_HOME") or (Path.home() / ".config")).expanduser()


def client_path() -> Path:
    return _config_home() / "poststeward" / "client.json"


def _read() -> dict[str, Any]:
    path = client_path()
    if not path.exists():
        return {"schema_version": 1}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise CloudError("CLIENT_CONFIG_INVALID", f"Cannot read PostSteward client state: {exc}") from exc
    if not isinstance(value, dict) or value.get("schema_version") != 1:
        raise CloudError("CLIENT_CONFIG_INVALID", "Unsupported PostSteward client state.")
    return value


def _write(value: dict[str, Any]) -> None:
    path = client_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.parent.chmod(0o700)
    tmp = path.with_name(path.name + ".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    os.fchmod(fd, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        json.dump(value, handle, sort_keys=True, separators=(",", ":"))
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(tmp, path)
    path.chmod(0o600)


def origin() -> str:
    value = str(
        os.environ.get("POSTSTEWARD_ORIGIN")
        or _read().get("origin")
        or DEFAULT_ORIGIN
    ).strip().rstrip("/")
    parsed = urllib.parse.urlsplit(value)
    if parsed.scheme != "https" or not parsed.netloc or parsed.username or parsed.password:
        raise CloudError("ORIGIN_INVALID", "PostSteward origin must be an HTTPS origin.")
    return urllib.parse.urlunsplit((parsed.scheme, parsed.netloc, "", "", ""))


def _request(
    method: str,
    path: str,
    *,
    payload: dict[str, Any] | None = None,
    token: str | None = None,
    timeout: float = 30,
) -> tuple[int, dict[str, Any]]:
    headers = {
        "Accept": "application/json",
        "User-Agent": "poststeward-local-runtime",
    }
    body = None
    if payload is not None:
        body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        headers["Content-Type"] = "application/json"
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = urllib.request.Request(origin() + path, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read()
            value = json.loads(raw.decode("utf-8")) if raw else {}
            if not isinstance(value, dict):
                raise CloudError("RESPONSE_INVALID", "PostSteward returned non-object JSON.")
            return response.status, value
    except urllib.error.HTTPError as exc:
        raw = exc.read()
        try:
            value = json.loads(raw.decode("utf-8"))
            error = value.get("error") if isinstance(value, dict) else {}
            code = str((error or {}).get("code") or f"HTTP_{exc.code}")
            message = str((error or {}).get("message") or f"PostSteward returned HTTP {exc.code}.")
        except Exception:
            code = f"HTTP_{exc.code}"
            message = f"PostSteward returned HTTP {exc.code}."
        raise CloudError(code, message, exc.code) from exc
    except urllib.error.URLError as exc:
        raise CloudError("NETWORK_ERROR", f"Could not reach PostSteward: {exc.reason}") from exc


def _runtime_version() -> str:
    from ocpf_post import __version__

    return __version__


def _source_revision() -> str | None:
    root = os.environ.get("POSTSTEWARD_RUNTIME_ROOT")
    if not root:
        return None
    path = Path(root) / "POSTSTEWARD_RUNTIME_PROVENANCE.json"
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return None
    candidate = value.get("source_revision")
    return str(candidate) if candidate else None


def installation_identity() -> dict[str, Any]:
    value = _read()
    installation = value.get("installation_id")
    if not isinstance(installation, str):
        installation = str(uuid.uuid4())
        value.update(
            {
                "schema_version": 1,
                "installation_id": installation,
                "label": socket.gethostname()[:120] or "PostSteward runtime",
                "platform": f"{platform.system()} {platform.release()} {platform.machine()}"[:160],
                "origin": origin(),
            }
        )
        _write(value)
    return value


def runtime_token() -> str:
    value = _read()
    token = str(os.environ.get("POSTSTEWARD_RUNTIME_TOKEN") or value.get("runtime_token") or "").strip()
    if not token:
        raise CloudError(
            "RUNTIME_PAIRING_REQUIRED",
            "This PostSteward runtime is not paired. Run 'poststeward onboard'.",
            3,
        )
    return token


def runtime_context() -> dict[str, Any]:
    value = _read()
    return {
        "origin": origin(),
        "installation_id": value.get("installation_id"),
        "workspace": value.get("workspace"),
        "token_expires_at": value.get("token_expires_at"),
        "authority_generation": value.get("authority_generation"),
    }


def bindings() -> dict[str, Any]:
    _, value = _request("GET", "/api/runtime/bindings", token=runtime_token())
    executor = value.get("executor") if isinstance(value.get("executor"), dict) else {}
    current = _read()
    current.update(
        {
            "schema_version": 1,
            "origin": origin(),
            "workspace": value.get("workspace"),
            "authority_generation": executor.get("authorityGeneration"),
            "executor_mode": executor.get("executorMode"),
        }
    )
    _write(current)
    return value


def heartbeat() -> dict[str, Any]:
    current = bindings()
    executor = current.get("executor") if isinstance(current.get("executor"), dict) else {}
    generation = executor.get("authorityGeneration")
    if executor.get("executorMode") != "local":
        raise CloudError(
            "RUNTIME_EXECUTOR_NOT_LOCAL",
            "This installation does not currently own local execution authority.",
            3,
        )
    _, value = _request(
        "POST",
        "/api/runtime/heartbeat",
        token=runtime_token(),
        payload={"authorityGeneration": generation},
    )
    return value


def onboard(*, no_open: bool = False, wait: bool = True) -> dict[str, Any]:
    current = installation_identity()
    existing = str(current.get("runtime_token") or "").strip()
    if existing:
        try:
            value = bindings()
            print("PostSteward runtime is already paired.")
            print(json.dumps(value, indent=2, ensure_ascii=False))
            return value
        except CloudError as exc:
            if exc.code != "RUNTIME_UNAUTHENTICATED":
                raise

    _, started = _request(
        "POST",
        "/api/runtime/pairing/start",
        payload={
            "installationId": current["installation_id"],
            "label": current["label"],
            "platform": current["platform"],
            "runtimeVersion": _runtime_version(),
            **({"sourceRevision": _source_revision()} if _source_revision() else {}),
        },
    )
    pairing_id = str(started["pairingId"])
    poll_token = str(started["pollToken"])
    url = str(started["verificationUrl"])
    code = str(started["userCode"])
    expires_at = int(started["expiresAt"])
    print("PostSteward runtime pairing")
    print(f"Code: {code}")
    print(f"Open: {url}")
    print("Approve the exact machine details in your owner workspace.")
    if not no_open:
        try:
            webbrowser.open(url, new=2)
        except Exception:
            pass
    if not wait:
        return started

    poll_ms = max(1000, int(started.get("pollAfterMs") or 3000))
    while int(time.time() * 1000) < expires_at:
        try:
            status, value = _request(
                "GET",
                "/api/runtime/pairing/status?" + urllib.parse.urlencode({"pairing_id": pairing_id}),
                token=poll_token,
            )
        except CloudError as exc:
            if exc.status in {404, 409, 410}:
                raise
            time.sleep(poll_ms / 1000)
            continue
        if status == 202 or value.get("status") == "pending":
            time.sleep(poll_ms / 1000)
            continue
        if value.get("status") == "paired":
            updated = _read()
            executor = value.get("executor") if isinstance(value.get("executor"), dict) else {}
            updated.update(
                {
                    "schema_version": 1,
                    "origin": origin(),
                    "installation_id": value["installationId"],
                    "workspace": value["workspace"],
                    "runtime_token": value["runtimeToken"],
                    "token_expires_at": value["tokenExpiresAt"],
                    "authority_generation": executor.get("authorityGeneration"),
                    "executor_mode": executor.get("executorMode"),
                }
            )
            _write(updated)
            print(f"Paired workspace: {value['workspace']}")
            print("Provider OAuth credentials remain in PostSteward Cloud.")
            return bindings()
        raise CloudError("RUNTIME_PAIRING_INVALID_RESPONSE", "Pairing returned an unexpected state.")
    raise CloudError("RUNTIME_PAIRING_EXPIRED", "Pairing approval window expired. Run onboarding again.", 3)


def logout() -> None:
    value = _read()
    for key in (
        "runtime_token",
        "token_expires_at",
        "workspace",
        "authority_generation",
        "executor_mode",
    ):
        value.pop(key, None)
    _write(value)


def mcp_info() -> dict[str, Any]:
    return {
        "schema_version": 1,
        "url": origin() + "/mcp",
        "authorization": "Bearer <owner-issued scoped agent token>",
        "runtime_pairing_token_is_not_an_agent_token": True,
    }


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(prog="poststeward cloud")
    sub = parser.add_subparsers(dest="command", required=True)
    onboard_parser = sub.add_parser("onboard")
    onboard_parser.add_argument("--no-open", action="store_true")
    onboard_parser.add_argument("--no-wait", action="store_true")
    sub.add_parser("status")
    sub.add_parser("providers")
    sub.add_parser("heartbeat")
    sub.add_parser("logout")
    sub.add_parser("mcp")
    args = parser.parse_args(argv)
    try:
        if args.command == "onboard":
            value = onboard(no_open=args.no_open, wait=not args.no_wait)
            if not args.no_wait:
                print(json.dumps(value, indent=2, ensure_ascii=False))
        elif args.command in {"status", "providers"}:
            print(json.dumps(bindings(), indent=2, ensure_ascii=False))
        elif args.command == "heartbeat":
            print(json.dumps(heartbeat(), indent=2, ensure_ascii=False))
        elif args.command == "logout":
            logout()
            print("Local PostSteward runtime token removed. Revoke the installation in the owner workspace if needed.")
        elif args.command == "mcp":
            print(json.dumps(mcp_info(), indent=2, ensure_ascii=False))
        return 0
    except CloudError as exc:
        print(f"PostSteward cloud blocked [{exc.code}]: {exc}", file=sys.stderr)
        return exc.status if 1 <= exc.status <= 125 else 1


if __name__ == "__main__":
    raise SystemExit(main())
