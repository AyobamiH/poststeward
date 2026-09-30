from __future__ import annotations

import base64
import hashlib
import json
import os
import secrets
import socket
import time
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any

from ocpf_post.model import AccountIdentity
from ocpf_post.providers.base import AmbiguousProviderEffect, Provider, ProviderRejected, ProviderUnavailable
from ocpf_post.state import (
    provider_settings_file,
    provider_token_file,
    read_json,
    write_private_json,
    delete_private_json,
)

AUTH_URL = "https://x.com/i/oauth2/authorize"
TOKEN_URL = "https://api.x.com/2/oauth2/token"
REVOKE_URL = "https://api.x.com/2/oauth2/revoke"
ME_URL = "https://api.x.com/2/users/me"
POST_URL = "https://api.x.com/2/tweets"
USAGE_URL = "https://api.x.com/2/usage/tweets"
DEFAULT_SCOPES = ("tweet.read", "tweet.write", "users.read", "offline.access")
DEFAULT_REDIRECT_URI = "http://127.0.0.1:8765/callback"
USER_AGENT = "OneClickPostFactory-post-once/0.1.0"
TRANSIENT_HTTP_STATUSES = {408, 425, 429}


class TransportError(RuntimeError):
    pass


def _safe_payload(payload: dict[str, Any]) -> str:
    scrubbed = {
        key: value
        for key, value in payload.items()
        if key.lower() not in {"access_token", "refresh_token", "token", "client_secret"}
    }
    return json.dumps(scrubbed, ensure_ascii=False)[:1200]


def _request_json(
    url: str,
    *,
    method: str = "GET",
    headers: dict[str, str] | None = None,
    json_body: dict[str, Any] | None = None,
    form: dict[str, str] | None = None,
    timeout: int = 30,
) -> tuple[int, dict[str, Any]]:
    hdrs = {"Accept": "application/json", "User-Agent": USER_AGENT}
    if headers:
        hdrs.update(headers)
    body: bytes | None = None
    if json_body is not None:
        hdrs["Content-Type"] = "application/json"
        body = json.dumps(json_body, separators=(",", ":")).encode("utf-8")
    elif form is not None:
        hdrs["Content-Type"] = "application/x-www-form-urlencoded"
        body = urllib.parse.urlencode(form).encode("utf-8")
    request = urllib.request.Request(url, data=body, headers=hdrs, method=method)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read().decode("utf-8", "replace")
            status = response.status
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", "replace")
        status = exc.code
    except (urllib.error.URLError, TimeoutError, socket.timeout) as exc:
        raise TransportError(str(exc)) from exc
    try:
        payload = json.loads(raw) if raw else {}
    except json.JSONDecodeError:
        payload = {"raw": raw[:1000]}
    if not isinstance(payload, dict):
        payload = {"data": payload}
    return status, payload


def _base64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


def _make_pkce() -> tuple[str, str]:
    verifier = _base64url(secrets.token_bytes(48))
    challenge = _base64url(hashlib.sha256(verifier.encode("ascii")).digest())
    return verifier, challenge


class XProvider(Provider):
    name = "x"

    def __init__(
        self,
        *,
        credential_dir=None,
        client_id: str | None = None,
        client_secret: str | None = None,
        redirect_uri: str | None = None,
        effect_scope: str | None = None,
    ) -> None:
        self.scoped = credential_dir is not None
        self.token_file = credential_dir / "token.json" if self.scoped else provider_token_file("x")
        self.settings_file = credential_dir / "settings.json" if self.scoped else provider_settings_file("x")
        saved = {**(read_json(self.token_file) if self.scoped else {}), **read_json(self.settings_file)}
        self.client_id = str(client_id or (None if self.scoped else os.environ.get("X_CLIENT_ID")) or saved.get("client_id") or "")
        self.client_secret = client_secret or (None if self.scoped else os.environ.get("X_CLIENT_SECRET")) or (saved.get("client_secret") if self.scoped else None)
        self.redirect_uri = str(
            redirect_uri
            or (None if self.scoped else os.environ.get("X_REDIRECT_URI"))
            or saved.get("redirect_uri")
            or DEFAULT_REDIRECT_URI
        )

    def require_client_id(self) -> str:
        if not self.client_id:
            raise ProviderRejected(0, "X_CLIENT_ID is required. Export it or pass --client-id.")
        return self.client_id

    def _token_headers(self) -> dict[str, str]:
        if not self.client_secret:
            return {}
        client_id = self.require_client_id()
        basic = base64.b64encode(f"{client_id}:{self.client_secret}".encode("utf-8")).decode("ascii")
        return {"Authorization": f"Basic {basic}"}

    def _save_token(self, payload: dict[str, Any]) -> None:
        access = payload.get("access_token")
        if not access:
            raise ProviderRejected(0, f"X token response did not contain access_token: {_safe_payload(payload)}")
        now = int(time.time())
        previous = read_json(self.token_file)
        expires_in = int(payload.get("expires_in") or 7200)
        write_private_json(
            self.token_file,
            {
                **({"client_secret": self.client_secret} if self.scoped and self.client_secret else {}),
                "access_token": access,
                "refresh_token": payload.get("refresh_token") or previous.get("refresh_token"),
                "token_type": payload.get("token_type", "bearer"),
                "scope": payload.get("scope", " ".join(DEFAULT_SCOPES)),
                "obtained_at": now,
                "expires_at": now + expires_in,
                "client_id": self.require_client_id(),
                "redirect_uri": self.redirect_uri,
            },
        )

    def authorize(self, timeout_seconds: int = 300) -> AccountIdentity:
        client_id = self.require_client_id()
        parsed = urllib.parse.urlparse(self.redirect_uri)
        if parsed.scheme != "http" or parsed.hostname not in {"127.0.0.1", "localhost"}:
            raise ProviderRejected(0, "Standalone auth requires a localhost HTTP callback")
        port = parsed.port or 80
        expected_path = parsed.path or "/"
        verifier, challenge = _make_pkce()
        state = _base64url(secrets.token_bytes(24))
        params = {
            "response_type": "code",
            "client_id": client_id,
            "redirect_uri": self.redirect_uri,
            "scope": " ".join(DEFAULT_SCOPES),
            "state": state,
            "code_challenge": challenge,
            "code_challenge_method": "S256",
        }
        auth_url = AUTH_URL + "?" + urllib.parse.urlencode(params)
        write_private_json(
            self.settings_file,
            {"client_id": client_id, "redirect_uri": self.redirect_uri},
        )

        result: dict[str, str] = {}

        class CallbackHandler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:  # noqa: N802
                parsed_request = urllib.parse.urlparse(self.path)
                if parsed_request.path != expected_path:
                    self.send_response(404)
                    self.end_headers()
                    return
                params_qs = urllib.parse.parse_qs(parsed_request.query)
                for key in ("code", "state", "error", "error_description"):
                    if params_qs.get(key):
                        result[key] = params_qs[key][0]
                body = (
                    "<html><body><h2>Post-Once authorization code received.</h2>"
                    "<p>Return to the terminal while Post-Once completes the token exchange.</p>"
                    "<p>This page alone does not mean the connection succeeded.</p></body></html>"
                ).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, _format: str, *_args: Any) -> None:
                return

        try:
            server = HTTPServer(("127.0.0.1", port), CallbackHandler)
        except OSError as exc:
            raise ProviderRejected(0, f"Could not listen on 127.0.0.1:{port}: {exc}") from exc
        server.timeout = 1
        print(f"Callback: {self.redirect_uri}")
        print("Scopes: " + " ".join(DEFAULT_SCOPES))
        print("Open this URL if your browser does not open automatically:\n")
        print(auth_url)
        try:
            webbrowser.open(auth_url, new=1, autoraise=True)
        except Exception:
            pass
        deadline = time.time() + timeout_seconds
        while time.time() < deadline and not result:
            server.handle_request()
        server.server_close()
        if not result:
            raise ProviderRejected(0, "Timed out waiting for X authorisation callback")
        if result.get("error"):
            raise ProviderRejected(0, f"X authorisation failed: {result.get('error')} {result.get('error_description', '')}".strip())
        if result.get("state") != state:
            raise ProviderRejected(0, "OAuth state mismatch")
        code = result.get("code")
        if not code:
            raise ProviderRejected(0, "OAuth callback contained no authorization code")
        form = {
            "code": code,
            "grant_type": "authorization_code",
            "redirect_uri": self.redirect_uri,
            "code_verifier": verifier,
        }
        if not self.client_secret:
            form["client_id"] = client_id
        try:
            status, payload = _request_json(
                TOKEN_URL,
                method="POST",
                headers=self._token_headers(),
                form=form,
            )
        except TransportError as exc:
            raise ProviderRejected(0, f"X token exchange network error: {exc}") from exc
        if not 200 <= status < 300:
            raise ProviderRejected(status, _safe_payload(payload))
        self._save_token(payload)
        return self.account()

    def refresh(self, quiet: bool = False) -> None:
        stored = read_json(self.token_file)
        refresh = (None if self.scoped else os.environ.get("X_REFRESH_TOKEN")) or stored.get("refresh_token")
        if not refresh:
            raise ProviderRejected(0, "No refresh token available. Run `ocpf-post x auth` again.")
        client_id = self.require_client_id()
        form = {"refresh_token": str(refresh), "grant_type": "refresh_token"}
        if not self.client_secret:
            form["client_id"] = client_id
        try:
            status, payload = _request_json(
                TOKEN_URL,
                method="POST",
                headers=self._token_headers(),
                form=form,
            )
        except TransportError as exc:
            raise ProviderRejected(0, f"X refresh network error: {exc}") from exc
        if not 200 <= status < 300:
            raise ProviderRejected(status, _safe_payload(payload))
        self._save_token(payload)
        if not quiet:
            print("X token refreshed.")

    def _readonly_access_token(self) -> str:
        """Return current token material without refreshing or changing authority."""
        env_token = (None if self.scoped else os.environ.get("X_USER_ACCESS_TOKEN")) or (
            None if self.scoped else os.environ.get("X_OAUTH2_ACCESS_TOKEN")
        )
        if env_token:
            return str(env_token)
        token = read_json(self.token_file).get("access_token")
        if not token:
            raise ProviderRejected(0, "No X user token. Run `ocpf-post x auth` first.")
        return str(token)

    def _access_token(self) -> str:
        env_token = (None if self.scoped else os.environ.get("X_USER_ACCESS_TOKEN")) or (None if self.scoped else os.environ.get("X_OAUTH2_ACCESS_TOKEN"))
        if env_token:
            return env_token
        stored = read_json(self.token_file)
        token = stored.get("access_token")
        if not token:
            raise ProviderRejected(0, "No X user token. Run `ocpf-post x auth` first.")
        expires_at = int(stored.get("expires_at") or 0)
        if expires_at and expires_at <= int(time.time()) + 90:
            self.refresh(quiet=True)
            stored = read_json(self.token_file)
            token = stored.get("access_token")
        if not token:
            raise ProviderRejected(0, "No X access token available after refresh")
        return str(token)

    def _readonly_bearer(self, url: str, *, query: dict[str, str] | None = None) -> tuple[int, dict[str, Any]]:
        token = self._readonly_access_token()
        if query:
            separator = "&" if "?" in url else "?"
            url = url + separator + urllib.parse.urlencode(query)
        try:
            return _request_json(
                url,
                method="GET",
                headers={"Authorization": f"Bearer {token}"},
            )
        except TransportError:
            raise

    def readonly_account(self) -> AccountIdentity:
        """Verify current X identity through GET only and never refresh."""
        try:
            status, payload = self._readonly_bearer(ME_URL)
        except TransportError as exc:
            raise ProviderUnavailable(f"X read-only account verification network error: {exc}") from exc
        if status in TRANSIENT_HTTP_STATUSES or status >= 500:
            raise ProviderUnavailable(f"X read-only account verification temporarily unavailable (HTTP {status})")
        if not 200 <= status < 300:
            raise ProviderRejected(status, _safe_payload(payload))
        data = payload.get("data")
        if not isinstance(data, dict) or not data.get("id"):
            raise ProviderRejected(status, "Unexpected X /2/users/me response")
        return AccountIdentity(
            provider="x",
            account_id=str(data["id"]),
            username=str(data.get("username")) if data.get("username") else None,
            name=str(data.get("name")) if data.get("name") else None,
        )

    def readonly_usage(self, *, days: int = 7) -> dict[str, Any]:
        """Observe X project usage without refreshing or changing credential authority."""
        if type(days) is not int or not 1 <= days <= 90:
            raise ValueError("X usage days must be between 1 and 90")
        try:
            status, payload = self._readonly_bearer(
                USAGE_URL,
                query={"days": str(days), "usage.fields": "project_cap,project_usage,cap_reset_day"},
            )
        except TransportError as exc:
            raise ProviderUnavailable(f"X usage observation network error: {exc}") from exc
        if status in TRANSIENT_HTTP_STATUSES or status >= 500:
            raise ProviderUnavailable(f"X usage observation temporarily unavailable (HTTP {status})")
        if not 200 <= status < 300:
            raise ProviderRejected(status, _safe_payload(payload))
        data = payload.get("data")
        if not isinstance(data, dict):
            raise ProviderRejected(status, "Unexpected X usage response")
        return {
            "project_cap": data.get("project_cap"),
            "project_usage": data.get("project_usage"),
            "cap_reset_day": data.get("cap_reset_day"),
            "billing_credit_balance": None,
            "boundary": "Read-only X API usage observation; monetary credit balance is not established.",
        }

    def recent_posts(
        self,
        *,
        account_id: str,
        start_time: str,
        end_time: str,
        max_results: int = 100,
        max_pages: int = 3,
    ) -> dict[str, Any]:
        """List own X Posts in a bounded window through GET only and never refresh."""
        if not str(account_id).isdigit():
            raise ValueError("X forensic account_id must be numeric")
        if type(max_results) is not int or not 5 <= max_results <= 100:
            raise ValueError("X forensic max_results must be between 5 and 100")
        if type(max_pages) is not int or not 1 <= max_pages <= 3:
            raise ValueError("X forensic max_pages must be between 1 and 3")
        rows: list[dict[str, Any]] = []
        pagination_token: str | None = None
        reads = 0
        for _page in range(max_pages):
            query = {
                "start_time": start_time,
                "end_time": end_time,
                "max_results": str(max_results),
                "tweet.fields": "id,text,created_at",
            }
            if pagination_token:
                query["pagination_token"] = pagination_token
            try:
                status, payload = self._readonly_bearer(
                    f"https://api.x.com/2/users/{urllib.parse.quote(str(account_id), safe='')}/tweets",
                    query=query,
                )
            except TransportError as exc:
                raise ProviderUnavailable(f"X forensic listing network error: {exc}") from exc
            reads += 1
            if status in TRANSIENT_HTTP_STATUSES or status >= 500:
                raise ProviderUnavailable(f"X forensic listing temporarily unavailable (HTTP {status})")
            if not 200 <= status < 300:
                raise ProviderRejected(status, _safe_payload(payload))
            data = payload.get("data") or []
            if not isinstance(data, list) or len(data) > max_results:
                raise ProviderRejected(status, "Unexpected X forensic listing response")
            for row in data:
                if not isinstance(row, dict):
                    continue
                rows.append({
                    "id": str(row.get("id") or ""),
                    "text": row.get("text"),
                    "created_at": row.get("created_at"),
                })
            meta = payload.get("meta") if isinstance(payload.get("meta"), dict) else {}
            pagination_token = str(meta.get("next_token") or "") or None
            if not pagination_token or len(data) < max_results:
                pagination_token = None
                break
        return {
            "posts": rows,
            "reads": reads,
            "truncated": bool(pagination_token),
            "boundary": "GET-only X own-Post listing using existing token material; no refresh, publish or delete.",
        }

    def _bearer(self, url: str, *, method: str = "GET", body: dict[str, Any] | None = None) -> tuple[int, dict[str, Any]]:
        token = self._access_token()
        try:
            status, payload = _request_json(
                url,
                method=method,
                headers={"Authorization": f"Bearer {token}"},
                json_body=body,
            )
        except TransportError:
            raise
        if status == 401 and read_json(self.token_file).get("refresh_token"):
            self.refresh(quiet=True)
            token = self._access_token()
            status, payload = _request_json(
                url,
                method=method,
                headers={"Authorization": f"Bearer {token}"},
                json_body=body,
            )
        return status, payload

    def account(self) -> AccountIdentity:
        try:
            status, payload = self._bearer(ME_URL)
        except TransportError as exc:
            raise ProviderUnavailable(f"X account verification network error: {exc}") from exc
        if status in TRANSIENT_HTTP_STATUSES or status >= 500:
            raise ProviderUnavailable(
                f"X account verification temporarily unavailable (HTTP {status}): {_safe_payload(payload)}"
            )
        if not 200 <= status < 300:
            raise ProviderRejected(status, _safe_payload(payload))
        data = payload.get("data")
        if not isinstance(data, dict) or not data.get("id"):
            raise ProviderRejected(status, f"Unexpected /2/users/me response: {_safe_payload(payload)}")
        return AccountIdentity(
            provider="x",
            account_id=str(data["id"]),
            username=str(data.get("username")) if data.get("username") else None,
            name=str(data.get("name")) if data.get("name") else None,
        )

    def publish(self, text: str) -> dict[str, Any]:
        return self._publish_text(text)

    def reply(self, text: str, reply_to_id: str) -> dict[str, Any]:
        return self._publish_text(text, reply_to_id=reply_to_id)

    def _publish_text(self, text: str, reply_to_id: str | None = None) -> dict[str, Any]:
        body = {"text": text}
        if reply_to_id:
            body["reply"] = {"in_reply_to_tweet_id": reply_to_id}
        try:
            status, payload = self._bearer(POST_URL, method="POST", body=body)
        except TransportError as exc:
            raise AmbiguousProviderEffect(f"network failure after live POST began: {exc}") from exc
        if 500 <= status <= 599:
            raise AmbiguousProviderEffect(f"X returned HTTP {status} after live POST: {_safe_payload(payload)}")
        if not 200 <= status < 300:
            raise ProviderRejected(status, _safe_payload(payload))
        data = payload.get("data")
        if not isinstance(data, dict) or not data.get("id"):
            raise AmbiguousProviderEffect(f"X returned success without a usable post id: {_safe_payload(payload)}")
        return data

    def verify_post(self, post_id: str, expected_text: str) -> bool:
        url = f"https://api.x.com/2/tweets/{urllib.parse.quote(post_id)}"
        for delay in (0, 1, 2):
            if delay:
                time.sleep(delay)
            try:
                status, payload = self._bearer(url)
            except TransportError:
                continue
            if status == 404:
                continue
            if not 200 <= status < 300:
                return False
            data = payload.get("data")
            return bool(
                isinstance(data, dict)
                and str(data.get("id")) == post_id
                and data.get("text") == expected_text
            )
        return False

    def post_url(self, account: AccountIdentity, post_id: str) -> str:
        if account.username:
            return f"https://x.com/{account.username}/status/{post_id}"
        return f"https://x.com/i/web/status/{post_id}"

    def logout(self) -> None:
        stored = read_json(self.token_file)
        token = stored.get("refresh_token") or stored.get("access_token")
        if token:
            form = {"token": str(token)}
            if not self.client_secret:
                form["client_id"] = self.require_client_id()
            try:
                _request_json(REVOKE_URL, method="POST", headers=self._token_headers(), form=form)
            except TransportError:
                pass
        delete_private_json(self.token_file)
