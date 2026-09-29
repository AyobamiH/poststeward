from __future__ import annotations

import os
import re
import time
import urllib.parse
from pathlib import Path
from typing import Any

from ocpf_post.model import AccountIdentity
from ocpf_post.providers.base import AmbiguousProviderEffect, Provider, ProviderRejected
from ocpf_post.providers.http import TransportError, request_json, safe_payload
from ocpf_post.state import (
    config_dir,
    provider_settings_file,
    provider_token_file,
    read_json,
    write_private_json,
)

USERINFO_URL = "https://api.linkedin.com/v2/userinfo"
POSTS_URL = "https://api.linkedin.com/rest/posts"
COMMENTS_URL = "https://api.linkedin.com/rest/socialActions"
TOKEN_URL = "https://www.linkedin.com/oauth/v2/accessToken"
INTROSPECT_URL = "https://www.linkedin.com/oauth/v2/introspectToken"
DEFAULT_VERSION = "202608"
DOCUMENTED_CURRENT_VERSION = "202609"
ACCESS_TOKEN_SECONDS = 60 * 24 * 60 * 60
REFRESH_WINDOW_SECONDS = 7 * 24 * 60 * 60


def secret_file() -> Path:
    return config_dir() / "linkedin-client-secret"


class LinkedInProvider(Provider):
    name = "linkedin"

    def __init__(self, *, credential_dir: Path | str | None = None,
                 actor_urn: str | None = None, **_kwargs: Any) -> None:
        self.credential_dir = Path(credential_dir) if credential_dir is not None else None
        self.actor_urn = str(actor_urn).strip() if actor_urn else None
        if self.actor_urn and not re.fullmatch(
            r"urn:li:(?:person:[A-Za-z0-9_-]{2,120}|organization:[1-9][0-9]{0,29}|organizationBrand:[1-9][0-9]{0,29})",
            self.actor_urn,
        ):
            raise ValueError("Invalid LinkedIn actor URN")
        settings = self._settings()
        self.version = str(
            os.environ.get("LINKEDIN_VERSION")
            or settings.get("version")
            or DEFAULT_VERSION
        )

    def _credential_marker_path(self) -> Path | None:
        return (self.credential_dir / "token.json") if self.credential_dir else None

    def _uses_default_credentials(self) -> bool:
        marker = self._credential_marker_path()
        if marker is None:
            return False
        value = read_json(marker)
        return value == {"credential_source": "provider_default"}

    def _token_path(self) -> Path:
        if self._uses_default_credentials():
            return provider_token_file("linkedin")
        return (self.credential_dir / "token.json") if self.credential_dir else provider_token_file("linkedin")

    def _settings_path(self) -> Path:
        if self._uses_default_credentials():
            return provider_settings_file("linkedin")
        return (self.credential_dir / "settings.json") if self.credential_dir else provider_settings_file("linkedin")

    def _stored(self) -> dict[str, Any]:
        return read_json(self._token_path())

    def recorded_scopes(self) -> set[str] | None:
        """Return locally recorded OAuth scopes without contacting LinkedIn.

        None means the local token metadata does not record scopes. An empty set
        means a scope field was explicitly present but contained no usable scope
        names. Raw token material and the complete scope list are never returned
        by higher-level permission projections.
        """
        raw = self._stored().get("scope")
        if raw is None:
            return None
        if isinstance(raw, str):
            values = raw.replace(",", " ").split()
        elif isinstance(raw, (list, tuple, set)):
            values = [str(value).strip() for value in raw]
        else:
            return set()
        return {value for value in values if value}

    def read_permission(self, purpose: str, account_id: str | None = None) -> dict[str, Any]:
        """Describe the recorded LinkedIn read authority for one actor/purpose.

        This is local metadata inspection only. It never refreshes a token,
        calls LinkedIn, changes authority or claims that a recorded scope is
        currently accepted by the provider.
        """
        if purpose not in {"posts", "comments"}:
            raise ValueError("Unsupported LinkedIn read permission purpose")
        actor = str(account_id or self.actor_urn or self._stored_person_urn() or "")
        if actor.startswith("urn:li:person:") or not actor:
            actor_type = "member"
            required_scope = "r_member_social" if purpose == "posts" else "r_member_social_feed"
        elif actor.startswith(("urn:li:organization:", "urn:li:organizationBrand:")):
            actor_type = "organization"
            required_scope = "r_organization_social" if purpose == "posts" else "r_organization_social_feed"
        else:
            raise ValueError("Invalid LinkedIn actor for read permission")
        scopes = self.recorded_scopes()
        scope_recorded = scopes is not None
        status = (
            "unknown"
            if scopes is None
            else "granted"
            if required_scope in scopes
            else "missing"
        )
        return {
            "status": status,
            "purpose": purpose,
            "actor_type": actor_type,
            "scope_recorded": scope_recorded,
            "required_scope": required_scope,
            "boundary": (
                "Local OAuth scope metadata only. Recorded scope presence is not live provider acceptance; "
                "missing recorded authority blocks automatic LinkedIn reads until credentials are re-authorised."
            ),
        }

    def _settings(self) -> dict[str, Any]:
        settings = read_json(self._settings_path())
        if self.credential_dir:
            token = read_json(self._token_path())
            for key in ("client_id", "client_secret", "person_urn", "version"):
                if key in token and key not in settings:
                    settings[key] = token[key]
        return settings

    def _client_id(self) -> str | None:
        value = os.environ.get("LINKEDIN_CLIENT_ID") or self._settings().get("client_id")
        return str(value).strip() if value else None

    def _client_secret(self) -> str | None:
        value = self._settings().get("client_secret")
        if value:
            return str(value).strip()
        env = os.environ.get("LINKEDIN_CLIENT_SECRET")
        if env:
            return env
        path = secret_file()
        return path.read_text(encoding="utf-8").strip() if path.exists() else None

    def _stored_person_urn(self) -> str | None:
        value = self._settings().get("person_urn")
        if not value:
            return None
        urn = str(value).strip()
        return urn if urn.startswith("urn:li:person:") else None

    def _readonly_access_token(self) -> str:
        """Return current LinkedIn token material without refreshing authority."""
        stored = self._stored()
        token = stored.get("access_token")
        if token:
            return str(token)
        env = os.environ.get("LINKEDIN_ACCESS_TOKEN") or os.environ.get("LINKEDIN_TOKEN")
        if env:
            return str(env)
        raise ProviderRejected(
            0,
            "No LinkedIn access token. Run `./scripts/import-linkedin-credentials` or `./scripts/import-ocpf-db-credentials` first.",
        )

    def _access_token(self) -> str:
        stored = self._stored()
        token = stored.get("access_token")
        if token:
            expires_at = int(stored.get("expires_at") or 0)
            if (
                expires_at
                and expires_at <= int(time.time()) + REFRESH_WINDOW_SECONDS
                and stored.get("refresh_token")
            ):
                self.refresh(quiet=True)
                stored = self._stored()
                token = stored.get("access_token")
            if token:
                return str(token)
        env = os.environ.get("LINKEDIN_ACCESS_TOKEN") or os.environ.get("LINKEDIN_TOKEN")
        if env:
            return env
        raise ProviderRejected(
            0,
            "No LinkedIn access token. Run `./scripts/import-linkedin-credentials` or `./scripts/import-ocpf-db-credentials` first.",
        )

    def refresh(self, quiet: bool = False) -> None:
        stored = self._stored()
        refresh_token = stored.get("refresh_token")
        client_id = self._client_id()
        client_secret = self._client_secret()
        if not refresh_token or not client_id or not client_secret:
            raise ProviderRejected(
                0,
                "LinkedIn refresh requires refresh token, client ID and client secret",
            )
        try:
            status, _headers, payload = request_json(
                TOKEN_URL,
                method="POST",
                form={
                    "grant_type": "refresh_token",
                    "refresh_token": str(refresh_token),
                    "client_id": str(client_id),
                    "client_secret": str(client_secret),
                },
            )
        except TransportError as exc:
            raise ProviderRejected(0, f"LinkedIn token refresh network error: {exc}") from exc
        if not 200 <= status < 300 or not payload.get("access_token"):
            raise ProviderRejected(status, safe_payload(payload))
        now = int(time.time())
        write_private_json(
            self._token_path(),
            {
                "access_token": str(payload["access_token"]),
                "refresh_token": str(payload.get("refresh_token") or refresh_token),
                "obtained_at": now,
                "expires_at": now + int(payload.get("expires_in") or ACCESS_TOKEN_SECONDS),
                "refresh_token_expires_in": payload.get("refresh_token_expires_in")
                or stored.get("refresh_token_expires_in"),
                "scope": payload.get("scope") or stored.get("scope"),
                **{key: stored[key] for key in ("client_id", "client_secret", "person_urn", "version")
                   if stored.get(key) is not None},
            },
        )
        if not quiet:
            print("LinkedIn token refreshed.")

    def _headers(self, token: str | None = None) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {token or self._access_token()}",
            "X-Restli-Protocol-Version": "2.0.0",
            "Linkedin-Version": self.version,
        }

    def _readonly_headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._readonly_access_token()}",
            "X-Restli-Protocol-Version": "2.0.0",
            "Linkedin-Version": self.version,
        }

    def readonly_userinfo(self) -> dict[str, Any]:
        """Read the live OIDC subject without refreshing token authority."""
        try:
            status, _headers, payload = request_json(
                USERINFO_URL,
                headers={"Authorization": f"Bearer {self._readonly_access_token()}"},
            )
        except TransportError as exc:
            raise ProviderRejected(0, f"LinkedIn read-only userinfo network error: {exc}") from exc
        if not 200 <= status < 300 or not payload.get("sub"):
            raise ProviderRejected(status, "LinkedIn read-only userinfo could not prove a member subject")
        return {
            "sub": str(payload["sub"]),
            "name": str(payload.get("name")) if payload.get("name") else None,
        }

    def readonly_introspect(self) -> dict[str, Any]:
        """Inspect exactly one current token without refreshing or mutating it."""
        client_id = self._client_id()
        client_secret = self._client_secret()
        if not client_id or not client_secret:
            raise ProviderRejected(0, "LinkedIn token introspection requires client ID and client secret")
        try:
            status, _headers, payload = request_json(
                INTROSPECT_URL,
                method="POST",
                form={
                    "client_id": client_id,
                    "client_secret": client_secret,
                    "token": self._readonly_access_token(),
                },
            )
        except TransportError as exc:
            raise ProviderRejected(0, f"LinkedIn token introspection network error: {exc}") from exc
        if not 200 <= status < 300:
            raise ProviderRejected(status, "LinkedIn token introspection rejected")
        return {
            "active": payload.get("active") is True,
            "status": str(payload.get("status")) if payload.get("status") else None,
            "client_id": str(payload.get("client_id")) if payload.get("client_id") else None,
            "scope": str(payload.get("scope")) if payload.get("scope") else None,
            "expires_at": payload.get("expires_at"),
            "auth_type": str(payload.get("auth_type")) if payload.get("auth_type") else None,
        }

    def readonly_organisation_access(self, actor_urn: str) -> dict[str, Any]:
        """Bounded organization author read using current token only; never refresh."""
        if not actor_urn.startswith(("urn:li:organization:", "urn:li:organizationBrand:")):
            raise ValueError("LinkedIn organization actor URN is required")
        try:
            status, _headers, payload = request_json(
                POSTS_URL,
                headers={**self._readonly_headers(), "X-RestLi-Method": "FINDER"},
                query={
                    "author": actor_urn,
                    "q": "author",
                    "count": "1",
                    "viewContext": "AUTHOR",
                    "sortBy": "LAST_MODIFIED",
                },
            )
        except TransportError as exc:
            raise ProviderRejected(0, "LinkedIn read-only organization actor verification unavailable") from exc
        if not 200 <= status < 300:
            raise ProviderRejected(status, "LinkedIn read-only organization actor verification rejected")
        elements = payload.get("elements")
        if not isinstance(elements, list):
            raise ProviderRejected(status, "Invalid LinkedIn organization actor response")
        return {
            "status": "reachable",
            "actor_urn": actor_urn,
            "elements_observed": len(elements),
            "boundary": "GET-only LinkedIn organization author observation; no refresh or publication.",
        }

    def api_version_evidence(self) -> dict[str, Any]:
        return {
            "pinned": self.version,
            "documented_current": DOCUMENTED_CURRENT_VERSION,
            "state": "current" if self.version == DOCUMENTED_CURRENT_VERSION else "attention",
            "boundary": "Version metadata only; provider compatibility is not inferred from latest-version naming.",
        }

    def _userinfo(self) -> dict[str, Any]:
        try:
            status, _headers, payload = request_json(
                USERINFO_URL,
                headers={"Authorization": f"Bearer {self._access_token()}"},
            )
        except TransportError as exc:
            raise ProviderRejected(
                0,
                f"LinkedIn account verification network error: {exc}",
            ) from exc
        if not 200 <= status < 300:
            raise ProviderRejected(
                status,
                "LinkedIn userinfo readback failed. The token may not include `openid profile`: "
                + safe_payload(payload),
            )
        if not payload.get("sub"):
            raise ProviderRejected(
                status,
                f"Unexpected LinkedIn userinfo response: {safe_payload(payload)}",
            )
        return payload

    def _introspect(self) -> dict[str, Any]:
        client_id = self._client_id()
        client_secret = self._client_secret()
        if not client_id or not client_secret:
            raise ProviderRejected(
                0,
                "LinkedIn token introspection requires client ID and client secret",
            )
        try:
            status, _headers, payload = request_json(
                INTROSPECT_URL,
                method="POST",
                form={
                    "client_id": client_id,
                    "client_secret": client_secret,
                    "token": self._access_token(),
                },
            )
        except TransportError as exc:
            raise ProviderRejected(
                0,
                f"LinkedIn token introspection network error: {exc}",
            ) from exc
        if not 200 <= status < 300 or payload.get("active") is not True:
            raise ProviderRejected(status, "LinkedIn access token is not active: " + safe_payload(payload))
        return payload

    def _member_account(self) -> AccountIdentity:
        stored_urn = self._stored_person_urn()
        try:
            info = self._userinfo()
        except ProviderRejected as userinfo_error:
            if not stored_urn:
                raise
            try:
                self._introspect()
            except ProviderRejected as introspection_error:
                raise ProviderRejected(
                    introspection_error.status,
                    "LinkedIn could not verify the configured member identity because both userinfo and token introspection failed",
                ) from introspection_error
            return AccountIdentity(
                provider="linkedin",
                account_id=stored_urn,
                username=None,
                name=stored_urn,
            )

        author_urn = f"urn:li:person:{info['sub']}"
        if stored_urn and stored_urn != author_urn:
            raise ProviderRejected(
                409,
                "LinkedIn identity mismatch: live userinfo does not match the configured person URN",
            )
        return AccountIdentity(
            provider="linkedin",
            account_id=author_urn,
            username=None,
            name=str(info.get("name")) if info.get("name") else author_urn,
        )

    def _organisation_access(self, actor_urn: str) -> None:
        """Verify the same member grant can read the reviewed organization actor.

        LinkedIn's Posts author finder is itself restricted by
        r_organization_social and Page role, so a successful bounded read proves
        the actor is reachable without requesting broader organization-admin
        authority merely for verification.
        """
        encoded = urllib.parse.quote(actor_urn, safe="")
        try:
            status, _headers, payload = request_json(
                POSTS_URL,
                headers={**self._headers(), "X-RestLi-Method": "FINDER"},
                query={
                    "author": actor_urn,
                    "q": "author",
                    "count": "1",
                    "viewContext": "AUTHOR",
                    "sortBy": "LAST_MODIFIED",
                },
            )
        except TransportError as exc:
            raise ProviderRejected(0, "LinkedIn organization actor verification unavailable") from exc
        if not 200 <= status < 300:
            raise ProviderRejected(
                status,
                "LinkedIn organization actor verification failed; organization social read permission or Page role may be required",
            )
        if not isinstance(payload.get("elements", []), list):
            raise ProviderRejected(status, "Invalid LinkedIn organization actor response")

    def account(self) -> AccountIdentity:
        member = self._member_account()
        actor = self.actor_urn or member.account_id
        if actor.startswith("urn:li:person:"):
            if actor != member.account_id:
                raise ProviderRejected(409, "LinkedIn scoped person actor does not match the authenticated member")
            return member
        self._organisation_access(actor)
        return AccountIdentity(
            provider="linkedin",
            account_id=actor,
            username=None,
            name=actor,
        )

    def comments_page(self, target_urn: str, *, start: int = 0, count: int = 100) -> tuple[int, dict[str, Any]]:
        if not isinstance(target_urn, str) or not (
            re.fullmatch(r"urn:li:(?:share|ugcPost|activity):[0-9]{1,30}", target_urn)
            or re.fullmatch(
                r"urn:li:comment:\(urn:li:(?:share|ugcPost|activity):[0-9]{1,30},[0-9]{1,30}\)",
                target_urn,
            )
        ):
            raise ValueError("Invalid LinkedIn social action target")
        if type(start) is not int or start < 0 or type(count) is not int or not 1 <= count <= 100:
            raise ValueError("Invalid LinkedIn comment page")
        encoded = urllib.parse.quote(target_urn, safe="")
        status, _headers, payload = request_json(
            f"{COMMENTS_URL}/{encoded}/comments",
            headers=self._headers(),
            query={"start": str(start), "count": str(count)},
        )
        return status, payload

    @staticmethod
    def _comment_parts(comment_urn: str) -> tuple[str, str]:
        match = re.fullmatch(
            r"urn:li:comment:\((urn:li:(?:share|ugcPost|activity):[0-9]{1,30}),([0-9]{1,30})\)",
            str(comment_urn),
        )
        if not match:
            raise ValueError("Invalid LinkedIn comment URN")
        return match.group(1), match.group(2)

    def comment_lookup(self, comment_urn: str) -> tuple[int, dict[str, Any]]:
        object_urn, comment_id = self._comment_parts(comment_urn)
        encoded = urllib.parse.quote(object_urn, safe="")
        status, _headers, payload = request_json(
            f"{COMMENTS_URL}/{encoded}/comments/{comment_id}",
            headers=self._headers(),
        )
        return status, payload

    def reply(self, text: str, parent_post_id: str, *, root_post_id: str | None = None) -> dict[str, Any]:
        if not text.strip():
            raise ProviderRejected(0, "LinkedIn comment text is empty")
        account = self.account()
        if not parent_post_id.startswith("urn:li:comment:"):
            raise ProviderRejected(0, "LinkedIn replies require a known parent comment URN")
        root = root_post_id
        if not root or not re.fullmatch(r"urn:li:(?:share|ugcPost|activity):[0-9]{1,30}", root):
            raise ProviderRejected(0, "LinkedIn reply requires the receipt-backed root post URN")
        encoded = urllib.parse.quote(parent_post_id, safe="")
        body = {
            "actor": account.account_id,
            "object": root,
            "parentComment": parent_post_id,
            "message": {"text": text},
        }
        try:
            status, headers, payload = request_json(
                f"{COMMENTS_URL}/{encoded}/comments",
                method="POST",
                headers=self._headers(),
                json_body=body,
            )
        except TransportError as exc:
            raise AmbiguousProviderEffect(f"network failure after LinkedIn comment POST began: {exc}") from exc
        if status >= 500 or status in {408, 409}:
            raise AmbiguousProviderEffect(f"LinkedIn returned HTTP {status} after comment POST began")
        if not 200 <= status < 300:
            raise ProviderRejected(status, safe_payload(payload))
        comment_urn = payload.get("commentUrn")
        if not comment_urn:
            comment_id = payload.get("id") or headers.get("x-restli-id")
            object_urn = payload.get("object") or root
            if comment_id:
                comment_urn = f"urn:li:comment:({object_urn},{comment_id})"
        if not comment_urn:
            raise AmbiguousProviderEffect("LinkedIn accepted the comment but returned no usable comment URN")
        return {"id": str(comment_urn)}

    def publish(self, text: str) -> dict[str, Any]:
        if not text.strip():
            raise ProviderRejected(0, "LinkedIn post text is empty")
        account = self.account()
        body = {
            "author": account.account_id,
            "commentary": text,
            "visibility": "PUBLIC",
            "distribution": {
                "feedDistribution": "MAIN_FEED",
                "targetEntities": [],
                "thirdPartyDistributionChannels": [],
            },
            "lifecycleState": "PUBLISHED",
            "isReshareDisabledByAuthor": False,
        }
        try:
            status, headers, payload = request_json(
                POSTS_URL,
                method="POST",
                headers=self._headers(),
                json_body=body,
            )
        except TransportError as exc:
            raise AmbiguousProviderEffect(
                f"network failure after LinkedIn live POST began: {exc}"
            ) from exc
        if status >= 500 or status in {408, 409}:
            raise AmbiguousProviderEffect(
                f"LinkedIn returned HTTP {status} after live POST began: {safe_payload(payload)}"
            )
        if not 200 <= status < 300:
            raise ProviderRejected(status, safe_payload(payload))
        post_id = headers.get("x-restli-id") or payload.get("id")
        if not post_id:
            raise AmbiguousProviderEffect(
                "LinkedIn accepted the POST but returned no usable x-restli-id; inspect the member feed before retrying"
            )
        return {"id": str(post_id)}

    def verify_post(self, post_id: str, expected_text: str) -> bool:
        encoded = urllib.parse.quote(post_id, safe="")
        try:
            status, _headers, payload = request_json(
                f"{POSTS_URL}/{encoded}",
                headers=self._headers(),
            )
        except TransportError:
            return False
        if not 200 <= status < 300:
            # Readback can require restricted r_member_social. Creation receipt remains authoritative.
            return False
        account = self.account()
        return (
            str(payload.get("id") or "") == post_id
            and payload.get("commentary") == expected_text
            and str(payload.get("author") or "") == account.account_id
        )

    def post_url(self, account: AccountIdentity, post_id: str) -> str:
        return f"https://www.linkedin.com/feed/update/{post_id}/"


def recorded_read_permission(account_id: str, purpose: str) -> dict[str, Any]:
    """Inspect the credential bound to one exact LinkedIn actor without provider I/O."""
    from ocpf_post.account_profiles import profile, directory
    from ocpf_post.registry import resolve_provider_default_identity

    identity = str(account_id or "").strip()
    if not identity:
        raise ValueError("Exact LinkedIn account identity is required")
    row = profile("linkedin", identity)
    if row:
        provider = LinkedInProvider(
            credential_dir=directory("linkedin", identity),
            actor_urn=identity,
        )
    else:
        founder = resolve_provider_default_identity("linkedin")
        if not founder or founder.get("account_id") != identity:
            return {
                "status": "unknown",
                "purpose": purpose,
                "scope_recorded": False,
                "required_scope": None,
                "boundary": "Unknown LinkedIn account identity; no provider call was attempted.",
            }
        provider = LinkedInProvider(actor_urn=identity)
    return provider.read_permission(purpose, identity)
