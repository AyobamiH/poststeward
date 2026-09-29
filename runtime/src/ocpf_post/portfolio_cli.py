from __future__ import annotations

import argparse
from collections import Counter
import json
import sys
from pathlib import Path

import ocpf_post.portfolio as portfolio_module
from ocpf_post.portfolio_cross_platform import plan_refill as cross_platform_plan_refill

# Cross-platform separation is a selection layer only. apply_refill still owns
# reservation creation and run-due remains the only automated publisher.
portfolio_module.plan_refill = cross_platform_plan_refill

from ocpf_post.portfolio import (  # noqa: E402
    PortfolioError,
    apply_refill,
    load_policy,
    portfolio_status,
    reconcile_allocator_schedules,
    write_default_policy,
)

plan_refill = cross_platform_plan_refill


def _die(message: str, code: int = 1) -> None:
    print(f"Error: {message}", file=sys.stderr)
    raise SystemExit(code)


def _print_plan(result: dict) -> None:
    print(f"Portfolio plan generated at {result['generated_at']}")
    print(f"Horizon: {result['horizon_minutes']} minutes")
    print(f"Timezone: {result['timezone']}")
    print(f"Selection: {result.get('selection', 'legacy')}")
    if result.get('selection') == 'fair':
        print("Fairness: project service window=24h; waiting age bands=6h")
        service = result.get("service_policy") or {}
        if service.get("cycle"):
            print("Service: " + " -> ".join(service["cycle"]) + "; empty protected shares remain borrowable")
    diversity = result.get("diversity") or {}
    if diversity:
        print(
            "Diversity: "
            f"topic_cooldown={diversity.get('topic_cooldown_hours')}h "
            f"avoid_consecutive_project={str(diversity.get('avoid_consecutive_project')).lower()} "
            f"family_penalty={diversity.get('family_repeat_penalty')} "
            + (f"same_day_project_penalty={diversity.get('same_day_project_penalty')}"
               if result.get('selection') != 'fair' else "")
        )
    cross_platform = result.get("cross_platform") or {}
    if cross_platform:
        print(f"Cross-platform: topic_cooldown={cross_platform.get('topic_cooldown_minutes')}m")
    plan = result.get("plan") or []
    if not plan:
        print("No eligible open slots in the current horizon.")
    else:
        for item in plan:
            print(
                f"{item['run_at']}  {item['provider']:<8}  {item['campaign']:<24} "
                f"{item['lane']:<11} p{item['priority']}  {item['project']}: {item['title']}"
            )
    replacements = result.get("cross_platform_replacements") or []
    if replacements:
        print(f"Cross-platform substitutions: {len(replacements)}")
        for item in replacements:
            print(
                f"  {item['provider']} {item['run_at']} "
                f"{item['replaced_campaign']} -> {item['replacement_campaign']}"
            )
    print()
    for provider, values in (result.get("capacity") or {}).items():
        print(
            f"{provider}: eligible={values['eligible_unscheduled']} planned={values['planned_in_horizon']} "
            f"post_target/day={values['daily_target']} reply_target/day={values['reply_target']}"
        )
    for provider, values in (result.get("capacity") or {}).items():
        if "daily_budget_used" in values:
            print(f"{provider}: day={values['local_day']} budget_used={values['daily_budget_used']} "
                  f"remaining={values['daily_budget_remaining']} target_met={values['daily_target_met']}")
    for identity, values in result.get("account_capacity", {}).items():
        print(f"{identity}: eligible={values['eligible_unscheduled']} planned={values['planned_in_horizon']} target/day={values['daily_target']} remaining={values['daily_budget_remaining']}")
    print(result.get("reply_boundary", ""))


def cmd_status(args: argparse.Namespace) -> None:
    try:
        result = portfolio_status()
    except PortfolioError as exc:
        _die(str(exc), 2)
    if args.json:
        print(json.dumps(result, indent=2, ensure_ascii=False)); return
    print(f"Eligible deliveries: {result['eligible_deliveries']}")
    print(f"Active schedules: {result['active_schedules']}")
    print(f"Allocator-owned active: {result['allocator_owned_active']}")
    print("Eligible by lane: " + ", ".join(f"{k}={v}" for k, v in sorted(result["eligible_by_lane"].items())))
    print("Eligible by provider: " + ", ".join(f"{k}={v}" for k, v in sorted(result["eligible_by_provider"].items())))
    for provider, targets in result["targets"].items():
        print(f"{provider}: {targets['posts_per_day']} posts/day target; {targets['replies_per_day']} replies/day target")
    for identity, settings in result.get("account_targets", {}).items():
        print(f"{identity}: target/day={settings['daily_target']} enabled={settings['enabled']}")
    pacing = result.get("release_pacing") or {}
    if pacing.get("mode") == "steady_originals":
        print("Steady originals: extra reserve does not increase today's normal amount.")
        for row in pacing.get("accounts", []):
            identity = row.get("identity") or row["provider"]
            print(f"{identity}: normal_originals/day={row['normal_originals_per_day']} hard_safety_ceiling={row['hard_daily_ceiling']}")
        print("One whole thread is one original; thread parts and API requests are different units. Existing bookings remain intact.")
    print(result["reply_boundary"])


def cmd_plan(args: argparse.Namespace) -> None:
    try:
        result = plan_refill(horizon_minutes=args.horizon_minutes)
    except PortfolioError as exc:
        _die(str(exc), 2)
    if args.json:
        print(json.dumps(result, indent=2, ensure_ascii=False)); return
    _print_plan(result)


def cmd_refill(args: argparse.Namespace) -> None:
    try:
        result = apply_refill(horizon_minutes=args.horizon_minutes) if args.apply else plan_refill(horizon_minutes=args.horizon_minutes)
    except PortfolioError as exc:
        _die(str(exc), 2)
    if args.json:
        print(json.dumps(result, indent=2, ensure_ascii=False))
    elif args.apply:
        cancelled = result.get("cancelled_for_freshness") or []
        scheduled = result.get("scheduled") or []
        errors = result.get("errors") or []
        print(f"Freshness cancellations: {len(cancelled)}")
        for record in cancelled:
            print(f"CANCELLED {record.get('schedule_id')} {record.get('campaign')}/{record.get('provider')}: {record.get('portfolio_reason')}")
        print(f"New reservations: {len(scheduled)}")
        for record in scheduled:
            print(f"SCHEDULED {record.get('schedule_id')} {record.get('campaign')}/{record.get('provider')} at {record.get('run_at')}")
        if errors:
            print(f"Reservation errors: {len(errors)}")
            for error in errors:
                print(f"ERROR {error['campaign']}/{error['provider']}: {error['error']}")
        for provider, values in (result.get("capacity") or {}).items():
            gap = max(0, int(values["daily_target"]) - int(values["eligible_unscheduled"]))
            print(
                f"{provider}: eligible={values['eligible_unscheduled']} planned={values['planned_in_horizon']} "
                f"target/day={values['daily_target']} inventory_gap_indicator={gap}"
            )
        for provider, values in (result.get("capacity") or {}).items():
            if "daily_budget_used" in values:
                print(f"{provider}: day={values['local_day']} budget_used={values['daily_budget_used']} "
                      f"remaining={values['daily_budget_remaining']} target_met={values['daily_target_met']}")
        for identity, values in result.get("account_capacity", {}).items():
            print(f"{identity}: eligible={values['eligible_unscheduled']} planned={values['planned_in_horizon']} target/day={values['daily_target']} remaining={values['daily_budget_remaining']}")
        print(result.get("reply_boundary", ""))
        _print_watch(result.get("queue_watch", {}))
    else:
        _print_plan(result)
        print("\nDry plan only. Re-run with --apply to create durable reservations; no provider post is created by refill.")
    if args.apply and result.get("errors"):
        raise SystemExit(4)


def _watch_inspection(result: dict, *, issue: str | None = None, provider: str | None = None,
                      project: str | None = None, account_id: str | None = None,
                      limit: int = 25) -> dict:
    if type(limit) is not int or not 1 <= limit <= 500:
        raise PortfolioError("Queue inspection limit must be between 1 and 500")
    filters = {"issue": issue, "provider": provider, "project": project, "account_id": account_id}
    rows = []
    for row in result.get("issues", []):
        if issue and row.get("code") != issue:
            continue
        if provider and row.get("provider") != provider:
            continue
        if project and row.get("project") != project:
            continue
        if account_id and str(row.get("account_id") or "") != account_id:
            continue
        rows.append(row)
    rows.sort(key=lambda row: (
        -float(row.get("age_hours") or 0),
        -int(row.get("unselected_evaluations") or 0),
        str(row.get("provider") or ""),
        str(row.get("project") or ""),
        str(row.get("campaign") or ""),
        str(row.get("code") or ""),
    ))
    groups: dict[tuple[str, str, str], dict] = {}
    for row in rows:
        key = (str(row.get("provider") or ""), str(row.get("account_id") or ""), str(row.get("project") or ""))
        group = groups.setdefault(key, {
            "provider": key[0], "account_id": key[1] or None, "project": key[2],
            "issue_rows": 0, "oldest_age_hours": 0.0,
            "max_waiting_evaluations": 0, "max_unselected_evaluations": 0,
        })
        group["issue_rows"] += 1
        group["oldest_age_hours"] = max(group["oldest_age_hours"], float(row.get("age_hours") or 0))
        group["max_waiting_evaluations"] = max(group["max_waiting_evaluations"], int(row.get("waiting_evaluations") or 0))
        group["max_unselected_evaluations"] = max(group["max_unselected_evaluations"], int(row.get("unselected_evaluations") or 0))
    ordered_groups = sorted(groups.values(), key=lambda group: (
        -int(group["issue_rows"]), -float(group["oldest_age_hours"]),
        group["provider"], str(group.get("account_id") or ""), group["project"],
    ))
    return {
        "schema_version": 1,
        "read_only": True,
        "queue_status": result.get("status"),
        "observed_at": result.get("observed_at"),
        "last_evaluated_at": result.get("last_evaluated_at"),
        "selection": result.get("selection"),
        "filters": filters,
        "matching_issue_rows": len(rows),
        "returned_issue_rows": min(len(rows), limit),
        "truncated": len(rows) > limit,
        "issue_counts": dict(Counter(str(row.get("code") or "unknown") for row in rows)),
        "groups": ordered_groups,
        "issues": rows[:limit],
        "capacity": result.get("capacity", {}),
        "account_capacity": result.get("account_capacity", {}),
        "survival": result.get("survival", {}),
        "boundary": (
            "Read-only bounded view over the stored queue report. Issue rows may contain multiple codes for one queue item. "
            "No observation, reservation, provider call, retry, capacity change or publication was attempted."
        ),
    }


def _print_watch(result: dict) -> None:
    if result.get("read_only") and "matching_issue_rows" in result:
        print(
            f"Queue inspection: status={result.get('queue_status', 'unavailable')} "
            f"matches={result['matching_issue_rows']} returned={result['returned_issue_rows']} "
            f"truncated={str(result['truncated']).lower()}"
        )
        filters = ", ".join(f"{k}={v}" for k, v in result.get("filters", {}).items() if v)
        if filters:
            print(f"Filters: {filters}")
        for group in result.get("groups", [])[:10]:
            account = f"/{group['account_id']}" if group.get("account_id") else ""
            print(
                f"  group {group['provider']}{account} {group['project']}: rows={group['issue_rows']} "
                f"oldest={group['oldest_age_hours']}h unselected_eval_max={group['max_unselected_evaluations']}"
            )
        for row in result.get("issues", [])[:10]:
            print(f"  {row['code']}: {row['campaign']}/{row['provider']} age={row['age_hours']}h; {row['reason']}")
        if result.get("truncated"):
            print("  Output is bounded; narrow filters or increase --limit to inspect more rows.")
        print(result.get("boundary", ""))
        return
    print(f"Queue watch: {result.get('status', 'unavailable')}; issues={json.dumps(result.get('issue_counts', {}))}")
    survival = result.get("survival") or {}
    if survival:
        print(
            "Inventory survival: "
            f"waiting={survival.get('waiting', 0)} "
            f"rescue_window={survival.get('rescue_window', 0)} "
            f"capacity_pressure={survival.get('capacity_pressure', 0)} "
            f"capacity_shortfall={survival.get('capacity_shortfall', 0)} "
            f"expired_unpublished={survival.get('expired_unpublished', 0)} "
            f"expired_after_rescue_window={survival.get('expired_after_rescue_window', 0)}"
        )
    for row in result.get("issues", [])[:5]:
        print(f"  {row['code']}: {row['campaign']}/{row['provider']} age={row['age_hours']}h; {row['reason']}")
    if len(result.get("issues", [])) > 5:
        print("  More exceptions are recorded; use portfolio watch --json for the complete report.")


def cmd_watch(args: argparse.Namespace) -> None:
    from ocpf_post.queue_watch import report, safe_observe
    inspection = any((args.issue, args.provider, args.project, args.account_id, args.limit is not None))
    if inspection and args.apply:
        _die("Filtered/bounded queue inspection is read-only; omit --apply", 2)
    try:
        if inspection:
            result = _watch_inspection(
                report(), issue=args.issue, provider=args.provider, project=args.project,
                account_id=args.account_id, limit=25 if args.limit is None else args.limit,
            )
        else:
            result = safe_observe() if args.apply else report(include_records=args.json)
    except PortfolioError as exc:
        _die(str(exc), 2)
    except (OSError, ValueError, KeyError, TypeError):
        result = {"status": "unavailable", "boundary": "Stored queue supervision could not be read; inspect locally."}
    if args.json:
        print(json.dumps(result, indent=2, ensure_ascii=False))
    else:
        _print_watch(result)
    status = result.get("queue_status") if result.get("read_only") else result.get("status")
    if status in {"unavailable", "stale", "not_observed"}:
        raise SystemExit(2)
    if status == "attention":
        raise SystemExit(3)


def cmd_reconcile(args: argparse.Namespace) -> None:
    try:
        result = reconcile_allocator_schedules()
    except PortfolioError as exc:
        _die(str(exc), 2)
    if args.json:
        print(json.dumps(result, indent=2, ensure_ascii=False)); return
    if not result:
        print("No allocator-owned schedules required freshness cancellation."); return
    for record in result:
        print(f"CANCELLED {record.get('schedule_id')} {record.get('campaign')}/{record.get('provider')}: {record.get('portfolio_reason')}")


def cmd_policy(args: argparse.Namespace) -> None:
    try:
        if args.selection:
            from ocpf_post.portfolio_queue import set_selection
            print(f"Selection set to {args.selection}: {set_selection(args.selection)}")
            print("Applies to future refills. Existing schedules, budgets and windows are unchanged.")
            return
        if args.write_default:
            path = write_default_policy(force=args.force)
            print(f"Wrote default portfolio policy: {path}")
            return
        print(json.dumps(load_policy(), indent=2, ensure_ascii=False))
    except PortfolioError as exc:
        _die(str(exc), 2)


def cmd_queue(args: argparse.Namespace) -> None:
    from ocpf_post.portfolio_queue import read_snapshot, replay, snapshot, write_artifact
    try:
        result = (snapshot(horizon_minutes=args.horizon_minutes) if args.portfolio_command == "snapshot"
                  else replay(read_snapshot(Path(args.file).expanduser())))
        if args.output:
            path = Path(args.output).expanduser()
            if path.exists():
                raise PortfolioError("Output already exists; choose a new path")
            write_artifact(path, result)
            print(json.dumps({"result": "written", "path": str(path),
                              "snapshot_sha256": result["snapshot_sha256"]}, indent=2))
        else:
            print(json.dumps(result, indent=2, ensure_ascii=False))
    except (PortfolioError, OSError, ValueError, KeyError, TypeError, AttributeError) as exc:
        _die(f"Queue inspection failed: {exc}", 2)


def cmd_volume_audit(args: argparse.Namespace) -> None:
    from ocpf_post.volume_audit import audit

    try:
        result = audit(day=args.date, timezone_name=args.timezone)
    except (PortfolioError, ValueError) as exc:
        _die(str(exc), 2)
    if args.json:
        print(json.dumps(result, indent=2, ensure_ascii=False))
        return
    print(f"Historical volume audit: {result['date']} | {result['timezone']}")
    for row in result["days"]:
        marker = "*" if row["date"] == result["date"] else " "
        print(
            f"{marker} {row['date']}: scheduled={row['scheduled_total']} "
            f"published_terminal={row['scheduled_published_terminal']} "
            f"nonpublished={row['scheduled_nonpublished']} "
            f"effects_on_day={row['publication_effects_on_day']}"
        )
    target = result["target"]
    print(
        "Target linkage: "
        f"schedule_effects={target['publication_effects_for_scheduled_work']} "
        f"scheduled_without_receipt={target['scheduled_without_receipt_effect']}"
    )
    if target["nonpublished_schedules"]:
        print("Non-published target schedules:")
        for row in target["nonpublished_schedules"]:
            print(
                f"  {row.get('schedule_id')} {row.get('provider')} "
                f"{row.get('campaign')} status={row.get('status')} "
                f"last_event={row.get('last_event')}"
            )
    observation = result["evidence_observation"]
    print(f"Evidence: {observation['code']} — {observation['detail']}")
    print(result["boundary"])


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="ocpf-post portfolio", description="Rolling, freshness-aware, diversity-controlled portfolio GTM allocator above the existing durable scheduler")
    sub = parser.add_subparsers(dest="portfolio_command", required=True)
    from ocpf_post.capacity_experiment import add_parsers
    add_parsers(sub)
    from ocpf_post.operating_calendar import cmd_calendar
    calendar = sub.add_parser("calendar", help="Show saved reservations, observed outcomes and provisional selections together")
    calendar.add_argument("--horizon-minutes", type=int, default=1440)
    calendar.add_argument("--json", action="store_true")
    calendar.add_argument("--save", action="store_true")
    calendar.set_defaults(func=cmd_calendar)
    status = sub.add_parser("status", help="Show eligible inventory, active reservations and daily targets"); status.add_argument("--json", action="store_true"); status.set_defaults(func=cmd_status)
    audit = sub.add_parser("volume-audit", help="Compare one local day of schedules and receipt-backed publication effects with its adjacent days")
    audit.add_argument("--date", required=True, help="Local calendar date in YYYY-MM-DD form")
    audit.add_argument("--timezone", help="IANA timezone; defaults to the portfolio policy timezone")
    audit.add_argument("--json", action="store_true")
    audit.set_defaults(func=cmd_volume_audit)
    plan = sub.add_parser("plan", help="Dry-plan the next rolling reservation horizon with diversity controls"); plan.add_argument("--horizon-minutes", type=int, default=None); plan.add_argument("--json", action="store_true"); plan.set_defaults(func=cmd_plan)
    refill = sub.add_parser("refill", help="Fill open near-term slots from fresh COPY-READY campaign inventory"); refill.add_argument("--horizon-minutes", type=int, default=None); refill.add_argument("--apply", action="store_true", help="Create durable reservations; never publishes directly"); refill.add_argument("--json", action="store_true"); refill.set_defaults(func=cmd_refill)
    reconcile = sub.add_parser("reconcile", help="Cancel allocator-owned schedules whose campaign is no longer fresh"); reconcile.add_argument("--json", action="store_true"); reconcile.set_defaults(func=cmd_reconcile)
    policy = sub.add_parser("policy", help="Show or update the operator policy")
    changes = policy.add_mutually_exclusive_group()
    changes.add_argument("--write-default", action="store_true")
    changes.add_argument("--selection", choices=("legacy", "fair"), help="Change selection for future refills; preserve cadence and existing reservations")
    policy.add_argument("--force", action="store_true"); policy.set_defaults(func=cmd_policy)
    capture = sub.add_parser("snapshot", help="Capture stable complete allocator inputs and baseline without publishing")
    capture.add_argument("--horizon-minutes", type=int, default=1440)
    capture.add_argument("--output", help="Write a new private JSON file instead of stdout")
    capture.set_defaults(func=cmd_queue)
    watch = sub.add_parser("watch", help="Read automatic waiting/deadline supervision or record a local observation")
    watch.add_argument("--apply", action="store_true", help="Observe local queue and receipts; never reserves, publishes or changes authority")
    watch.add_argument("--issue", help="Read-only: show only one issue code, for example waiting_too_long")
    watch.add_argument("--provider", choices=("x", "threads", "linkedin"), help="Read-only: restrict issues to one provider")
    watch.add_argument("--project", help="Read-only: restrict issues to one project")
    watch.add_argument("--account-id", help="Read-only: restrict issues to one expected provider account ID")
    watch.add_argument("--limit", type=int, help="Read-only: bound detailed issue rows (1-500; default 25 when filtering)")
    watch.add_argument("--json", action="store_true"); watch.set_defaults(func=cmd_watch)
    replay = sub.add_parser("replay", help="Compare baseline and fair selection entirely offline")
    replay.add_argument("--file", required=True); replay.add_argument("--output")
    replay.set_defaults(func=cmd_queue)
    return parser


def main() -> None:
    args = build_parser().parse_args(); args.func(args)


if __name__ == "__main__":
    main()
