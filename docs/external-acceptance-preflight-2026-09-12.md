# External acceptance preflight receipt — 12 September 2026

This is a non-secret receipt for the live staging prerequisite check that followed PR #34. It does not claim completion of any destructive or owner-consent acceptance gate.

## Repository and workflow evidence

- PR #34 merged as `e63f4422bd2aaf10567a785f9bf45eb3081f9003`.
- PR verification run `34678571549` completed successfully.
- Post-merge main verification run `34678656280` completed successfully.
- Protected staging preflight run `34678656296`, job `103512996362`, executed against the GitHub `staging` environment.
- The preflight emits only public values and secret-presence booleans. It does not emit protected values.

## Observed protected staging state

The run at `2026-09-12T06:38:39.280Z` observed:

| Input | Observation |
| --- | --- |
| Effective origin | `https://poststeward-staging.woeinvests.workers.dev` |
| GitHub App client ID | absent |
| GitHub App slug | absent |
| GitHub App client secret | absent |
| Current encryption root | present |
| Next encryption root | absent |
| Stripe sandbox enabled variable | absent/false |
| Stripe sandbox Price variable | absent |
| Stripe sandbox runtime key | absent |
| Stripe sandbox webhook secret | absent |
| Active PostSteward staging Portal configuration | not created by the run because no protected setup key was available |

The Stripe account was also checked separately in test mode. The existing PostSteward USD 5 monthly Price remains active, while active Billing Portal configuration inventory was empty before the preflight work. No webhook was created because the available tooling cannot capture its one-time signing secret and place it into the protected staging environment in the same operation.

## Consequence for the six remaining gates

### Private GitHub

The application flow is implemented, but a real staging GitHub App cannot be granted until the exact staging App registration and protected client secret are present. Owner installation/consent is still separate evidence and must use selected repositories only with read-only Contents/Metadata.

### PITR

The deployed recovery state machine uses the Durable Object PITR storage APIs. Real restore remains an owner-only, state-changing acceptance operation against a disposable staging workspace. CI/non-destructive workflows must not manufacture that evidence.

### Disposable workspace erasure

The owner lifecycle path and tombstone/cleanup implementation are present. Acceptance still requires a deliberately disposable owner workspace; the primary owner acceptance workspace must not be erased to satisfy the checklist.

### Root-secret replacement

Only the current encryption root is present. No next root exists in the protected environment, so no real rewrap/restart/replacement can safely begin. The per-envelope old-root to new-root primitive remains engineering support, not live acceptance.

### Native browser WebMCP

The application registers scoped tools through `document.modelContext` and exposes an in-page `workspace_status` native round-trip check. Browser-agent acceptance still requires an authenticated browser that implements the native WebMCP API; CI and remote MCP are not substitutes.

### Stripe lifecycle

The Product/Price exists, but protected sandbox runtime/setup/webhook configuration is absent. No Checkout, customer, subscription or payment acceptance object was created by this pass. A webhook must not be created until its one-time signing secret can be stored directly in the staging secret store.

## Follow-up hardening

The follow-up Stripe change separates Portal setup authority from the Worker runtime payment key. `STRIPE_SANDBOX_OPERATOR_KEY` is workflow-only and is never deployed to the Worker. Sandbox Portal session creation explicitly selects the one metadata-bound reviewed PostSteward staging Portal configuration and fails closed if it is missing, duplicated, live-mode or weakened. This avoids depending on an account-wide default configuration and avoids giving the Worker Portal-configuration write authority.
