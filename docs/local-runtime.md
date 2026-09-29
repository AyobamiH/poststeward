# PostSteward Local

PostSteward now has two deliberately separate execution models under one product
brand:

1. **Hosted PostSteward** — Cloudflare-hosted workspace, agent grants, provider
   connections, HTTP/MCP operations and hosted receipts.
2. **PostSteward Local** — an installable local publishing runtime powered by the
   independent Post-Once lineage.

They share the product promise — owner-governed, agent-operable publishing with
durable evidence — but they do not share state or silently transfer provider
authority.

## Installation model

The public installation surface follows the useful OpenClaw pattern:

```bash
curl -fsSL --proto '=https' --tlsv1.2 https://poststeward.com/install.sh | bash
```

Useful bounded forms:

```bash
# Install without launching onboarding.
curl -fsSL --proto '=https' --tlsv1.2 https://poststeward.com/install.sh |
  bash -s -- --no-onboard

# Verify command/discovery after installation.
curl -fsSL --proto '=https' --tlsv1.2 https://poststeward.com/install.sh |
  bash -s -- --no-onboard --verify

# Preview the resolved runtime without changing files.
curl -fsSL --proto '=https' --tlsv1.2 https://poststeward.com/install.sh |
  bash -s -- --dry-run
```

The installer:

- supports Linux/WSL2 with Python >=3.10 and Git;
- resolves the public `post-once-runtime-beta` distribution branch to one exact
  Git commit before installation;
- checks out that exact commit detached;
- installs `poststeward` and `post-once` user shims;
- records a private non-secret install receipt;
- optionally verifies the installed discovery surface;
- launches Fresh onboarding by default only when an interactive TTY exists;
- never connects a provider, schedules, publishes or activates unattended
  automation merely because installation succeeded.

## Runtime distribution

The implementation repository for the local runtime remains
`AyobamiH/post-once-bootstrap`. A reviewed release snapshot is mirrored to the
public `post-once-runtime-beta` branch of `AyobamiH/poststeward` for distribution.

The mirror is a release artifact, not a second mutable development branch. A new
runtime snapshot must be copied only after its exact source revision has passed the
standalone repository's review/CI gates.

This keeps the public curl path independent of private-repository invitations while
retaining exact-revision installation and rollback evidence.

## Provider onboarding

Fresh local onboarding supports X, Threads and LinkedIn.

```text
install
-> Fresh local setup
-> connect one or more providers
-> exact provider identity/write-readiness verification
-> one user-owned project + manual-only campaign
-> verification_ready
-> dry-run
-> optional explicit --live provider effect
-> optional activation preview/review/apply for unattended automation
```

Provider OAuth is a human authority step. After authority exists, agents can use the
machine-readable CLI surfaces. An agent must not infer provider or publishing
authority from installation.

### X

X uses Authorization Code + PKCE with a localhost callback.

### Threads

Threads uses Authorization Code, exchanges the short-lived credential for a
long-lived token, preserves required scope evidence and performs read-only account
and publishing-readiness observation before Fresh verification.

### LinkedIn

LinkedIn supports member and Page actors. Member onboarding requests
`openid profile w_member_social` plus optional member readback when approved.
Page onboarding requests `w_organization_social r_organization_social` for the
exact organization/organizationBrand actor.

LinkedIn Page publishing remains constrained by LinkedIn application approval and
the signed-in member's Page role. The local runtime cannot manufacture provider
permissions.

## Human and agent boundary

The local runtime is designed as **human-governed, agent-operable** software:

- humans establish identity, consent, provider credentials and activation authority;
- agents inspect capabilities/state, submit reviewed content, schedule or publish
  only through granted boundaries;
- durable receipts and ambiguous-effect states block blind repeat writes;
- activation and deactivation remain reviewed authority transactions.

## Relationship to hosted PostSteward

Hosted and local PostSteward may eventually share a higher-level account/operation
experience, but this distribution step does not copy hosted provider tokens into
local storage or vice versa.

The hosted product remains the remote HTTP/MCP surface. PostSteward Local provides a
machine-local execution surface for humans and agents that want local-first operation.
