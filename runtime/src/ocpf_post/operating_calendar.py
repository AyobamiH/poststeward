"""One local view of committed work, observed outcomes and provisional choices."""
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from ocpf_post import local_store
from ocpf_post.state import state_dir


def report(*, now=None, horizon_minutes=1440):
    from ocpf_post.portfolio_cross_platform import plan_refill
    from ocpf_post.scheduler import schedule_records
    from ocpf_post.schedule_semantics import ACTIVE_STATUSES
    from ocpf_post.portfolio_queue import capture_inputs, explanations
    from ocpf_post.portfolio import load_policy
    now = now or datetime.now(timezone.utc)
    if not 1 <= horizon_minutes <= 1440:
        raise ValueError("Calendar horizon must be 1..1440 minutes")
    policy = load_policy()
    # capture_inputs supplies one frozen selection and exclusions. The public
    # planner accepts these inputs, so the report never reserves a slot.
    before = schedule_records()
    inputs = capture_inputs(now, horizon_minutes, policy)
    plan = plan_refill(now=now, horizon_minutes=horizon_minutes, policy=policy, inputs=inputs)
    zone = ZoneInfo(plan["timezone"])
    lower, upper = now - timedelta(hours=24), now + timedelta(minutes=horizon_minutes)
    entries = []
    def parsed(value):
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    keys = set()
    schedules = schedule_records()
    for row in schedules:
        run = parsed(row["run_at"])
        active = row["status"] in ACTIVE_STATUSES
        observed = parsed(row.get("updated_at") or row["run_at"])
        if (active and run <= upper) or (not active and lower <= observed <= now):
            entry = {k: row.get(k) for k in ("schedule_id", "campaign", "provider", "account_id", "status", "run_at", "updated_at", "post_id", "url", "readback_verified", "retry_at")}
            entry.update(kind="reservation" if active else "observed_outcome", local_time=run.astimezone(zone).isoformat(),
                         reason="saved_schedule" if active else "local_schedule_outcome")
            entries.append(entry)
            keys.add((row["campaign"], row["provider"], row.get("account_id")))
    reasons = {(d.get("campaign"), d.get("provider"), d.get("run_at")): d["reason"] for d in plan.get("decisions", []) if d.get("campaign")}
    for row in plan["plan"]:
        if (row["campaign"], row["provider"], row.get("account_id")) in keys:
            continue
        entries.append({**{k: row.get(k) for k in ("campaign", "provider", "account_id", "project", "run_at")},
                        "kind": "forecast", "status": "projected", "local_time": parsed(row["run_at"]).astimezone(zone).isoformat(),
                        "reason": reasons.get((row["campaign"], row["provider"], row["run_at"]), "eligible_selection")})
    entries.sort(key=lambda r: (r["run_at"], r["provider"], str(r.get("account_id")), r["campaign"]))
    accounts = {}
    for row in entries:
        key = row["provider"] + ":" + str(row.get("account_id") or "unknown")
        account = accounts.setdefault(key, {"next_reservation": None, "next_forecast": None, "latest_outcome": None})
        field = "next_reservation" if row["kind"] == "reservation" else "next_forecast" if row["kind"] == "forecast" else "latest_outcome"
        if field == "latest_outcome":
            if not account[field] or row["updated_at"] > account[field]["updated_at"]:
                account[field] = row
        elif account[field] is None:
            account[field] = row
    waiting = explanations(inputs, plan)
    risks = [r for r in waiting if r["expiry_risk"] == "unselected_expires_within_horizon"]
    return {"schema_version": 1, "observed_at": now.isoformat(), "timezone": plan["timezone"],
            "horizon_minutes": horizon_minutes, "status": "observed" if before == schedules else "snapshot_changed", "accounts": accounts, "entries": entries,
            "unselected_count": sum(r["status"] == "waiting_not_selected_in_horizon" for r in waiting),
            "deadline_risks": risks, "decisions": plan.get("decisions", []),
            "capacity": plan["capacity"], "account_capacity": plan.get("account_capacity", {}),
            "boundary": "Local snapshot: forecasts can change; reservations are saved work; outcomes retain verified/unverified/ambiguous status. No publication guarantee or new reservation."}


def cmd_calendar(args):
    import json
    value = report(horizon_minutes=args.horizon_minutes)
    if args.save:
        local_store.write(state_dir() / "operating-calendar.json", value)
    if args.json:
        print(json.dumps(value, indent=2))
    else:
        print(f"Observed {value['observed_at']} | {value['timezone']}")
        for key, row in value["accounts"].items():
            reserved, forecast = row["next_reservation"], row["next_forecast"]
            print(f"{key}: reserved={reserved['local_time'] if reserved else 'none'}; forecast={forecast['local_time'] if forecast else 'none'}")
        print(f"Unselected: {value['unselected_count']}; deadlines without a slot: {len(value['deadline_risks'])}")
        print(value["boundary"])
