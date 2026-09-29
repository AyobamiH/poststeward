"""PostSteward-native Fresh onboarding over hosted provider authority.

This is the convergence layer between the embedded A-K local runtime and PostSteward
Cloud. Provider credentials stay hosted. The local runtime imports only stable,
verified provider identities and reviewed local campaign intent.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import sys
import tempfile
from typing import Any

from ocpf_post.campaigns import normalize_campaign_id
from ocpf_post.onboarding import (
    TEXT_LIMITS,
    import_campaign,
    import_project,
    validate_project,
)
from ocpf_post.poststeward_cloud import CloudError, bindings, relay
from ocpf_post.setup_engine import SetupEngine, SetupEngineError
from ocpf_post.setup_store import SetupStoreError


SCHEMA_VERSION = 1
PROVIDERS = ("x", "threads", "linkedin")


class PostStewardOnboardingError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def _fail(code: str, message: str) -> None:
    raise PostStewardOnboardingError(code, message)


def _project_slug(value: str) -> str:
    slug = str(value or "").strip().lower()
    if not re.fullmatch(r"[a-z][a-z0-9]*(?:-[a-z0-9]+)*", slug):
        _fail("poststeward.onboarding.project_invalid", "Project must be a lowercase hyphenated identifier.")
    return slug


def _prefix(project: str) -> str:
    compact = re.sub(r"[^A-Z0-9]", "", project.upper())
    if not compact:
        _fail("poststeward.onboarding.project_invalid", "Project cannot produce a campaign prefix.")
    return compact[:29] + "-"


def _local_alias(provider: str, cloud_alias: str, used: set[str]) -> str:
    raw = re.sub(r"[^a-z0-9-]+", "-", str(cloud_alias).strip().lower().replace("_", "-"))
    raw = re.sub(r"-+", "-", raw).strip("-")
    if not raw or not raw[0].isalpha():
        raw = f"{provider}-{raw or 'account'}"
    raw = raw[:64].rstrip("-")
    candidate = raw
    suffix = 2
    while candidate in used:
        tail = f"-{suffix}"
        candidate = raw[: 64 - len(tail)].rstrip("-") + tail
        suffix += 1
    used.add(candidate)
    return candidate


def _text(value: str) -> str:
    result = str(value or "")
    if not result or result != result.strip():
        _fail("poststeward.onboarding.text_invalid", "Reviewed campaign text must be non-empty and trimmed.")
    if any((ord(char) < 32 and char not in "\n\t") or ord(char) == 127 for char in result):
        _fail("poststeward.onboarding.text_invalid", "Reviewed text contains unsupported control characters.")
    return result


def _selected_account(
    accounts: list[dict[str, Any]],
    provider: str,
    requested: str | None,
) -> dict[str, Any] | None:
    candidates = [row for row in accounts if row.get("provider") == provider and row.get("active") is True]
    if requested:
        matches = [row for row in candidates if row.get("alias") == requested]
        if len(matches) != 1:
            _fail(
                "poststeward.onboarding.account_not_found",
                f"Connected {provider} alias {requested!r} was not found.",
            )
        return matches[0]
    if not candidates:
        return None
    if len(candidates) > 1:
        aliases = ", ".join(sorted(str(row.get("alias")) for row in candidates))
        _fail(
            "poststeward.onboarding.account_selection_required",
            f"Multiple {provider} destinations are connected ({aliases}); choose --{provider}-alias explicitly.",
        )
    return candidates[0]


def _identity(row: dict[str, Any]) -> str:
    identity = row.get("identity") if isinstance(row.get("identity"), dict) else {}
    value = str(identity.get("id") or "").strip()
    if not value:
        _fail("poststeward.onboarding.identity_missing", "Hosted provider binding has no stable identity.")
    return value


def _cloud_readiness(row: dict[str, Any]) -> dict[str, Any]:
    provider = str(row["provider"])
    expected = _identity(row)
    try:
        observed = relay({"action": "account", "provider": provider, "accountId": expected})
    except CloudError as exc:
        _fail(
            "poststeward.onboarding.provider_readback_failed",
            f"{provider} identity readback failed: {exc}",
        )
    account = observed.get("account") if isinstance(observed.get("account"), dict) else {}
    identity = account.get("identity") if isinstance(account.get("identity"), dict) else {}
    observed_id = str(identity.get("id") or "")
    capabilities = row.get("capabilities") if isinstance(row.get("capabilities"), dict) else {}
    oauth = capabilities.get("oauth") is True
    blockers: list[str] = []
    if observed_id != expected:
        blockers.append("provider.identity.mismatch")
    if not oauth:
        blockers.append("provider.oauth.hosted_connection_required")
    return {
        "schema_version": 1,
        "provider": provider,
        "expected_identity": expected,
        "observed_identity": observed_id or None,
        "identity_match": "match" if observed_id == expected else "mismatch",
        "credential_origin": "poststeward_cloud",
        "write_scope_state": "granted" if oauth else "unproven",
        "readback_scope_state": "hosted_capability",
        "ready_for_write_configuration": not blockers,
        "blocking_reasons": blockers,
        "provider_consequence_attempted": False,
        "boundary": (
            "Read-only stable-identity verification through the PostSteward provider relay. "
            "Provider credentials remain in PostSteward Cloud and no publication is attempted."
        ),
    }


def _json_file(root: Path, name: str, value: dict[str, Any]) -> Path:
    path = root / name
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    path.chmod(0o600)
    return path


def _setup(
    *,
    operator_label: str,
    timezone: str | None,
    pace: str | None,
    daily_originals: int | None,
    machine_label: str | None,
    workspace: str | Path | None,
) -> tuple[SetupEngine, dict[str, Any]]:
    engine = SetupEngine(workspace)
    try:
        current = engine.status()
    except (SetupEngineError, SetupStoreError):
        if timezone is None or pace is None:
            _fail(
                "poststeward.onboarding.setup_configuration_required",
                "First configuration requires --timezone and --pace.",
            )
        current = engine.start(
            "fresh",
            operator_label=operator_label,
            timezone=timezone,
            pace=pace,
            daily_originals=daily_originals,
            machine_label=machine_label,
        )
    if current.get("mode") != "fresh":
        _fail("poststeward.onboarding.fresh_required", "PostSteward configure requires a Fresh setup operation.")
    stage = current.get("session", {}).get("stage")
    if stage not in {"configuration_ready", "verification_ready"}:
        _fail(
            "poststeward.onboarding.setup_stage_invalid",
            f"Fresh setup must be configuration_ready; observed {stage}.",
        )
    return engine, current


def plan(
    *,
    project_id: str,
    label: str,
    campaign_text: str,
    operator_label: str | None = None,
    timezone: str | None = None,
    pace: str | None = None,
    daily_originals: int | None = None,
    machine_label: str | None = None,
    campaign_id: str | None = None,
    source_id: str = "poststeward-owner-approved",
    x_alias: str | None = None,
    threads_alias: str | None = None,
    linkedin_alias: str | None = None,
    workspace: str | Path | None = None,
) -> dict[str, Any]:
    project = _project_slug(project_id)
    display = str(label or "").strip()
    if not display or len(display) > 120:
        _fail("poststeward.onboarding.label_invalid", "Project label must be 1-120 characters.")
    reviewed = _text(campaign_text)
    engine, setup = _setup(
        operator_label=str(operator_label or display),
        timezone=timezone,
        pace=pace,
        daily_originals=daily_originals,
        machine_label=machine_label,
        workspace=workspace,
    )
    cloud = bindings()
    cloud_accounts = cloud.get("accounts") if isinstance(cloud.get("accounts"), list) else []
    requested = {"x": x_alias, "threads": threads_alias, "linkedin": linkedin_alias}
    selected = [
        row
        for provider in PROVIDERS
        if (row := _selected_account(cloud_accounts, provider, requested[provider])) is not None
    ]
    if not selected:
        _fail(
            "poststeward.onboarding.provider_connection_required",
            "Connect at least one X, Threads or LinkedIn destination in the PostSteward owner workspace.",
        )

    readiness = [_cloud_readiness(row) for row in selected]
    blockers = sorted(
        {
            code
            for row in readiness
            for code in row.get("blocking_reasons", [])
            if code
        }
    )
    if blockers:
        _fail(
            "poststeward.onboarding.provider_authority_blocked",
            "One or more selected provider connections are not ready: " + ", ".join(blockers),
        )

    used: set[str] = set()
    account_rows: dict[str, dict[str, Any]] = {}
    defaults: dict[str, str] = {}
    destinations: dict[str, str] = {}
    texts: dict[str, str] = {}
    selection: list[dict[str, Any]] = []
    for row in selected:
        provider = str(row["provider"])
        alias = _local_alias(provider, str(row.get("alias") or provider), used)
        identity = _identity(row)
        username = str((row.get("identity") or {}).get("username") or "").strip()
        account_rows[alias] = {
            "provider": provider,
            "account_id": identity,
            "label": username or str(row.get("alias") or provider),
        }
        defaults[provider] = alias
        destinations[provider] = alias
        limit = int(TEXT_LIMITS[provider])
        if len(reviewed) > limit:
            _fail(
                "poststeward.onboarding.text_too_long",
                f"Reviewed text exceeds the {provider} whole-publication limit of {limit} characters.",
            )
        texts[provider] = reviewed
        selection.append(
            {
                "provider": provider,
                "cloud_alias": row.get("alias"),
                "local_alias": alias,
                "account_id": identity,
                "binding_version": row.get("version"),
            }
        )

    prefix = _prefix(project)
    cid = str(campaign_id or f"{prefix}001").strip().upper()
    try:
        if normalize_campaign_id(cid) != cid:
            raise ValueError("not canonical")
    except ValueError:
        _fail("poststeward.onboarding.campaign_invalid", "Campaign ID must use uppercase letters, numbers and hyphens.")
    if not cid.startswith(prefix):
        _fail(
            "poststeward.onboarding.campaign_prefix_mismatch",
            f"Campaign ID must start with {prefix}.",
        )
    source = str(source_id or "").strip()
    if not source or len(source) > 200:
        _fail("poststeward.onboarding.source_invalid", "Source ID must be 1-200 characters.")

    project_value = {
        "schema_version": 1,
        "project": project,
        "label": display,
        "campaign_prefixes": [prefix],
        "accounts": account_rows,
        "default_accounts": defaults,
    }
    validate_project(project_value)
    campaign_value = {
        "schema_version": 1,
        "campaign": cid,
        "project": project,
        "title": f"{display} reviewed PostSteward campaign",
        "status": "COPY-READY",
        "destinations": destinations,
        "texts": texts,
        "source": {"type": "owner_approved", "source_id": source},
    }
    expected = [
        {"provider": row["provider"], "account_id": row["expected_identity"]}
        for row in readiness
    ]
    review_body = {
        "schema_version": SCHEMA_VERSION,
        "workspace": cloud.get("workspace"),
        "cloud_installation_id": cloud.get("installationId"),
        "setup_session_id": setup.get("session", {}).get("session_id"),
        "setup_revision": setup.get("session", {}).get("revision"),
        "project": project_value,
        "campaign": campaign_value,
        "selected_accounts": selection,
        "provider_readiness": readiness,
        "expected_identities": expected,
        "campaign_allocation_enabled": False,
        "publishing_authority": False,
        "automation_enabled": False,
        "provider_consequence_attempted": False,
    }
    digest = hashlib.sha256(
        json.dumps(review_body, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    ).hexdigest()
    return {
        **review_body,
        "status": "preview",
        "review_sha256": digest,
        "next_action": "Re-run poststeward configure with --apply --expected-sha256 <review_sha256>.",
        "boundary": (
            "Preview binds exact hosted identities to one local project and manual-only campaign. "
            "Provider identity reads are read-only; no campaign allocation, schedule, provider write or automation activation occurs."
        ),
    }


def apply(*, expected_sha256: str, **kwargs: Any) -> dict[str, Any]:
    preview = plan(**kwargs)
    if preview["review_sha256"] != str(expected_sha256 or ""):
        _fail("poststeward.onboarding.review_changed", "Onboarding review changed; preview again.")
    engine = SetupEngine(kwargs.get("workspace"))
    with tempfile.TemporaryDirectory(prefix="poststeward-onboard-") as temp:
        root = Path(temp)
        project_file = _json_file(root, "project.json", preview["project"])
        project_preview = import_project(project_file)
        project_result = import_project(
            project_file,
            apply=True,
            expected_sha256=project_preview["input_sha256"],
        )
        campaign_file = _json_file(root, "campaign.json", preview["campaign"])
        campaign_preview = import_campaign(campaign_file, allocate=False)
        campaign_result = import_campaign(
            campaign_file,
            apply=True,
            expected_sha256=campaign_preview["input_sha256"],
            allocate=False,
        )
    verified = engine.verify_fresh(
        preview["provider_readiness"],
        preview["expected_identities"],
        session_id=preview["setup_session_id"],
    )
    return {
        **preview,
        "status": "verification_ready",
        "project_import": project_result,
        "campaign_import": campaign_result,
        "setup": verified,
        "publishing_authority": bool(verified.get("publishing_authority")),
        "automation_enabled": bool(verified.get("automation_enabled")),
        "next_actions": [
            f"poststeward campaign show --campaign {preview['campaign']['campaign']} --provider <provider>",
            "Review executor authority in the PostSteward owner workspace before local activation.",
            "poststeward setup activate ...  # preview/review/apply; cloud executor fence is mandatory",
        ],
    }


def _campaign_text(args: argparse.Namespace) -> str:
    if args.text is not None:
        return str(args.text)
    if args.text_file:
        path = Path(args.text_file).expanduser()
        if not path.is_file() or path.is_symlink():
            _fail("poststeward.onboarding.text_file_invalid", "Text file must be a regular file.")
        return path.read_text(encoding="utf-8").rstrip("\n")
    if args.stdin:
        return sys.stdin.read().rstrip("\n")
    _fail("poststeward.onboarding.text_required", "Supply --text, --text-file or --stdin.")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="poststeward configure",
        description="Bind hosted X/Threads/LinkedIn identities to one Fresh local PostSteward runtime.",
    )
    parser.add_argument("--workspace")
    parser.add_argument("--project", required=True)
    parser.add_argument("--label", required=True)
    parser.add_argument("--operator-label")
    parser.add_argument("--timezone")
    parser.add_argument("--pace", choices=["occasional", "regular", "active", "high", "custom"])
    parser.add_argument("--daily-originals", type=int)
    parser.add_argument("--machine-label")
    parser.add_argument("--campaign-id")
    parser.add_argument("--source-id", default="poststeward-owner-approved")
    parser.add_argument("--x-alias")
    parser.add_argument("--threads-alias")
    parser.add_argument("--linkedin-alias")
    text_source = parser.add_mutually_exclusive_group(required=True)
    text_source.add_argument("--text")
    text_source.add_argument("--text-file")
    text_source.add_argument("--stdin", action="store_true")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--expected-sha256")
    parser.add_argument("--json", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    kwargs = {
        "project_id": args.project,
        "label": args.label,
        "campaign_text": _campaign_text(args),
        "operator_label": args.operator_label,
        "timezone": args.timezone,
        "pace": args.pace,
        "daily_originals": args.daily_originals,
        "machine_label": args.machine_label,
        "campaign_id": args.campaign_id,
        "source_id": args.source_id,
        "x_alias": args.x_alias,
        "threads_alias": args.threads_alias,
        "linkedin_alias": args.linkedin_alias,
        "workspace": args.workspace,
    }
    try:
        value = (
            apply(expected_sha256=str(args.expected_sha256 or ""), **kwargs)
            if args.apply
            else plan(**kwargs)
        )
    except (PostStewardOnboardingError, CloudError, SetupEngineError, SetupStoreError, OSError) as exc:
        code = getattr(exc, "code", "POSTSTEWARD_ONBOARDING_BLOCKED")
        if args.json:
            print(json.dumps({"schema_version": 1, "status": "blocked", "code": code, "message": str(exc)}))
        else:
            print(f"PostSteward configure blocked [{code}]: {exc}", file=sys.stderr)
        return 3
    if args.json:
        print(json.dumps(value, indent=2, ensure_ascii=False))
    else:
        print("PostSteward Fresh configuration")
        print(f"Status: {value['status']}")
        print(f"Workspace: {value.get('workspace')}")
        print(f"Campaign: {value['campaign']['campaign']} (manual-only)")
        print("Destinations: " + ", ".join(row["provider"] for row in value["selected_accounts"]))
        print(f"Publishing authority: {'YES' if value.get('publishing_authority') else 'NO'}")
        print(f"Automation enabled: {'YES' if value.get('automation_enabled') else 'NO'}")
        if value.get("review_sha256"):
            print(f"Review SHA-256: {value['review_sha256']}")
        for action in value.get("next_actions", []):
            print(f"Next: {action}")
        if value.get("next_action"):
            print(f"Next: {value['next_action']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
