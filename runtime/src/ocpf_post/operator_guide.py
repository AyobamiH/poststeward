"""Evidence-bound owner guidance. Pure projection: no subprocess, network or writes.

Commands are fixed inspection recipes, not strings taken from runtime evidence.
The browser receives this view with the existing composite snapshot. A suggestion
is never an execution request, and copying it never means an issue is resolved.
"""
from __future__ import annotations

from datetime import datetime, timezone
from functools import lru_cache
import re
import shlex
from typing import Any

UTC = timezone.utc
STALE_AFTER_SECONDS = 120
GUIDE_VERSION = 1
PROVIDERS = {"x", "threads", "linkedin"}
IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:@/-]{0,159}\Z")
# This set controls presentation only, never command authority.
BEGINNER = {
    "help", "health", "doctor", "work status", "schedule list", "schedule inspect",
    "portfolio status", "portfolio volume-audit", "portfolio calendar", "replenish status",
    "vault status", "vault coverage", "engagement status", "engagement worker-status",
    "campaign show", "campaign explain", "receipt", "threads receipt", "linkedin receipt",
}


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _rows(value: Any) -> list[dict[str, Any]]:
    return [row for row in value if isinstance(row, dict)] if isinstance(value, list) else []


def _number(value: Any) -> float:
    if type(value) not in (int, float):
        return 0.0
    try:
        number = float(value)
        return number if float("-inf") < number < float("inf") else 0.0
    except OverflowError:
        return 0.0


def _at(value: Any) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return parsed.astimezone(UTC) if parsed.tzinfo else None
    except (TypeError, ValueError):
        return None


def _id(value: Any) -> str | None:
    return value if isinstance(value, str) and IDENTIFIER.fullmatch(value) else None


def inspection(path: str, *args: str) -> dict[str, Any]:
    """Validate curated recipe names against the native catalogue, fail closed."""
    from ocpf_post.cli_catalog import COMMANDS
    command = next((row for row in COMMANDS if row.path == path), None)
    if command is None or command.consequence != "READ_ONLY":
        raise ValueError("Guide recipe is not a catalogued read-only command")
    argv = ["./ocpf-post", *path.split(), *args]
    if any(part in {"--apply", "--live", "--allow-duplicate"} for part in argv):
        raise ValueError("Guide recipes cannot grant execution authority")
    if any("\n" in part or "\r" in part or "\x00" in part for part in argv):
        raise ValueError("Multiline guide commands are not supported")
    return {"path": path, "argv": argv, "command": shlex.join(argv),
            "effect": "Inspection only", "execution": "copy_to_terminal_only"}


def _campaign_inspection(row: dict[str, Any]) -> dict[str, Any]:
    schedule = _id(row.get("schedule_id"))
    if schedule:
        return inspection("schedule inspect", schedule, "--json")
    campaign, provider = _id(row.get("campaign")), row.get("provider")
    if campaign and provider in PROVIDERS:
        return inspection("campaign explain", "--campaign=" + campaign,
                          "--provider=" + str(provider), "--json")
    return inspection("work status", "--json")


def _card(key: str, title: str, meaning: str, *, priority: int, evidence: dict[str, Any],
          source: str, action: dict[str, Any], next_step: str, expect: str,
          topic: str, state: str = "inspect", caution: str = "") -> dict[str, Any]:
    return {"id": key, "title": title, "meaning": meaning, "priority": priority,
            "state": state, "evidence": evidence, "evidence_path": source,
            "action": action, "next_step": next_step, "expect": expect,
            "topic": topic, "caution": caution, "automatic_action": False}


def build(snapshot: dict[str, Any], *, now: datetime | None = None) -> dict[str, Any]:
    """Explain bounded current evidence; never infer causality from trace history."""
    now = (now or datetime.now(UTC)).astimezone(UTC)
    observed = _at(snapshot.get("observed_at"))
    age = (now - observed).total_seconds() if observed else None
    fresh = age is not None and -5 <= age <= STALE_AFTER_SECONDS
    items: list[dict[str, Any]] = []
    integrity = _dict(snapshot.get("state_integrity"))
    work = _dict(snapshot.get("work"))
    activity = _dict(snapshot.get("activity"))
    operator = _dict(snapshot.get("operator"))
    reserve = _dict(operator.get("reserve"))
    caps = _dict(snapshot.get("capabilities"))
    admission = _dict(snapshot.get("admission"))

    if not fresh:
        items.append(_card("evidence-stale", "Let’s get a current picture first",
            "The last observation is missing or out of date. Older findings can still help you explore, but they are not a fresh diagnosis.",
            priority=0, evidence={"observed_at": snapshot.get("observed_at")}, source="observed_at",
            action=inspection("console", "--snapshot"), next_step="Inspect a fresh local snapshot",
            expect="A recent observed_at timestamp and the actual runtime revision. Do not repeat an earlier repair just because an old warning remains visible.",
            topic="runtime", state="unknown"))
    else:
        if integrity.get("status") in {"attention", "blocked", "invalid"} or _number(integrity.get("invalid_count")) > 0:
            items.append(_card("ledger-attention", "Check the evidence before making changes",
                "A local integrity check needs attention. This is a reason to inspect the existing records, not to delete or rebuild them.",
                priority=0, evidence={k: integrity.get(k) for k in ("status", "scope", "invalid_count", "critical_ledger_status")},
                source="state_integrity", action=inspection("state verify"), next_step="Inspect the full integrity report",
                expect="The exact affected ledger or file. This check may be slower than the live console.", topic="receipts",
                caution="Do not reset state, remove ledgers or resend publications to clear this warning."))

        # Work is the current backlog. Execution events are historical, not current blockers.
        for index, row in enumerate(_rows(work.get("items"))[:240]):
            blocker = str(row.get("blocker") or "")
            if row.get("state") == "manual_review" and (row.get("do_not_replay") is True or blocker in {"ambiguous_effect", "partial_effect", "receipt_inconsistent"}):
                evidence = _dict(row.get("evidence"))
                identity = {key: evidence.get(key) for key in ("campaign", "provider", "account_id", "schedule_id")}
                items.append(_card("effect-review:" + str(index), "Check the existing post. Don’t resend it.",
                    "An outcome still needs review. A post or part of a thread may already exist, so a second send could create a duplicate.",
                    priority=10, evidence={**identity, "blocker": blocker, "source_observed_at": row.get("observed_at")},
                    source=f"work.items[{index}]", action=_campaign_inspection(identity), next_step="Inspect the saved outcome",
                    expect="Existing provider IDs, schedule state and receipt evidence. A copied command or a closed drawer does not resolve this condition.",
                    topic="receipts", state="review", caution="No replay, retry, expiry renewal or automatic repair is suggested."))
            deadline = _at(row.get("deadline"))
            if row.get("state") in {"deadline", "actionable"} and deadline and now < deadline and (deadline - now).total_seconds() <= 86400:
                items.append(_card("deadline:" + str(index), "There’s a time-sensitive item to review",
                    "A recorded deadline is approaching. First check the existing work and its eligibility; adding time is not the same as making it valid.",
                    priority=20, evidence={"deadline": row["deadline"], "dependency": row.get("dependency")},
                    source=f"work.items[{index}]", action=inspection("work status", "--json"), next_step="Inspect the work and its prerequisite",
                    expect="The named dependency, current state and do_not_renew / do_not_replay flags.", topic="supply"))

        blockers = [value for key in ("core_blockers", "activated_optional_blockers")
                    for value in (caps.get(key) if isinstance(caps.get(key), list) else [])
                    if isinstance(value, str)]
        if blockers:
            items.append(_card("capability-blocked", "A required capability needs a closer look",
                "One of the required or explicitly enabled capabilities has a recorded blocker. Optional features that have never been enabled are not treated as failures.",
                priority=15, evidence={"blockers": blockers[:20]}, source="capabilities",
                action=inspection("capabilities"), next_step="Inspect the capability and its prerequisite",
                expect="Distinguish implemented, configured, authorised, observed and accepted. Some prerequisites need a provider or a person, not a local repair.",
                topic="overview", state="inspect"))

        if admission.get("mode") == "paused":
            items.append(_card("admission-paused", "New content is being held at admission",
                "This gate controls new inventory. Previously accepted work can still move through the normal schedule. Paused does not mean the publisher is broken.",
                priority=25, evidence={"mode": admission.get("mode"), "reasons": admission.get("reasons"),
                                      "source_observed_at": admission.get("observed_at")}, source="admission",
                action=inspection("work status", "--json"), next_step="Inspect why admission is holding work",
                expect="A recorded pressure condition or prerequisite. Do not change limits simply to remove the label.", topic="admission", state="waiting"))

        counts = _dict(reserve.get("status_counts"))
        pressured = sum(int(_number(counts.get(key))) for key in ("empty", "emergency", "fallback"))
        if pressured:
            items.append(_card("reserve-pressure", "Check what’s ready now and what will last",
                "Protected reserve is low on some routes. Content expiring soon may still be runnable today, so this does not by itself mean publishing has stopped.",
                priority=30, evidence={"empty": counts.get("empty", 0), "emergency": counts.get("emergency", 0),
                                      "fallback": counts.get("fallback", 0), "protected_items": reserve.get("available_items")},
                source="operator.reserve", action=inspection("replenish status"), next_step="Inspect runnable content and reserve",
                expect="Compare runnable, reserved, expiring_before_fallback_horizon and reserve_available_items. Then inspect vault coverage or the specific campaign, rather than forcing a refill.",
                topic="supply", caution="The 100/day ceiling is a safety maximum, not a content target."))

        publishing = next((row for row in _rows(caps.get("capabilities")) if row.get("id") == "provider-publishing"), {})
        permissions = _dict(_dict(publishing.get("detail")).get("linkedin_recorded_read_authority"))
        if _dict(permissions.get("posts")).get("status") == "missing":
            items.append(_card("linkedin-readback", "A LinkedIn post can be published without readback",
                "The recorded permission does not allow post readback. A creation receipt is still evidence of publication; missing readback is not permission to publish again.",
                priority=50, evidence={"readback_permission": "missing", "required_scope": _dict(permissions.get("posts")).get("required_scope")},
                source="capabilities.provider-publishing.detail.linkedin_recorded_read_authority.posts",
                action=inspection("capabilities"), next_step="Inspect the recorded permission boundary",
                expect="Publishing authority and reading authority shown separately. Changing this permission may require a provider-side approval, not another local command.",
                topic="verify", state="external", caution="Do not resend a published_unverified post."))

        pacing = _dict(_dict(_dict(snapshot.get("portfolio")).get("status")).get("release_pacing"))
        if pacing.get("mode") == "steady_originals":
            items.append(_card("steady-originals", "Your reserve can grow without speeding up today",
                "The normal daily amount comes from your saved account settings. Extra fresh content stays in reserve for later opportunities; it does not turn the 100 ceiling into today's target.",
                priority=26, evidence={"unit": pacing.get("unit"), "accounts": _rows(pacing.get("accounts"))[:30]},
                source="portfolio.status.release_pacing", action=inspection("portfolio status", "--json"),
                next_step="Inspect your saved daily amounts and reserve",
                expect="normal_originals_per_day and hard_daily_ceiling are separate. One complete thread counts as one original; parts and API requests are separate. Earlier bookings are honoured, not silently cancelled.",
                topic="release-pace", state="informational"))

        timings = _dict(snapshot.get("projection_timing_ms"))
        parts = [(key, _number(value)) for key, value in timings.items() if key != "total"]
        if parts and max(value for _, value in parts) >= 3000:
            name, duration = max(parts, key=lambda pair: pair[1])
            items.append(_card("projection-cost", "The view is taking a little time to build",
                "One measured projection is expensive. This is a console-performance finding, not proof that publishing is delayed.",
                priority=70, evidence={"component": name, "milliseconds": duration}, source="projection_timing_ms",
                action=inspection("console", "--snapshot"), next_step="Inspect the component timings",
                expect="The slow component and its measured duration. A code improvement may be needed; repeatedly raising the HTTP timeout is not a repair.",
                topic="runtime", state="engineering"))

    # Do not turn absent or unavailable evidence into an all-clear.
    complete = (
        integrity.get("critical_ledger_status") in {"observed", "attention"}
        and caps.get("status") in {"observed", "attention"}
        and work.get("status") in {"observed", "open", "attention"}
        and isinstance(work.get("items"), list)
        and isinstance(activity.get("active_schedules"), list)
        and isinstance(activity.get("recent_receipts"), list)
        and bool(_rows(operator.get("stages")))
    )
    if not items:
        future = sorted([row for row in _rows(activity.get("active_schedules"))
                         if row.get("status") == "scheduled" and (_at(row.get("run_at")) or now) > now],
                        key=lambda row: str(row.get("run_at")))
        if future and complete:
            row = future[0]
            items.append(_card("next-scheduled", "The next step is already scheduled",
                "There’s no need to run the publisher manually for this reservation. Its normal timer can pick it up when it is due.",
                priority=90, evidence={k: row.get(k) for k in ("run_at", "provider", "campaign", "schedule_id")},
                source="activity.active_schedules", action=_campaign_inspection(row), next_step="Explore the saved reservation",
                expect="A future run_at time and the recorded schedule state. A forecast alone would not prove this reservation exists.", topic="schedule", state="waiting"))
        else:
            items.append(_card("no-urgent" if complete else "evidence-incomplete",
                "Nothing urgent in the checks shown" if complete else "Some of the picture is still missing",
                "No intervention is indicated by these checks. You can explore the pipeline without changing it." if complete else
                "There isn’t enough current evidence here to call the whole system healthy. You can inspect the missing checks without changing anything.",
                priority=90, evidence={"coverage": "checks_shown_only" if complete else "incomplete"}, source="work / capabilities / state_integrity",
                action=inspection("work status", "--json"), next_step="Explore the current work",
                expect="Named dependencies and evidence, including work that is waiting rather than failing.", topic="overview",
                state="informational" if complete else "unknown"))
    items.sort(key=lambda item: (item["priority"], item["id"]))
    contexts = {}
    for event in _rows(_dict(snapshot.get("execution")).get("events"))[:240]:
        action = selected_inspection(event)
        if action and isinstance(event.get("id"), str):
            contexts[event["id"]] = {
                "action": action,
                "identity": {key: event.get(key) for key in ("campaign", "provider", "account_id", "schedule_id")},
                "observed_at": event.get("at"),
                "boundary": "Selected historical evidence is context, not proof of a current blocker.",
            }
    return {"schema_version": GUIDE_VERSION, "source_revision": snapshot.get("revision"),
            "contexts": contexts,
            "observed_at": snapshot.get("observed_at"), "fresh": fresh,
            "stale_after_seconds": STALE_AFTER_SECONDS, "items": items[:12],
            "additional_count": max(0, len(items) - 12), "complete_checks": complete,
            "boundary": "Rule-based guidance from this snapshot only. No command execution, extra state scan or provider call. Copying is not completion."}


@lru_cache(maxsize=1)
def command_library() -> dict[str, Any]:
    """Static help only, loaded once on demand, not on the live snapshot path."""
    from ocpf_post.cli_catalog import catalogue
    data = catalogue()
    commands = []
    for row in data["commands"]:
        commands.append({key: row.get(key) for key in ("path", "summary", "consequence", "safe_form", "notes", "arguments")} |
                        {"level": "beginner" if row["path"] in BEGINNER else "advanced",
                         "help_command": shlex.join(["./ocpf-post", "help", *row["path"].split()])})
    return {"schema_version": GUIDE_VERSION, "cli_version": data["cli_version"], "commands": commands,
            "topics": topics(), "boundary": "Help is not execution. Mutation commands are reference-only; copy opens their help, never an apply command."}


def topics() -> list[dict[str, Any]]:
    definitions = [
        ("overview", "How does Post-Once work?", "Sources and reviewed vaults supply content. Admission decides what can enter inventory. The allocator creates durable schedules. The publisher creates provider effects, and receipts and readback record what is known. Each stage answers a different question.", "work status", ["--json"], "The current dependency and the next evidence to inspect."),
        ("today", "What was published today, and where did it come from?", "Count first creation receipts, not later verification updates or future reservations. One native thread is one logical campaign publication. Origin comes from recorded vault and source metadata, not a guessed campaign prefix. Replies and partial effects are separate.", "portfolio volume-audit", [], "Use a London calendar date with --date YYYY-MM-DD. For copy and vault origins, use the repository’s owner-daily.sh report."),
        ("supply", "Why is reserve low when there is content?", "Protected reserve excludes unreserved content that expires before the fallback horizon. Compare content runnable today, already reserved work and longer-lived reserve before deciding that more content is needed.", "replenish status", [], "runnable, reserved, near-expiry items and reserve_available_items are different measurements."),
        ("admission", "Why hasn’t approved content entered the queue?", "Approved source copy still needs valid identity, freshness, duplicate and admission checks. A held entry is not a failed publication. The gate can pause new inventory while existing reservations continue.", "work status", ["--json"], "The specific prerequisite or pressure condition. Inspection does not bypass it."),
        ("release-pace", "Can my reserve grow without publishing more today?", "Yes. Steady originals uses the saved daily amount for each account while keeping its safety ceiling separate. Building or importing extra stock does not increase that amount. One whole thread is one original, but may use several provider writes. Existing bookings are honoured; freshness, approval and duplicate checks still apply. A daily amount is not a promise to publish low-quality or expired content.", "portfolio status", ["--json"], "Look at release_pacing.accounts for normal_originals_per_day and hard_daily_ceiling. More stock means more preparation, not permission to publish more today."),
        ("schedule", "Is a forecast the same as a booked post?", "No. A forecast describes a possible selection; a durable schedule records an authorised reservation. Future run times are not failed executions. Timers drive normal operation without a daily manual send.", "schedule list", [], "Active durable schedules and run times. The calendar additionally separates forecasts from booked work."),
        ("receipts", "What proves that a publication happened?", "A durable provider ID linked to the exact account, campaign and copy is publication evidence. Readback is a separate check. Uncertain and partial outcomes need inspection without resending; no receipt is invented by changing a vault label.", "work status", ["--json"], "Known IDs and explicit uncertainty. Select a campaign or beat for its exact inspection command."),
        ("verify", "Why does LinkedIn say published, but unverified?", "Writing a post and reading it back can require different permissions. A published_unverified receipt records an effect without proven readback. It must not be treated as a failed send, and unavailable analytics are not zero.", "capabilities", [], "The recorded publishing and read-authority boundary."),
        ("learning", "When can the system learn from a post?", "Measurements need the right account and comparable post ages. Missing metrics stay unknown. An insufficient-evidence state can mean waiting for a measurement window or more valid comparisons, not a broken learner.", "work status", ["--json"], "The observation window and evidence prerequisite. Don’t force a premature capture or lower a threshold."),
        ("replies", "Are conversations separate from campaign posts?", "Yes. Collected replies use their own review and authorisation path. Inspecting the inbox or worker status does not send a reply, and campaign publication totals are not conversation totals.", "engagement worker-status", [], "Review mode, model readiness and account-scoped reply policy."),
        ("runtime", "Does pulling Git update the background service?", "The control checkout and running immutable release can differ. A Git pull alone does not prove that services use the new code. Inspect the actual runtime before attempting another upgrade. Review changes during live writes are a safety rejection, not permission to bypass the guard.", "runtime status", [], "The control checkout and active runtime revision, considered separately."),
    ]
    return [{"id": key, "question": question, "answer": answer,
             "action": inspection(path, *args) if key != "today" else inspection("help", "portfolio", "volume-audit"),
             "expect": expect} for key, question, answer, path, args, expect in definitions]


def selected_inspection(selection: dict[str, Any]) -> dict[str, Any] | None:
    """Used by tests/clients that need exact scoped commands without executing them."""
    if _id(selection.get("schedule_id")) or (_id(selection.get("campaign")) and selection.get("provider") in PROVIDERS):
        return _campaign_inspection(selection)
    return None


def decorate_page(body: bytes) -> bytes:
    """Compose the optional guide after the existing cockpit, without rewriting it.

    The bridge only reads the cockpit model and subscribes to its existing SSE
    connection. No extra stream, snapshot timer or command execution is created.
    The original health drawer IDs and event handlers remain intact as fallback.
    """
    if body.count(b"</head>") != 1 or body.count(b"</body>") != 1:
        raise ValueError("Execution page must expose one head and body extension point")
    body = body.replace(b"</head>", b'<link rel="stylesheet" href="/guide.css">\n</head>')
    bridge = b'''<script src="/guide.js"></script>
<script>
if (window.PostOnceGuide && typeof model !== 'undefined' && typeof stream !== 'undefined') {
  window.PostOnceGuide.mount({snapshot: () => model.snapshot, selection: () => model.selected,
    stream: stream, open: openSystem, close: closeSystem});
}
</script>
'''
    return body.replace(b"</body>", bridge + b"</body>")
