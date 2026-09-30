"""Feedback wrapper that projects verified-effect baseline equivalence in memory.

Stored performance snapshots and historical receipts remain immutable. The wrapper
serialises the temporary iterator substitution, restores it in ``finally``, and
persists only the normal feedback report plus the small equivalence sidecar when
``apply`` is requested.
"""
from __future__ import annotations

import copy

from ocpf_post import learning_equivalence, local_store, performance
from ocpf_post.state import state_dir


def _lock_path():
    return state_dir() / "performance-feedback-equivalence.lock"


def _project(rows, mapping):
    out = []
    for original in rows:
        row = copy.deepcopy(original)
        identity = tuple(row.get(k) for k in ("campaign", "provider", "account_id", "post_id"))
        attestation = mapping.get(identity)
        if attestation:
            editorial = learning_equivalence.editorial_from_attestation(attestation)
            if editorial:
                row["editorial"] = editorial
        out.append(row)
    return out


def build(*, apply=False, now=None, enable=None):
    """Run the existing feedback engine over an exact, read-only projected view."""
    from ocpf_post import performance_feedback as base
    from ocpf_post.performance_review import publications

    with local_store.locked(_lock_path()):
        pub = publications()
        projection = learning_equivalence.projection_map(pub)
        recorded = []
        if apply:
            for row in projection["attestations"]:
                result = learning_equivalence.record(row, now=now)
                if result["status"] in {"recorded", "already_recorded"}:
                    recorded.append(result["attestation"])

        original_iterator = performance.iter_snapshots
        rows = list(original_iterator())
        projected_rows = _project(rows, projection["projected"])
        performance.iter_snapshots = lambda: list(projected_rows)
        try:
            result = base.build(apply=apply, now=now, enable=enable)
        finally:
            performance.iter_snapshots = original_iterator

        result["effect_equivalence"] = {
            "projected_count": len(projection["projected"]),
            "recorded_count": len(recorded),
            "conflicts": projection["conflicts"],
        }
        result["boundary"] = (
            result.get("boundary", "")
            + " Exact same-account payload reuse may project a newer frozen insight attribution "
              "onto one older published_verified effect only when project, deterministic inventory "
              "slot and insight lineage also match. This projection is read-only: historical receipts "
              "and performance snapshots are never rewritten."
        ).strip()

        if apply:
            with local_store.locked(base.path()):
                local_store.write(base.path(), result)
        return result
