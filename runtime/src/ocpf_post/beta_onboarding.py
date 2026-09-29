"""Private-beta onboarding helpers for the standalone Post-Once product.

These helpers deliberately stop before unattended activation. They make the first
fresh-user path smaller without weakening the existing SetupEngine or provider
consequence boundaries:

    setup -> connect X -> bind one user project/campaign -> verify-fresh

No helper in this module schedules, publishes, enables a runtime source, opts a
campaign into allocation, or activates systemd automation.
"""
from __future__ import annotations

from getpass import getpass
import json
import os
from pathlib import Path
import re
import tempfile
from typing import Any, Callable

from ocpf_post.campaigns import normalize_campaign_id
from ocpf_post.model import AccountIdentity
from ocpf_post.onboarding import (
    TEXT_LIMITS,
    import_campaign,
    import_project,
    validate_project,
)
from ocpf_post.provider_readiness import observe_x
from ocpf_post.providers import get_provider
from ocpf_post.providers.base import ProviderRejected, ProviderUnavailable
from ocpf_post.providers.x import XProvider
from ocpf_post.setup_engine import SetupEngine, SetupEngineError
from ocpf_post.setup_store import SetupStoreError
from ocpf_post.state import config_dir, ensure_private_dir


SCHEMA_VERSION = 1
DEFAULT_X_REDIRECT_URI = "http://127.0.0.1:8765/callback"


class BetaOnboardingError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def _fail(code: str, message: str) -> None:
    raise BetaOnboardingError(code, message)


def _write_private_text(path: Path, value: str) -> None:
    ensure_private_dir(path.parent)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    os.fchmod(fd, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        handle.write(value)
        if not value.endswith("\n"):
            handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())


def _stored_x_secret() -> str:
    path = config_dir() / "x-client-secret"
    if not path.is_file() or path.is_symlink():
        return ""
    try:
        return path.read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def connect_x(
    *,
    client_id: str,
    redirect_uri: str = DEFAULT_X_REDIRECT_URI,
    client_secret: str | None = None,
    persist_client_secret: bool = True,
    provider_factory: Callable[..., Any] = XProvider,
) -> dict[str, Any]:
    """Run X OAuth and persist a confidential-client secret only after success."""
    identifier = str(client_id or "").strip()
    callback = str(redirect_uri or "").strip()
    if not identifier:
        _fail("beta.onboarding.x_client_id_required", "X OAuth client ID is required")
    if not callback:
        _fail("beta.onboarding.x_redirect_required", "X OAuth redirect URI is required")

    explicit_secret = str(client_secret or "").strip()
    secret = explicit_secret or str(os.environ.get("X_CLIENT_SECRET") or "").strip() or _stored_x_secret()

    provider = provider_factory(
        client_id=identifier,
        client_secret=secret or None,
        redirect_uri=callback,
    )
    try:
        account = provider.authorize()
        observed = provider.readonly_account()
    except (ProviderRejected, ProviderUnavailable) as exc:
        _fail("beta.onboarding.x_authorization_failed", str(exc))

    if observed.account_id != account.account_id:
        _fail(
            "beta.onboarding.x_identity_drift",
            "X identity changed between authorization and read-only verification",
        )

    if secret and persist_client_secret:
        _write_private_text(config_dir() / "x-client-secret", secret)

    return {
        "schema_version": SCHEMA_VERSION,
        "status": "connected",
        "provider": "x",
        "account_id": observed.account_id,
        "display": observed.display,
        "redirect_uri": callback,
        "client_secret_persisted": bool(secret and persist_client_secret),
        "publishing_authority": False,
        "automation_enabled": False,
        "provider_consequence_attempted": False,
        "next_action": "post-once setup onboard-x",
        "boundary": (
            "OAuth authorization plus read-only identity verification only. "
            "No post, reply, schedule, source activation, campaign allocation or "
            "unattended-automation activation was attempted."
        ),
    }


def prompt_and_connect_x(
    *,
    client_id: str,
    redirect_uri: str = DEFAULT_X_REDIRECT_URI,
    prompt_secret: bool = False,
    client_secret_file: str | None = None,
) -> dict[str, Any]:
    if prompt_secret and client_secret_file:
        _fail(
            "beta.onboarding.x_secret_input_conflict",
            "Use either --prompt-client-secret or --client-secret-file, not both",
        )
    secret: str | None = None
    if prompt_secret:
        secret = getpass("X OAuth 2.0 Client Secret (hidden; blank for public client): ").strip()
    elif client_secret_file:
        path = Path(client_secret_file).expanduser()
        if not path.is_file() or path.is_symlink():
            _fail("beta.onboarding.x_secret_file_invalid", "X client-secret file must be a regular file")
        try:
            secret = path.read_text(encoding="utf-8").strip()
        except OSError as exc:
            raise BetaOnboardingError(
                "beta.onboarding.x_secret_file_unreadable",
                "Could not read the X client-secret file",
            ) from exc
        if not secret:
            _fail("beta.onboarding.x_secret_file_empty", "X client-secret file is empty")
    return connect_x(
        client_id=client_id,
        redirect_uri=redirect_uri,
        client_secret=secret,
    )


def _derived_prefix(project_id: str) -> str:
    compact = re.sub(r"[^A-Za-z0-9]", "", project_id).upper()
    if not compact:
        _fail("beta.onboarding.project_invalid", "Project ID cannot produce a campaign prefix")
    return compact[:29] + "-"


def _campaign_text(text: str) -> str:
    value = str(text or "")
    if not value or value != value.strip():
        _fail("beta.onboarding.campaign_text_invalid", "Campaign text must be non-empty and trimmed")
    if len(value) > TEXT_LIMITS["x"]:
        _fail(
            "beta.onboarding.campaign_text_too_long",
            f"X onboarding text must be at most {TEXT_LIMITS['x']} characters",
        )
    if any((ord(char) < 32 and char not in "\n\t") or ord(char) == 127 for char in value):
        _fail("beta.onboarding.campaign_text_invalid", "Campaign text contains unsupported control characters")
    return value


def _inputs(
    *,
    project_id: str,
    project_label: str,
    account: AccountIdentity,
    campaign_id: str | None,
    campaign_text: str,
    source_id: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    project_name = str(project_id or "").strip()
    label = str(project_label or "").strip()
    source = str(source_id or "").strip()
    prefix = _derived_prefix(project_name)
    campaign = str(campaign_id or f"{prefix}001").strip().upper()
    try:
        normalized_campaign = normalize_campaign_id(campaign)
    except ValueError as exc:
        raise BetaOnboardingError("beta.onboarding.campaign_id_invalid", str(exc)) from exc
    if normalized_campaign != campaign:
        _fail("beta.onboarding.campaign_id_invalid", "Campaign ID must already be uppercase")
    text = _campaign_text(campaign_text)

    project = {
        "schema_version": 1,
        "project": project_name,
        "label": label,
        "campaign_prefixes": [prefix],
        "accounts": {
            "x-owner": {
                "provider": "x",
                "account_id": account.account_id,
                "label": account.display,
            }
        },
        "default_accounts": {"x": "x-owner"},
    }
    validate_project(project)

    if not campaign.startswith(prefix):
        _fail(
            "beta.onboarding.campaign_prefix_mismatch",
            f"Campaign ID must start with generated project prefix {prefix}",
        )
    if not source or len(source) > 200:
        _fail("beta.onboarding.source_id_invalid", "Source ID must be 1-200 characters")
    if any(ord(char) < 32 or ord(char) == 127 for char in source):
        _fail("beta.onboarding.source_id_invalid", "Source ID contains unsupported control characters")

    campaign_value = {
        "schema_version": 1,
        "campaign": campaign,
        "project": project_name,
        "title": f"{label} beta onboarding campaign",
        "status": "COPY-READY",
        "destinations": {"x": "x-owner"},
        "texts": {"x": text},
        "source": {"type": "owner_approved", "source_id": source},
    }
    return project, campaign_value


def _json_file(root: Path, name: str, value: dict[str, Any]) -> Path:
    path = root / name
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    path.chmod(0o600)
    return path


def onboard_x(
    *,
    project_id: str,
    project_label: str,
    campaign_text: str,
    campaign_id: str | None = None,
    source_id: str = "beta-onboarding",
    session_id: str | None = None,
    workspace: str | Path | None = None,
    apply: bool = False,
    provider: Any | None = None,
) -> dict[str, Any]:
    """Bind one X identity/project/manual-only campaign and verify Fresh readiness."""
    engine = SetupEngine(workspace)
    try:
        setup = engine.status(session_id)
    except (SetupEngineError, SetupStoreError) as exc:
        _fail(
            "beta.onboarding.fresh_setup_required",
            f"Fresh setup is required first: {exc}",
        )

    if setup.get("mode") != "fresh":
        _fail("beta.onboarding.fresh_setup_required", "X beta onboarding requires a Fresh setup session")

    stage = setup.get("session", {}).get("stage")
    if stage == "verification_ready":
        return {
            "schema_version": SCHEMA_VERSION,
            "status": "already_verified",
            "provider": "x",
            "setup": setup,
            "publishing_authority": bool(setup.get("publishing_authority")),
            "automation_enabled": bool(setup.get("automation_enabled")),
            "provider_consequence_attempted": False,
            "next_actions": [
                "post-once publish --provider x --campaign <campaign>  # dry run",
                "post-once setup activate ...                         # optional unattended automation",
            ],
        }
    if stage != "configuration_ready":
        _fail(
            "beta.onboarding.setup_stage_invalid",
            f"Fresh X onboarding requires configuration_ready; observed {stage}",
        )

    x = provider or get_provider("x")
    try:
        account = x.readonly_account()
        readiness = observe_x(x, expected_identity=account.account_id)
    except (ProviderRejected, ProviderUnavailable) as exc:
        _fail(
            "beta.onboarding.x_not_connected",
            f"Connect X first with 'post-once setup connect-x': {exc}",
        )

    if readiness.get("ready_for_write_configuration") is not True:
        reasons = ", ".join(str(item) for item in readiness.get("blocking_reasons", [])) or "unknown"
        _fail(
            "beta.onboarding.x_readiness_blocked",
            f"X is connected but not ready for configured writes: {reasons}",
        )

    project, campaign = _inputs(
        project_id=project_id,
        project_label=project_label,
        account=account,
        campaign_id=campaign_id,
        campaign_text=campaign_text,
        source_id=source_id,
    )

    plan = {
        "schema_version": SCHEMA_VERSION,
        "status": "preview" if not apply else "applying",
        "provider": "x",
        "account_id": account.account_id,
        "display": account.display,
        "project": project,
        "campaign": campaign,
        "campaign_allocation_enabled": False,
        "provider_ready_for_write_configuration": True,
        "publishing_authority": False,
        "automation_enabled": False,
        "provider_consequence_attempted": False,
        "boundary": (
            "Local project/campaign registration plus read-only Fresh verification. "
            "The campaign remains manual-only; no provider write, schedule, source activation "
            "or unattended automation is authorized by this command."
        ),
    }
    if not apply:
        return {
            **plan,
            "next_action": "Re-run with --apply after reviewing this exact local-only onboarding plan.",
        }

    with tempfile.TemporaryDirectory(prefix="post-once-beta-onboard-") as temp:
        root = Path(temp)
        project_file = _json_file(root, "project.json", project)
        project_preview = import_project(project_file)
        project_result = import_project(
            project_file,
            apply=True,
            expected_sha256=project_preview["input_sha256"],
        )

        campaign_file = _json_file(root, "campaign.json", campaign)
        campaign_preview = import_campaign(campaign_file, allocate=False)
        campaign_result = import_campaign(
            campaign_file,
            apply=True,
            expected_sha256=campaign_preview["input_sha256"],
            allocate=False,
        )

    verified = engine.verify_fresh(
        [readiness],
        [{"provider": "x", "account_id": account.account_id}],
        session_id=session_id,
    )
    campaign_name = campaign["campaign"]
    return {
        **plan,
        "status": "verification_ready",
        "project_import": project_result,
        "campaign_import": campaign_result,
        "setup": verified,
        "publishing_authority": bool(verified.get("publishing_authority")),
        "automation_enabled": bool(verified.get("automation_enabled")),
        "next_actions": [
            f"post-once publish --provider x --campaign {campaign_name}",
            f"post-once publish --provider x --campaign {campaign_name} --live",
            "post-once setup activate ...  # optional unattended automation; preview/review/apply",
        ],
    }
