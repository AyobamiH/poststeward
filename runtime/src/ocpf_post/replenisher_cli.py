from __future__ import annotations

import argparse
import json
import sys

from ocpf_post.portfolio_source_loader import PortfolioSourceError, merged_source_profiles
from ocpf_post.admission import AdmissionError, decide as admission_decision
from ocpf_post.admission_runtime import controlled_refresh
from ocpf_post.replenisher import ReplenisherError, replenisher_status  # noqa: E402
from ocpf_post.replenisher_reconcile import reconcile_runtime_sources  # noqa: E402


def _error_envelope(code: str, message: str, *, retryable: bool,
                    metadata: dict | None = None) -> dict:
    return {
        "schema_version": 1,
        "ok": False,
        "error": {
            "code": code,
            "message": message,
            "retryable": retryable,
            "metadata": metadata or {},
        },
        "consequence": "NO_PROVIDER_EFFECT",
    }


def _die(args: argparse.Namespace, message: str, *, error_code: str = "REPLENISH_UNAVAILABLE",
         retryable: bool = False, exit_code: int = 2, metadata: dict | None = None) -> None:
    if getattr(args, "json", False):
        print(json.dumps(
            _error_envelope(error_code, message, retryable=retryable, metadata=metadata),
            indent=2, ensure_ascii=False,
        ))
    else:
        print(f"Error: {message}", file=sys.stderr)
    raise SystemExit(exit_code)


def _typed_error(args: argparse.Namespace, exc: Exception, *, default_code: str) -> None:
    text = str(exc)
    if "source-onboarding operation is active" in text:
        from ocpf_post.onboarding import coordination_status
        _die(
            args,
            "Another source operation is active; wait for the current holder to finish.",
            error_code="SOURCE_OPERATION_ACTIVE",
            retryable=True,
            metadata=coordination_status("source-onboarding"),
        )
    _die(
        args,
        type(exc).__name__,
        error_code=default_code,
        retryable=isinstance(exc, (BlockingIOError, TimeoutError)),
        metadata={"error_type": type(exc).__name__},
    )


def cmd_status(args: argparse.Namespace) -> None:
    try:
        result = replenisher_status()
        configured_sources = len(merged_source_profiles()["projects"])
        admission = admission_decision()
    except (ReplenisherError, PortfolioSourceError, AdmissionError, ValueError, OSError) as exc:
        _typed_error(args, exc, default_code="REPLENISH_STATUS_UNAVAILABLE")
    result = dict(result)
    result["configured_sources"] = configured_sources
    result["admission"] = {**admission, "diagnostic_only": True, "active_gate": "scoped_admission"}
    from ocpf_post.scoped_admission import status as scoped_status
    from ocpf_post.source_observations import load as observation_state
    result["scoped_admission"] = scoped_status()
    result["source_observations"] = {
        name: {k: row.get(k) for k in (
            "status", "observed_at", "head_sha", "buffered_head_sha",
            "last_processed_sha", "expired_delivery_count",
        )}
        for name, row in observation_state()["projects"].items()
    }
    if args.json:
        print(json.dumps(result, indent=2, ensure_ascii=False))
        return
    print(f"Runtime campaigns: {result['runtime_campaign_count']}")
    print(f"Observed repositories: {result['observed_repositories']}")
    print(f"Configured source repositories: {configured_sources}")
    print("GitHub token: " + ("available" if result["github_token_available"] else "not configured (public repos only)"))
    print(f"Legacy aggregate backlog diagnostic: {'open' if admission['admitted'] else 'paused'} ({', '.join(admission['reasons'])})")
    print(f"Eligible unreserved inventory: {admission['metrics']['eligible_unreserved']}; aged: {admission['metrics']['aged_count']}; expiry-risk: {admission['metrics']['expiry_risk_count']}")


def cmd_acknowledge_gap(args: argparse.Namespace) -> None:
    from ocpf_post.source_observations import acknowledge_gap
    try:
        value = acknowledge_gap(
            project=args.project, apply=args.apply, expected_sha256=args.expected_sha256,
        )
    except (OSError, ValueError, RuntimeError) as exc:
        _typed_error(args, exc, default_code="SOURCE_GAP_ACKNOWLEDGEMENT_UNAVAILABLE")
    print(json.dumps(value, indent=2, ensure_ascii=False))


def cmd_observe(args: argparse.Namespace) -> None:
    from ocpf_post.source_observations import observe
    try:
        value = observe(
            apply=args.apply, project=args.project, budget_seconds=args.budget_seconds,
        )
    except (OSError, ValueError, RuntimeError) as exc:
        _typed_error(args, exc, default_code="SOURCE_OBSERVATION_UNAVAILABLE")
    print(json.dumps(value, indent=2, ensure_ascii=False))


def cmd_refresh(args: argparse.Namespace) -> None:
    try:
        result = controlled_refresh(apply=args.apply, project=args.project)
    except (ReplenisherError, PortfolioSourceError, AdmissionError, ValueError, OSError) as exc:
        _typed_error(args, exc, default_code="REPLENISH_REFRESH_UNAVAILABLE")
    if args.json:
        print(json.dumps(result, indent=2, ensure_ascii=False))
        return
    for project in result["projects"]:
        if project["status"] == "ok":
            suffix = f" created={project.get('created', 0)} pending_events={project.get('pending_events', 0)}"
        elif project["status"] == "admission_paused":
            suffix = f" ({', '.join(project.get('reasons') or ['backpressure'])})"
        else:
            suffix = f" ({project.get('detail') or project.get('source_reason')})"
        print(f"{project['project']}: {project['status']}{suffix}")
    print(f"Static campaigns {'created' if args.apply else 'planned'}: {len(result['static_campaigns'])}")
    print(f"Development-event campaigns {'created' if args.apply else 'planned'}: {len(result['event_campaigns'])}")
    print(f"Evidence-grounded campaigns {'created' if args.apply else 'planned'}: {len(result.get('generative_campaigns') or [])}")
    fallback = result.get("generative") if isinstance(result.get("generative"), dict) else {}
    if fallback:
        print(
            "Optional API fallback: "
            f"limit={fallback.get('portfolio_daily_limit')} "
            f"used={fallback.get('api_candidates_used')} "
            f"remaining={fallback.get('api_candidates_remaining')} "
            f"projects_blocked_by_global_limit={fallback.get('projects_blocked_by_global_limit')}"
        )
        if fallback.get("failure_categories"):
            print("Fallback failures: " + ", ".join(fallback["failure_categories"]))
    print(result["boundary"])
    if not args.apply:
        print("Dry refresh only. Apply is separately subject to current admission pressure and does not confer scheduling authority.")


def cmd_recover_generated(args: argparse.Namespace) -> None:
    from ocpf_post.generated_supply_recovery import (
        GeneratedRecoveryError,
        preview as preview_generated_recovery,
        recover as apply_generated_recovery,
    )
    try:
        if args.apply:
            if not args.expected_sha256:
                _die(
                    args,
                    "--apply requires --expected-sha256 from a fresh recovery preview",
                    error_code="RECOVERY_REVIEW_REQUIRED",
                )
            result = apply_generated_recovery(
                args.review_file,
                provider=args.provider,
                expected_sha256=args.expected_sha256,
            )
        else:
            result = preview_generated_recovery(
                args.review_file,
                provider=args.provider,
            )
    except GeneratedRecoveryError as exc:
        _die(
            args,
            str(exc),
            error_code="GENERATED_RECOVERY_INVALID",
            retryable=False,
        )
    except (BlockingIOError, OSError, ValueError, RuntimeError) as exc:
        _typed_error(args, exc, default_code="GENERATED_RECOVERY_UNAVAILABLE")

    if args.json:
        print(json.dumps(result, indent=2, ensure_ascii=False))
        return

    if args.apply:
        print(f"Recovered successor campaigns: {result['created_count']}")
        print(f"Already recovered: {result['already_recovered_count']}")
        print(f"Blocked during apply: {result['blocked_count']}")
        for row in result.get("created", [])[:20]:
            print(
                f"{row['status'].upper()} {row['source_campaign']} -> "
                f"{row['successor_campaign']} ({row['provider']})"
            )
        if len(result.get("created", [])) > 20:
            print("Output bounded; use --json for the complete result.")
    else:
        print(f"Reviewed generated campaigns: {result['reviewed_generated_campaigns']}")
        print(f"Recoverable now: {result['recoverable_count']}")
        print("States: " + json.dumps(result["states"], sort_keys=True))
        print(f"review_sha256: {result['review_sha256']}")
        print("Preview only. Re-run with --apply and --expected-sha256 to create successor inventory.")
    print(result["boundary"])


def cmd_reconcile(args: argparse.Namespace) -> None:
    try:
        result = reconcile_runtime_sources()
    except (OSError, ValueError, RuntimeError) as exc:
        _typed_error(args, exc, default_code="SOURCE_RECONCILIATION_UNAVAILABLE")
    if args.json:
        print(json.dumps(result, indent=2, ensure_ascii=False))
        return
    if not result:
        print("No runtime campaigns were superseded by a newer README snapshot.")
        return
    for item in result:
        print(f"SUPERSEDED {item['campaign']} {item['previous_readme_sha']} -> {item['current_readme_sha']}")


def cmd_lock_status(args: argparse.Namespace) -> None:
    from ocpf_post.onboarding import coordination_status
    value = coordination_status("source-onboarding")
    if args.json:
        print(json.dumps(value, indent=2, ensure_ascii=False))
    elif value.get("status") == "active":
        print(
            "Source operation active: "
            f"operation={value.get('operation') or 'unknown'} "
            f"holder={value.get('holder_identity') or 'unknown'} "
            f"age_seconds={value.get('age_seconds')}"
        )
    else:
        print("Source operation lock: " + str(value.get("status") or "unknown"))


def cmd_wait(args: argparse.Namespace) -> None:
    from ocpf_post.onboarding import wait_for_coordination
    try:
        value = wait_for_coordination(
            "source-onboarding", timeout_seconds=args.timeout, poll_seconds=args.poll_interval,
        )
    except ValueError as exc:
        _typed_error(args, exc, default_code="INVALID_WAIT_ARGUMENT")
    if args.json:
        print(json.dumps(value, indent=2, ensure_ascii=False))
    elif value.get("wait_status") == "available":
        print(f"Source operation lock became available after {value.get('waited_seconds')}s.")
    elif value.get("wait_status") == "timeout":
        print(
            "Source operation lock is still active after "
            f"{value.get('waited_seconds')}s; holder={value.get('holder_identity') or 'unknown'}."
        )
    else:
        print("Source operation lock status unavailable.")
    if value.get("wait_status") == "timeout":
        raise SystemExit(3)
    if value.get("wait_status") == "unavailable":
        raise SystemExit(2)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ocpf-post replenish",
        description="Evidence-to-campaign replenishment above the portfolio allocator",
    )
    sub = parser.add_subparsers(dest="replenish_command", required=True)

    status = sub.add_parser("status", help="Show runtime inventory, source cursor and admission-pressure state")
    status.add_argument("--json", action="store_true")
    status.set_defaults(func=cmd_status)

    lock_status = sub.add_parser("lock-status", help="Inspect the source-operation coordination lease")
    lock_status.add_argument("--json", action="store_true")
    lock_status.set_defaults(func=cmd_lock_status)

    wait = sub.add_parser("wait", help="Wait bounded time for the source-operation lock to become available")
    wait.add_argument("--timeout", type=float, default=30.0)
    wait.add_argument("--poll-interval", type=float, default=0.1)
    wait.add_argument("--json", action="store_true")
    wait.set_defaults(func=cmd_wait)

    acknowledge = sub.add_parser("acknowledge-gap", help="Review an archived, explicit source-gap checkpoint; never automatic")
    acknowledge.add_argument("--project", required=True)
    acknowledge.add_argument("--apply", action="store_true")
    acknowledge.add_argument("--expected-sha256")
    acknowledge.add_argument("--json", action="store_true")
    acknowledge.set_defaults(func=cmd_acknowledge_gap)

    observe = sub.add_parser("observe", help="Read bounded source evidence independently of admission pressure")
    observe.add_argument("--apply", action="store_true")
    observe.add_argument("--project")
    observe.add_argument("--budget-seconds", type=int, default=180)
    observe.add_argument("--json", action="store_true")
    observe.set_defaults(func=cmd_observe)

    refresh = sub.add_parser("refresh", help="Admit saved source observations within destination budgets")
    refresh.add_argument("--apply", action="store_true", help="Admit runtime campaigns per destination without fetching GitHub")
    refresh.add_argument("--json", action="store_true")
    refresh.add_argument("--project", help="Inspect/replenish only this enabled project")
    refresh.set_defaults(func=cmd_refresh)

    recover_generated = sub.add_parser(
        "recover-generated",
        help="Recover reviewed legacy generated copy as fresh admission-controlled successor inventory",
    )
    recover_generated.add_argument("--review-file", required=True, help="Reviewed JSON array containing campaign and exact text")
    recover_generated.add_argument("--provider", choices=("x", "threads", "linkedin"), default="linkedin")
    recover_generated.add_argument("--apply", action="store_true", help="Create admitted successor campaigns; never schedules or publishes")
    recover_generated.add_argument("--expected-sha256", help="review_sha256 from a fresh dry recovery preview")
    recover_generated.add_argument("--json", action="store_true")
    recover_generated.set_defaults(func=cmd_recover_generated)

    reconcile = sub.add_parser("reconcile", help="Disable runtime campaigns whose README source snapshot has been superseded")
    reconcile.add_argument("--json", action="store_true")
    reconcile.set_defaults(func=cmd_reconcile)

    from ocpf_post.runtime_sources_cli import add_source_commands
    add_source_commands(sub)
    return parser


def main() -> None:
    args = build_parser().parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
