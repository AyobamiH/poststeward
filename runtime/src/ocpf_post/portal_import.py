from __future__ import annotations

import argparse
import os
import time
from getpass import getpass
from pathlib import Path
from typing import Any

from ocpf_post.providers.x import DEFAULT_REDIRECT_URI, DEFAULT_SCOPES
from ocpf_post.state import (
    config_dir,
    ensure_private_dir,
    provider_settings_file,
    provider_token_file,
    read_json,
    write_private_json,
)

ACCESS_TOKEN_LIFETIME_SECONDS = 2 * 60 * 60


def client_secret_file() -> Path:
    return config_dir() / "x-client-secret"


def _write_private_text(path: Path, value: str) -> None:
    ensure_private_dir(path.parent)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(value, encoding="utf-8")
    try:
        tmp.chmod(0o600)
    except OSError:
        pass
    os.replace(tmp, path)
    try:
        path.chmod(0o600)
    except OSError:
        pass


def import_x_portal_credentials(
    *,
    access_token: str,
    refresh_token: str,
    client_id: str | None = None,
    client_secret: str | None = None,
    redirect_uri: str | None = None,
    access_expires_in: int = ACCESS_TOKEN_LIFETIME_SECONDS,
) -> dict[str, Any]:
    access_token = access_token.strip()
    refresh_token = refresh_token.strip()
    if not access_token:
        raise ValueError("X OAuth 2.0 access token is required")
    if not refresh_token:
        raise ValueError("X OAuth 2.0 refresh token is required")
    if access_expires_in <= 0:
        raise ValueError("access_expires_in must be positive")

    saved = read_json(provider_settings_file("x"))
    resolved_client_id = str(
        client_id
        or os.environ.get("X_CLIENT_ID")
        or saved.get("client_id")
        or ""
    ).strip()
    if not resolved_client_id:
        raise ValueError("X OAuth 2.0 Client ID is required")

    resolved_redirect = str(
        redirect_uri
        or os.environ.get("X_REDIRECT_URI")
        or saved.get("redirect_uri")
        or DEFAULT_REDIRECT_URI
    )
    now = int(time.time())

    write_private_json(
        provider_settings_file("x"),
        {
            "client_id": resolved_client_id,
            "redirect_uri": resolved_redirect,
        },
    )
    write_private_json(
        provider_token_file("x"),
        {
            "access_token": access_token,
            "refresh_token": refresh_token,
            "token_type": "bearer",
            "scope": " ".join(DEFAULT_SCOPES),
            "obtained_at": now,
            "expires_at": now + int(access_expires_in),
            "client_id": resolved_client_id,
            "redirect_uri": resolved_redirect,
            "source": "x_developer_portal",
        },
    )

    secret_path: Path | None = None
    if client_secret:
        secret_path = client_secret_file()
        _write_private_text(secret_path, client_secret.strip())

    return {
        "token_file": str(provider_token_file("x")),
        "secret_file": str(secret_path) if secret_path else None,
        "expires_at": now + int(access_expires_in),
        "client_id": resolved_client_id,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="import-x-credentials",
        description="Securely import X Developer Portal OAuth 2.0 tokens for post-once",
    )
    parser.add_argument("--from-env", action="store_true", help="Read tokens from environment instead of hidden prompts")
    parser.add_argument("--client-id", help="OAuth 2.0 Client ID; defaults to X_CLIENT_ID or saved settings")
    parser.add_argument("--public-client", action="store_true", help="Do not require/store a confidential-client secret")
    return parser


def main() -> None:
    args = build_parser().parse_args()
    if args.from_env:
        access_token = os.environ.get("X_OAUTH2_ACCESS_TOKEN") or os.environ.get("X_USER_ACCESS_TOKEN") or ""
        refresh_token = os.environ.get("X_REFRESH_TOKEN") or ""
    else:
        access_token = getpass("X OAuth 2.0 access token: ")
        refresh_token = getpass("X OAuth 2.0 refresh token: ")

    client_secret = None
    if not args.public_client:
        client_secret = os.environ.get("X_CLIENT_SECRET")
        if not client_secret:
            client_secret = getpass("X OAuth 2.0 client secret (stored locally for automatic refresh): ")
        if not client_secret:
            raise SystemExit("Error: confidential-client secret is required for automatic refresh")

    try:
        result = import_x_portal_credentials(
            access_token=access_token,
            refresh_token=refresh_token,
            client_id=args.client_id,
            client_secret=client_secret,
        )
    except ValueError as exc:
        raise SystemExit(f"Error: {exc}") from exc

    print("X portal credentials imported without printing secret values.")
    print(f"Token file:  {result['token_file']}")
    if result["secret_file"]:
        print(f"Secret file: {result['secret_file']}")
    print("Directory permissions: 0700; credential files: 0600 where supported.")
    print("The stored refresh token will be used to renew the 2-hour access token automatically.")
    print("Run `./ocpf-post x status` next.")


if __name__ == "__main__":
    main()
