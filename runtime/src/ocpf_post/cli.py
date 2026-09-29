from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import Any

from ocpf_post import __version__
from ocpf_post.campaigns import (
    builtin_text,
    destination_binding_error,
    normalize_campaign_id,
)
from ocpf_post.direct_publication import DirectPublicationStopped, publish as publish_direct
from ocpf_post.providers import get_provider
from ocpf_post.publication_payload import build_publication
from ocpf_post.providers.base import AmbiguousProviderEffect, ProviderRejected, ProviderUnavailable
from ocpf_post.state import (
    config_dir,
    latest_receipt,
    provider_token_file,
    receipts_file,
    state_dir,
    terminal_effect_receipt,
)


def _utc_now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _die(message: str, code: int = 1) -> None:
    print(f"Error: {message}", file=sys.stderr)
    raise SystemExit(code)


def _provider(args: argparse.Namespace):
    try:
        from ocpf_post.providers import for_campaign
        factory = for_campaign if getattr(args, "campaign", None) else get_provider
        positional = [getattr(args, "provider", "x")]
        if getattr(args, "campaign", None):
            positional.append(args.campaign)
        return factory(
            *positional,
            client_id=getattr(args, "client_id", None),
            client_secret=getattr(args, "client_secret", None),
            redirect_uri=getattr(args, "redirect_uri", None),
        )
    except (ValueError, ProviderRejected, ProviderUnavailable) as exc:
        _die(str(exc))


def _text_for_publish(args: argparse.Namespace, campaign: str, provider_name: str) -> str:
    if args.text:
        return args.text.strip()
    if args.file:
        return Path(args.file).read_text(encoding="utf-8").strip()
    if args.stdin:
        return sys.stdin.read().strip()
    text = builtin_text(campaign, provider_name)
    if text:
        return text
    _die("No built-in payload exists. Use --text, --file or --stdin.")


def _print_account(account) -> None:
    print(f"Authenticated {account.provider.upper()} account: {account.display} ({account.account_id})")


def cmd_doctor(args: argparse.Namespace) -> None:
    from ocpf_post.capabilities import report as capability_report
    from ocpf_post.runtime_attestation import attest
    from ocpf_post.state_registry import verify as verify_state

    py_ok = sys.version_info >= (3, 10)
    state = verify_state()
    capability = capability_report()
    value = {
        "schema_version": 1,
        "cli_version": __version__,
        "python": sys.version.split()[0],
        "python_supported": py_ok,
        "config_dir": str(config_dir()),
        "state_dir": str(state_dir()),
        "state_integrity": {
            "status": state.get("status"),
            "invalid_count": state.get("invalid_count"),
            "critical_ledger_status": state.get("critical_ledger_status"),
        },
        "runtime": attest(),
        "capabilities": capability,
        "boundary": "Diagnostic only. No provider call, credential refresh, state repair, schedule mutation or publication.",
    }
    if getattr(args, "deep", False):
        from ocpf_post.health import report as health_report
        value["health"] = health_report(check_timers=True)
    if getattr(args, "json", False):
        print(json.dumps(value, indent=2, ensure_ascii=False))
    else:
        print(f"post-once {__version__}")
        print(f"Python: {value['python']} {'OK' if py_ok else 'UNSUPPORTED'}")
        print(f"Config dir: {value['config_dir']}")
        print(f"State dir:  {value['state_dir']}")
        print(f"State integrity: {value['state_integrity']['status']} (invalid={value['state_integrity']['invalid_count']})")
        print(
            f"Capabilities: {len(capability.get('capabilities', []))} registered; "
            f"core={capability.get('core_status', capability.get('status'))}; "
            f"optional_pending={capability.get('optional_pending_count', 0)}; "
            f"readiness={capability.get('status')}"
        )
        if capability.get("core_blockers"):
            print("Core blockers: " + ", ".join(capability["core_blockers"]))
        if capability.get("activated_optional_blockers"):
            print("Activated optional blockers: " + ", ".join(capability["activated_optional_blockers"]))
        print(f"Checkout: {value['runtime'].get('git_commit_sha') or 'unknown'} · clean={value['runtime'].get('checkout_clean')}")
        if "health" in value:
            health = value["health"]
            print(f"Deep health: {health.get('status')}")
            attention = [row for row in health.get("findings", []) if row.get("level") == "attention"]
            if attention:
                counts = {}
                for row in attention:
                    code = str(row.get("code") or "attention")
                    counts[code] = counts.get(code, 0) + 1
                summary = ", ".join(
                    f"{code}×{count}" if count > 1 else code
                    for code, count in sorted(counts.items())
                )
                print("Health attention: " + summary)
                safe_fields = (
                    "campaign", "provider", "account_id", "schedule_id", "stage", "inbox_id", "connector_id",
                    "forensic_status", "forensic_candidate_count", "forensic_http_status",
                    "forensic_retry_at", "forensic_observed_at",
                    "forensic_automatic_retry", "forensic_next_action",
                )
                shown = 0
                for row in attention:
                    context = {key: row.get(key) for key in safe_fields if row.get(key) is not None}
                    if not context:
                        continue
                    code = str(row.get("code") or "attention")
                    print("Health example: " + code + " " + json.dumps(context, ensure_ascii=False))
                    shown += 1
                    if shown >= 5:
                        break
    if not py_ok:
        raise SystemExit(2)
    if state.get("status") == "attention":
        raise SystemExit(3)


def cmd_x_auth(args: argparse.Namespace) -> None:
    provider = _provider(args)
    try:
        account = provider.authorize()
    except (ProviderRejected, ProviderUnavailable) as exc:
        _die(str(exc))
    print("Authorisation complete.")
    _print_account(account)


def cmd_x_status(args: argparse.Namespace) -> None:
    provider = _provider(args)
    try:
        account = provider.account()
    except (ProviderRejected, ProviderUnavailable) as exc:
        _die(str(exc))
    _print_account(account)


def cmd_x_refresh(args: argparse.Namespace) -> None:
    provider = _provider(args)
    try:
        provider.refresh()
        _print_account(provider.account())
    except (ProviderRejected, ProviderUnavailable) as exc:
        _die(str(exc))


def cmd_x_logout(args: argparse.Namespace) -> None:
    provider = _provider(args)
    try:
        provider.logout()
    except ProviderRejected as exc:
        _die(str(exc))
    print("Local X token removed.")


def cmd_campaign_show(args: argparse.Namespace) -> None:
    try:
        campaign = normalize_campaign_id(args.campaign)
    except ValueError as exc:
        _die(str(exc))
    text = builtin_text(campaign, args.provider)
    if not text:
        _die(f"No built-in {args.provider} payload for {campaign}", 4)
    print(text)


def cmd_campaign_reconcile(args: argparse.Namespace) -> None:
    from ocpf_post.publication_reconcile import reconcile
    value = reconcile(
        apply=args.apply,
        campaign=args.campaign,
        provider=args.provider,
        schedule_id=args.schedule_id,
    )
    print(json.dumps(value, indent=2, ensure_ascii=False))
    if value.get("status") == "attention":
        raise SystemExit(3)


def cmd_publish(args: argparse.Namespace) -> None:
    try:
        campaign = normalize_campaign_id(args.campaign)
    except ValueError as exc:
        _die(str(exc))
    provider_name = args.provider.strip().lower()
    text = _text_for_publish(args, campaign, provider_name)
    if not text:
        _die("Post text is empty")
    try:
        publication = build_publication(provider_name, text)
    except ValueError as exc:
        _die(str(exc))
    digest = publication["text_sha256"]
    provider = _provider(args)
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
        _die("A prior terminal/ambiguous effect receipt exists. Use --allow-duplicate only when intentional.", 3)

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
            now=_utc_now,
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
    receipt = latest_receipt(campaign, args.provider.strip().lower(), args.account_id)
    if not receipt:
        _die(f"No receipt found for {campaign}/{args.provider}", 4)
    print(json.dumps(receipt, indent=2, ensure_ascii=False))


def _common_x_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--client-id", help="X OAuth 2.0 Client ID (or X_CLIENT_ID)")
    parser.add_argument("--client-secret", help="X Client Secret for confidential clients (prefer X_CLIENT_SECRET)")
    parser.add_argument("--redirect-uri", help="Registered localhost callback URI (or X_REDIRECT_URI)")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ocpf-post",
        description="Post-Once operator control plane for receipt-backed publishing, scheduling, evidence and portfolio automation",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    from ocpf_post.operations import add_parsers
    add_parsers(sub)
    from ocpf_post.engagement import add_parsers as engagement_parsers
    engagement_parsers(sub)

    doctor = sub.add_parser("doctor", help="Check current platform prerequisites without publishing")
    doctor.add_argument("--json", action="store_true")
    doctor.add_argument("--deep", action="store_true", help="Include the full local health/timer report")
    doctor.set_defaults(func=cmd_doctor)

    x = sub.add_parser("x", help="Manage X OAuth user authorization")
    _common_x_options(x)
    x_sub = x.add_subparsers(dest="x_command", required=True)
    x_auth = x_sub.add_parser("auth", help="Run OAuth 2.0 Authorization Code + PKCE")
    x_auth.set_defaults(func=cmd_x_auth, provider="x")
    x_status = x_sub.add_parser("status", help="Read back the exact authenticated X account")
    x_status.set_defaults(func=cmd_x_status, provider="x")
    x_refresh = x_sub.add_parser("refresh", help="Refresh the stored X access token")
    x_refresh.set_defaults(func=cmd_x_refresh, provider="x")
    x_logout = x_sub.add_parser("logout", help="Revoke/remove the stored X token")
    x_logout.set_defaults(func=cmd_x_logout, provider="x")

    campaign = sub.add_parser("campaign", help="Inspect built-in campaign payloads")
    campaign_sub = campaign.add_subparsers(dest="campaign_command", required=True)
    show = campaign_sub.add_parser("show")
    show.add_argument("--campaign", required=True)
    show.add_argument("--provider", default="x")
    show.set_defaults(func=cmd_campaign_show)

    from ocpf_post.campaign_explain import cmd_explain
    explain = campaign_sub.add_parser("explain", help="Explain local copy provenance, allocation and publication evidence")
    explain.add_argument("--campaign", required=True)
    explain.add_argument("--provider", default="x", choices=["x", "threads", "linkedin"])
    explain.add_argument("--json", action="store_true")
    explain.set_defaults(func=cmd_explain)

    from ocpf_post.onboarding import add_import_arguments, cmd_import_campaign
    import_cmd = campaign_sub.add_parser("import", help="Preview or add an approved runtime campaign")
    add_import_arguments(import_cmd, campaign=True)
    import_cmd.set_defaults(func=cmd_import_campaign)

    from ocpf_post.evidence_briefs import cmd_import_brief
    brief = campaign_sub.add_parser("brief", help="Preview or import reviewed evidence briefs as distinct campaigns")
    add_import_arguments(brief, campaign=True)
    brief.set_defaults(func=cmd_import_brief)

    from ocpf_post.operations import cmd_receipts, cmd_operations
    brief_receipts = campaign_sub.add_parser("receipts", help="Inspect scheduled reviewed-brief publication receipts")
    brief_receipts.add_argument("--project", required=True)
    brief_receipts.add_argument("--brief-id")
    brief_receipts.set_defaults(func=cmd_receipts)

    reconcile_cmd = campaign_sub.add_parser(
        "reconcile",
        help="Preview or perform bounded GET-only readback/forensic reconciliation of existing effects",
    )
    reconcile_cmd.add_argument("--campaign")
    reconcile_cmd.add_argument("--provider", choices=("x", "threads", "linkedin"))
    reconcile_cmd.add_argument("--schedule-id")
    reconcile_cmd.add_argument("--apply", action="store_true")
    reconcile_cmd.set_defaults(func=cmd_campaign_reconcile)
    operations = sub.add_parser("operations", help="Observe project or portfolio operating outcomes from local evidence")
    operation_scope = operations.add_mutually_exclusive_group(required=True)
    operation_scope.add_argument("--project")
    operation_scope.add_argument("--all", dest="all_projects", action="store_true")
    operations.add_argument("--save", action="store_true", help="Save a private local report and print its summary")
    operations.set_defaults(func=cmd_operations)

    publish = sub.add_parser("publish", help="Dry-run or explicitly publish one campaign")
    publish.add_argument("--provider", default="x", choices=["x"])
    publish.add_argument("--campaign", required=True)
    source = publish.add_mutually_exclusive_group()
    source.add_argument("--text")
    source.add_argument("--file")
    source.add_argument("--stdin", action="store_true")
    publish.add_argument("--live", action="store_true", help="Actually create the external post")
    publish.add_argument("--allow-duplicate", action="store_true")
    _common_x_options(publish)
    publish.set_defaults(func=cmd_publish)

    receipt = sub.add_parser("receipt", help="Show the latest local publication receipt")
    receipt.add_argument("--campaign", required=True)
    receipt.add_argument("--provider", default="x")
    receipt.add_argument("--account-id")
    receipt.set_defaults(func=cmd_receipt)
    from ocpf_post.account_profiles_cli import add_parser
    add_parser(sub)
    from ocpf_post.outcome_connectors import add_parser as add_outcome_connectors
    add_outcome_connectors(sub)
    from ocpf_post.alert_delivery import add_parser as add_alert_delivery
    add_alert_delivery(sub)
    from ocpf_post.platform_cli import add_parsers as add_platform_parsers
    add_platform_parsers(sub)
    return parser


def main() -> None:
    args = build_parser().parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
