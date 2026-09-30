"""Provider adapter that keeps social credentials in PostSteward Cloud.

The copied Post-Once-derived scheduler/direct-publication paths see the historical
Provider interface. This adapter translates that interface into PostSteward's
executor-fenced runtime relay while preserving the existing local receipt model.
"""
from __future__ import annotations

import hashlib
import json
import time
from typing import Any

from ocpf_post.model import AccountIdentity
from ocpf_post.poststeward_cloud import CloudError, bindings, heartbeat, relay
from ocpf_post.providers.base import (
    AmbiguousProviderEffect,
    Provider,
    ProviderRejected,
    ProviderUnavailable,
)


DEFINITE_PROVIDER_CODES = {
    "PROVIDER_HTTP_400",
    "PROVIDER_HTTP_401",
    "PROVIDER_HTTP_403",
    "PROVIDER_HTTP_404",
    "PROVIDER_HTTP_422",
    "PROVIDER_HTTP_429",
}
PRECONSEQUENCE_CODES = {
    "NETWORK_ERROR",
    "RUNTIME_PAIRING_REQUIRED",
    "RUNTIME_UNAUTHENTICATED",
    "RUNTIME_EXECUTOR_NOT_LOCAL",
    "RUNTIME_EXECUTOR_FENCED",
    "RUNTIME_ACCOUNT_BINDING_MISMATCH",
    "RUNTIME_PROVIDER_IDENTITY_DRIFT",
}


class PostStewardRelayProvider(Provider):
    def __init__(
        self,
        provider: str,
        *,
        account_id: str | None = None,
        effect_scope: str | None = None,
        bridge_command_id: str | None = None,
        **_kwargs: Any,
    ) -> None:
        normalized = str(provider or "").strip().lower()
        if normalized not in {"x", "threads", "linkedin"}:
            raise ValueError(f"Unsupported PostSteward relay provider: {provider}")
        self.name = normalized
        self._account_id = str(account_id or "").strip() or None
        self._effect_scope = str(effect_scope or "manual").strip() or "manual"
        self._bridge_command_id = bridge_command_id
        self._account: AccountIdentity | None = None
        self._post_effects: dict[str, str] = {}
        self._post_urls: dict[str, str] = {}
        self._effect_counter = 0

    def _binding(self) -> dict[str, Any]:
        value = bindings()
        rows = value.get("accounts")
        candidates = [
            row
            for row in (rows if isinstance(rows, list) else [])
            if isinstance(row, dict)
            and row.get("provider") == self.name
            and row.get("active") is True
            and isinstance(row.get("identity"), dict)
        ]
        if self._account_id:
            candidates = [
                row
                for row in candidates
                if str((row.get("identity") or {}).get("id") or "") == self._account_id
            ]
        if len(candidates) != 1:
            if not candidates:
                raise ProviderRejected(
                    409,
                    f"No active PostSteward {self.name} connection matches this runtime account.",
                )
            raise ProviderRejected(
                409,
                f"Multiple active PostSteward {self.name} connections exist; bind an exact account identity.",
            )
        return candidates[0]

    def _generation(self) -> int:
        value = bindings()
        executor = value.get("executor")
        generation = executor.get("authorityGeneration") if isinstance(executor, dict) else None
        if type(generation) is not int or generation < 1:
            raise ProviderRejected(409, "PostSteward executor generation is unavailable.")
        if executor.get("executorMode") != "local":
            raise ProviderRejected(
                409,
                "This runtime does not currently own PostSteward execution authority.",
            )
        try:
            renewed = heartbeat()
        except CloudError as exc:
            raise ProviderRejected(
                exc.status,
                f"PostSteward executor lease could not be renewed: {exc}",
            ) from exc
        renewed_generation = renewed.get("authorityGeneration")
        if renewed_generation != generation:
            raise ProviderRejected(
                409,
                "PostSteward executor generation changed during consequence preflight.",
            )
        return generation

    def _effect_id(self, text: str, reply_to_id: str | None = None) -> str:
        # Counter is intentionally excluded: the same logical part/reply must reach
        # the same cloud fence after a bounded caller retry.
        body = {
            "provider": self.name,
            "account_id": self.account().account_id,
            "scope": self._effect_scope,
            "text_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
            "reply_to_id": reply_to_id or None,
        }
        return hashlib.sha256(
            json.dumps(body, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()

    @staticmethod
    def _digest(text: str) -> str:
        return hashlib.sha256(text.encode("utf-8")).hexdigest()

    def _map_read_error(self, exc: CloudError) -> Exception:
        if exc.code in DEFINITE_PROVIDER_CODES:
            try:
                status = int(exc.code.rsplit("_", 1)[1])
            except (ValueError, IndexError):
                status = exc.status
            return ProviderRejected(status, str(exc))
        if exc.code in PRECONSEQUENCE_CODES or exc.status >= 500:
            return ProviderUnavailable(str(exc))
        return ProviderRejected(exc.status, str(exc))

    def _relay_read(self, payload: dict[str, Any]) -> dict[str, Any]:
        try:
            return relay(payload)
        except CloudError as exc:
            raise self._map_read_error(exc) from exc

    def _relay_effect(self, payload: dict[str, Any]) -> dict[str, Any]:
        try:
            return relay(payload)
        except CloudError as exc:
            if exc.code in DEFINITE_PROVIDER_CODES:
                try:
                    status = int(exc.code.rsplit("_", 1)[1])
                except (ValueError, IndexError):
                    status = exc.status
                raise ProviderRejected(status, str(exc)) from exc
            if exc.code in {
                "RUNTIME_COMMAND_EFFECT_REFUSED",
                "RUNTIME_EXECUTOR_FENCED",
                "RUNTIME_UNAUTHENTICATED",
                "RUNTIME_ACCOUNT_BINDING_MISMATCH",
                "RUNTIME_PROVIDER_IDENTITY_DRIFT",
                "RUNTIME_TEXT_DIGEST_MISMATCH",
                "EXTERNAL_EFFECT_FENCE_MISMATCH",
            }:
                # These are definite local/cloud authority refusals before provider
                # consequence. Do not mislabel them as an uncertain provider effect.
                raise ProviderRejected(exc.status, str(exc)) from exc
            raise AmbiguousProviderEffect(
                f"PostSteward relay outcome is uncertain ({exc.code}); inspect the existing effect receipt before retrying."
            ) from exc

    def account(self) -> AccountIdentity:
        if self._account is not None:
            return self._account
        row = self._binding()
        identity = row["identity"]
        expected = str(identity["id"])
        try:
            observed = self._relay_read(
                {
                    "action": "account",
                    "provider": self.name,
                    "accountId": expected,
                }
            )
        except ProviderUnavailable:
            raise
        account = observed.get("account") if isinstance(observed, dict) else None
        seen = account.get("identity") if isinstance(account, dict) else None
        if not isinstance(seen, dict) or str(seen.get("id") or "") != expected:
            raise ProviderRejected(
                409,
                "PostSteward cloud identity readback does not match the runtime binding.",
            )
        self._account_id = expected
        self._account = AccountIdentity(
            provider=self.name,
            account_id=expected,
            username=(
                str(seen.get("username"))
                if seen.get("username") and not str(seen.get("username")).startswith("urn:")
                else None
            ),
            name=str(seen.get("username") or expected),
        )
        return self._account

    def readonly_account(self) -> AccountIdentity:
        return self.account()

    def _publish_text(self, text: str, reply_to_id: str | None = None) -> dict[str, Any]:
        value = str(text or "")
        if not value.strip():
            raise ProviderRejected(0, f"{self.name} post text is empty")
        account = self.account()
        effect_id = self._effect_id(value, reply_to_id)
        common = {
            "authorityGeneration": self._generation(),
            "provider": self.name,
            "accountId": account.account_id,
            "effectId": effect_id,
            "campaign": self._effect_scope,
            **({"bridgeCommandId": self._bridge_command_id} if self._bridge_command_id else {}),
            "text": value,
            "textDigest": self._digest(value),
            **({"replyToId": reply_to_id} if reply_to_id else {}),
        }

        if self.name == "threads":
            created = self._relay_effect({"action": "container_create", **common})
            container_id = str(created.get("id") or "")
            if not container_id:
                raise AmbiguousProviderEffect(
                    "PostSteward Threads relay returned no durable container ID."
                )
            ready = False
            for delay in (0, 1, 2, 4):
                if delay:
                    time.sleep(delay)
                state = self._relay_read(
                    {
                        "action": "container_status",
                        "provider": self.name,
                        "accountId": account.account_id,
                        "containerId": container_id,
                    }
                )
                status = str(state.get("status") or "")
                if status in {"FINISHED", "PUBLISHED"}:
                    ready = True
                    break
                if status in {"ERROR", "EXPIRED", "NOT_VISIBLE"}:
                    raise ProviderRejected(
                        409,
                        f"Threads container stopped before publication ({status}).",
                    )
            if not ready:
                raise AmbiguousProviderEffect(
                    "Threads container exists but readiness was not established within the bounded wait; no second container will be created."
                )
            published = self._relay_effect(
                {"action": "publish", **common, "containerId": container_id}
            )
        else:
            published = self._relay_effect({"action": "publish", **common})

        post_id = str(published.get("id") or "")
        if not post_id:
            raise AmbiguousProviderEffect(
                "PostSteward relay returned no durable provider post ID."
            )
        self._post_effects[post_id] = effect_id
        if published.get("url"):
            self._post_urls[post_id] = str(published["url"])
        return {
            "id": post_id,
            **({"url": self._post_urls[post_id]} if post_id in self._post_urls else {}),
            "publishing_method": "poststeward_cloud_relay",
        }

    def publish(self, text: str) -> dict[str, Any]:
        return self._publish_text(text)

    def reply(self, text: str, reply_to_id: str) -> dict[str, Any]:
        return self._publish_text(text, reply_to_id=reply_to_id)

    def verify_post(self, post_id: str, expected_text: str) -> bool:
        account = self.account()
        effect_id = self._post_effects.get(post_id) or self._effect_id(expected_text)
        value = self._relay_read(
            {
                "action": "verify",
                "provider": self.name,
                "accountId": account.account_id,
                "effectId": effect_id,
                "campaign": self._effect_scope,
                "text": expected_text,
                "textDigest": self._digest(expected_text),
                "postId": post_id,
            }
        )
        if value.get("url"):
            self._post_urls[post_id] = str(value["url"])
        return value.get("verified") is True

    def post_url(self, account: AccountIdentity, post_id: str) -> str:
        if post_id in self._post_urls:
            return self._post_urls[post_id]
        if self.name == "x":
            return (
                f"https://x.com/{account.username}/status/{post_id}"
                if account.username
                else f"https://x.com/i/web/status/{post_id}"
            )
        if self.name == "linkedin":
            return f"https://www.linkedin.com/feed/update/{post_id}/"
        if account.username:
            return f"https://www.threads.net/@{account.username}"
        return "https://www.threads.net/"

    def performance(self, post_id: str) -> dict[str, Any]:
        # Metrics do not need publication text. The hosted relay accepts a bounded
        # synthetic effect descriptor for this read-only evidence request.
        account = self.account()
        try:
            value = self._relay_read(
                {
                    "action": "metrics",
                    "provider": self.name,
                    "accountId": account.account_id,
                    "effectId": self._post_effects.get(post_id)
                    or hashlib.sha256(
                        f"metrics:{self.name}:{account.account_id}:{post_id}".encode("utf-8")
                    ).hexdigest(),
                    "campaign": self._effect_scope,
                    "text": "[metrics-read]",
                    "textDigest": self._digest("[metrics-read]"),
                    "postId": post_id,
                }
            )
            metrics = value.get("metrics")
            return metrics if isinstance(metrics, dict) else {
                "availability": {"status": "unavailable", "detail": "Hosted relay returned no metrics object."},
                "metrics": {},
            }
        except (ProviderUnavailable, ProviderRejected) as exc:
            return {
                "availability": {"status": "unavailable", "detail": str(exc)},
                "metrics": {},
            }
