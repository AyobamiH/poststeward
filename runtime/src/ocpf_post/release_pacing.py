"""Separate saved daily release amounts from stock-building and safety ceilings.

Policy preparation is pure. The explicit apply entry point changes only the local
portfolio policy, under a review hash. No provider call, reservation, retry,
expiry extension, content change or generator allowance is performed here.
"""
from __future__ import annotations

from contextlib import contextmanager
from copy import deepcopy
import hashlib
import json
import os
import time
from pathlib import Path
from typing import Any

MODE = "steady_originals"
UNIT = "logical_publications_per_account_per_local_day"
MARKER = {"schema_version": 1, "mode": MODE, "unit": UNIT}
MAX_POLICY_BYTES = 1_000_000


def enabled(policy: dict[str, Any]) -> bool:
    return policy.get("release_pacing") == MARKER


def _integer(value: Any, name: str) -> int:
    if type(value) is not int or not 0 <= value <= 100:
        raise ValueError(f"{name} must be an integer between 0 and 100")
    return value


def safety_ceiling(settings: dict[str, Any]) -> int:
    """The absolute guard is not the amount the owner normally releases."""
    return _integer(settings.get("hard_daily_ceiling", settings.get("daily_target")), "hard_daily_ceiling")


def validate(policy: dict[str, Any]) -> None:
    """Reject a misleading marker rather than silently falling back to a burst."""
    if "release_pacing" not in policy:
        return
    if not enabled(policy):
        raise ValueError("Unsupported release_pacing policy")
    for provider, settings in policy["providers"].items():
        if settings.get("flow_mode") != "fixed":
            raise ValueError(f"{provider}: steady originals requires fixed daily allocation")
        if _integer(settings.get("daily_target"), "daily_target") > safety_ceiling(settings):
            raise ValueError(f"{provider}: daily originals cannot exceed the hard safety ceiling")


def prepare(policy: dict[str, Any]) -> dict[str, Any]:
    """Keep every saved numeric target, window, mix and source control unchanged.

    Old unannotated owner policies currently load as admission mode with a 100
    ceiling. We preserve that ceiling explicitly while using their saved target
    for the normal opportunity grid. Never substitute illustrative 20/20/6 values.
    """
    from ocpf_post.portfolio import validate_policy

    value = deepcopy(policy)
    validate_policy(value)
    if "release_pacing" in value and not enabled(value):
        raise ValueError("Refusing to replace an unknown release-pacing policy")
    for provider, settings in value["providers"].items():
        target = _integer(settings.get("daily_target"), f"{provider} daily_target")
        ceiling = _integer(settings.get("hard_daily_ceiling", 100), f"{provider} hard_daily_ceiling")
        if target > ceiling:
            raise ValueError(f"{provider}: saved target exceeds the existing safety ceiling")
        settings["flow_mode"] = "fixed"
        settings["hard_daily_ceiling"] = ceiling
    value["release_pacing"] = dict(MARKER)
    validate_policy(value)
    return value


def scoped_settings(policy: dict[str, Any], account: dict[str, Any]) -> dict[str, Any]:
    """Keep each additional account's own original target, not the founder's."""
    result = deepcopy(account["policy"])
    authority = policy["providers"][account["provider"]]
    result["flow_mode"] = authority.get("flow_mode", "fixed")
    if enabled(policy):
        result["hard_daily_ceiling"] = safety_ceiling(authority)
        if _integer(result.get("daily_target"), "account daily_target") > result["hard_daily_ceiling"]:
            raise ValueError("Account daily originals exceed the provider safety ceiling")
    else:
        # Preserve the pre-existing account inheritance exactly when not opted in.
        from ocpf_post.portfolio import _daily_limit
        result["hard_daily_ceiling"] = _daily_limit(authority)
    return result


def capacity_fields(policy: dict[str, Any], settings: dict[str, Any], used: int) -> dict[str, Any]:
    """Describe the normal allocation allowance separately from the absolute cap."""
    if not enabled(policy):
        return {}
    limit = _integer(settings.get("daily_target"), "daily_target")
    ceiling = safety_ceiling(settings)
    return {
        "release_pacing": MODE,
        "budget_unit": UNIT,
        "daily_release_limit": limit,
        "daily_release_remaining": max(0, limit - used),
        "daily_release_limit_met": used >= limit,
        "hard_daily_ceiling": ceiling,
        "hard_daily_ceiling_met": used >= ceiling,
        "hard_ceiling_remaining": max(0, ceiling - used),
        "budget_scope": "Saved daily originals release allowance. Active reservations and terminal effects "
                        "count once per logical publication, not once per thread part. "
                        "Unfilled opportunities are not catch-up debt; extra inventory stays available for later.",
    }


def summary(policy: dict[str, Any], accounts: dict[str, dict[str, Any]] | None = None) -> dict[str, Any]:
    """Small configuration-only projection; never equate a target with success."""
    steady = enabled(policy)
    if not steady:
        return {"schema_version": 1, "mode": "not_enabled", "unit": UNIT,
      "accounts": [], "reserve_increases_release_amount": None,
      "existing_reservations": "retained_and_counted_not_rewritten",
      "boundary": "Steady originals has not been enabled. No numeric targets were changed."}
    rows: list[dict[str, Any]] = []
    for provider, settings in sorted(policy["providers"].items()):
        rows.append({"scope": "default_account", "provider": provider,
                     "normal_originals_per_day": settings["daily_target"] if steady else None,
                     "hard_daily_ceiling": safety_ceiling(settings),
                     "flow_mode": settings.get("flow_mode", "fixed")})
    for identity, account in sorted((accounts or {}).items()):
        if account["provider"] not in policy["providers"]:
            continue
        settings = scoped_settings(policy, account)
        rows.append({"scope": "additional_account", "identity": identity,
                     "provider": account["provider"], "enabled": account["enabled"],
                     "normal_originals_per_day": settings["daily_target"] if steady else None,
                     "hard_daily_ceiling": safety_ceiling(settings),
                     "flow_mode": settings.get("flow_mode", "fixed")})
    return {"schema_version": 1, "mode": MODE if steady else "not_enabled", "unit": UNIT,
            "accounts": rows, "reserve_increases_release_amount": False if steady else None,
            "existing_reservations": "retained_and_counted_not_rewritten",
            "boundary": "One complete post or thread is one logical original per destination account. "
                        "Thread parts, reads, attempted writes and API charges are different units. "
                        "Stock does not increase the saved daily amount. No expiry is extended and no quota is guaranteed."}


def _read_policy(path: Path) -> bytes:
    if path.is_symlink() or not path.is_file():
        raise ValueError("A real saved portfolio policy is required; no default daily amounts will be invented")
    if path.stat().st_size > MAX_POLICY_BYTES:
        raise ValueError("Portfolio policy exceeds the size bound")
    return path.read_bytes()


def _decode(raw: bytes) -> dict[str, Any]:
    def unique(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("Duplicate JSON policy key")
            result[key] = value
        return result
    value = json.loads(raw, object_pairs_hook=unique)
    if not isinstance(value, dict):
        raise ValueError("Portfolio policy must be a JSON object")
    return value


def _digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def _account_controls(accounts: dict[str, dict[str, Any]]) -> dict[str, Any]:
    # Only scheduling controls participate. Never copy credential material into a review.
    return {identity: {key: row[key] for key in ("provider", "enabled", "policy")}
            for identity, row in sorted(accounts.items())}


def review(*, policy_path: Path | None = None, accounts: dict[str, dict[str, Any]] | None = None) -> dict[str, Any]:
    from ocpf_post.account_profiles import profiles
    from ocpf_post.portfolio import policy_file

    path = policy_path if policy_path is not None else policy_file()
    raw = _read_policy(path)
    before = _decode(raw)
    after = prepare(before)
    account_rows = profiles() if accounts is None else accounts
    proposed = summary(after, account_rows)  # Validate every scoped target before any write.
    identity = {"schema_version": 1, "operation": MODE, "policy_path": str(path.resolve()),
                "policy_sha256": hashlib.sha256(raw).hexdigest(),
                "proposed_policy_sha256": _digest(after),
                "account_controls_sha256": _digest(_account_controls(account_rows))}
    return {"schema_version": 1, "status": "already_enabled" if before == after else "preview",
            "applied": False, "review_sha256": _digest(identity), **identity,
            "daily_amounts_changed": False, "proposed": proposed,
            "boundary": "Preview only. Keep saved daily targets; preserve the safety ceiling and existing bookings. "
                        "No provider, generation, schedule, content, expiry, timer or credential change."}


@contextmanager
def policy_lock(path: Path, *, timeout: float = 60.0):
    """Serialize explicit policy apply and allocator mutations without killing work."""
    import fcntl
    from ocpf_post.state import ensure_private_dir

    ensure_private_dir(path.parent)
    descriptor = os.open(str(path) + ".lock", os.O_CREAT | os.O_RDWR, 0o600)
    deadline = time.monotonic() + max(0.0, timeout)
    try:
        while True:
            try:
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    raise RuntimeError("Release policy is busy; nothing was changed. Let the current allocation finish.")
                time.sleep(0.1)
        yield
    finally:
        os.close(descriptor)


def apply(expected_sha256: str, *, evidence_dir: Path, policy_path: Path | None = None,
          accounts: dict[str, dict[str, Any]] | None = None) -> dict[str, Any]:
    from ocpf_post import local_store
    from ocpf_post.account_profiles import profiles
    from ocpf_post.portfolio import policy_file
    from ocpf_post.state import config_dir, state_dir

    if not isinstance(expected_sha256, str) or len(expected_sha256) != 64:
        raise ValueError("A complete reviewed SHA-256 is required")
    path = policy_path if policy_path is not None else policy_file()
    evidence = evidence_dir.resolve()
    for live in (path.parent.resolve(), config_dir().resolve(), state_dir().resolve()):
        if evidence == live or live in evidence.parents:
            raise ValueError("Owner evidence must be saved outside live config/state")
    _read_policy(path)
    with policy_lock(path):
        account_rows = profiles() if accounts is None else accounts
        current = review(policy_path=path, accounts=account_rows)
        if current["review_sha256"] != expected_sha256:
            raise ValueError("Release-pacing review changed; nothing was applied")
        if current["status"] == "already_enabled":
            return current
        raw = _read_policy(path)
        if hashlib.sha256(raw).hexdigest() != current["policy_sha256"]:
            raise ValueError("Release-pacing policy changed; nothing was applied")
        after = prepare(_decode(raw))
        # Save rollback evidence outside the live fingerprint namespace before writing.
        evidence.mkdir(mode=0o700, parents=False, exist_ok=False)
        backup = evidence / "portfolio-policy.before.json"
        with backup.open("xb") as handle:
            backup.chmod(0o600)
            handle.write(raw)
            handle.flush()
            os.fsync(handle.fileno())
        if _read_policy(path) != raw:
            raise ValueError("Release-pacing policy changed before commit; backup retained, policy untouched")
        if accounts is None and _digest(_account_controls(profiles())) != current["account_controls_sha256"]:
            raise ValueError("Account scheduling controls changed before commit; policy untouched")
        local_store.write(path, after)
        if _decode(_read_policy(path)) != after:
            raise ValueError("Policy write readback failed; inspect saved backup before any retry")
        result = {**current, "status": "enabled", "applied": True,
                  "policy_sha256_after": hashlib.sha256(_read_policy(path)).hexdigest(),
                  "backup_path": str(backup),
                  "boundary": "Only portfolio release policy changed. Saved numeric targets, safety ceilings, "
                              "windows, source budgets, original copy and existing schedules remain unchanged. "
                              "New automatic reservations follow the saved daily originals pace; earlier bookings remain valid."}
        local_store.write(evidence / "result.json", result)
        return result
