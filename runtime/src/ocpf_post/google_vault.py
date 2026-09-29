"""Read designated Google Docs using operator-owned refresh credentials."""
from __future__ import annotations

import os
from pathlib import Path
import re
from urllib.parse import urlencode, urlsplit, parse_qs

from ocpf_post.bounded_http import json_request, post_json
from ocpf_post.onboarding import OnboardingError, _decode, _import_lock
from ocpf_post.state import config_dir, write_private_json

DRIVE_READ_SCOPE = "https://www.googleapis.com/auth/drive.readonly"
DOCUMENT_WRITE_SCOPE = "https://www.googleapis.com/auth/documents"
WRITEBACK_ENV = "OCPF_POST_GOOGLE_VAULT_WRITEBACK_ENABLED"


def credential_path():
    return config_dir() / "google-drive-credential.json"


def _credential_scopes(value):
    raw = value.get("scopes")
    if raw is None:
        raw = value.get("scope")
    if isinstance(raw, str):
        values = raw.split()
    elif isinstance(raw, list):
        values = [str(item) for item in raw]
    else:
        values = []
    # Legacy post-once credentials were explicitly authorised read-only.
    return set(values) or {DRIVE_READ_SCOPE}


def credential_capabilities():
    path = credential_path()
    if not path.exists():
        return {
            "credential_present": False,
            "read": False,
            "document_write": False,
            "writeback_enabled": _writeback_enabled(),
        }
    try:
        value = _decode(path.read_bytes())
        scopes = _credential_scopes(value)
    except (OSError, ValueError):
        return {
            "credential_present": True,
            "read": False,
            "document_write": False,
            "writeback_enabled": _writeback_enabled(),
        }
    return {
        "credential_present": True,
        "read": DRIVE_READ_SCOPE in scopes or DOCUMENT_WRITE_SCOPE in scopes,
        "document_write": DOCUMENT_WRITE_SCOPE in scopes,
        "writeback_enabled": _writeback_enabled(),
        "scopes_recorded": sorted(scopes),
    }


def _writeback_enabled():
    return os.environ.get(WRITEBACK_ENV, "").strip().lower() in {"1", "true", "yes"}


def install_credentials(path):
    path = Path(path)
    if path.stat().st_mode & 0o077:
        raise ValueError("Credential file must be private: chmod 600 FILE")
    value = _decode(path.read_bytes())
    if (value.get("type") != "authorized_user" or any(not isinstance(value.get(k), str) or not value[k]
            for k in ("client_id", "client_secret", "refresh_token"))):
        raise ValueError("Expected Google authorized_user credentials with client ID, secret and refresh token")
    scopes = sorted(_credential_scopes(value))
    with _import_lock("google-credential"):
        write_private_json(credential_path(), {
            k: value[k] for k in ("type", "client_id", "client_secret", "refresh_token")
        } | {"scopes": scopes})
    return {
        "result": "credential_saved",
        "credential_contents": "never printed",
        "capabilities": {
            "read": DRIVE_READ_SCOPE in scopes or DOCUMENT_WRITE_SCOPE in scopes,
            "document_write": DOCUMENT_WRITE_SCOPE in scopes,
        },
    }


def _token(*, required_scopes=()):
    path = credential_path()
    if not path.exists():
        raise ValueError("Google Drive host credentials are not connected")
    if path.stat().st_mode & 0o077:
        raise ValueError("Google credential permissions must be 0600")
    value = _decode(path.read_bytes())
    scopes = _credential_scopes(value)
    missing = sorted(set(required_scopes) - scopes)
    if missing:
        raise ValueError(
            "Google credential lacks document write authority; re-authorize the vault credential "
            f"with {WRITEBACK_ENV}=1 before enabling receipt writeback"
        )
    body = urlencode({k: value[k] for k in ("client_id", "client_secret", "refresh_token")}
                    | {"grant_type": "refresh_token"})
    from ocpf_post.google_connection import record_refresh
    try:
        response = json_request("https://oauth2.googleapis.com/token", data=body,
                                headers={"Content-Type": "application/x-www-form-urlencoded"})
    except (ValueError, OSError) as exc:
        record_refresh(value, success=False, error_type=type(exc).__name__)
        raise
    token = response.get("access_token")
    if not isinstance(token, str) or not token:
        raise ValueError("Google refresh did not return an access token")
    record_refresh(value, success=True)
    return token


def _text(elements):
    result = []
    for element in elements:
        if "paragraph" in element:
            result.extend(e.get("textRun", {}).get("content", "") for e in element["paragraph"].get("elements", []))
        for row in element.get("table", {}).get("tableRows", []):
            for cell in row.get("tableCells", []):
                result.append(_text(cell.get("content", [])))
    return "".join(result)


def document_text(document):
    def tabs(values):
        out = []
        for tab in values:
            out.append(_text(tab.get("documentTab", {}).get("body", {}).get("content", [])))
            out.extend(tabs(tab.get("childTabs", [])))
        return out
    return "\n".join(tabs(document["tabs"])) if "tabs" in document else _text(document.get("body", {}).get("content", []))


def document_paragraphs(document):
    """Flatten paragraph text plus Google Docs indices for guarded status writes."""
    rows = []

    def elements(values, tab_id=None):
        for element in values:
            paragraph = element.get("paragraph")
            if isinstance(paragraph, dict):
                text = "".join(
                    part.get("textRun", {}).get("content", "")
                    for part in paragraph.get("elements", [])
                    if isinstance(part, dict)
                )
                start = element.get("startIndex")
                end = element.get("endIndex")
                if isinstance(start, int) and isinstance(end, int):
                    rows.append({
                        "text": text,
                        "start_index": start,
                        "end_index": end,
                        "tab_id": tab_id,
                    })
            table = element.get("table")
            if isinstance(table, dict):
                for table_row in table.get("tableRows", []):
                    for cell in table_row.get("tableCells", []):
                        elements(cell.get("content", []), tab_id)

    def tabs(values):
        for tab in values:
            props = tab.get("tabProperties", {}) if isinstance(tab, dict) else {}
            tab_id = str(props.get("tabId") or "") or None
            body = (tab.get("documentTab", {}) if isinstance(tab, dict) else {}).get("body", {})
            elements(body.get("content", []), tab_id)
            tabs(tab.get("childTabs", []) if isinstance(tab, dict) else [])

    if "tabs" in document:
        tabs(document.get("tabs", []))
    else:
        elements(document.get("body", {}).get("content", []), None)
    return rows


def read_document(document_id):
    if not re.fullmatch(r"[A-Za-z0-9_-]{10,150}", document_id):
        raise ValueError("Invalid Google document ID")
    headers = {"Authorization": "Bearer " + _token()}
    metadata_url = ("https://www.googleapis.com/drive/v3/files/" + document_id
                    + "?fields=id,mimeType,version,modifiedTime,trashed")
    before = json_request(metadata_url, headers=headers)
    if before.get("trashed") or before.get("mimeType") != "application/vnd.google-apps.document":
        raise ValueError("Vault must be a live native Google Doc")
    document = json_request("https://docs.googleapis.com/v1/documents/" + document_id
                            + "?includeTabsContent=true&suggestionsViewMode=PREVIEW_WITHOUT_SUGGESTIONS",
                            headers=headers, limit=8_000_000)
    after = json_request(metadata_url, headers=headers)
    if before != after or document.get("documentId") != document_id or not before.get("version"):
        raise ValueError("Document changed during read; retry on the next sync")
    return {
        "document_id": document_id,
        "version": str(before["version"]),
        "modified_at": before["modifiedTime"],
        "revision_id": document.get("revisionId"),
        "text": document_text(document),
        "paragraphs": document_paragraphs(document),
    }


def _section_marker(value):
    text = value.strip()
    if text == "POST-ONCE APPROVED ENTRIES BEGIN":
        return "begin", None
    if text == "POST-ONCE APPROVED ENTRIES END":
        return "end", None
    match = re.fullmatch(r"POST-ONCE (X|THREADS|LINKEDIN) APPROVED ENTRIES (BEGIN|END)", text)
    if not match:
        return None
    return match.group(2).lower(), match.group(1).lower()


def _entry_records(document):
    records = []
    by_tab = {}
    for row in document.get("paragraphs", []):
        if isinstance(row, dict):
            by_tab.setdefault(row.get("tab_id"), []).append(row)
    for tab_id, rows in by_tab.items():
        rows.sort(key=lambda row: int(row.get("start_index", 0)))
        section = None
        current = None

        def finish():
            nonlocal current
            if current is not None:
                records.append(current)
                current = None

        for row in rows:
            text = str(row.get("text") or "")
            marker = _section_marker(text)
            if marker:
                finish()
                action, provider = marker
                section = ("generic" if provider is None else provider) if action == "begin" else None
                continue
            if section is None:
                continue
            campaign_match = re.search(r'"campaign"\s*:\s*"([^"]+)"', text)
            if campaign_match:
                finish()
                current = {
                    "campaign": campaign_match.group(1),
                    "section": section,
                    "tab_id": tab_id,
                    "paragraphs": [row],
                }
                continue
            if current is not None:
                current["paragraphs"].append(row)
        finish()
    return records


def _record_provider(record):
    for row in record.get("paragraphs", []):
        match = re.search(r'"provider"\s*:\s*"([^"]+)"', str(row.get("text") or ""))
        if match:
            return match.group(1).strip().lower()
    section = record.get("section")
    return section if section in {"x", "threads", "linkedin"} else None


def _record_status(record):
    for row in record.get("paragraphs", []):
        text = str(row.get("text") or "")
        match = re.search(r'"status"\s*:\s*"([^"]+)"', text)
        if match:
            return match.group(1), row, match
    return None, None, None


def status_change_requests(document, changes):
    """Build exact descending-index APPROVED→PUBLISHED requests.

    Changes are base-campaign/provider identities already proven by the local receipt
    ledger. Narrative references outside the designated approved sections are ignored.
    """
    records = _entry_records(document)
    requests = []
    updated = []
    already_terminal = []
    for change in changes:
        campaign = str(change.get("base_campaign") or "")
        provider = str(change.get("provider") or "").lower()
        matches = [
            row for row in records
            if row.get("campaign") == campaign and _record_provider(row) == provider
        ]
        if len(matches) != 1:
            raise ValueError(f"Expected one designated vault entry for {campaign}/{provider}")
        status, paragraph, match = _record_status(matches[0])
        if status == "PUBLISHED":
            already_terminal.append({"base_campaign": campaign, "provider": provider})
            continue
        if status != "APPROVED" or paragraph is None or match is None:
            raise ValueError(f"Refusing receipt writeback over non-APPROVED status for {campaign}/{provider}")
        text = str(paragraph.get("text") or "")
        literal = "APPROVED"
        offset = text.find(literal, match.start(), match.end())
        if offset < 0:
            raise ValueError("Could not bind the approved status text to its document range")
        start = int(paragraph["start_index"]) + offset
        updated.append({
            "base_campaign": campaign,
            "provider": provider,
            "tab_id": paragraph.get("tab_id"),
            "start_index": start,
        })

    for row in sorted(updated, key=lambda value: (str(value.get("tab_id") or ""), int(value["start_index"])), reverse=True):
        range_value = {"startIndex": row["start_index"], "endIndex": row["start_index"] + len("APPROVED")}
        location = {"index": row["start_index"]}
        if row.get("tab_id"):
            range_value["tabId"] = row["tab_id"]
            location["tabId"] = row["tab_id"]
        requests.append({"deleteContentRange": {"range": range_value}})
        requests.append({"insertText": {"location": location, "text": "PUBLISHED"}})
    return requests, updated, already_terminal


def write_entry_statuses(document, changes):
    if not changes:
        return {"result": "no_changes", "updated": [], "already_terminal": []}
    if not _writeback_enabled():
        return {
            "result": "disabled",
            "updated": [],
            "already_terminal": [],
            "pending": len(changes),
        }
    revision_id = document.get("revision_id")
    if not isinstance(revision_id, str) or not revision_id:
        raise ValueError("Google document revision ID is required for guarded receipt writeback")
    requests, updated, already_terminal = status_change_requests(document, changes)
    if not requests:
        return {
            "result": "already_reconciled",
            "updated": [],
            "already_terminal": already_terminal,
        }
    headers = {"Authorization": "Bearer " + _token(required_scopes=(DOCUMENT_WRITE_SCOPE,))}
    _, response = post_json(
        "https://docs.googleapis.com/v1/documents/" + str(document["document_id"]) + ":batchUpdate",
        {"requests": requests, "writeControl": {"requiredRevisionId": revision_id}},
        headers=headers,
        limit=500_000,
    )
    return {
        "result": "updated",
        "updated": [{k: value.get(k) for k in ("base_campaign", "provider")} for value in updated],
        "already_terminal": already_terminal,
        "write_control": response.get("writeControl"),
    }


def authorize(client_file, *, port=8766):
    """Installed-client OAuth with PKCE, state and a loopback-only callback."""
    import base64
    import hashlib
    import secrets
    import time
    import webbrowser
    from http.server import HTTPServer, BaseHTTPRequestHandler
    path = Path(client_file)
    if path.stat().st_mode & 0o077 or path.stat().st_size > 100_000:
        raise ValueError("OAuth client file must be private (0600) and bounded")
    client = _decode(path.read_bytes()).get("installed", {})
    if any(not isinstance(client.get(k), str) or not client[k] for k in ("client_id", "client_secret")):
        raise ValueError("Expected a Google Desktop OAuth client JSON file")
    if type(port) is not int or not 1024 <= port <= 65535:
        raise ValueError("Invalid loopback callback port")
    scopes = [DRIVE_READ_SCOPE]
    if _writeback_enabled():
        scopes.append(DOCUMENT_WRITE_SCOPE)
    state = secrets.token_urlsafe(32); verifier = secrets.token_urlsafe(48)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
    redirect = f"http://127.0.0.1:{port}/callback"; received = {}
    class Callback(BaseHTTPRequestHandler):
        def setup(self):
            super().setup()
            self.connection.settimeout(2)
        def log_message(self, *args):
            pass  # OAuth codes never enter logs.
        def do_GET(self):
            parts = urlsplit(self.path)
            query = parse_qs(parts.query)
            state_ok = query.get("state") == [state]
            error = query.get("error", [None])[0]
            valid = parts.path == "/callback" and state_ok and len(query.get("code", [])) == 1
            if valid:
                received["code"] = query["code"][0]
            elif parts.path == "/callback" and state_ok and isinstance(error, str) and error:
                received["oauth_error"] = re.sub(r"[^a-z0-9_.-]", "_", error.lower())[:80]
            self.send_response(200 if valid else 400)
            self.end_headers()
            if valid:
                self.wfile.write(b"Google authorisation received. Return to the post-once terminal.")
            elif received.get("oauth_error"):
                self.wfile.write(b"Google returned an OAuth error. Return to the post-once terminal.")
            else:
                self.wfile.write(b"Invalid callback.")
    with _import_lock("google-credential"), HTTPServer(("127.0.0.1", port), Callback) as server:
        server.timeout = 1
        url = "https://accounts.google.com/o/oauth2/v2/auth?" + urlencode({
            "client_id": client["client_id"], "redirect_uri": redirect, "response_type": "code",
            "scope": " ".join(scopes), "access_type": "offline",
            "prompt": "consent", "state": state, "code_challenge": challenge, "code_challenge_method": "S256"})
        print("Open this Google consent URL in a browser on this machine (or with a loopback SSH tunnel):\n" + url, flush=True)
        webbrowser.open(url)
        deadline = time.monotonic() + 180
        while not received and time.monotonic() < deadline:
            server.handle_request()
        if not received:
            raise OnboardingError(
                "Google OAuth callback timed out before this WSL process received the browser redirect; "
                "no credentials were changed"
            )
        if received.get("oauth_error"):
            raise OnboardingError(
                "Google OAuth consent returned error="
                + str(received["oauth_error"])
                + "; no credentials were changed"
            )
        try:
            response = json_request("https://oauth2.googleapis.com/token", data=urlencode({
                "client_id": client["client_id"], "client_secret": client["client_secret"],
                "grant_type": "authorization_code", "code": received["code"], "redirect_uri": redirect,
                "code_verifier": verifier}), headers={"Content-Type": "application/x-www-form-urlencoded"})
        except ValueError as exc:
            detail = str(exc)
            safe_detail = detail if detail.startswith("HTTPS request returned status ") else type(exc).__name__
            raise OnboardingError(
                "Google OAuth callback arrived, but token exchange failed ("
                + safe_detail
                + "); no credentials were changed"
            ) from exc
        except OSError as exc:
            raise OnboardingError(
                "Google OAuth callback arrived, but token exchange transport failed ("
                + type(exc).__name__
                + "); no credentials were changed"
            ) from exc
        refresh = response.get("refresh_token")
        if not isinstance(refresh, str) or not refresh:
            raise OnboardingError(
                "Google OAuth token exchange succeeded but did not return a refresh token; "
                "no credentials were changed"
            )
        write_private_json(credential_path(), {
            "type": "authorized_user",
            "client_id": client["client_id"],
            "client_secret": client["client_secret"],
            "refresh_token": refresh,
            "scopes": scopes,
        })
    return {
        "result": "google_drive_authorized",
        "scopes": scopes,
        "document_write": DOCUMENT_WRITE_SCOPE in scopes,
        "publishing_authority_changed": False,
    }
