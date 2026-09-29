from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any


@dataclass(frozen=True)
class AccountIdentity:
    provider: str
    account_id: str
    username: str | None = None
    name: str | None = None
    biography: str | None = None

    @property
    def display(self) -> str:
        if self.username:
            return f"@{self.username}"
        return self.name or self.account_id


@dataclass(frozen=True)
class PublishReceipt:
    campaign: str
    provider: str
    account_id: str
    username: str | None
    status: str
    text_sha256: str
    recorded_at: str
    post_id: str | None = None
    url: str | None = None
    readback_verified: bool = False
    detail: str | None = None
    schedule_id: str | None = None
    publication_type: str | None = None
    part_count: int | None = None
    publication_part_ids: list[str] | None = None
    completed_part_count: int | None = None
    failed_part_index: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
