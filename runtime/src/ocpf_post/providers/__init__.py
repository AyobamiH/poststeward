from __future__ import annotations

from typing import Any

from ocpf_post.providers.linkedin import LinkedInProvider
from ocpf_post.providers.threads import ThreadsProvider
from ocpf_post.providers.x import XProvider


class _ReadbackBoundary:
    """Consequence-safe provider proxy used by scheduler account routing.

    Once publish() has returned a durable provider id, readback failure cannot
    safely become resend authority. Any ordinary exception during verify_post()
    therefore degrades to False so the scheduler persists published_unverified.
    KeyboardInterrupt/SystemExit still propagate because they are not Exception.
    """

    def __init__(self, provider: Any) -> None:
        self._provider = provider

    def __getattr__(self, name: str) -> Any:
        return getattr(self._provider, name)

    def verify_post(self, post_id: str, expected_text: str) -> bool:
        try:
            return bool(self._provider.verify_post(post_id, expected_text))
        except Exception:
            return False


def get_provider(name: str, **kwargs):
    normalized = name.strip().lower()
    if normalized == "x":
        return XProvider(**kwargs)
    if normalized == "threads":
        return ThreadsProvider(**kwargs)
    if normalized == "linkedin":
        return LinkedInProvider(**kwargs)
    raise ValueError(f"Unsupported provider: {name}")


def get_extended_provider(name: str):
    return get_provider(name)


def for_account(name, account_id, *, factory=None, require_enabled=True, registered_identity=False, **kwargs):
    """Route one exact account identity to its credential authority.

    Canonical founder/operator identities use the provider-default credential.
    Registered additional identities use their scoped credential directory.
    Unknown identities never fall back to founder credentials.
    """
    from ocpf_post.account_profiles import profile, unavailable, directory
    from ocpf_post.registry import resolve_provider_default_identity

    normalized = str(name).strip().lower()
    identity = str(account_id or "").strip()
    if not identity:
        raise ValueError("Exact provider account identity is required")

    row = profile(normalized, identity)
    injected = factory is not None and factory not in (get_provider, get_extended_provider)
    if row:
        if require_enabled and unavailable(normalized, identity):
            raise ValueError("Additional account is inactive or disconnected")
        kwargs["credential_dir"] = directory(normalized, identity)
        if normalized == "linkedin":
            kwargs["actor_urn"] = identity
    elif injected:
        from ocpf_post.registry import provider_identity_registered
        if not registered_identity and not provider_identity_registered(normalized, identity):
            raise ValueError("Injected provider identity is not registered")
    else:
        founder = resolve_provider_default_identity(normalized)
        if not founder or founder["account_id"] != identity:
            raise ValueError(
                "Account identity is neither the canonical provider default nor a registered additional account"
            )

    if injected:
        provider = factory(normalized)
    else:
        provider = get_provider(normalized, **kwargs)
    return _ReadbackBoundary(provider)


def for_campaign(name, campaign, *, factory=None, **kwargs):
    from ocpf_post.campaigns import destination_binding
    binding = destination_binding(campaign, name)
    if not binding:
        raise ValueError(f"No destination account is bound for {campaign}/{name}")
    return for_account(name, binding["account_id"], factory=factory, registered_identity=True, **kwargs)
