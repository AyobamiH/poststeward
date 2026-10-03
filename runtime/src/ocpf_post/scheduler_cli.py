from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from datetime import datetime
from zoneinfo import ZoneInfo

from ocpf_post.scheduler import (
    DEFAULT_TIMEZONE,
    RunnerBusy,
    ScheduleError,
    cancel_schedule,
    create_schedule,
    due_schedules,
    get_schedule,
    iter_schedule_events,
    run_due,
    schedule_records,
)


_SAFE_SCHEDULE_FIELDS = (
    "schedule_id",
    "status",
    "campaign",
    "provider",
    "account_id",
    "account_label",
    "run_at",
    "display_timezone",
    "text_sha256",
    "payload_source",
    "recorded_at",
    "updated_at",
    "last_event",
    "detail",
    "post_id",
    "url",
    "receipt_status",
    "readback_verified",
    "runner_pid",
    "retry_at",
    "preflight_attempts",
    "failure_class",
    "failure_stage",
    "provider_http_status",
    "automatic_retry",
    "provider_problem_type",
    "provider_reason",
    "provider_error_code",
    "circuit_failure_class",
    "circuit_retry_at",
    "provider_problem_type",
    "provider_reason",
    "provider_error_code",
    "circuit_failure_class",
    "circuit_retry_at",
)

_SAFE_EVENT_FIELDS = (
    "event",
    "status",
    "schedule_id",
    "recorded_at",
    "detail",
    "post_id",
    "url",
    "receipt_status",
    "readback_verified",
    "runner_pid",
    "retry_at",
    "preflight_attempts",
    "failure_class",
    "failure_stage",
    "provider_http_status",
    "automatic_retry",
)


def _die(message: str, code: int = 1) -> None:
    print(f"Error: {message}", file=sys.stderr)
    raise SystemExit(code)


def _local_time(record: dict) -> str:
    run_at = datetime.fromisoformat(str(record["run_at"]).replace("Z", "+00:00"))
    zone_name = str(record.get("display_timezone") or DEFAULT_TIMEZONE)
    try:
        zone = ZoneInfo(zone_name)
    except Exception:
        return str(record["run_at"])
    return run_at.astimezone(zone).strftime("%Y-%m-%d %H:%M %Z")


def _print_schedule(record: dict) -> None:
    print(f"Schedule: {record.get('schedule_id')}")
    print(f"Status:   {record.get('status')}")
    print(f"Campaign: {record.get('campaign')}")
    print(f"Provider: {record.get('provider')}")
    print(f"Account:  {record.get('account_label')} ({record.get('account_id')})")
    print(f"Run at:   {_local_time(record)} [{record.get('run_at')} UTC]")
    print(f"SHA256:   {record.get('text_sha256')}")
    if record.get("retry_at"):
        print(f"Retry at: {record.get('retry_at')} UTC")
        print(f"Preflight attempts: {record.get('preflight_attempts') or 0}")
    if record.get("failure_class"):
        print(f"Failure class: {record.get('failure_class')}")
    if record.get("url"):
        print(f"URL:      {record.get('url')}")
    if record.get("detail"):
        print(f"Detail:   {record.get('detail')}")


def _safe_subset(value: dict, fields: tuple[str, ...]) -> dict:
    return {key: value[key] for key in fields if key in value}


def _recovery_action(record: dict) -> str:
    status = str(record.get("status") or "unknown")
    if status == "failed":
        return (
            "Terminal failure. Do not rerun this schedule. Use Detail and the event history to repair the "
            "underlying provider/account condition, then re-verify identity and consequence safety before "
            "authorising any fresh reviewed schedule."
        )
    if status == "ambiguous_effect":
        return (
            "Provider effect is uncertain. Do not retry or create replacement work until provider/readback "
            "evidence proves whether the external post exists."
        )
    if status == "drift_blocked":
        return (
            "No consequence was authorised after drift was detected. Reconcile the stored payload, account "
            "binding or vault authority before creating fresh reviewed work."
        )
    if status == "duplicate_blocked":
        return "A terminal receipt already exists. Treat that receipt as the authority; do not republish."
    if status == "executing":
        return (
            "This item was claimed and is never auto-retried after a crash. Inspect the runner/event and "
            "receipt evidence before taking any further consequence."
        )
    if status == "published_unverified":
        return (
            "Publication has a durable provider ID, but exact provider text was not proven by readback. "
            "Compare the live provider body with the frozen payload evidence when read authority or manual "
            "inspection is available; do not republish."
        )
    if status == "published_verified":
        return "Publication is terminal and readback-verified. No recovery action is required."
    if status == "cancelled":
        return "Schedule is terminally cancelled. No provider consequence should be attempted from this record."
    if status == "scheduled" and record.get("failure_class") == "provider_unavailable" and record.get("retry_at"):
        return (
            "A read-only provider preflight was transiently unavailable before any publish request began. "
            f"The schedule remains active and the runner will not try again before {record.get('retry_at')}. "
            "Cancellation remains available; do not create a duplicate replacement while this reservation is active."
        )
    if status == "scheduled":
        return "Schedule remains pending. This command is inspection-only and makes no scheduling or provider change."
    return "Inspect the immutable event history before taking any consequence; no retry is implied by this status."


def _payload_integrity(record: dict) -> dict:
    text = str(record.get("text") or "")
    expected_sha = str(record.get("text_sha256") or "") or None
    calculated_sha = hashlib.sha256(text.encode("utf-8")).hexdigest() if text else None
    status = str(record.get("status") or "")
    readback_verified = record.get("readback_verified") is True
    if status == "published_verified" and readback_verified:
        verification_state = "exact_payload_verified"
    elif status == "published_unverified":
        verification_state = "durable_provider_id_only"
    elif status == "ambiguous_effect":
        verification_state = "provider_effect_ambiguous"
    else:
        verification_state = "not_provider_verified"
    paragraphs = len([part for part in re.split(r"\n\s*\n", text) if part.strip()]) if text else 0
    result = {
        "stored_text_present": bool(text),
        "expected_text_characters": len(text) if text else 0,
        "expected_nonempty_lines": sum(bool(line.strip()) for line in text.splitlines()) if text else 0,
        "expected_paragraphs": paragraphs,
        "expected_text_sha256": expected_sha,
        "calculated_text_sha256": calculated_sha,
        "local_payload_hash_matches": bool(expected_sha and calculated_sha and expected_sha == calculated_sha),
        "provider_readback_verified": readback_verified,
        "provider_text_match_proven": verification_state == "exact_payload_verified",
        "verification_state": verification_state,
        "boundary": (
            "Local frozen-text hash/length prove what Post-Once authorised. A durable provider ID without "
            "readback does not prove that the provider stored the complete body."
        ),
    }
    if record.get("provider") == "linkedin" and record.get("account_id"):
        try:
            from ocpf_post.providers.linkedin import recorded_read_permission
            result["read_authority"] = recorded_read_permission(str(record["account_id"]), "posts")
        except (OSError, ValueError, KeyError, TypeError, RuntimeError):
            result["read_authority"] = {
                "status": "unavailable",
                "purpose": "posts",
                "boundary": "Local LinkedIn read-authority metadata could not be resolved; no provider call was attempted.",
            }
    return result


def _inspection_payload(record: dict) -> dict:
    schedule_id = str(record.get("schedule_id") or "")
    raw_events = [
        event for event in iter_schedule_events()
        if str(event.get("schedule_id") or "") == schedule_id
    ]
    events = [_safe_subset(event, _SAFE_EVENT_FIELDS) for event in raw_events]
    historical_consequence = any(
        str(event.get("event") or "") in {
            "claimed", "publication_part_created", "completed", "ambiguous_effect",
        }
        or str(event.get("failure_stage") or "") == "provider_consequence"
        for event in raw_events
    )
    return {
        "schema_version": 1,
        "read_only": True,
        # Compatibility field: this describes the inspect invocation, not historical execution.
        "provider_consequence_attempted": False,
        "inspection_consequence_attempted": False,
        "historical_provider_consequence_attempted": historical_consequence,
        "schedule": _safe_subset(record, _SAFE_SCHEDULE_FIELDS),
        "payload_integrity": _payload_integrity(record),
        "events": events,
        "recovery_action": _recovery_action(record),
        "inspection_boundary": (
            "Inspection is read-only. provider_consequence_attempted=false refers only to this inspection "
            "command; historical_provider_consequence_attempted reports whether the stored event history "
            "shows an earlier provider consequence."
        ),
    }


def cmd_create(args: argparse.Namespace) -> None:
    try:
        record = create_schedule(
            campaign=args.campaign,
            provider=args.provider,
            at=args.at,
            timezone_name=args.timezone,
        )
    except ScheduleError as exc:
        _die(str(exc), 2)
    print("SCHEDULED — no external post was created.")
    _print_schedule(record)


def cmd_list(args: argparse.Namespace) -> None:
    records = schedule_records()
    if not args.all:
        records = [record for record in records if record.get("status") in {"scheduled", "executing"}]
    if args.json:
        print(json.dumps(records, indent=2, ensure_ascii=False))
        return
    if not records:
        print("No matching schedules.")
        return
    for index, record in enumerate(records):
        if index:
            print()
        _print_schedule(record)


def cmd_inspect(args: argparse.Namespace) -> None:
    record = get_schedule(args.schedule_id)
    if record is None:
        _die(f"Unknown schedule: {args.schedule_id}", 2)
    payload = _inspection_payload(record)
    if args.json:
        print(json.dumps(payload, indent=2, ensure_ascii=False))
        return
    _print_schedule(payload["schedule"])
    integrity = payload["payload_integrity"]
    print(
        "\nPayload integrity: "
        f"chars={integrity['expected_text_characters']} "
        f"paragraphs={integrity['expected_paragraphs']} "
        f"local_hash_match={integrity['local_payload_hash_matches']} "
        f"verification={integrity['verification_state']}"
    )
    print("\nEvent history:")
    events = payload["events"]
    if not events:
        print("  No matching schedule events were found.")
    for event in events:
        summary = f"  {event.get('recorded_at') or '?'} {event.get('event') or '?'} -> {event.get('status') or '?'}"
        print(summary)
        if event.get("detail"):
            print(f"    Detail: {event['detail']}")
    print(f"\nAction: {payload['recovery_action']}")
    print("Inspection only. No provider consequence or schedule mutation was attempted.")


def cmd_cancel(args: argparse.Namespace) -> None:
    try:
        record = cancel_schedule(args.schedule_id)
    except ScheduleError as exc:
        _die(str(exc), 2)
    print("CANCELLED — no provider consequence will be attempted for this schedule.")
    _print_schedule(record)


def cmd_run_due(args: argparse.Namespace) -> None:
    if args.check:
        records = due_schedules(limit=args.limit)
        if not records:
            print("No schedules are due.")
            return
        print(f"Due schedules: {len(records)}")
        for index, record in enumerate(records):
            if index:
                print()
            _print_schedule(record)
        print("\nCheck only. No provider consequence was attempted.")
        return

    try:
        if __import__("os").environ.get("POSTSTEWARD_REQUIRE_CLOUD_FENCE") == "1":
            from ocpf_post.poststeward_bridge import run_once
            from ocpf_post.poststeward_cloud import CloudError
            try:
                run_once()
            except CloudError as exc:
                _die(f"Cloud executor command/lease check blocked [{exc.code}]", 3)
        from ocpf_post.run_due_cycle import cycle
        # Preserve the cloud bridge/fence above; signal the existing allocator only.
        results = run_due(limit=args.limit)
        cycle(limit=args.limit, due_runner=lambda **_kwargs: results)
    except RunnerBusy as exc:
        _die(str(exc), 3)
    except ScheduleError as exc:
        _die(str(exc), 2)

    if not results:
        print("No schedules are due.")
        return
    attention = False
    for index, record in enumerate(results):
        if index:
            print()
        _print_schedule(record)
        status = record.get("status")
        if status in {"published_verified", "published_unverified"}:
            continue
        if status == "scheduled" and record.get("failure_class") == "provider_unavailable" and record.get("retry_at"):
            continue
        attention = True
    if attention:
        raise SystemExit(4)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ocpf-post",
        description="Durable, fail-closed scheduling control plane for post-once",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    schedule = sub.add_parser("schedule", help="Create, inspect or cancel durable schedules")
    schedule_sub = schedule.add_subparsers(dest="schedule_command", required=True)

    create = schedule_sub.add_parser("create", help="Authorise one future provider consequence")
    create.add_argument("--provider", required=True, choices=["x", "threads", "linkedin"])
    create.add_argument("--campaign", required=True)
    create.add_argument("--at", required=True, help="YYYY-MM-DD HH:MM Europe/London or ISO-8601 with offset")
    create.add_argument("--timezone", default=DEFAULT_TIMEZONE, help="Timezone when --at has no zone")
    create.set_defaults(func=cmd_create)

    listing = schedule_sub.add_parser("list", help="List active schedules")
    listing.add_argument("--all", action="store_true", help="Include terminal schedule history")
    listing.add_argument("--json", action="store_true")
    listing.set_defaults(func=cmd_list)

    inspect = schedule_sub.add_parser("inspect", help="Inspect one schedule and its immutable event history")
    inspect.add_argument("schedule_id")
    inspect.add_argument("--json", action="store_true")
    inspect.set_defaults(func=cmd_inspect)

    cancel = schedule_sub.add_parser("cancel", help="Cancel a schedule before it is claimed")
    cancel.add_argument("schedule_id")
    cancel.set_defaults(func=cmd_cancel)

    run = sub.add_parser("run-due", help="Execute due schedules through the proven provider consequence path")
    run.add_argument("--limit", type=int, default=20)
    run.add_argument("--check", action="store_true", help="Show due schedules without publishing")
    run.set_defaults(func=cmd_run_due)
    return parser


def main() -> None:
    args = build_parser().parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
