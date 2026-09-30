from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

from ocpf_post.campaigns import (
    builtin_text,
    destination_binding_error,
    normalize_campaign_id,
)
from ocpf_post.direct_publication import DirectPublicationStopped, publish as publish_direct
from ocpf_post.providers import get_extended_provider
from ocpf_post.publication_payload import build_publication
from ocpf_post.providers.base import AmbiguousProviderEffect, ProviderRejected, ProviderUnavailable
from ocpf_post.state import (
    latest_receipt,
    receipts_file,
    terminal_effect_receipt,
)


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _die(message: str, code: int = 1) -> None:
    print(f"Error: {message}", file=sys.stderr)
    raise SystemExit(code)


def _provider(name: str, campaign: str | None = None):
    try:
        if campaign:
            from ocpf_post.providers import for_campaign
            return for_campaign(name, campaign)
        return get_extended_provider(name)
    except ValueError as exc:
        _die(str(exc))


def _text(args: argparse.Namespace, campaign: str, provider: str) -> str:
    if args.text:
        return args.text.strip()
    if args.file:
        return Path(args.file).read_text(encoding="utf-8").strip()
    if args.stdin:
        return sys.stdin.read().strip()
    value = builtin_text(campaign, provider)
    if value:
        return value
    _die("No built-in payload exists. Use --text, --file or --stdin.")


def _print_account(account) -> None:
    print(
        f"Authenticated {account.provider.upper()} account: "
        f"{account.display} ({account.account_id})"
    )
    biography = getattr(account, "biography", None)
    if biography is not None:
        print(f"Biography: {biography or '[empty]'}")


def cmd_status(args: argparse.Namespace) -> None:
    provider = _provider(args.provider)
    try:
        account = provider.account()
    except (ProviderRejected, ProviderUnavailable) as exc:
        _die(str(exc))
    _print_account(account)


def cmd_refresh(args: argparse.Namespace) -> None:
    provider = _provider(args.provider)
    if not hasattr(provider, "refresh"):
        _die(f"{args.provider} does not expose token refresh")
    try:
        provider.refresh()
        account = provider.account()
    except (ProviderRejected, ProviderUnavailable) as exc:
        _die(str(exc))
    _print_account(account)


def cmd_publish(args: argparse.Namespace) -> None:
    try:
        campaign = normalize_campaign_id(args.campaign)
    except ValueError as exc:
        _die(str(exc))
    provider_name = args.provider.lower()
    text = _text(args, campaign, provider_name)
    if not text:
        _die("Post text is empty")
    try:
        publication = build_publication(provider_name, text)
    except ValueError as exc:
        _die(str(exc))
    digest = publication["text_sha256"]
    provider = _provider(provider_name, campaign)
    try:
        account = provider.account()
    except (ProviderRejected, ProviderUnavailable) as exc:
        _die(str(exc))

    binding_error = destination_binding_error(campaign, provider_name, account)
    if args.live and binding_error:
        _die(binding_error, 6)

    prior = terminal_effect_receipt(campaign, provider_name, account.account_id)
    if prior and not args.allow_duplicate:
        print(f"Refusing duplicate consequence for {campaign} on {account.display}.")
        print(f"Prior status: {prior.get('status')}")
        if prior.get("url"):
            print(f"Prior URL: {prior['url']}")
        _die(
            "A prior terminal/ambiguous effect receipt exists. "
            "Use --allow-duplicate only when intentional.",
            3,
        )

    print("\nOneClickPostFactory post-once")
    print(f"Campaign: {campaign}")
    print(f"Provider: {provider_name}")
    print(f"Account:  {account.display} ({account.account_id})")
    print(f"SHA256:   {digest}")
    print("Mode:     " + ("LIVE" if args.live else "DRY RUN"))
    print(f"Shape:    {publication['publication_type']} ({publication['part_count']} part(s))")
    if binding_error:
        print("Live gate: BLOCKED")
        print(f"Reason:    {binding_error}")
    else:
        print("Live gate: BOUND DESTINATION")
    print("\n--- post text ---")
    print(text)
    print("--- end post ---\n")
    if not args.live:
        if binding_error:
            print("Dry run only. Live publication remains blocked until the intended account is bound.")
        else:
            print("Dry run only. Re-run with --live to create the external post.")
        return

    from ocpf_post.campaigns import builtin_manifest
    if builtin_manifest(campaign).get("vault") or builtin_manifest(campaign).get("payload_frozen"):
        _die("Vault and frozen source campaigns use the guarded scheduler; direct publication is not supported", 6)

    try:
        result = publish_direct(
            provider=provider,
            account=account,
            campaign=campaign,
            provider_name=provider_name,
            text=text,
            now=_now,
        )
    except DirectPublicationStopped as exc:
        _die(
            f"{exc}. Receipt written to {receipts_file()}; do not blind-retry.",
            5,
        )
    except ProviderRejected as exc:
        _die(str(exc))

    if result["status"] == "published_verified":
        print("PUBLISHED + READBACK VERIFIED")
    else:
        print("PUBLISHED, READBACK NOT VERIFIED")
        print("Durable provider IDs exist, so duplicate retry remains blocked.")
    if result.get("url"):
        print(result["url"])
    print(f"Receipt log: {receipts_file()}")


def cmd_receipt(args: argparse.Namespace) -> None:
    try:
        campaign = normalize_campaign_id(args.campaign)
    except ValueError as exc:
        _die(str(exc))
    receipt = latest_receipt(campaign, args.provider, args.account_id)
    if not receipt:
        _die(f"No receipt found for {campaign}/{args.provider}", 4)
    print(json.dumps(receipt, indent=2, ensure_ascii=False))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="ocpf-post")
    parser.add_argument("provider", choices=["threads", "linkedin"])
    sub = parser.add_subparsers(dest="command", required=True)

    status = sub.add_parser("status")
    status.set_defaults(func=cmd_status)

    refresh = sub.add_parser("refresh")
    refresh.set_defaults(func=cmd_refresh)

    publish = sub.add_parser("publish")
    publish.add_argument("--campaign", required=True)
    source = publish.add_mutually_exclusive_group()
    source.add_argument("--text")
    source.add_argument("--file")
    source.add_argument("--stdin", action="store_true")
    publish.add_argument("--live", action="store_true")
    publish.add_argument("--allow-duplicate", action="store_true")
    publish.set_defaults(func=cmd_publish)

    receipt = sub.add_parser("receipt")
    receipt.add_argument("--campaign", required=True)
    receipt.add_argument("--account-id")
    receipt.set_defaults(func=cmd_receipt)
    return parser


def main() -> None:
    args = build_parser().parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
