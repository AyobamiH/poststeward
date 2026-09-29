from __future__ import annotations

import argparse
import json
import sys

from ocpf_post.campaigns import builtin_manifest, normalize_campaign_id
from ocpf_post.performance import capture, iter_snapshots, latest_snapshot, performance_file


def _die(message: str, code: int = 1) -> None:
    print(f"Error: {message}", file=sys.stderr)
    raise SystemExit(code)


def _providers_for_campaign(campaign: str) -> list[str]:
    manifest = builtin_manifest(campaign)
    providers = manifest.get("providers")
    if not isinstance(providers, list):
        return []
    out = []
    for provider in providers:
        value = str(provider).strip().lower()
        if value and value not in out:
            out.append(value)
    return out


def cmd_capture(args: argparse.Namespace) -> None:
    try:
        campaign = normalize_campaign_id(args.campaign)
    except ValueError as exc:
        _die(str(exc))
    providers = [args.provider] if args.provider != "all" else _providers_for_campaign(campaign)
    if not providers:
        _die(f"No providers declared for {campaign}", 4)
    snapshots = []
    for provider in providers:
        try:
            snapshots.append(capture(campaign, provider))
        except ValueError as exc:
            snapshots.append({
                "campaign": campaign,
                "provider": provider,
                "capture_error": str(exc),
            })
    print(json.dumps(snapshots, indent=2, ensure_ascii=False))
    print(f"Performance log: {performance_file()}")


def cmd_show(args: argparse.Namespace) -> None:
    campaign = normalize_campaign_id(args.campaign)
    providers = [args.provider] if args.provider else _providers_for_campaign(campaign)
    out = []
    for provider in providers:
        value = latest_snapshot(campaign, provider)
        if value:
            out.append(value)
    if not out:
        _die(f"No performance snapshots for {campaign}", 4)
    print(json.dumps(out, indent=2, ensure_ascii=False))


def cmd_compare(args: argparse.Namespace) -> None:
    requested = {normalize_campaign_id(value) for value in args.campaigns}
    latest: dict[tuple[str, str], dict] = {}
    for value in iter_snapshots():
        campaign = str(value.get("campaign") or "")
        provider = str(value.get("provider") or "")
        if campaign in requested and provider:
            latest[(campaign, provider)] = value
    rows = [latest[key] for key in sorted(latest)]
    if not rows:
        _die("No matching performance snapshots", 4)
    print(json.dumps(rows, indent=2, ensure_ascii=False))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="ocpf-post performance")
    sub = parser.add_subparsers(dest="command", required=True)

    from ocpf_post.business_outcomes import add_parsers as outcome_parsers
    outcome_parsers(sub)
    from ocpf_post import performance_review
    from ocpf_post.onboarding import _run_cli
    review = sub.add_parser("review", help="Compare receipt-linked observations at matching post ages")
    review.add_argument("campaigns", nargs="+")
    review.add_argument("--provider", required=True, choices=["x", "threads", "linkedin"])
    review.add_argument("--account-id", required=True)
    review.add_argument("--age-hours", type=float, default=24)
    review.add_argument("--tolerance-hours", type=float, default=2)
    review.set_defaults(func=lambda a: _run_cli(lambda: performance_review.review(a.campaigns, provider=a.provider, account_id=a.account_id, age_hours=a.age_hours, tolerance_hours=a.tolerance_hours)))
    feedback = sub.add_parser('feedback', help='Build evidence-bound selection feedback')
    feedback.add_argument('--apply', action='store_true')
    toggles = feedback.add_mutually_exclusive_group()
    toggles.add_argument('--disable', action='store_true')
    toggles.add_argument('--enable', action='store_true')
    from ocpf_post.performance_feedback import build
    feedback.set_defaults(func=lambda a: _run_cli(lambda: build(apply=a.apply, enable=False if a.disable else True if a.enable else None)))
    due = sub.add_parser("capture-due", help="Preview or capture a bounded cohort near the target post age")
    due.add_argument("--age-hours", type=float)
    due.add_argument("--tolerance-hours", type=float, default=2)
    due.add_argument("--apply", action="store_true")
    def capture_due(a):
        if a.age_hours is not None:
            return performance_review.capture_due(age_hours=a.age_hours, tolerance_hours=a.tolerance_hours, apply=a.apply)
        from ocpf_post.performance_windows import capture_sweep
        return capture_sweep(apply=a.apply)
    due.set_defaults(func=lambda a: _run_cli(lambda: capture_due(a)))

    capture_cmd = sub.add_parser("capture", help="Read provider metrics and append a truthful snapshot")
    capture_cmd.add_argument("--campaign", required=True)
    capture_cmd.add_argument("--provider", choices=["all", "x", "threads", "linkedin"], default="all")
    capture_cmd.set_defaults(func=cmd_capture)

    show = sub.add_parser("show", help="Show the latest local snapshot")
    show.add_argument("--campaign", required=True)
    show.add_argument("--provider", choices=["x", "threads", "linkedin"])
    show.set_defaults(func=cmd_show)

    compare = sub.add_parser("compare", help="Compare latest snapshots across campaigns/providers")
    compare.add_argument("campaigns", nargs="+")
    compare.set_defaults(func=cmd_compare)
    return parser


def main() -> None:
    args = build_parser().parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
