"""Production admission boundary for ordinary source replenishment.

This wrapper is deliberately separate from allocator execution. It serialises the
pressure snapshot + preview + admission + write sequence so two refill processes
cannot both buy inventory against the same budget. Admission-control corruption or
lock contention pauses only *new* source inventory; callers may continue draining
already accepted inventory through the allocator.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from ocpf_post import local_store
from ocpf_post.admission import AdmissionError, state_file
from ocpf_post.source_pipeline import refresh as _controlled_refresh
from ocpf_post.onboarding import OnboardingError

UTC = timezone.utc
# The recurring source-observation stage is itself bounded to 215 seconds by the
# operating cycle. A 240-second authority-level wait covers normal overlap plus
# scheduling jitter while remaining well below the 15-minute refill cadence.
SOURCE_OPERATION_WAIT_SECONDS = 240.0


def _paused_result(*, project: str | None, now: datetime | None, reason: str, error_type: str) -> dict[str, Any]:
    from ocpf_post.portfolio_source_loader import merged_source_profiles

    observed = (now or datetime.now(UTC)).astimezone(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    profiles = merged_source_profiles()["projects"]
    targets = [project] if project in profiles else sorted(profiles) if project is None else [project]
    rows = [{"project": name, "status": "admission_paused", "reasons": [reason]} for name in targets]
    return {
        "schema_version": 1,
        "projects": rows,
        "static_campaigns": [],
        "event_campaigns": [],
        "generative_campaigns": [],
        "admission": [{
            "schema_version": 1,
            "admitted": False,
            "mode": "paused",
            "reasons": [reason],
            "observed_at": observed,
            "error_type": error_type,
            "boundary": "Ordinary source admission failed closed. Existing accepted inventory was not changed and may continue to drain.",
        }],
        "boundary": "Ordinary source admission paused safely; no source cursor or runtime campaign was written.",
    }


def controlled_refresh(*, apply: bool, project: str | None = None, now: datetime | None = None) -> dict[str, Any]:
    if not apply:
        return _controlled_refresh(apply=False, project=project, now=now)

    try:
        with local_store.locked(state_file()):
            return _controlled_refresh(
                apply=True,
                project=project,
                now=now,
                source_lock_timeout_seconds=SOURCE_OPERATION_WAIT_SECONDS,
            )
    except BlockingIOError:
        return _paused_result(project=project, now=now, reason="admission_writer_busy", error_type="BlockingIOError")
    except OnboardingError as exc:
        if "source-onboarding operation is active" in str(exc):
            return _paused_result(project=project, now=now, reason="source_operation_active", error_type="OnboardingError")
        return _paused_result(project=project, now=now, reason="admission_observation_unavailable", error_type="OnboardingError")
    except (AdmissionError, OSError, ValueError, KeyError, TypeError) as exc:
        # Error messages can contain local paths or evidence detail. Expose only
        # the typed reason; new source admission is not important enough to
        # weaken privacy or block draining the already accepted queue.
        return _paused_result(project=project, now=now, reason="admission_observation_unavailable", error_type=type(exc).__name__)
