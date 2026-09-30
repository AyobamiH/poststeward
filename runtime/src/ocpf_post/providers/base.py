from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any

from ocpf_post.model import AccountIdentity


class ProviderError(RuntimeError):
    pass


@dataclass
class ProviderRejected(ProviderError):
    status: int
    message: str

    def __str__(self) -> str:
        return f"provider rejected request (HTTP {self.status}): {self.message}"


class ProviderUnavailable(ProviderError):
    """A read-only/pre-consequence provider check was transiently unavailable.

    This must only be raised when no social publish/reply consequence has begun.
    Callers may defer and retry the preflight later without risking a duplicate post.
    """


class AmbiguousProviderEffect(ProviderError):
    """The request may have reached the provider, so a blind retry is unsafe."""


class Provider(ABC):
    name: str

    @abstractmethod
    def account(self) -> AccountIdentity:
        raise NotImplementedError

    @abstractmethod
    def publish(self, text: str) -> dict[str, Any]:
        raise NotImplementedError

    @abstractmethod
    def verify_post(self, post_id: str, expected_text: str) -> bool:
        raise NotImplementedError

    @abstractmethod
    def post_url(self, account: AccountIdentity, post_id: str) -> str:
        raise NotImplementedError

    def performance(self, post_id: str) -> dict[str, Any]:
        """Return a read-only, truth-preserving performance snapshot.

        Providers that cannot expose metrics under the current permission set should
        return unavailable state instead of synthesising zeros.
        """
        return {
            "metrics": {},
            "availability": {
                "status": "unsupported",
                "detail": f"{self.name} performance capture is not implemented",
            },
        }
