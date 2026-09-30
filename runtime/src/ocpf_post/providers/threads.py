from __future__ import annotations

import os
import time
import urllib.parse
from typing import Any

from ocpf_post.model import AccountIdentity
from ocpf_post.providers.base import AmbiguousProviderEffect, Provider, ProviderRejected, ProviderUnavailable
from ocpf_post.providers.http import TransportError, request_json, safe_payload
from ocpf_post.state import provider_token_file, read_json, write_private_json

API_BASE = "https://graph.threads.net/v1.0"
REFRESH_URL = "https://graph.threads.net/refresh_access_token"
PROFILE_FIELDS = "id,username,name,threads_biography"
POST_FIELDS = "id,text,permalink,username"
LONG_LIVED_SECONDS = 60 * 24 * 60 * 60
REFRESH_WINDOW_SECONDS = 7 * 24 * 60 * 60
TRANSIENT_HTTP_STATUSES = {408, 425, 429}


class ThreadsProvider(Provider):
    name = "threads"

    def __init__(self, credential_dir=None, **_kwargs: Any) -> None:
        self.scoped = credential_dir is not None
        self.token_file = credential_dir / "token.json" if self.scoped else provider_token_file("threads")
        self._permalinks: dict[str, str] = {}

    def _stored(self) -> dict[str, Any]:
        return read_json(self.token_file)

    def _save_token(self, token: str, *, expires_in: int = LONG_LIVED_SECONDS) -> None:
        now = int(time.time())
        write_private_json(
            self.token_file,
            {
                "access_token": token,
                "obtained_at": now,
                "expires_at": now + int(expires_in),
                "token_type": "bearer",
            },
        )

    def _access_token(self) -> str:
        stored = self._stored()
        token = stored.get("access_token")
        if token:
            expires_at = int(stored.get("expires_at") or 0)
            if expires_at and expires_at <= int(time.time()) + REFRESH_WINDOW_SECONDS:
                self.refresh(quiet=True)
                stored = self._stored()
                token = stored.get("access_token")
            if token:
                return str(token)
        env = (None if self.scoped else os.environ.get("THREADS_ACCESS_TOKEN"))
        if env:
            return env
        raise ProviderRejected(0, "No Threads access token. Run `./scripts/import-threads-credentials` first.")

    def _readonly_access_token(self) -> str:
        """Return existing token material without refreshing or changing authority."""
        stored = self._stored()
        token = stored.get("access_token")
        if token:
            return str(token)
        env = None if self.scoped else os.environ.get("THREADS_ACCESS_TOKEN")
        if env:
            return env
        raise ProviderRejected(0, "No Threads access token. Run `./scripts/import-threads-credentials` first.")

    def _readonly_bearer(
        self,
        url: str,
        *,
        query: dict[str, str] | None = None,
    ) -> tuple[int, dict[str, str], dict[str, Any]]:
        return request_json(
            url,
            method="GET",
            headers={"Authorization": f"Bearer {self._readonly_access_token()}"},
            query=query,
        )

    def readonly_account(self) -> AccountIdentity:
        """Verify the current account through GET only; never refresh the token."""
        try:
            status, _headers, payload = self._readonly_bearer(
                f"{API_BASE}/me",
                query={"fields": PROFILE_FIELDS},
            )
        except TransportError as exc:
            raise ProviderUnavailable(f"Threads read-only account verification network error: {exc}") from exc
        if status in TRANSIENT_HTTP_STATUSES or status >= 500:
            raise ProviderUnavailable(
                f"Threads read-only account verification temporarily unavailable (HTTP {status})"
            )
        if not 200 <= status < 300:
            raise ProviderRejected(status, "Threads read-only account verification rejected")
        if not payload.get("id"):
            raise ProviderRejected(status, "Unexpected Threads /me response")
        return AccountIdentity(
            provider="threads",
            account_id=str(payload["id"]),
            username=str(payload.get("username")) if payload.get("username") else None,
            name=str(payload.get("name")) if payload.get("name") else None,
            biography=(
                str(payload.get("threads_biography"))
                if payload.get("threads_biography") is not None
                else None
            ),
        )

    def recent_threads(
        self,
        *,
        since_epoch: int,
        until_epoch: int,
        limit: int = 50,
        max_pages: int = 3,
    ) -> dict[str, Any]:
        """List own Threads posts in a bounded historical window using GET only."""
        if type(since_epoch) is not int or type(until_epoch) is not int or since_epoch >= until_epoch:
            raise ValueError("Invalid Threads forensic time window")
        if type(limit) is not int or not 1 <= limit <= 50:
            raise ValueError("Threads forensic page limit must be between 1 and 50")
        if type(max_pages) is not int or not 1 <= max_pages <= 3:
            raise ValueError("Threads forensic page count must be between 1 and 3")
        posts: list[dict[str, Any]] = []
        after = None
        reads = 0
        for _page in range(max_pages):
            query = {
                "fields": "id,text,permalink,username,timestamp",
                "since": str(since_epoch),
                "until": str(until_epoch),
                "limit": str(limit),
            }
            if after:
                query["after"] = after
            try:
                status, _headers, payload = self._readonly_bearer(
                    f"{API_BASE}/me/threads",
                    query=query,
                )
            except TransportError as exc:
                raise ProviderUnavailable(f"Threads forensic listing network error: {exc}") from exc
            reads += 1
            if status in TRANSIENT_HTTP_STATUSES or status >= 500:
                raise ProviderUnavailable(f"Threads forensic listing temporarily unavailable (HTTP {status})")
            if not 200 <= status < 300:
                raise ProviderRejected(status, "Threads forensic listing rejected")
            data = payload.get("data")
            if not isinstance(data, list) or len(data) > limit:
                raise ProviderRejected(status, "Unexpected Threads forensic listing response")
            for row in data:
                if not isinstance(row, dict):
                    continue
                posts.append({
                    "id": str(row.get("id") or ""),
                    "text": row.get("text"),
                    "permalink": row.get("permalink"),
                    "username": row.get("username"),
                    "timestamp": row.get("timestamp"),
                })
            paging = payload.get("paging") if isinstance(payload.get("paging"), dict) else {}
            cursors = paging.get("cursors") if isinstance(paging.get("cursors"), dict) else {}
            after = cursors.get("after")
            if not after or len(data) < limit:
                after = None
                break
        return {
            "posts": posts,
            "reads": reads,
            "truncated": bool(after),
            "boundary": "GET-only own-post listing using existing token material; no refresh, publish, reply or delete.",
        }

    def publishing_limit(self) -> dict[str, Any]:
        """Observe current Threads publishing quota through GET only; never refresh."""
        fields = "quota_usage,config,reply_quota_usage,reply_config"
        try:
            status, _headers, payload = self._readonly_bearer(
                f"{API_BASE}/me/threads_publishing_limit",
                query={"fields": fields},
            )
        except TransportError as exc:
            raise ProviderUnavailable(f"Threads publishing quota observation network error: {exc}") from exc
        if status in TRANSIENT_HTTP_STATUSES or status >= 500:
            raise ProviderUnavailable(
                f"Threads publishing quota observation temporarily unavailable (HTTP {status})"
            )
        if not 200 <= status < 300:
            raise ProviderRejected(status, "Threads publishing quota observation rejected")
        data = payload.get("data")
        if not isinstance(data, list) or len(data) != 1 or not isinstance(data[0], dict):
            raise ProviderRejected(status, "Unexpected Threads publishing quota response")
        row = data[0]
        config = row.get("config") if isinstance(row.get("config"), dict) else {}
        reply_config = row.get("reply_config") if isinstance(row.get("reply_config"), dict) else {}
        return {
            "quota_usage": row.get("quota_usage"),
            "quota_total": config.get("quota_total"),
            "quota_duration": config.get("quota_duration"),
            "reply_quota_usage": row.get("reply_quota_usage"),
            "reply_quota_total": reply_config.get("quota_total"),
            "reply_quota_duration": reply_config.get("quota_duration"),
            "boundary": "GET-only Threads publishing-limit observation; no token refresh or publication.",
        }

    def refresh(self, quiet: bool = False) -> None:
        stored = self._stored()
        token = stored.get("access_token") or (None if self.scoped else os.environ.get("THREADS_ACCESS_TOKEN"))
        if not token:
            raise ProviderRejected(0, "No Threads long-lived token available to refresh")
        try:
            status, _headers, payload = request_json(
                REFRESH_URL,
                query={"grant_type": "th_refresh_token", "access_token": str(token)},
            )
        except TransportError as exc:
            # Refresh is an authority-changing provider request. A lost response can
            # have token semantics, so do not classify it as safe preflight retry.
            raise ProviderRejected(0, f"Threads token refresh network error: {exc}") from exc
        if not 200 <= status < 300 or not payload.get("access_token"):
            raise ProviderRejected(status, safe_payload(payload))
        self._save_token(
            str(payload["access_token"]),
            expires_in=int(payload.get("expires_in") or LONG_LIVED_SECONDS),
        )
        if not quiet:
            print("Threads token refreshed.")

    def _bearer(
        self,
        url: str,
        *,
        method: str = "GET",
        query: dict[str, str] | None = None,
    ) -> tuple[int, dict[str, str], dict[str, Any]]:
        token = self._access_token()
        return request_json(
            url,
            method=method,
            headers={"Authorization": f"Bearer {token}"},
            query=query,
        )

    def account(self) -> AccountIdentity:
        try:
            status, _headers, payload = self._bearer(
                f"{API_BASE}/me",
                query={"fields": PROFILE_FIELDS},
            )
        except TransportError as exc:
            raise ProviderUnavailable(f"Threads account verification network error: {exc}") from exc
        if status in TRANSIENT_HTTP_STATUSES or status >= 500:
            raise ProviderUnavailable(
                f"Threads account verification temporarily unavailable (HTTP {status}): {safe_payload(payload)}"
            )
        if not 200 <= status < 300:
            raise ProviderRejected(status, safe_payload(payload))
        if not payload.get("id"):
            raise ProviderRejected(status, f"Unexpected Threads /me response: {safe_payload(payload)}")
        return AccountIdentity(
            provider="threads",
            account_id=str(payload["id"]),
            username=str(payload.get("username")) if payload.get("username") else None,
            name=str(payload.get("name")) if payload.get("name") else None,
            biography=(
                str(payload.get("threads_biography"))
                if payload.get("threads_biography") is not None
                else None
            ),
        )

    def publish(self, text: str) -> dict[str, Any]:
        return self._publish_text(text)

    def reply(self, text: str, reply_to_id: str) -> dict[str, Any]:
        return self._publish_text(text, reply_to_id=reply_to_id)

    def _publish_text(self, text: str, reply_to_id: str | None = None) -> dict[str, Any]:
        if not text.strip():
            raise ProviderRejected(0, "Threads post text is empty")
        # Meta's text-only path publishes in this request. There is no draft
        # container here, and no second POST or fallback is safe after uncertainty.
        query = {"media_type": "TEXT", "text": text, "auto_publish_text": "true"}
        if reply_to_id:
            query["reply_to_id"] = reply_to_id
        try:
            status, _headers, published = self._bearer(
                f"{API_BASE}/me/threads", method="POST", query=query,
            )
        except TransportError as exc:
            # Provider prose can contain request URLs/copy; keep only the stage.
            raise AmbiguousProviderEffect(
                "Threads auto_publish_text transport failure; publication may exist; no retry or fallback"
            ) from exc
        if status in (400, 401, 403, 404, 422, 429) and isinstance(published.get("error"), dict) and published["error"] and not published.get("id"):
            raise ProviderRejected(status, "Threads auto_publish_text rejected: " + safe_payload(published))
        post_id = published.get("id")
        if (not 200 <= status < 300 or published.get("error")
                or type(post_id) not in (str, int) or not str(post_id).isascii()
                or not str(post_id).isdigit() or int(post_id) <= 0):
            raise AmbiguousProviderEffect(
                f"Threads auto_publish_text returned HTTP {status} without a trustworthy post ID; no retry or fallback"
            )
        # Return immediately so the caller durably records the effect before
        # optional permalink/readback requests. Verification stays with the caller.
        return {"id": str(post_id), "publishing_method": "auto_publish_text"}

    def _post_details(self, post_id: str) -> dict[str, Any] | None:
        try:
            status, _headers, payload = self._bearer(
                f"{API_BASE}/{urllib.parse.quote(post_id, safe='')}",
                query={"fields": POST_FIELDS},
            )
        except TransportError:
            return None
        if not 200 <= status < 300:
            return None
        if payload.get("permalink"):
            self._permalinks[post_id] = str(payload["permalink"])
        return payload

    def verify_post(self, post_id: str, expected_text: str) -> bool:
        for delay in (0, 1, 2):
            if delay:
                time.sleep(delay)
            payload = self._post_details(post_id)
            if payload is None:
                continue
            return str(payload.get("id")) == post_id and payload.get("text") == expected_text
        return False

    def post_url(self, account: AccountIdentity, post_id: str) -> str:
        if post_id in self._permalinks:
            return self._permalinks[post_id]
        if account.username:
            return f"https://www.threads.net/@{account.username}"
        return "https://www.threads.net/"
