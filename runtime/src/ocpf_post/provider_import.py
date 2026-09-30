from __future__ import annotations

import argparse
import os
import time
from getpass import getpass
from pathlib import Path

from ocpf_post.providers.linkedin import (
    ACCESS_TOKEN_SECONDS as LINKEDIN_ACCESS_SECONDS,
    DEFAULT_VERSION,
    secret_file as linkedin_secret_file,
)
from ocpf_post.providers.threads import LONG_LIVED_SECONDS as THREADS_LONG_SECONDS
from ocpf_post.state import (
    config_dir,
    ensure_private_dir,
    provider_settings_file,
    provider_token_file,
    write_private_json,
)


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


def import_threads(
    access_token: str,
    *,
    expires_in: int = THREADS_LONG_SECONDS,
) -> None:
    token = access_token.strip()
    if not token:
        raise ValueError("Threads access token is required")
    if expires_in <= 0:
        raise ValueError("expires_in must be positive")
    now = int(time.time())
    write_private_json(
        provider_token_file("threads"),
        {
            "access_token": token,
            "obtained_at": now,
            "expires_at": now + expires_in,
            "token_type": "bearer",
        },
    )


def import_linkedin(
    access_token: str,
    *,
    refresh_token: str | None = None,
    client_id: str | None = None,
    client_secret: str | None = None,
    expires_in: int = LINKEDIN_ACCESS_SECONDS,
    version: str = DEFAULT_VERSION,
) -> None:
    access = access_token.strip()
    if not access:
        raise ValueError("LinkedIn access token is required")
    if expires_in <= 0:
        raise ValueError("expires_in must be positive")
    now = int(time.time())
    write_private_json(
        provider_token_file("linkedin"),
        {
            "access_token": access,
            "refresh_token": refresh_token.strip() if refresh_token else None,
            "obtained_at": now,
            "expires_at": now + expires_in,
            "token_type": "bearer",
        },
    )
    settings: dict[str, str] = {"version": version}
    if client_id:
        settings["client_id"] = client_id.strip()
    write_private_json(provider_settings_file("linkedin"), settings)
    if client_secret:
        _write_private_text(linkedin_secret_file(), client_secret.strip())


def _threads_main(args: argparse.Namespace) -> None:
    token = getpass("Threads long-lived access token: ")
    import_threads(token, expires_in=args.expires_in)
    print(
        f"Threads token stored under {config_dir()} with user-only permissions."
    )


def _linkedin_main(args: argparse.Namespace) -> None:
    access = getpass("LinkedIn access token: ")
    refresh = getpass("LinkedIn refresh token (optional, press Enter to skip): ")
    client_id = input("LinkedIn Client ID (optional unless using refresh): ").strip()
    secret = getpass("LinkedIn Client Secret (optional unless using refresh): ")
    import_linkedin(
        access,
        refresh_token=refresh or None,
        client_id=client_id or None,
        client_secret=secret or None,
        expires_in=args.expires_in,
        version=args.version,
    )
    print(
        f"LinkedIn credentials stored under {config_dir()} with user-only permissions."
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="provider", required=True)

    threads = sub.add_parser("threads")
    threads.add_argument("--expires-in", type=int, default=THREADS_LONG_SECONDS)
    threads.set_defaults(func=_threads_main)

    linkedin = sub.add_parser("linkedin")
    linkedin.add_argument("--expires-in", type=int, default=LINKEDIN_ACCESS_SECONDS)
    linkedin.add_argument("--version", default=DEFAULT_VERSION)
    linkedin.set_defaults(func=_linkedin_main)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
