from __future__ import annotations

import hashlib
import json
import os
import re
import time
import uuid
from contextlib import AbstractContextManager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Iterable
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from ocpf_post.campaigns import builtin_text, destination_binding_error, normalize_campaign_id
from ocpf_post.model import PublishReceipt
from ocpf_post.ledger import append_jsonl, iter_jsonl
from ocpf_post.providers import get_provider
from ocpf_post.providers.base import AmbiguousProviderEffect, ProviderRejected, ProviderUnavailable
from ocpf_post.publication_payload import build_publication, validate_frozen_publication
from ocpf_post.schedule_semantics import ACTIVE_STATUSES as SEMANTIC_ACTIVE_STATUSES, classify_schedule, due_actionable
from ocpf_post.state import append_receipt, ensure_private_dir, receipts_file, state_dir, terminal_effect_receipt

UTC = timezone.utc
DEFAULT_TIMEZONE = "Europe/London"
SUPPORTED_PROVIDERS = {"x", "threads", "linkedin"}
ACTIVE_STATUSES = set(SEMANTIC_ACTIVE_STATUSES)
RUNNER_LOCK_STALE_SECONDS = 30 * 60
PREFLIGHT_RETRY_BASE_SECONDS = 60
PREFLIGHT_RETRY_MAX_SECONDS = 15 * 60
PROVIDER_CIRCUIT_BASE_SECONDS = 15 * 60
PROVIDER_GENERIC_FORBIDDEN_THRESHOLD = 3
PROVIDER_CIRCUIT_MAX_SECONDS = 4 * 60 * 60
PROVIDER_CIRCUIT_CLASSES = frozenset({
    "provider_rate_limited",
    "provider_usage_limit",
    "provider_posting_limit",
    "provider_automation_restricted",
    "provider_write_restricted",
    "provider_account_locked",
    "provider_auth_rejected",
    "provider_client_forbidden",
})


class ScheduleError(RuntimeError):
    pass


class RunnerBusy(ScheduleError):
    pass


_PROVIDER_HTTP_RE = re.compile(r"HTTP\s+(\d{3})")


def _safe_provider_problem(raw: str) -> dict[str, Any]:
    text = str(raw or "")
    start = text.find("{")
    end = text.rfind("}")
    if start < 0 or end <= start:
        return {}
    try:
        value = json.loads(text[start:end + 1])
    except json.JSONDecodeError:
        return {}
    return value if isinstance(value, dict) else {}


def _problem_code(payload: dict[str, Any]) -> int | None:
    value = payload.get("code")
    if type(value) is int:
        return value
    errors = payload.get("errors")
    if isinstance(errors, list):
        for row in errors:
            if isinstance(row, dict) and type(row.get("code")) is int:
                return int(row["code"])
    return None


def _provider_failure_class(status: int | None, payload: dict[str, Any] | None = None) -> str:
    code = int(status or 0)
    payload = payload or {}
    problem_type = str(payload.get("type") or "").lower()
    reason = str(payload.get("reason") or "").lower()
    title = str(payload.get("title") or "").lower()
    x_code = _problem_code(payload)

    if code == 402 or "usage-capped" in problem_type or "usagecap" in title.replace(" ", ""):
        return "provider_usage_limit"
    if code == 429:
        return "provider_rate_limited"
    if code == 401 or x_code in {89, 99}:
        return "provider_auth_rejected"
    if x_code == 185:
        return "provider_posting_limit"
    if x_code == 187:
        return "provider_duplicate_content"
    if x_code == 226:
        return "provider_automation_restricted"
    if x_code == 261:
        return "provider_write_restricted"
    if x_code == 326:
        return "provider_account_locked"
    if x_code == 186:
        return "provider_content_too_long"
    if "client-forbidden" in problem_type or reason == "client-not-enrolled":
        return "provider_client_forbidden"
    if code == 403:
        return "provider_forbidden"
    if code in {400, 404, 409, 422}:
        return "provider_request_rejected"
    return "provider_rejected"


def _problem_metadata(payload: dict[str, Any]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    problem_type = payload.get("type")
    reason = payload.get("reason")
    code = _problem_code(payload)
    if isinstance(problem_type, str) and problem_type:
        result["provider_problem_type"] = problem_type[:240]
    if isinstance(reason, str) and reason:
        result["provider_reason"] = reason[:120]
    if code is not None:
        result["provider_error_code"] = code
    return result


def provider_failure_metadata(
    value: ProviderRejected | dict[str, Any], *, stage: str | None = None,
) -> dict[str, Any]:
    """Return a safe machine-readable classification without granting retry authority.

    Existing ledgers may predate explicit fields, so historical schedule records can
    be classified from their already-redacted detail string at read time. The detail
    itself is never copied into bounded operator projections.
    """
    if isinstance(value, ProviderRejected):
        status = int(value.status or 0)
        payload = _safe_provider_problem(value.message)
        return {
            "failure_class": _provider_failure_class(status, payload),
            "failure_stage": stage or "provider_consequence",
            "provider_http_status": status,
            "automatic_retry": False,
            **_problem_metadata(payload),
        }

    if not isinstance(value, dict):
        return {}
    if value.get("failure_class"):
        result = {
            key: value.get(key)
            for key in (
                "failure_class", "failure_stage", "provider_http_status", "automatic_retry",
                "provider_problem_type", "provider_reason", "provider_error_code",
                "circuit_failure_class", "circuit_retry_at",
            )
            if value.get(key) is not None
        }
        if "automatic_retry" not in result and value.get("status") == "failed":
            result["automatic_retry"] = False
        return result

    detail = str(value.get("detail") or "")
    if value.get("status") != "failed" or "provider rejected" not in detail.lower():
        return {}

    match = _PROVIDER_HTTP_RE.search(detail)
    status = int(match.group(1)) if match else 0
    payload = _safe_provider_problem(detail)
    inferred_stage = stage
    if inferred_stage is None:
        inferred_stage = (
            "identity_preflight"
            if "before consequence" in detail.lower() or "identity check" in detail.lower()
            else "provider_consequence"
        )
    return {
        "failure_class": _provider_failure_class(status, payload),
        "failure_stage": inferred_stage,
        "provider_http_status": status,
        "automatic_retry": False,
        "classification_basis": "historical_safe_detail",
        **_problem_metadata(payload),
    }


def _utc_now_dt() -> datetime:
    return datetime.now(tz=UTC)


def _utc_iso(value: datetime | None = None) -> str:
    dt = (value or _utc_now_dt()).astimezone(UTC).replace(microsecond=0)
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse_utc(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(UTC)


def _digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def schedules_file() -> Path:
    return state_dir() / "schedule-events.jsonl"


def runner_lock_file() -> Path:
    return state_dir() / "run-due.lock"


def _append_event(value: dict[str, Any]) -> None:
    append_jsonl(schedules_file(), value)


def iter_schedule_events() -> Iterable[dict[str, Any]]:
    return iter_jsonl(
        schedules_file(),
        required=("schedule_id", "event", "status", "recorded_at"),
    )


def schedule_records() -> list[dict[str, Any]]:
    records: dict[str, dict[str, Any]] = {}
    for event in iter_schedule_events():
        schedule_id = str(event["schedule_id"])
        kind = event.get("event")
        if kind == "scheduled":
            records[schedule_id] = dict(event)
            records[schedule_id]["last_event"] = "scheduled"
            records[schedule_id]["updated_at"] = event.get("recorded_at")
            continue
        record = records.get(schedule_id)
        if record is None:
            continue
        if event.get("status"):
            record["status"] = event["status"]
        record["last_event"] = kind
        record["updated_at"] = event.get("recorded_at")
        if kind == "publication_part_created" and event.get("part_post_id"):
            part_ids = list(record.get("publication_part_ids") or [])
            part_id = str(event["part_post_id"])
            if part_id not in part_ids:
                part_ids.append(part_id)
            record["publication_part_ids"] = part_ids
        for key in (
            "detail", "post_id", "url", "receipt_status", "readback_verified", "runner_pid",
            "retry_at", "preflight_attempts", "failure_class", "failure_stage",
            "provider_http_status", "automatic_retry", "provider_problem_type",
            "provider_reason", "provider_error_code", "circuit_failure_class", "circuit_retry_at",
            "publication_root_post_id", "publication_completed_parts",
            "publication_failed_part_index", "publication_failure_kind",
        ):
            if key in event:
                record[key] = event[key]
    return sorted(records.values(), key=lambda item: (str(item.get("run_at")), str(item.get("schedule_id"))))


def get_schedule(schedule_id: str) -> dict[str, Any] | None:
    return next((record for record in schedule_records() if record.get("schedule_id") == schedule_id), None)


def provider_write_circuit(
    provider: str, account_id: str, *, now: datetime | None = None,
) -> dict[str, Any]:
    """Derive a bounded write circuit from durable schedule outcomes.

    Only definite account-wide/provider-wide rejection classes participate.
    Candidate-specific failures such as duplicate content never open this circuit.
    A later successful publication resets the failure streak. No provider call or
    state mutation occurs here.
    """
    now_dt = (now or _utc_now_dt()).astimezone(UTC)
    rows = [
        row for row in schedule_records()
        if str(row.get("provider") or "") == str(provider)
        and str(row.get("account_id") or "") == str(account_id)
    ]
    rows.sort(key=lambda row: str(row.get("updated_at") or row.get("recorded_at") or row.get("run_at") or ""))

    streak = 0
    generic_forbidden_streak = 0
    latest: dict[str, Any] | None = None
    latest_meta: dict[str, Any] = {}
    trigger = None
    for row in rows:
        status = str(row.get("status") or "")
        if status in {"published_verified", "published_unverified"}:
            streak = 0
            generic_forbidden_streak = 0
            latest = None
            latest_meta = {}
            trigger = None
            continue
        if status != "failed":
            continue

        meta = provider_failure_metadata(row)
        failure_class = str(meta.get("failure_class") or "")

        if failure_class == "provider_forbidden":
            generic_forbidden_streak += 1
            if generic_forbidden_streak >= PROVIDER_GENERIC_FORBIDDEN_THRESHOLD:
                streak = generic_forbidden_streak
                latest = row
                latest_meta = meta
                trigger = "repeated_generic_forbidden"
            continue

        generic_forbidden_streak = 0
        if failure_class not in PROVIDER_CIRCUIT_CLASSES:
            continue
        streak += 1
        latest = row
        latest_meta = meta
        trigger = "typed_account_wide_failure"

    if latest is None:
        return {
            "open": False,
            "provider": provider,
            "account_id": str(account_id),
            "automatic_retry_authority": False,
        }

    observed_text = latest.get("updated_at") or latest.get("recorded_at") or latest.get("run_at")
    observed_at = _parse_utc(str(observed_text)) if observed_text else now_dt
    exponent = max(0, min(streak - 1, 4))
    delay = min(PROVIDER_CIRCUIT_BASE_SECONDS * (2 ** exponent), PROVIDER_CIRCUIT_MAX_SECONDS)
    retry_at = observed_at + timedelta(seconds=delay)
    return {
        "open": retry_at > now_dt,
        "provider": provider,
        "account_id": str(account_id),
        "failure_class": latest_meta.get("failure_class"),
        "provider_http_status": latest_meta.get("provider_http_status"),
        "provider_problem_type": latest_meta.get("provider_problem_type"),
        "provider_reason": latest_meta.get("provider_reason"),
        "provider_error_code": latest_meta.get("provider_error_code"),
        "source_schedule_id": latest.get("schedule_id"),
        "observed_at": _utc_iso(observed_at),
        "retry_at": _utc_iso(retry_at),
        "consecutive_failures": streak,
        "trigger": trigger,
        "generic_forbidden_threshold": PROVIDER_GENERIC_FORBIDDEN_THRESHOLD,
        "automatic_retry_authority": False,
        "boundary": (
            "Derived from definite durable schedule rejections. Typed account-wide failures "
            "open directly; otherwise three consecutive generic forbidden failures with no "
            "intervening success open the same bounded circuit. The circuit suppresses new "
            "write attempts temporarily and never replays a failed publication."
        ),
    }


def parse_schedule_at(value: str, *, default_timezone: str = DEFAULT_TIMEZONE) -> tuple[datetime, str]:
    raw = value.strip()
    if not raw:
        raise ScheduleError("Scheduled time is empty")

    try:
        aware = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        aware = None
    if aware is not None and aware.tzinfo is not None:
        return aware.astimezone(UTC), str(aware.tzinfo)

    parts = raw.rsplit(" ", 1)
    if len(parts) == 2 and ("/" in parts[1] or parts[1].upper() == "UTC"):
        local_text, timezone_name = parts
    else:
        local_text, timezone_name = raw, default_timezone

    try:
        zone = ZoneInfo(timezone_name)
    except ZoneInfoNotFoundError as exc:
        raise ScheduleError(f"Unknown timezone: {timezone_name}") from exc

    naive = None
    for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%dT%H:%M", "%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S"):
        try:
            naive = datetime.strptime(local_text, fmt)
            break
        except ValueError:
            continue
    if naive is None:
        raise ScheduleError("Use YYYY-MM-DD HH:MM Europe/London or ISO-8601 with an explicit offset")

    fold0 = naive.replace(tzinfo=zone, fold=0)
    fold1 = naive.replace(tzinfo=zone, fold=1)
    if fold0.utcoffset() != fold1.utcoffset():
        raise ScheduleError("Ambiguous DST time; use an ISO-8601 timestamp with an explicit offset")
    round_trip = fold0.astimezone(UTC).astimezone(zone).replace(tzinfo=None)
    if round_trip != naive:
        raise ScheduleError("Non-existent DST local time; choose another time or use an explicit offset")
    return fold0.astimezone(UTC), timezone_name


def create_schedule(*, campaign: str, provider: str, at: str, timezone_name: str = DEFAULT_TIMEZONE,
                    provider_factory: Callable[[str], Any] = get_provider,
                    now: datetime | None = None) -> dict[str, Any]:
    campaign = normalize_campaign_id(campaign)
    provider_name = provider.strip().lower()
    if provider_name not in SUPPORTED_PROVIDERS:
        raise ScheduleError(f"Unsupported provider: {provider}")
    text = builtin_text(campaign, provider_name)
    if not text:
        raise ScheduleError(f"No built-in {provider_name} payload exists for {campaign}")
    publication = build_publication(provider_name, text)
    digest = publication["text_sha256"]

    run_at, display_timezone = parse_schedule_at(at, default_timezone=timezone_name)
    now_dt = (now or _utc_now_dt()).astimezone(UTC)
    if run_at <= now_dt:
        raise ScheduleError("Scheduled time must be in the future")

    try:
        from ocpf_post.providers import for_campaign
        provider_impl = for_campaign(provider_name, campaign, factory=provider_factory)
        account = provider_impl.account()
    except ProviderUnavailable as exc:
        raise ScheduleError(f"Provider identity check temporarily unavailable: {exc}") from exc
    except (ValueError, ProviderRejected) as exc:
        raise ScheduleError(f"Provider identity check failed: {exc}") from exc

    binding_error = destination_binding_error(campaign, provider_name, account)
    if binding_error:
        raise ScheduleError(binding_error)
    from ocpf_post.campaigns import builtin_manifest
    manifest = builtin_manifest(campaign)
    from ocpf_post.vault_sync import guard
    vault_reason = guard(manifest, provider_name)
    if vault_reason:
        raise ScheduleError(vault_reason)
    from ocpf_post.source_guard import guard as source_guard
    source_reason = source_guard(manifest, now=run_at)
    if source_reason:
        raise ScheduleError(source_reason)
    from ocpf_post.generated_supply_guard import delivery_guard as generated_delivery_guard
    generated_reason = generated_delivery_guard(manifest, provider_name, str(account.account_id), text)
    if generated_reason:
        raise ScheduleError(generated_reason)
    circuit = provider_write_circuit(provider_name, str(account.account_id), now=now_dt)
    if circuit.get("open") is True:
        raise ScheduleError(
            "Provider/account write circuit is open until "
            f"{circuit.get('retry_at')} after {circuit.get('failure_class')}."
        )

    prior = terminal_effect_receipt(campaign, provider_name, account.account_id)
    if prior:
        raise ScheduleError(f"A terminal publication receipt already exists: {prior.get('status')} {prior.get('url') or ''}".strip())

    for record in schedule_records():
        if (record.get("campaign") == campaign and record.get("provider") == provider_name
                and record.get("account_id") == account.account_id and record.get("status") in ACTIVE_STATUSES):
            raise ScheduleError(f"An active schedule already exists: {record.get('schedule_id')}")

    event = {
        "event": "scheduled",
        "status": "scheduled",
        "schedule_id": f"sch_{uuid.uuid4().hex}",
        "campaign": campaign,
        "provider": provider_name,
        "account_id": account.account_id,
        "account_label": account.display,
        "run_at": _utc_iso(run_at),
        "display_timezone": display_timezone,
        "text": text,
        "text_sha256": digest,
        "publication_type": publication["publication_type"],
        "publication_sha256": publication["publication_sha256"],
        "publication_parts": publication["parts"],
        "part_count": publication["part_count"],
        "payload_source": "builtin_campaign",
        "recorded_at": _utc_iso(now_dt),
    }
    _append_event(event)
    return dict(event)


def _retry_clear_fields(record: dict[str, Any]) -> dict[str, Any]:
    fields = (
        "retry_at", "failure_class", "failure_stage", "provider_http_status",
        "automatic_retry", "provider_problem_type", "provider_reason",
        "provider_error_code", "circuit_failure_class", "circuit_retry_at",
    )
    if not any(record.get(field) is not None for field in fields):
        return {}
    return {field: None for field in fields}


def cancel_schedule(schedule_id: str, *, now: datetime | None = None) -> dict[str, Any]:
    record = get_schedule(schedule_id)
    if record is None:
        raise ScheduleError(f"Unknown schedule: {schedule_id}")
    if record.get("status") != "scheduled":
        raise ScheduleError(f"Only a scheduled item can be cancelled; {schedule_id} is {record.get('status')}")
    _append_event({
        "event": "cancelled",
        "status": "cancelled",
        "schedule_id": schedule_id,
        "recorded_at": _utc_iso(now),
        **_retry_clear_fields(record),
        "detail": "Cancelled explicitly before consequence.",
    })
    return get_schedule(schedule_id) or record


def due_schedules(*, now: datetime | None = None, limit: int = 20) -> list[dict[str, Any]]:
    now_dt = (now or _utc_now_dt()).astimezone(UTC)
    return [record for record in schedule_records() if due_actionable(record, now=now_dt)][:max(0, int(limit))]


def _pid_alive(pid: int) -> bool:
    if pid <= 0 or os.name != "posix":
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


class RunnerLock(AbstractContextManager["RunnerLock"]):
    def __init__(self, *, stale_seconds: int = RUNNER_LOCK_STALE_SECONDS) -> None:
        self.path = runner_lock_file()
        self.stale_seconds = stale_seconds
        self.token = uuid.uuid4().hex
        self.acquired = False

    def _try_break_stale(self) -> bool:
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            raw = {}
        created = float(raw.get("created_epoch") or 0)
        pid = int(raw.get("pid") or 0)
        age = time.time() - created if created else self.stale_seconds + 1
        if age <= self.stale_seconds or _pid_alive(pid):
            return False
        try:
            self.path.unlink()
            return True
        except FileNotFoundError:
            return True
        except OSError:
            return False

    def __enter__(self) -> "RunnerLock":
        ensure_private_dir(self.path.parent)
        payload = json.dumps({"pid": os.getpid(), "token": self.token, "created_epoch": time.time(), "recorded_at": _utc_iso()}, separators=(",", ":")).encode("utf-8")
        for _attempt in range(2):
            try:
                fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            except FileExistsError:
                if self._try_break_stale():
                    continue
                raise RunnerBusy(f"Another run-due process owns {self.path}")
            try:
                os.write(fd, payload)
                os.fsync(fd)
            finally:
                os.close(fd)
            self.acquired = True
            return self
        raise RunnerBusy(f"Could not acquire {self.path}")

    def __exit__(self, exc_type, exc, tb) -> None:
        if self.acquired:
            try:
                raw = json.loads(self.path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                raw = {}
            if raw.get("token") == self.token:
                try:
                    self.path.unlink()
                except FileNotFoundError:
                    pass
            self.acquired = False
        return None


def _schedule_event(schedule_id: str, event: str, status: str, **extra: Any) -> None:
    _append_event({"event": event, "status": status, "schedule_id": schedule_id, "recorded_at": _utc_iso(), **extra})


def _block(schedule_id: str, status: str, detail: str, **extra: Any) -> dict[str, Any]:
    record = get_schedule(schedule_id) or {}
    event_extra = _retry_clear_fields(record)
    event_extra.update(extra)
    _schedule_event(schedule_id, status, status, detail=detail, **event_extra)
    return get_schedule(schedule_id) or {"schedule_id": schedule_id, "status": status, "detail": detail}


def _preflight_retry_delay(attempt: int) -> int:
    exponent = max(0, min(attempt - 1, 4))
    return min(PREFLIGHT_RETRY_BASE_SECONDS * (2 ** exponent), PREFLIGHT_RETRY_MAX_SECONDS)


def _defer_preflight(schedule_id: str, exc: Exception, *, now: datetime | None = None) -> dict[str, Any]:
    record = get_schedule(schedule_id) or {}
    attempt = int(record.get("preflight_attempts") or 0) + 1
    delay = _preflight_retry_delay(attempt)
    now_dt = (now or _utc_now_dt()).astimezone(UTC)
    retry_at = now_dt + timedelta(seconds=delay)
    _append_event({
        "event": "preflight_deferred",
        "status": "scheduled",
        "schedule_id": schedule_id,
        "recorded_at": _utc_iso(now_dt),
        "retry_at": _utc_iso(retry_at),
        "preflight_attempts": attempt,
        "failure_class": "provider_unavailable",
        "detail": (
            "Provider preflight was unavailable before consequence; no publish request was attempted. "
            f"Automatic retry is deferred until {_utc_iso(retry_at)}: {exc}"
        ),
    })
    return get_schedule(schedule_id) or {
        "schedule_id": schedule_id,
        "status": "scheduled",
        "retry_at": _utc_iso(retry_at),
        "preflight_attempts": attempt,
        "failure_class": "provider_unavailable",
    }


def _defer_provider_circuit(
    schedule_id: str, circuit: dict[str, Any], *, now: datetime | None = None,
) -> dict[str, Any]:
    now_dt = (now or _utc_now_dt()).astimezone(UTC)
    retry_at = str(circuit.get("retry_at") or _utc_iso(now_dt + timedelta(minutes=15)))
    _append_event({
        "event": "provider_circuit_deferred",
        "status": "scheduled",
        "schedule_id": schedule_id,
        "recorded_at": _utc_iso(now_dt),
        "retry_at": retry_at,
        "failure_class": "provider_circuit_open",
        "failure_stage": "pre_consequence",
        "automatic_retry": True,
        "circuit_failure_class": circuit.get("failure_class"),
        "circuit_retry_at": retry_at,
        "detail": (
            "Provider/account write circuit is open from a prior definite rejection; "
            "no publish request was attempted for this schedule."
        ),
    })
    return get_schedule(schedule_id) or {
        "schedule_id": schedule_id,
        "status": "scheduled",
        "retry_at": retry_at,
        "failure_class": "provider_circuit_open",
        "failure_stage": "pre_consequence",
        "automatic_retry": True,
        "circuit_failure_class": circuit.get("failure_class"),
        "circuit_retry_at": retry_at,
    }


def _thread_receipt(
    *,
    campaign: str,
    provider_name: str,
    account: Any,
    schedule_id: str,
    stored_digest: str,
    status: str,
    root_post_id: str | None,
    root_url: str | None,
    part_count: int,
    readback_verified: bool,
    detail: str,
    publication_part_ids: list[str] | None = None,
    completed_part_count: int | None = None,
    failed_part_index: int | None = None,
) -> PublishReceipt:
    return PublishReceipt(
        campaign=campaign,
        provider=provider_name,
        account_id=account.account_id,
        schedule_id=schedule_id,
        username=account.username,
        status=status,
        text_sha256=stored_digest,
        recorded_at=_utc_iso(),
        post_id=root_post_id,
        url=root_url,
        readback_verified=readback_verified,
        detail=detail,
        publication_type="thread",
        part_count=part_count,
        publication_part_ids=list(publication_part_ids or []),
        completed_part_count=completed_part_count,
        failed_part_index=failed_part_index,
    )


def _execute_thread(
    *,
    schedule_id: str,
    record: dict[str, Any],
    provider_impl: Any,
    account: Any,
) -> dict[str, Any]:
    """Execute one frozen X/Threads reply chain without replaying completed parts."""
    campaign = str(record["campaign"])
    provider_name = str(record["provider"])
    stored_digest = str(record["text_sha256"])
    parts = record.get("publication_parts")
    if not isinstance(parts, list) or len(parts) < 2:
        return _block(schedule_id, "drift_blocked", "Frozen thread payload is missing or incomplete.")

    created_parts: list[dict[str, Any]] = []
    verified_parts = 0
    root_post_id: str | None = None
    root_url: str | None = None

    for part in parts:
        index = int(part["index"])
        text = str(part["text"])
        part_digest = str(part["text_sha256"])
        reply_to = created_parts[-1]["post_id"] if created_parts else None
        _schedule_event(
            schedule_id,
            "publication_part_claimed",
            "executing",
            part_index=index,
            part_count=len(parts),
            part_text_sha256=part_digest,
            reply_to_post_id=reply_to,
            publication_root_post_id=root_post_id,
            publication_completed_parts=len(created_parts),
            detail="Thread part claimed immediately before provider consequence; it is never blindly replayed.",
        )
        try:
            created = (
                provider_impl.publish(text)
                if reply_to is None
                else provider_impl.reply(text, reply_to)
            )
        except AmbiguousProviderEffect as exc:
            receipt = _thread_receipt(
                campaign=campaign,
                provider_name=provider_name,
                account=account,
                schedule_id=schedule_id,
                stored_digest=stored_digest,
                status="ambiguous_effect",
                root_post_id=root_post_id,
                root_url=root_url,
                part_count=len(parts),
                readback_verified=False,
                detail=f"Thread part {index} effect is ambiguous; blind continuation/retry is blocked: {exc}",
                publication_part_ids=[row["post_id"] for row in created_parts],
                completed_part_count=len(created_parts),
                failed_part_index=index,
            )
            append_receipt(receipt.to_dict())
            _schedule_event(
                schedule_id,
                "ambiguous_effect",
                "ambiguous_effect",
                receipt_status="ambiguous_effect",
                post_id=root_post_id,
                url=root_url,
                publication_root_post_id=root_post_id,
                publication_completed_parts=len(created_parts),
                publication_failed_part_index=index,
                publication_failure_kind="ambiguous",
                detail=(
                    f"Thread part {index}/{len(parts)} may have produced an external effect. "
                    f"Parts 1-{len(created_parts)} are durably recorded; no blind retry or continuation."
                ),
            )
            return get_schedule(schedule_id) or receipt.to_dict()
        except ProviderRejected as exc:
            if not created_parts:
                _schedule_event(
                    schedule_id,
                    "failed",
                    "failed",
                    publication_failed_part_index=index,
                    publication_failure_kind="rejected",
                    detail=f"Provider rejected the first thread part: {exc}. This schedule will not auto-retry.",
                    **provider_failure_metadata(exc, stage="provider_consequence"),
                )
                return get_schedule(schedule_id) or {"schedule_id": schedule_id, "status": "failed"}
            receipt = _thread_receipt(
                campaign=campaign,
                provider_name=provider_name,
                account=account,
                schedule_id=schedule_id,
                stored_digest=stored_digest,
                status="partial_effect",
                root_post_id=root_post_id,
                root_url=root_url,
                part_count=len(parts),
                readback_verified=False,
                detail=f"Thread stopped after {len(created_parts)} parts because part {index} was rejected.",
                publication_part_ids=[row["post_id"] for row in created_parts],
                completed_part_count=len(created_parts),
                failed_part_index=index,
            )
            append_receipt(receipt.to_dict())
            _schedule_event(
                schedule_id,
                "partial_effect",
                "partial_effect",
                receipt_status="partial_effect",
                post_id=root_post_id,
                url=root_url,
                publication_root_post_id=root_post_id,
                publication_completed_parts=len(created_parts),
                publication_failed_part_index=index,
                publication_failure_kind="rejected",
                detail=(
                    f"Provider definitely rejected thread part {index}/{len(parts)} after earlier parts published. "
                    "The partial external publication is terminal and requires review; it is never auto-retried."
                ),
            )
            return get_schedule(schedule_id) or receipt.to_dict()
        except Exception as exc:
            receipt = _thread_receipt(
                campaign=campaign,
                provider_name=provider_name,
                account=account,
                schedule_id=schedule_id,
                stored_digest=stored_digest,
                status="ambiguous_effect",
                root_post_id=root_post_id,
                root_url=root_url,
                part_count=len(parts),
                readback_verified=False,
                detail=f"Unexpected error after thread consequence began at part {index}: {type(exc).__name__}",
                publication_part_ids=[row["post_id"] for row in created_parts],
                completed_part_count=len(created_parts),
                failed_part_index=index,
            )
            append_receipt(receipt.to_dict())
            _schedule_event(
                schedule_id,
                "ambiguous_effect",
                "ambiguous_effect",
                receipt_status="ambiguous_effect",
                post_id=root_post_id,
                url=root_url,
                publication_root_post_id=root_post_id,
                publication_completed_parts=len(created_parts),
                publication_failed_part_index=index,
                publication_failure_kind="unexpected",
                detail="Thread consequence became uncertain; no blind retry or continuation is permitted.",
            )
            return get_schedule(schedule_id) or receipt.to_dict()

        post_id = str(created["id"])
        if not root_post_id:
            root_post_id = post_id
            root_url = provider_impl.post_url(account, post_id)
        created_parts.append({
            "index": index,
            "post_id": post_id,
            "reply_to_post_id": reply_to,
            "text_sha256": part_digest,
        })
        _schedule_event(
            schedule_id,
            "publication_part_created",
            "executing",
            part_index=index,
            part_count=len(parts),
            part_post_id=post_id,
            part_text_sha256=part_digest,
            reply_to_post_id=reply_to,
            publication_root_post_id=root_post_id,
            publication_completed_parts=len(created_parts),
            detail="Provider returned a durable ID for this frozen thread part before optional readback.",
        )
        try:
            verified = bool(provider_impl.verify_post(post_id, text))
        except (ProviderRejected, ProviderUnavailable, OSError, TimeoutError):
            verified = False
        if verified:
            verified_parts += 1
        _schedule_event(
            schedule_id,
            "publication_part_verified" if verified else "publication_part_unverified",
            "executing",
            part_index=index,
            part_count=len(parts),
            part_post_id=post_id,
            part_text_sha256=part_digest,
            publication_root_post_id=root_post_id,
            publication_completed_parts=len(created_parts),
            part_readback_verified=verified,
            detail=(
                "Thread part readback verified."
                if verified
                else "Thread part has a durable provider ID but readback did not verify."
            ),
        )

    all_verified = verified_parts == len(parts)
    provisional = _thread_receipt(
        campaign=campaign,
        provider_name=provider_name,
        account=account,
        schedule_id=schedule_id,
        stored_digest=stored_digest,
        status="published_unverified",
        root_post_id=root_post_id,
        root_url=root_url,
        part_count=len(parts),
        readback_verified=False,
        detail=f"All {len(parts)} thread parts returned durable provider IDs; aggregate readback pending/finalising.",
        publication_part_ids=[row["post_id"] for row in created_parts],
        completed_part_count=len(created_parts),
    )
    append_receipt(provisional.to_dict())
    final_status = "published_unverified"
    if all_verified:
        final = _thread_receipt(
            campaign=campaign,
            provider_name=provider_name,
            account=account,
            schedule_id=schedule_id,
            stored_digest=stored_digest,
            status="published_verified",
            root_post_id=root_post_id,
            root_url=root_url,
            part_count=len(parts),
            readback_verified=True,
            detail=f"All {len(parts)} frozen thread parts were created and independently read back.",
            publication_part_ids=[row["post_id"] for row in created_parts],
            completed_part_count=len(created_parts),
        )
        append_receipt(final.to_dict())
        final_status = "published_verified"
    _schedule_event(
        schedule_id,
        "completed",
        final_status,
        receipt_status=final_status,
        post_id=root_post_id,
        url=root_url,
        readback_verified=all_verified,
        publication_root_post_id=root_post_id,
        publication_completed_parts=len(parts),
        detail=(
            f"Scheduled thread completed with {len(parts)} verified parts."
            if all_verified
            else f"Scheduled thread completed with {len(parts)} durable parts; one or more readbacks remain unverified."
        ),
    )
    return get_schedule(schedule_id) or provisional.to_dict()


def execute_schedule(schedule_id: str, *, provider_factory: Callable[[str], Any] = get_provider,
                     now: datetime | None = None) -> dict[str, Any]:
    record = get_schedule(schedule_id)
    if record is None:
        raise ScheduleError(f"Unknown schedule: {schedule_id}")
    if record.get("status") != "scheduled":
        return record

    now_dt = (now or _utc_now_dt()).astimezone(UTC)
    meaning = classify_schedule(record, now=now_dt)
    if not meaning.actionable:
        return record

    campaign = str(record["campaign"])
    provider_name = str(record["provider"])

    stored_text = str(record.get("text") or "")
    stored_digest = str(record.get("text_sha256") or "")
    if not stored_text or _digest(stored_text) != stored_digest:
        return _block(schedule_id, "drift_blocked", "Persisted scheduled payload failed its integrity hash.")
    if record.get("publication_sha256") and not validate_frozen_publication(provider_name, record):
        return _block(schedule_id, "drift_blocked", "Frozen publication parts failed their integrity contract.")
    if record.get("payload_source") == "builtin_campaign":
        current_text = builtin_text(campaign, provider_name)
        if not current_text or _digest(current_text) != stored_digest:
            return _block(schedule_id, "drift_blocked", "Campaign payload changed after scheduling; stale or unreviewed payload refused.")

    circuit = provider_write_circuit(provider_name, str(record["account_id"]), now=now_dt)
    if circuit.get("open") is True:
        return _defer_provider_circuit(schedule_id, circuit, now=now_dt)

    try:
        from ocpf_post.providers import for_account
        # The scheduled account identity was authorised and frozen when the
        # reservation was created. Injected test/embedding providers may therefore
        # use that exact frozen identity here; the current campaign binding and
        # authenticated account are still checked immediately below before any
        # consequence.
        provider_impl = for_account(
            provider_name,
            str(record["account_id"]),
            factory=provider_factory,
            registered_identity=True,
        )
        account = provider_impl.account()
    except ProviderUnavailable as exc:
        return _defer_preflight(schedule_id, exc, now=now_dt)
    except ProviderRejected as exc:
        # ProviderRejected is a definite rejection, not a retry signal. Temporary
        # pre-consequence transport failure must be typed ProviderUnavailable by
        # the provider adapter. Error prose/status zero cannot grant retryability.
        return _block(
            schedule_id,
            "failed",
            f"Provider identity check failed before consequence: {exc}",
            **provider_failure_metadata(exc, stage="identity_preflight"),
        )
    except ValueError as exc:
        return _block(schedule_id, "failed", f"Provider identity check failed before consequence: {exc}")
    if account.account_id != record.get("account_id"):
        return _block(schedule_id, "drift_blocked", f"Authenticated account changed: expected {record.get('account_id')}, got {account.account_id}.")
    binding_error = destination_binding_error(campaign, provider_name, account)
    if binding_error:
        return _block(schedule_id, "drift_blocked", binding_error)

    prior = terminal_effect_receipt(campaign, provider_name, account.account_id)
    if prior:
        return _block(schedule_id, "duplicate_blocked", f"A terminal receipt already exists: {prior.get('status')} {prior.get('url') or ''}".strip())

    from ocpf_post.campaigns import builtin_manifest
    manifest = builtin_manifest(campaign)
    from ocpf_post.vault_sync import guard
    vault_reason = guard(manifest, provider_name)
    if vault_reason:
        return _block(schedule_id, "drift_blocked", vault_reason)
    from ocpf_post.source_guard import guard as source_guard
    source_reason = source_guard(manifest, now=now_dt)
    if source_reason:
        return _block(schedule_id, "drift_blocked", source_reason)
    from ocpf_post.generated_supply_guard import delivery_guard as generated_delivery_guard
    generated_reason = generated_delivery_guard(manifest, provider_name, str(account.account_id), stored_text)
    if generated_reason:
        return _block(schedule_id, "drift_blocked", generated_reason)

    claim_extra = {
        "runner_pid": os.getpid(),
        "detail": "Claimed immediately before provider consequence. A crashed executing item is never auto-retried.",
        **_retry_clear_fields(record),
    }
    _schedule_event(schedule_id, "claimed", "executing", **claim_extra)
    if record.get("publication_type") == "thread":
        return _execute_thread(
            schedule_id=schedule_id,
            record=record,
            provider_impl=provider_impl,
            account=account,
        )
    try:
        created = provider_impl.publish(stored_text)
    except AmbiguousProviderEffect as exc:
        receipt = PublishReceipt(campaign=campaign, provider=provider_name, account_id=account.account_id, schedule_id=schedule_id,
                                 username=account.username, status="ambiguous_effect", text_sha256=stored_digest,
                                 recorded_at=_utc_iso(), detail=str(exc),
                                 publication_type=str(record.get("publication_type") or "single"),
                                 part_count=int(record.get("part_count") or 1))
        append_receipt(receipt.to_dict())
        _schedule_event(schedule_id, "ambiguous_effect", "ambiguous_effect", receipt_status="ambiguous_effect",
                        detail=f"Provider effect is ambiguous; blind retry is blocked. Inspect {receipts_file()}.")
        return get_schedule(schedule_id) or receipt.to_dict()
    except ProviderRejected as exc:
        _schedule_event(
            schedule_id,
            "failed",
            "failed",
            detail=f"Provider rejected the consequence: {exc}. This schedule will not auto-retry.",
            **provider_failure_metadata(exc, stage="provider_consequence"),
        )
        return get_schedule(schedule_id) or {"schedule_id": schedule_id, "status": "failed"}
    except Exception as exc:
        # The durable claim was already written, therefore even an unexpected
        # transport/runtime error is consequence-ambiguous and never retry-safe.
        receipt = PublishReceipt(campaign=campaign, provider=provider_name, account_id=account.account_id, schedule_id=schedule_id,
                                 username=account.username, status="ambiguous_effect", text_sha256=stored_digest,
                                 recorded_at=_utc_iso(), detail=f"Unexpected post-consequence error: {type(exc).__name__}",
                                 publication_type=str(record.get("publication_type") or "single"),
                                 part_count=int(record.get("part_count") or 1))
        append_receipt(receipt.to_dict())
        _schedule_event(schedule_id, "ambiguous_effect", "ambiguous_effect", receipt_status="ambiguous_effect",
                        detail="Provider consequence began and outcome could not be established; blind retry is blocked.")
        return get_schedule(schedule_id) or receipt.to_dict()

    post_id = str(created["id"])
    url = provider_impl.post_url(account, post_id)
    provisional = PublishReceipt(campaign=campaign, provider=provider_name, account_id=account.account_id, schedule_id=schedule_id,
                                 username=account.username, status="published_unverified", text_sha256=stored_digest,
                                 recorded_at=_utc_iso(), post_id=post_id, url=url, readback_verified=False,
                                 detail="Provider creation response returned a durable post id; exact provider text is not proven until readback matches the frozen payload.",
                                 publication_type=str(record.get("publication_type") or "single"),
                                 part_count=int(record.get("part_count") or 1))
    append_receipt(provisional.to_dict())
    try:
        verified = provider_impl.verify_post(post_id, stored_text)
    except (ProviderRejected, ProviderUnavailable, OSError, TimeoutError):
        verified = False
    final_status = "published_unverified"
    if verified:
        url = provider_impl.post_url(account, post_id)
        final = PublishReceipt(campaign=campaign, provider=provider_name, account_id=account.account_id, schedule_id=schedule_id,
                               username=account.username, status="published_verified", text_sha256=stored_digest,
                               recorded_at=_utc_iso(), post_id=post_id, url=url, readback_verified=True,
                               detail="Provider creation response and readback agree on id/text.",
                               publication_type=str(record.get("publication_type") or "single"),
                               part_count=int(record.get("part_count") or 1))
        append_receipt(final.to_dict())
        final_status = "published_verified"
    _schedule_event(schedule_id, "completed", final_status, receipt_status=final_status, post_id=post_id, url=url,
                    readback_verified=verified,
                    detail=("Scheduled provider consequence completed and readback verified." if verified
                            else "Scheduled provider consequence completed with a durable provider id; exact provider text was not proven because readback was unavailable or did not verify."))
    return get_schedule(schedule_id) or provisional.to_dict()


def run_due(*, now: datetime | None = None, limit: int = 20,
            provider_factory: Callable[[str], Any] = get_provider) -> list[dict[str, Any]]:
    with RunnerLock():
        results: list[dict[str, Any]] = []
        for record in due_schedules(now=now, limit=limit):
            latest = get_schedule(str(record["schedule_id"]))
            if latest and latest.get("status") == "scheduled":
                results.append(execute_schedule(str(record["schedule_id"]), provider_factory=provider_factory, now=now))
        return results
