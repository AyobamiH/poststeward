from __future__ import annotations

import argparse
import json
import sys

from ocpf_post import local_store
from ocpf_post.portfolio import PortfolioError, load_policy, policy_file, write_default_policy
from ocpf_post.portfolio_policy import apply_provider_patch, preview_provider_patch


def _die(message: str, code: int = 2) -> None:
    print(f"Error: {message}", file=sys.stderr)
    raise SystemExit(code)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ocpf-post portfolio policy",
        description="Inspect or perform reviewed, bounded portfolio-policy changes.",
    )
    parser.add_argument("--json", action="store_true", help="Emit JSON where applicable")
    parser.add_argument("--write-default", action="store_true", help="Initialise the built-in conservative policy")
    parser.add_argument("--force", action="store_true", help="Allow --write-default to replace an existing policy")
    parser.add_argument("--selection", choices=("legacy", "fair"), help="Change future allocator selection only")
    parser.add_argument("--provider", choices=("x", "threads", "linkedin"), help="Provider subtree to preview or patch")
    parser.add_argument("--daily-target", type=int)
    parser.add_argument("--development-max", type=int)
    parser.add_argument("--commercial-min", type=int)
    parser.add_argument("--apply", action="store_true", help="Apply an exact reviewed provider patch")
    parser.add_argument("--expected-sha256", help="Fresh review_sha256 returned by provider-patch preview")
    return parser


def _provider_patch_requested(args: argparse.Namespace) -> bool:
    return bool(
        args.provider
        or args.daily_target is not None
        or args.development_max is not None
        or args.commercial_min is not None
        or args.apply
        or args.expected_sha256
    )


def _validate_mode(args: argparse.Namespace) -> None:
    patch = _provider_patch_requested(args)
    selected = sum(bool(value) for value in (args.write_default, args.selection, patch))
    if selected > 1:
        raise PortfolioError("Choose exactly one policy change mode: default, selection, or provider patch")
    if args.force and not args.write_default:
        raise PortfolioError("--force is only valid with --write-default")
    if patch:
        if not args.provider:
            raise PortfolioError("Provider policy patch requires --provider")
        if all(value is None for value in (args.daily_target, args.development_max, args.commercial_min)):
            raise PortfolioError("Provider policy patch requires at least one bounded field")
        if args.apply and not args.expected_sha256:
            raise PortfolioError("--apply requires --expected-sha256 from a fresh policy preview")
        if args.expected_sha256 and not args.apply:
            raise PortfolioError("--expected-sha256 is only valid with --apply")


def _emit(value, *, json_mode: bool) -> None:
    if json_mode or isinstance(value, dict):
        print(json.dumps(value, indent=2, ensure_ascii=False))
    else:
        print(value)


def main() -> None:
    args = _parser().parse_args()
    try:
        _validate_mode(args)
        if args.selection:
            from ocpf_post.portfolio_queue import set_selection
            with local_store.locked(policy_file()):
                path = set_selection(args.selection)
            result = {
                "schema_version": 1,
                "result": "updated",
                "selection": args.selection,
                "policy_file": str(path),
                "boundary": "Future selection only; existing reservations, provider effects and budgets are unchanged.",
            }
            _emit(result if args.json else f"Selection set to {args.selection}: {path}", json_mode=args.json)
            return
        if args.write_default:
            with local_store.locked(policy_file()):
                path = write_default_policy(force=args.force)
            result = {
                "schema_version": 1,
                "result": "updated",
                "policy_file": str(path),
                "boundary": "Built-in conservative policy written locally; no provider call or publication occurred.",
            }
            _emit(result if args.json else f"Wrote default portfolio policy: {path}", json_mode=args.json)
            return
        if _provider_patch_requested(args):
            kwargs = {
                "daily_target": args.daily_target,
                "development_max": args.development_max,
                "commercial_min": args.commercial_min,
            }
            result = (
                apply_provider_patch(
                    args.provider,
                    expected_sha256=args.expected_sha256,
                    **kwargs,
                )
                if args.apply
                else preview_provider_patch(args.provider, **kwargs)
            )
            _emit(result, json_mode=True)
            return
        _emit(load_policy(), json_mode=True)
    except (PortfolioError, BlockingIOError) as exc:
        _die(str(exc))


if __name__ == "__main__":
    main()
