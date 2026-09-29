"""ChatGPT Work / Google Docs reply handoff.

Pending public conversation context is exported through the existing private editorial
telemetry. ChatGPT Work authors or reviews exact reply text in a designated Google
Doc section. This module imports only hash-bound reviewed candidates back into
an isolated editorial store. Import never drafts or promotes replies: the local reply-worker policy
decides whether a reviewed candidate may become sendable. This module never calls
a language model or social provider.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
import json
import os
import re
from typing import Any

from ocpf_post import engagement as e, local_store
from ocpf_post.google_vault import read_document
from ocpf_post.state import config_dir

UTC = timezone.utc
BEGIN = "POSTSTEWARD REPLY CANDIDATES BEGIN"
END = "POSTSTEWARD REPLY CANDIDATES END"
LEGACY_BEGIN = "POST-ONCE APPROVED REPLIES BEGIN"
LEGACY_END = "POST-ONCE APPROVED REPLIES END"
MAX_ENTRIES = 100
MAX_REQUESTS = 10
MAX_INCOMING_CHARS = 2000
POLICY_FILE = "reply-work-policy.json"
REPLY_FRESHNESS_HOURS = 24


def _stamp(value: datetime | None = None) -> str:
    return (value or datetime.now(UTC)).astimezone(UTC).isoformat()


def policy_path():
    return config_dir() / POLICY_FILE


def policy() -> dict[str, Any]:
    value = local_store.read(policy_path()) or {
        "schema_version": 1,
        "enabled": False,
        "document_id": "",
        "max_age_minutes": 120,
    }
    if set(value) != {"schema_version", "enabled", "document_id", "max_age_minutes"}:
        raise ValueError("Invalid reply Work policy")
    if value.get("schema_version") != 1 or type(value.get("enabled")) is not bool:
        raise ValueError("Invalid reply Work policy")
    document_id = value.get("document_id")
    if not isinstance(document_id, str) or (document_id and not re.fullmatch(r"[A-Za-z0-9_-]{10,150}", document_id)):
        raise ValueError("Invalid reply Work document ID")
    age = value.get("max_age_minutes")
    if type(age) is not int or not 15 <= age <= 1440:
        raise ValueError("Invalid reply Work freshness window")
    return value


def mode() -> str:
    """Return enabled, disabled or attention without silently buying model fallback."""
    try:
        value = policy()
    except (OSError, ValueError, TypeError, KeyError):
        return "attention"
    return "enabled" if value["enabled"] and value["document_id"] else "disabled"


def enabled() -> bool:
    return mode() == "enabled"


def configure(document_id: str, *, apply: bool = False, enabled_value: bool = True,
              max_age_minutes: int = 120) -> dict[str, Any]:
    value = {
        "schema_version": 1,
        "enabled": bool(enabled_value),
        "document_id": str(document_id).strip(),
        "max_age_minutes": int(max_age_minutes),
    }
    # Reuse normal validation before any write.
    if not re.fullmatch(r"[A-Za-z0-9_-]{10,150}", value["document_id"]):
        raise ValueError("Invalid reply Work document ID")
    if not 15 <= value["max_age_minutes"] <= 1440:
        raise ValueError("Invalid reply Work freshness window")
    if apply:
        local_store.write(policy_path(), value)
    return {
        "schema_version": 1,
        "result": "configured" if apply else "preview",
        "policy": value,
        "boundary": "Local configuration only. No Google read, model call, draft, reply or provider effect.",
    }


def candidate_digest(entry: dict[str, Any]) -> str:
    from ocpf_post.vault_sync import digest
    return digest({key: value for key, value in entry.items() if key != "candidate_sha256"})


def approval_digest(entry: dict[str, Any]) -> str:
    """Backward-compatible helper name; the digest no longer conveys authority."""
    return candidate_digest(entry)


def _fresh_for_work(row: dict[str, Any], now: datetime) -> bool:
    """Only spend Work capacity on replies that can still be acted on usefully."""
    try:
        first_seen = e.at(str(row["first_seen_at"]))
    except (KeyError, TypeError, ValueError):
        return False
    age = now - first_seen
    if age < timedelta(0) or age >= timedelta(hours=REPLY_FRESHNESS_HOURS):
        return False
    draft = row.get("draft")
    if isinstance(draft, dict):
        try:
            if now >= e.at(str(draft["expires_at"])):
                return False
        except (KeyError, TypeError, ValueError):
            return False
    return True


def _freshness_sort_key(row: dict[str, Any]) -> tuple[float, str]:
    try:
        observed = e.at(str(row.get("first_seen_at") or "")).timestamp()
    except (TypeError, ValueError):
        observed = float("-inf")
    return observed, str(row.get("id") or "")


def requests(*, now: datetime | None = None, limit: int = MAX_REQUESTS) -> dict[str, Any]:
    """Project pending reply work into bounded private telemetry."""
    from ocpf_post import reply_worker as w

    now = (now or datetime.now(UTC)).astimezone(UTC)
    if mode() != "enabled":
        return {
            "schema_version": 1,
            "observed_at": _stamp(now),
            "count": 0,
            "requests": [],
            "boundary": "Reply Work handoff is not enabled; no reply context is exported.",
        }
    settings = w.policy()
    inbox = e.read()["inbox"]
    worker = w.state()
    rows = []
    terminal = {
        "no_response_needed", "opted_out", "published_verified", "published_unverified",
        "ambiguous_effect", "rejected", "sending",
    }
    for row in sorted(inbox.values(), key=_freshness_sort_key, reverse=True):
        provider = str(row.get("provider") or "")
        account_id = str(row.get("account_id") or "")
        account = provider + ":" + account_id
        if account not in settings["accounts"] or row.get("status") not in {"pending", "drafted"}:
            continue
        if row.get("attempted_at") or not _fresh_for_work(row, now):
            continue
        identity = str(row.get("id") or "")
        context = row.get("context") if isinstance(row.get("context"), dict) else {}
        if not identity or not context:
            continue
        item = worker.get("items", {}).get(identity, {})
        if item.get("status") in terminal:
            continue
        if item.get("status") == "review_required" and item.get("reason") in {"work_hold", "account_requires_explicit_send"}:
            continue
        draft = row.get("draft") if isinstance(row.get("draft"), dict) else {}
        text = str(context.get("text") or "")
        rows.append({
            "inbox_id": identity,
            "provider": provider,
            "account_id": account_id,
            "campaign": row.get("campaign"),
            "conversation_root_id": row.get("conversation_root_id"),
            "conversation_depth": row.get("conversation_depth", 0),
            "incoming_post_id": context.get("post_id"),
            "parent_post_id": context.get("parent_post_id"),
            "author": context.get("author"),
            "incoming_text": text[:MAX_INCOMING_CHARS],
            "context_sha256": e.digest(context),
            "first_seen_at": row.get("first_seen_at"),
            "last_seen_at": row.get("last_seen_at"),
            "engagement_status": row.get("status"),
            "worker_status": item.get("status"),
            "worker_reason": item.get("reason"),
            "existing_draft_text": draft.get("text") if isinstance(draft.get("text"), str) else None,
        })
        if len(rows) >= max(1, min(int(limit), MAX_REQUESTS)):
            break
    return {
        "schema_version": 1,
        "observed_at": _stamp(now),
        "count": len(rows),
        "requests": rows,
        "boundary": (
            "Fresh pending public reply context only (under 24 hours and with any draft still unexpired). "
            "Newest useful work is prioritised so stale backlog cannot consume Work capacity. ChatGPT Work may "
            "propose exact text in the designated Google Doc, but local context/account/effect checks remain "
            "authoritative and this projection creates no draft, send authority or provider consequence."
        ),
    }


def _section(text: str) -> list[dict[str, Any]]:
    lines = text.replace("\r\n", "\n").splitlines()
    starts = [index for index, line in enumerate(lines) if line.strip() == BEGIN]
    ends = [index for index, line in enumerate(lines) if line.strip() == END]
    if len(starts) == 1 and len(ends) == 1 and starts[0] < ends[0]:
        raw = "\n".join(lines[starts[0] + 1:ends[0]])
    else:
        if (
            ("POST-ONCE REPLY CANDIDATES BEGIN" in lines and "POST-ONCE REPLY CANDIDATES END" in lines)
            or ("POST-ONCE REVIEWED REPLY CANDIDATES BEGIN" in lines and "POST-ONCE REVIEWED REPLY CANDIDATES END" in lines)
        ):
            # Earlier Post-Once candidate formats are intentionally inert after
            # product graduation. Migrate the document to the PostSteward markers
            # before any candidate can enter the local worker.
            return []
        legacy_starts = [index for index, line in enumerate(lines) if line.strip() == LEGACY_BEGIN]
        legacy_ends = [index for index, line in enumerate(lines) if line.strip() == LEGACY_END]
        if len(legacy_starts) == 1 and len(legacy_ends) == 1 and legacy_starts[0] < legacy_ends[0]:
            # Transitional fail-closed behaviour: old machine approvals are never
            # imported under the reviewed-candidate contract. The Doc can be
            # migrated after the new runtime is installed without racing a send.
            return []
        raise ValueError("Reply Work document requires one complete candidates section")
    if len(raw.encode("utf-8")) > 500_000:
        raise ValueError("Reply Work section exceeds size limit")
    value = json.loads(raw)
    if not isinstance(value, dict) or set(value) != {"schema_version", "entries"}:
        raise ValueError("Invalid reply Work section")
    if value.get("schema_version") != 1 or not isinstance(value.get("entries"), list):
        raise ValueError("Invalid reply Work section")
    if len(value["entries"]) > MAX_ENTRIES:
        raise ValueError("Reply Work section exceeds entry limit")
    return value["entries"]


def _validate_entry(entry: Any) -> dict[str, Any]:
    fields = {
        "inbox_id", "provider", "account_id", "campaign", "context_sha256",
        "incoming_post_id", "parent_post_id", "reply_text", "status", "candidate_sha256",
    }
    if not isinstance(entry, dict) or set(entry) != fields:
        raise ValueError("Invalid reply Work entry shape")
    for key in fields:
        if not isinstance(entry.get(key), str):
            raise ValueError("Invalid reply Work entry field")
    provider = entry["provider"].strip().lower()
    if provider not in {"x", "threads", "linkedin"}:
        raise ValueError("Unsupported reply Work provider")
    if entry["status"] not in {"CANDIDATE", "HOLD", "SKIP"}:
        raise ValueError("Unknown reply Work status")
    if not re.fullmatch(r"[0-9a-f]{64}", entry["context_sha256"]):
        raise ValueError("Invalid reply context digest")
    if entry["candidate_sha256"] != candidate_digest(entry):
        raise ValueError("Reply Work candidate hash mismatch")
    text = entry["reply_text"].strip()
    limit = 280 if provider == "x" else 500
    if entry["status"] == "CANDIDATE":
        from ocpf_post.reply_model import text_allowed
        if not text_allowed(text, limit):
            raise ValueError("Reply candidate violates deterministic output restrictions")
    elif len(text) > limit:
        raise ValueError("Reply Work text exceeds provider bound")
    return {**entry, "provider": provider, "reply_text": text}


def prepare(document: dict[str, Any], *, now: datetime | None = None) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    from ocpf_post import reply_worker as w

    now = (now or datetime.now(UTC)).astimezone(UTC)
    settings = w.policy()
    inbox = e.read()["inbox"]
    decisions = []
    skipped = []
    seen = set()
    for raw in _section(str(document.get("text") or "")):
        entry = _validate_entry(raw)
        identity = entry["inbox_id"]
        if identity in seen:
            raise ValueError("Duplicate reply Work inbox ID")
        seen.add(identity)
        row = inbox.get(identity)
        if not isinstance(row, dict):
            skipped.append({"inbox_id": identity, "reason": "inbox_item_missing"})
            continue
        account = str(row.get("provider") or "") + ":" + str(row.get("account_id") or "")
        if account not in settings["accounts"]:
            skipped.append({"inbox_id": identity, "reason": "account_not_authorised"})
            continue
        context = row.get("context") if isinstance(row.get("context"), dict) else {}
        if (
            entry["provider"] != row.get("provider")
            or entry["account_id"] != str(row.get("account_id") or "")
            or entry["campaign"] != str(row.get("campaign") or "")
            or entry["context_sha256"] != e.digest(context)
            or entry["incoming_post_id"] != str(context.get("post_id") or "")
            or entry["parent_post_id"] != str(context.get("parent_post_id") or "")
        ):
            skipped.append({"inbox_id": identity, "reason": "context_or_identity_changed"})
            continue
        if row.get("status") not in {"pending", "drafted"} or row.get("attempted_at"):
            skipped.append({"inbox_id": identity, "reason": "reply_no_longer_sendable"})
            continue
        if not _fresh_for_work(row, now):
            skipped.append({"inbox_id": identity, "reason": "reply_window_expired"})
            continue
        decisions.append(entry)
    return decisions, skipped


def candidates_path():
    from ocpf_post.state import state_dir
    return state_dir() / "reply-work-candidates.json"


def _store_candidates(entries, document, *, now):
    """Replace editorial snapshot only; never mutate drafts, worker or policy."""
    value = {
        "schema_version": 1,
        "document_id": document["document_id"],
        "version": document.get("version"),
        "modified_at": document["modified_at"],
        "imported_at": _stamp(now),
        "entries": entries,
    }
    with local_store.locked(candidates_path()):
        local_store.write(candidates_path(), value)
    return [{"inbox_id": entry["inbox_id"], "result": "stored_candidate"} for entry in entries]


def sync(*, apply: bool = False, reader=read_document, now: datetime | None = None) -> dict[str, Any]:
    now = (now or datetime.now(UTC)).astimezone(UTC)
    settings = policy()
    if not settings["enabled"] or not settings["document_id"]:
        return {
            "schema_version": 1,
            "status": "disabled",
            "apply": apply,
            "boundary": "Reply Work handoff is disabled; existing reply behaviour is unchanged.",
        }
    document = reader(settings["document_id"])
    if document.get("document_id") != settings["document_id"]:
        raise ValueError("Reply Work document identity changed")
    modified = datetime.fromisoformat(str(document["modified_at"]).replace("Z", "+00:00")).astimezone(UTC)
    age_minutes = (now - modified).total_seconds() / 60
    if age_minutes < -1 or age_minutes > settings["max_age_minutes"]:
        return {
            "schema_version": 1,
            "status": "stale",
            "apply": apply,
            "document_id": settings["document_id"],
            "document_version": document.get("version"),
            "decision_count": 0,
            "candidate_count": 0,
            "hold_count": 0,
            "skip_count": 0,
            "skipped": [],
            "boundary": "Stale/future Google reply candidate cannot create or promote a local draft.",
        }
    decisions, skipped = prepare(document, now=now)
    applied = []
    if apply:
        applied = _store_candidates(decisions, document, now=now)
    return {
        "schema_version": 1,
        "status": "synced" if apply else "preview",
        "apply": apply,
        "document_id": settings["document_id"],
        "document_version": document.get("version"),
        "decision_count": len(decisions),
        "candidate_count": sum(entry["status"] == "CANDIDATE" for entry in decisions),
        "hold_count": sum(entry["status"] == "HOLD" for entry in decisions),
        "skip_count": sum(entry["status"] == "SKIP" for entry in decisions),
        "applied": applied,
        "skipped": skipped,
        "boundary": (
            "Google/Work output is accepted only against the same pre-consequence inbox/context/account. "
            "Import stores inert candidate data only. It never creates a draft or changes worker state or policy. "
            "The local worker independently admits candidates and revalidates authority before any send."
        ),
    }


def status() -> dict[str, Any]:
    value = policy()
    return {
        "schema_version": 1,
        "enabled": bool(value["enabled"] and value["document_id"]),
        "document_id": value["document_id"] or None,
        "max_age_minutes": value["max_age_minutes"],
        "requests": requests(),
        "boundary": "Read-only local reply Work status; no Google read or provider consequence.",
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("status")
    configure_parser = commands.add_parser("configure")
    configure_parser.add_argument("--document-id", required=True)
    configure_parser.add_argument("--max-age-minutes", type=int, default=120)
    configure_parser.add_argument("--disabled", action="store_true")
    configure_parser.add_argument("--apply", action="store_true")
    sync_parser = commands.add_parser("sync")
    sync_parser.add_argument("--apply", action="store_true")
    args = parser.parse_args(argv)

    if args.command == "status":
        result = status()
    elif args.command == "configure":
        result = configure(
            args.document_id,
            apply=args.apply,
            enabled_value=not args.disabled,
            max_age_minutes=args.max_age_minutes,
        )
    else:
        result = sync(apply=args.apply)
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

