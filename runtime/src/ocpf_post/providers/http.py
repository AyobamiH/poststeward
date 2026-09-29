from __future__ import annotations

import json
import socket
import urllib.error
import urllib.parse
import urllib.request
from typing import Any


class TransportError(RuntimeError):
    pass


def safe_payload(payload: dict[str, Any]) -> str:
    scrubbed = {
        key: value
        for key, value in payload.items()
        if key.lower() not in {"access_token", "refresh_token", "token", "client_secret", "app_secret"}
    }
    return json.dumps(scrubbed, ensure_ascii=False)[:1200]


def request_json(
    url: str,
    *,
    method: str = "GET",
    headers: dict[str, str] | None = None,
    json_body: dict[str, Any] | None = None,
    form: dict[str, str] | None = None,
    query: dict[str, str] | None = None,
    timeout: int = 30,
    user_agent: str = "OneClickPostFactory-post-once/0.2.0",
) -> tuple[int, dict[str, str], dict[str, Any]]:
    if query:
        separator = "&" if "?" in url else "?"
        url = url + separator + urllib.parse.urlencode(query)
    hdrs = {"Accept": "application/json", "User-Agent": user_agent}
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
            response_headers = {k.lower(): v for k, v in response.headers.items()}
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", "replace")
        status = exc.code
        response_headers = {k.lower(): v for k, v in exc.headers.items()} if exc.headers else {}
    except (urllib.error.URLError, TimeoutError, socket.timeout) as exc:
        raise TransportError(str(exc)) from exc
    try:
        payload = json.loads(raw) if raw else {}
    except json.JSONDecodeError:
        payload = {"raw": raw[:1000]}
    if not isinstance(payload, dict):
        payload = {"data": payload}
    return status, response_headers, payload
