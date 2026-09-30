"""Immutable single-post or reply-chain publication payloads.

X and Threads have bounded text posts. Approved content is never silently
truncated: over-length copy is deterministically segmented before scheduling and
the exact segment list is frozen into the schedule.
"""
from __future__ import annotations

import hashlib
import json
import re
from typing import Any

PART_LIMITS = {"x": 275, "threads": 500}
X_WEIGHT_ONE_RANGES = ((0, 4351), (8192, 8205), (8208, 8223), (8242, 8247))
X_URL_RE = re.compile(r"(?i)\b(?:https?://|www\.)\S+")
MAX_PARTS = 25
PUBLICATION_TEXT_LIMITS = {
    "x": PART_LIMITS["x"] * MAX_PARTS,
    "threads": PART_LIMITS["threads"] * MAX_PARTS,
    "linkedin": 3000,
}


def _digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _x_codepoint_weight(char: str) -> int:
    value = ord(char)
    return 1 if any(start <= value <= end for start, end in X_WEIGHT_ONE_RANGES) else 2


def x_weighted_length(text: str) -> int:
    """Conservative twitter-text v3-compatible weight.

    URL transformation follows the canonical 23-character rule. Emoji grapheme
    clustering is deliberately not reproduced; counting each non-light code point
    at weight 2 can only split earlier, never make an oversized X part look safe.
    """
    total = 0
    cursor = 0
    for match in X_URL_RE.finditer(text):
        total += sum(_x_codepoint_weight(char) for char in text[cursor:match.start()])
        total += 23
        cursor = match.end()
    total += sum(_x_codepoint_weight(char) for char in text[cursor:])
    return total


def _x_max_prefix_index(text: str, limit: int) -> int:
    total = 0
    cursor = 0
    for match in X_URL_RE.finditer(text):
        for index in range(cursor, match.start()):
            weight = _x_codepoint_weight(text[index])
            if total + weight > limit:
                return index
            total += weight
        if total + 23 > limit:
            return match.start()
        total += 23
        cursor = match.end()
    for index in range(cursor, len(text)):
        weight = _x_codepoint_weight(text[index])
        if total + weight > limit:
            return index
        total += weight
    return len(text)


def _preferred_cut(text: str, limit: int) -> int:
    """Find a natural raw-text boundary at or before the supplied index."""
    window = text[: limit + 1]
    boundaries = ("\n\n", "\n", ". ", "? ", "! ", "; ", ": ", ", ", " ")
    floor = max(1, int(limit * 0.45))
    for token in boundaries:
        index = window.rfind(token, floor)
        if index >= floor:
            return index + (0 if token.isspace() else len(token.rstrip()))
    index = window.rfind(" ")
    return index if index > 0 else limit


def split_text(text: str, limit: int) -> list[str]:
    """Split text at semantic boundaries; never ellipsise or discard words."""
    value = str(text or "").replace("\r\n", "\n").strip()
    if not value:
        raise ValueError("Publication text is empty")
    if limit < 1:
        raise ValueError("Publication part limit must be positive")
    parts: list[str] = []
    remaining = value
    while len(remaining) > limit:
        cut = _preferred_cut(remaining, limit)
        if cut <= 0:
            cut = limit
        part = remaining[:cut].strip()
        if not part:
            part = remaining[:limit]
            cut = limit
        parts.append(part)
        remaining = remaining[cut:].lstrip()
    if remaining:
        parts.append(remaining)
    if any(not part or len(part) > limit for part in parts):
        raise ValueError("Publication could not be segmented within provider limits")
    return parts


def split_x_text(text: str, limit: int = PART_LIMITS["x"]) -> list[str]:
    """Split X copy by weighted length without cutting transformed URLs."""
    value = str(text or "").replace("\r\n", "\n").strip()
    if not value:
        raise ValueError("Publication text is empty")
    parts: list[str] = []
    remaining = value
    while x_weighted_length(remaining) > limit:
        maximum = _x_max_prefix_index(remaining, limit)
        if maximum <= 0:
            raise ValueError("X publication contains an atomic token that cannot fit one post")
        cut = _preferred_cut(remaining, maximum)
        if cut <= 0 or x_weighted_length(remaining[:cut].strip()) > limit:
            cut = maximum
        part = remaining[:cut].strip()
        if not part or x_weighted_length(part) > limit:
            raise ValueError("X publication could not be segmented within weighted provider limits")
        parts.append(part)
        remaining = remaining[cut:].lstrip()
    if remaining:
        parts.append(remaining)
    if any(not part or x_weighted_length(part) > limit for part in parts):
        raise ValueError("X publication could not be segmented within weighted provider limits")
    return parts


def build_publication(provider: str, text: str) -> dict[str, Any]:
    provider = str(provider or "").strip().lower()
    value = str(text or "").strip()
    if not value:
        raise ValueError("Publication text is empty")
    maximum = PUBLICATION_TEXT_LIMITS.get(provider)
    if maximum is not None and len(value) > maximum:
        raise ValueError(
            f"{provider} publication exceeds the local {maximum}-character safety envelope; "
            "refusing to truncate approved content"
        )
    limit = PART_LIMITS.get(provider)
    if provider == "x":
        parts = split_x_text(value)
    else:
        parts = split_text(value, limit) if limit else [value]
    if len(parts) > MAX_PARTS:
        raise ValueError(f"{provider} publication exceeds the {MAX_PARTS}-part safety envelope")
    kind = "thread" if len(parts) > 1 and provider in PART_LIMITS else "single"
    frozen_parts = [
        {"index": index, "text": part, "text_sha256": _digest(part)}
        for index, part in enumerate(parts, start=1)
    ]
    identity = {
        "provider": provider,
        "publication_type": kind,
        "text_sha256": _digest(value),
        "parts": [
            {"index": row["index"], "text_sha256": row["text_sha256"]}
            for row in frozen_parts
        ],
    }
    return {
        "publication_type": kind,
        "text": value,
        "text_sha256": identity["text_sha256"],
        "parts": frozen_parts,
        "part_count": len(frozen_parts),
        "publication_sha256": hashlib.sha256(
            json.dumps(identity, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
        ).hexdigest(),
    }


def validate_frozen_publication(provider: str, record: dict[str, Any]) -> bool:
    try:
        rebuilt = build_publication(provider, str(record.get("text") or ""))
    except (TypeError, ValueError):
        return False
    return (
        rebuilt["text_sha256"] == record.get("text_sha256")
        and rebuilt["publication_type"] == record.get("publication_type", "single")
        and rebuilt["publication_sha256"] == record.get("publication_sha256")
        and rebuilt["parts"] == record.get("publication_parts")
    )
