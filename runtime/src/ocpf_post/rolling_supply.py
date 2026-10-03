"""Bounded desired-state controller for rolling PostSteward supply recovery.

This controller closes the safe local stages between durable supply demand and the
existing allocator. It never runs run-due, publishes, authors copy, extends expiry,
changes account/provider authority or enables paid model fallback.
"""
from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
from typing import Any, Callable

from ocpf_post import local_store
from ocpf_post.state import state_dir

UTC = timezone.utc
MAX_PASSES = 1
MAX_VAULTS_PER_PASS = 4
VAULT_TIMEOUT_SECONDS = 32
OBSERVE_BUDGET_SECONDS = 120
OBSERVE_TIMEOUT_SECONDS = 180
REFRESH_TIMEOUT_SECONDS = 180
LOCAL_STAGE_TIMEOUT_SECONDS = 90

ROOT = Path(__file__).resolve().parents[2]
FORBIDDEN_TOKENS = frozenset({
    "run-due", "publish", "threads publish", "linkedin publish", "--live",
})


def path() -> Path:
    return state_dir() / "rolling-supply-controller.json"


def _stamp(value: datetime) -> str:
    return value.astimezone(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _at(value: Any) -> datetime:
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("Rolling supply timestamp requires an offset")
    return parsed.astimezone(UTC)


def _command_safe(args: list[str]) -> None:
    joined = " ".join(args)
    if any(token in joined for token in FORBIDDEN_TOKENS):
        raise ValueError("Rolling supply controller cannot invoke provider consequence commands")


def run_stage(name: str, args: list[str], *, timeout: int) -> dict[str, Any]:
    """Run one bounded local stage without echoing arbitrary child output."""
    _command_safe(args)
    started = time.monotonic()
    env = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}
    pythonpath = str(ROOT / "src")
    if env.get("PYTHONPATH"):
        pythonpath += os.pathsep + env["PYTHONPATH"]
    env["PYTHONPATH"] = pythonpath
    with tempfile.TemporaryFile() as output:
        try:
            result = subprocess.run(
                args, cwd=ROOT, env=env, stdout=output, stderr=output,
                timeout=timeout, check=False,
            )
            code = result.returncode
            timed_out = False
        except subprocess.TimeoutExpired:
            code = None
            timed_out = True
        size = output.tell()
    return {
        "stage": name,
        "status": "timed_out" if timed_out else "completed" if code == 0 else "attention",
        "exit_code": code,
        "duration_seconds": round(time.monotonic() - started, 2),
        "output_bytes": size,
        "boundary": "Child output withheld; no candidate copy or provider response body is emitted.",
    }


def _continuity_state() -> dict[str, Any]:
    from ocpf_post.editorial_continuity import path as continuity_path
    value = local_store.read(continuity_path()) or {}
    acceptance = value.get("account_acceptance")
    recovery = value.get("recovery_work")
    if not isinstance(acceptance, dict) or not isinstance(recovery, list):
        return {
            "status": "unavailable",
            "reason": "continuity_state_not_recorded",
            "accounts": [],
            "recovery_work": [],
        }
    return {
        "status": str(acceptance.get("status") or "unavailable"),
        "observed_at": acceptance.get("observed_at"),
        "accounts": list(acceptance.get("accounts") or []),
        "recovery_work": [row for row in recovery if isinstance(row, dict)],
    }


def _measure(state: dict[str, Any]) -> dict[str, int]:
    accounts = [
        row for row in state.get("accounts", [])
        if isinstance(row, dict) and row.get("publishing_intent") is True
    ]
    blocked = [row for row in accounts if row.get("status") == "blocked"]
    recovering = [row for row in accounts if row.get("status") == "recovering"]
    return {
        "blocked_accounts": len(blocked),
        "recovering_accounts": len(recovering),
        "cadence_deficit": sum(
            max(0, int(row.get("stock_floor", 0) or 0)
                - int(row.get("runnable", 0) or 0)
                - int(row.get("reserved", 0) or 0))
            for row in blocked
        ),
        "reserve_deficit": sum(
            max(0, int(row.get("required_items", 0) or 0)
                - int(row.get("available_items", 0) or 0))
            for row in blocked
        ),
        "unreserved_schedule_opportunities": sum(
            max(0, int(row.get("unreserved_schedule_opportunities", 0) or 0))
            for row in blocked
        ),
        "available_items": sum(
            max(0, int(row.get("available_items", 0) or 0))
            for row in accounts
        ),
    }


def _progress(before: dict[str, int], after: dict[str, int]) -> bool:
    """Any movement toward desired state permits one more bounded pass."""
    return bool(
        after["blocked_accounts"] < before["blocked_accounts"]
        or after["cadence_deficit"] < before["cadence_deficit"]
        or after["reserve_deficit"] < before["reserve_deficit"]
        or after["unreserved_schedule_opportunities"] < before["unreserved_schedule_opportunities"]
        or after["available_items"] > before["available_items"]
    )


def _blocked_route_priorities(state: dict[str, Any]) -> dict[tuple[str, str], int]:
    """Physical account priority: import/collection before genuine authoring."""
    ranks = {
        "admission": 0,
        "collection": 1,
        "scheduling": 2,
        "generation": 3,
        "credentials": 4,
        "provider_recovery": 5,
    }
    result: dict[tuple[str, str], int] = {}
    for row in state.get("recovery_work", []):
        if not isinstance(row, dict):
            continue
        provider = str(row.get("provider") or "")
        account = str(row.get("account_id") or "")
        if not provider or not account:
            continue
        rank = ranks.get(str(row.get("blocker_stage") or ""), 9)
        key = (provider, account)
        result[key] = min(rank, result.get(key, 99))
    return result


def target_vaults(state: dict[str, Any], *, now: datetime | None = None,
                  limit: int = MAX_VAULTS_PER_PASS,
                  exclude: set[str] | None = None,
                  attempts_override: dict[str, str] | None = None) -> list[dict[str, Any]]:
    """Prioritise vaults serving blocked accounts while retaining oldest-attempt fairness."""
    from ocpf_post.registry import resolve_account
    from ocpf_post.vault_sync import observations, policies

    now = now or datetime.now(UTC)
    priorities = _blocked_route_priorities(state)
    observed = observations()
    cycle = local_store.read(state_dir() / "collection-cycle.json") or {}
    attempts = dict(cycle.get("vault_attempts")) if isinstance(cycle.get("vault_attempts"), dict) else {}
    if isinstance(attempts_override, dict):
        attempts.update({str(key): str(value) for key, value in attempts_override.items() if value})
    exclude = set(exclude or set())

    rows: list[dict[str, Any]] = []
    for vault_id, policy in policies().items():
        if str(vault_id) in exclude:
            continue
        if not isinstance(policy, dict) or policy.get("enabled") is not True:
            continue
        project = str(policy.get("project") or "")
        destinations = policy.get("destinations") if isinstance(policy.get("destinations"), dict) else {}
        routes = []
        route_ranks = []
        for provider, alias in destinations.items():
            try:
                account = resolve_account(project, str(alias), expected_provider=str(provider))
            except (OSError, ValueError, KeyError, TypeError):
                continue
            route = (str(provider), str(account["account_id"]))
            routes.append(route)
            if route in priorities:
                route_ranks.append(priorities[route])

        observation = observed.get(vault_id) if isinstance(observed.get(vault_id), dict) else {}
        stale = True
        raw_valid = observation.get("valid_until")
        if raw_valid:
            try:
                stale = now >= _at(raw_valid)
            except (TypeError, ValueError):
                stale = True

        # A blocked exact account is always eligible for a fresh sync. Stale/missing
        # observations come first, then admission/collection, then other blockers.
        if not route_ranks and not stale:
            continue
        route_rank = min(route_ranks, default=8)
        priority = (
            route_rank * 2 + (0 if stale else 1)
            if route_ranks
            else 20
        )
        rows.append({
            "vault_id": str(vault_id),
            "project": project,
            "priority": priority,
            "blocked_routes": [
                {"provider": provider, "account_id": account}
                for provider, account in routes if (provider, account) in priorities
            ],
            "stale_or_missing": stale,
            "last_attempt_at": attempts.get(vault_id),
        })

    def attempt_key(row: dict[str, Any]) -> tuple[int, str, str]:
        # Missing attempt is oldest. ISO UTC strings preserve chronology.
        attempt = str(row.get("last_attempt_at") or "")
        return int(row["priority"]), attempt, str(row["vault_id"])

    return sorted(rows, key=attempt_key)[:max(0, int(limit))]


def _needs_source_observation(state: dict[str, Any]) -> bool:
    stages = {
        str(row.get("blocker_stage") or "")
        for row in state.get("recovery_work", [])
        if isinstance(row, dict)
    }
    # Source observation remains the collection worker's normal responsibility.
    # Re-run it here only when continuity has positively classified collection
    # as the blocker; a generation deficit alone must not turn every refill tick
    # into a repository-polling burst.
    return "collection" in stages


def _needs_work_handoff(state: dict[str, Any]) -> bool:
    return any(
        isinstance(row, dict) and row.get("blocker_stage") == "generation"
        for row in state.get("recovery_work", [])
    )


def _continuity_stage() -> dict[str, Any]:
    return run_stage(
        "continuity",
        [sys.executable, str(ROOT / "scripts" / "editorial-continuity.py"), "--apply"],
        timeout=LOCAL_STAGE_TIMEOUT_SECONDS,
    )


def _sync_target_vaults(state: dict[str, Any], *, now: datetime,
                        exclude: set[str] | None = None,
                        attempts: dict[str, str] | None = None) -> list[dict[str, Any]]:
    rows = []
    for target in target_vaults(
        state, now=now, exclude=exclude, attempts_override=attempts,
    ):
        result = run_stage(
            "vault:" + target["vault_id"],
            [
                str(ROOT / "poststeward"), "vault", "sync", "--apply",
                "--vault-id", target["vault_id"],
            ],
            timeout=VAULT_TIMEOUT_SECONDS,
        )
        rows.append({**target, **result})
    return rows


def reconcile(*, apply: bool = False, horizon_minutes: int = 75,
              max_passes: int = MAX_PASSES,
              now_fn: Callable[[], datetime] | None = None) -> dict[str, Any]:
    """Run bounded desired-state passes until adequate, no-progress or pass limit."""
    if type(horizon_minutes) is not int or not 15 <= horizon_minutes <= 1440:
        raise ValueError("horizon_minutes must be between 15 and 1440")
    if type(max_passes) is not int or not 1 <= max_passes <= 3:
        raise ValueError("max_passes must be between 1 and 3")
    now_fn = now_fn or (lambda: datetime.now(UTC))

    if not apply:
        continuity = _continuity_state()
        return {
            "schema_version": 1,
            "status": "preview",
            "apply": False,
            "measure": _measure(continuity),
            "target_vaults": target_vaults(continuity, now=now_fn()),
            "needs_source_observation": _needs_source_observation(continuity),
            "needs_work_handoff": _needs_work_handoff(continuity),
            "boundary": (
                "Read-only controller preview. Apply may refresh source/vault evidence, admit already-authorised "
                "inventory and create allocator reservations. In explicitly configured work-workers-ai mode, "
                "the source-admission stage may also create bounded evidence-grounded fallback copy after the "
                "durable Work grace boundary. It never publishes."
            ),
        }

    previous_controller = local_store.read(path()) or {}
    attempt_history = (
        dict(previous_controller.get("vault_attempts"))
        if isinstance(previous_controller.get("vault_attempts"), dict)
        else {}
    )
    attempted_this_cycle: set[str] = set()
    report: dict[str, Any] = {
        "schema_version": 1,
        "started_at": _stamp(now_fn()),
        "passes": [],
        "apply": True,
        "vault_attempts": attempt_history,
    }

    # Sense first. The old loop acted before recomputing exact account deficits.
    sense = _continuity_stage()
    if sense["status"] != "completed":
        report.update(
            status="unavailable",
            reason="initial_continuity_unavailable",
            exit_code=sense.get("exit_code") or 2,
        )
        report["completed_at"] = _stamp(now_fn())
        local_store.write(path(), report)
        return report

    state = _continuity_state()
    if state["status"] in {"adequate", "no_enabled_destinations"}:
        report.update(status=state["status"], measure=_measure(state))
        report["completed_at"] = _stamp(now_fn())
        local_store.write(path(), report)
        return report

    for pass_number in range(1, max_passes + 1):
        started = now_fn()
        before = _measure(state)
        stages: list[dict[str, Any]] = []

        # Reconcile remote observations before consuming saved evidence. Targeted
        # vault reads are bounded and prioritised by exact blocked account.
        vaults = _sync_target_vaults(
            state, now=started, exclude=attempted_this_cycle, attempts=attempt_history,
        )
        stages.extend(vaults)
        for row in vaults:
            vault_id = str(row.get("vault_id") or "")
            if not vault_id:
                continue
            attempted_this_cycle.add(vault_id)
            attempt_history[vault_id] = _stamp(started)
        report["vault_attempts"] = dict(attempt_history)

        if _needs_source_observation(state):
            stages.append(run_stage(
                "source-observation",
                [
                    str(ROOT / "poststeward"), "replenish", "observe", "--apply",
                    "--budget-seconds", str(OBSERVE_BUDGET_SECONDS),
                ],
                timeout=OBSERVE_TIMEOUT_SECONDS,
            ))

        stages.append(run_stage(
            "source-admission",
            [str(ROOT / "poststeward"), "replenish", "refresh", "--apply", "--json"],
            timeout=REFRESH_TIMEOUT_SECONDS,
        ))
        source_reconcile = run_stage(
            "source-reconcile",
            [str(ROOT / "poststeward"), "replenish", "reconcile", "--json"],
            timeout=LOCAL_STAGE_TIMEOUT_SECONDS,
        )
        stages.append(source_reconcile)
        schedule_reconcile = run_stage(
            "schedule-reconcile",
            [str(ROOT / "poststeward"), "portfolio", "reconcile", "--json"],
            timeout=LOCAL_STAGE_TIMEOUT_SECONDS,
        )
        stages.append(schedule_reconcile)
        allocator = run_stage(
            "allocator",
            [
                str(ROOT / "poststeward"), "portfolio", "refill", "--apply", "--json",
                "--horizon-minutes", str(horizon_minutes),
            ],
            timeout=LOCAL_STAGE_TIMEOUT_SECONDS,
        )
        stages.append(allocator)

        # Preserve the established failure boundary: source/vault acquisition may
        # fail while accepted inventory continues draining, but allocator failure
        # is a cycle failure. Reconcile failures remain lower priority and are
        # surfaced only after continuity/acceptance have been evaluated.
        allocator_exit = allocator.get("exit_code")
        pipeline_exit = next(
            (
                row.get("exit_code")
                for row in (source_reconcile, schedule_reconcile)
                if row.get("exit_code") not in (None, 0)
            ),
            None,
        )

        after_sense = _continuity_stage()
        stages.append(after_sense)
        if after_sense["status"] != "completed":
            after = _continuity_state()
            report["passes"].append({
                "pass": pass_number, "started_at": _stamp(started),
                "before": before, "after": _measure(after), "stages": stages,
                "progress": False,
            })
            report.update(
                status="unavailable",
                reason="post_pass_continuity_unavailable",
                exit_code=after_sense.get("exit_code") or 2,
            )
            break

        after = _continuity_state()
        after_measure = _measure(after)
        progressed = _progress(before, after_measure)
        report["passes"].append({
            "pass": pass_number,
            "started_at": _stamp(started),
            "before": before,
            "after": after_measure,
            "stages": stages,
            "progress": progressed,
            "acceptance_status": after.get("status"),
            "remaining_blockers": [
                {
                    key: row.get(key)
                    for key in (
                        "provider", "account_id", "blocker_stage", "blocker_reason",
                        "cadence_deficit", "reserve_deficit", "available_items", "stock_floor",
                    )
                }
                for row in after.get("recovery_work", [])
                if isinstance(row, dict)
            ],
        })

        state = after
        if allocator_exit not in (None, 0):
            report.update(
                status="allocator_failed",
                reason="allocator_stage_failed",
                exit_code=int(allocator_exit),
            )
            break
        if after.get("status") in {"adequate", "no_enabled_destinations"}:
            if pipeline_exit not in (None, 0):
                report.update(
                    status="pipeline_failed",
                    reason="reconcile_stage_failed",
                    exit_code=int(pipeline_exit),
                )
            else:
                report["status"] = str(after["status"])
            break
        if after.get("status") == "recovering":
            # Immediate cadence is operational. Continue reserve recovery on later
            # timer activations, but do not fail the service merely because the
            # seven-day target is not yet rebuilt.
            report["status"] = "recovering"
            report["reason"] = "cadence_ready_reserve_rebuilding"
            break
        if not progressed:
            report["status"] = "blocked_no_progress"
            report["reason"] = "desired_state_unchanged_after_safe_reconciliation"
            break
    else:
        report["status"] = "blocked_progress_exhausted"

    report["measure"] = _measure(state)
    report["needs_work_handoff"] = _needs_work_handoff(state)
    report["completed_at"] = _stamp(now_fn())
    report["boundary"] = (
        "Desired-state rolling supply reconciliation only. Vault/source reads and existing admission may create "
        "COPY-READY inventory; the allocator may create durable future reservations. In explicit work-workers-ai "
        "mode, the source-admission stage may create bounded evidence-grounded fallback copy only after the durable "
        "Work grace boundary and only toward operational headroom. No run-due, social-provider write, expiry "
        "extension, publication-quota change or account substitution occurs."
    )
    local_store.write(path(), report)
    return report

