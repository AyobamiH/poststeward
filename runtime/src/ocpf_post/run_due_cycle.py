"""Run due schedules and wake the canonical refill controller after queue consumption.

The one-minute publisher is the event-driven fast path. The existing 15-minute
portfolio-refill timer remains the periodic reconciliation safety net. This
module never implements allocation itself; it only signals the existing refill
service after a due schedule leaves the active queue.
"""
from __future__ import annotations

from datetime import datetime, timezone
import json
import subprocess
from typing import Any, Callable

from ocpf_post import local_store
from ocpf_post.scheduler import RunnerBusy, ScheduleError, run_due
from ocpf_post.state import state_dir

UTC = timezone.utc
SAFE_FIELDS = (
    "schedule_id", "campaign", "provider", "account_id", "status",
    "receipt_status", "post_id", "failure_class", "failure_stage",
    "retry_at", "automatic_retry",
)


def path():
    return state_dir() / "post-consumption-refill.json"


def _stamp(now: datetime | None = None) -> str:
    value = now or datetime.now(UTC)
    return value.astimezone(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _safe(row: dict[str, Any]) -> dict[str, Any]:
    return {key: row.get(key) for key in SAFE_FIELDS if key in row}


def consumed(results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Schedules that left the active queue and therefore changed supply."""
    return [
        row for row in results
        if isinstance(row, dict) and str(row.get("status") or "") != "scheduled"
    ]


def attention(results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Preserve the scheduler CLI's established attention semantics."""
    rows = []
    for row in results:
        if not isinstance(row, dict):
            rows.append({})
            continue
        status = str(row.get("status") or "")
        if status in {"published_verified", "published_unverified"}:
            continue
        if (
            status == "scheduled"
            and row.get("failure_class") == "provider_unavailable"
            and row.get("retry_at")
        ):
            continue
        rows.append(row)
    return rows


def wake_refill(
    *,
    runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
) -> dict[str, Any]:
    """Best-effort event signal; the periodic timer remains the safety net."""
    try:
        result = runner(
            [
                "systemctl", "--user", "start", "--no-block",
                "poststeward-portfolio-refill.service",
            ],
            text=True,
            capture_output=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return {
            "status": "deferred_to_periodic_timer",
            "error_type": type(exc).__name__,
        }
    if result.returncode != 0:
        return {
            "status": "deferred_to_periodic_timer",
            "systemctl_exit_code": int(result.returncode),
        }
    return {"status": "signalled"}


def cycle(
    *,
    limit: int = 20,
    due_runner: Callable[..., list[dict[str, Any]]] = run_due,
    refill_waker: Callable[[], dict[str, Any]] = wake_refill,
    now: datetime | None = None,
    persist: bool = True,
) -> dict[str, Any]:
    """Execute one normal due pass, then signal refill only after consumption."""
    if type(limit) is not int or not 1 <= limit <= 100:
        raise ValueError("run-due limit must be between 1 and 100")

    observed_at = _stamp(now)
    results = due_runner(limit=limit)
    changed = consumed(results)
    problems = attention(results)
    wake = refill_waker() if changed else {"status": "not_needed"}

    report = {
        "schema_version": 1,
        "observed_at": observed_at,
        "due_result_count": len(results),
        "consumed_count": len(changed),
        "attention_count": len(problems),
        "results": [_safe(row) for row in results],
        "consumed_schedule_ids": [
            str(row.get("schedule_id") or "") for row in changed if row.get("schedule_id")
        ][:100],
        "refill_wake": wake,
        "periodic_safety_net": "poststeward-portfolio-refill.timer",
        "boundary": (
            "Existing scheduler consequences only. A schedule leaving active state signals the existing "
            "refill controller asynchronously. This layer never allocates, authors, republishes, retries "
            "an uncertain provider effect, changes pacing or creates a second replenishment path."
        ),
    }
    if persist:
        try:
            local_store.write(path(), report)
        except (OSError, ValueError):
            report["evidence_persist_status"] = "unavailable"
        else:
            report["evidence_persist_status"] = "recorded"
    return report


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--limit", type=int, default=20)
    args = parser.parse_args()

    try:
        value = cycle(limit=args.limit)
    except RunnerBusy as exc:
        print("PostSteward runner is busy; no second publisher was started.", file=__import__("sys").stderr)
        return 3
    except ScheduleError as exc:
        print("PostSteward run-due stopped before a safe completion: " + str(exc), file=__import__("sys").stderr)
        return 2

    print(json.dumps(value, indent=2, ensure_ascii=False, allow_nan=False))
    return 4 if value["attention_count"] else 0


if __name__ == "__main__":
    raise SystemExit(main())

