# Production readiness acceptance contract

This runbook defines the evidence required beyond the restricted-staging product loop. It is intentionally separate from provider P0 acceptance.

## Current status note — 14 September 2026

- Protected staging root cutover is **accepted**. The active writer is `next`; the legacy root is intentionally retained for recovery. Do not repeat the cutover or retire the old root merely to satisfy this runbook.
- Stripe sandbox lifecycle is **accepted**. Do not recreate payment/refund/cancellation merely for production-readiness evidence.
- Private GitHub live grant/read/revoke is **accepted**.
- Cloudflare PITR restore is **not accepted**: hosted `getBookmarkForTime()` fails before any restore can be armed.
- A synthetic staging cleanup has exercised normal workspace deletion successfully, but the full export -> deletion -> old-session denial -> tombstone -> new-workspace anti-resurrection production acceptance remains separate from that partial evidence.
- The GitHub repository ruleset inventory was still **empty** when re-read on 14 September. Merged-PR provenance in deployment is not server-side main protection.
- Production custom edge, capacity/cost observations, delivered alerts, hosted two-workspace cross-tenant evidence and public-signup/support/abuse ownership remain open.

The sections below are the production acceptance contract. Accepted staging effects are historical procedure only and must not be replayed unnecessarily.

## Operational alert ownership

Before production is accepted, configure and exercise alerts for:

- Worker uncaught errors and 5xx rate;
- edge/workspace/login rate-limit pressure;
- owner authentication failures above baseline;
- provider OAuth refresh failure, provider API failure and ambiguous external effects;
- Durable Object alarm/queue lag and D1 errors;
- recovery quarantine or unreconciled PITR state;
- Stripe signature failures, retryable webhook reconciliation, invoice/payment failure, refund and dispute reconciliation;
- storage/capacity thresholds and retained-state quota failures.

Each alert acceptance record must contain only: alert class, environment, test timestamp, release SHA, delivery destination label, received timestamp and acknowledgement/escalation result. Never put access tokens, customer IDs, provider credentials, webhook signatures, raw request bodies or workspace exports in alert evidence.

A production claim requires at least one delivered test alert for every configured delivery path and one deliberately failed notification path demonstrating escalation. Source code that can emit an error is not proof an operator receives it.

## Capacity and retention

Hard ceilings are safety bounds, not customer economics. Run a representative staging/load scenario and record the observations expected by `scripts/capacity-calibration.mjs`:

```json
{
  "workspaces": 100,
  "peakRecordsPerWorkspace": 5000,
  "peakBytesPerWorkspace": 4194304,
  "maxValueBytes": 32768,
  "peakDailyDeliveries": 5,
  "peakActiveSchedules": 20,
  "peakProfiles": 3,
  "requestsPerWorkspaceDay": 500,
  "providerPollsPerWorkspaceDay": 24
}
```

Run:

```sh
node scripts/capacity-calibration.mjs observations.json
```

The harness requires at least 30% headroom against the most constrained product/storage ceiling. Separately record Cloudflare request/CPU/storage costs, provider polling cost/quotas and alarm/webhook volume. Only then set customer-facing retention and usage limits.

## Cross-tenant hosted acceptance

Use two disposable, explicitly authorised workspaces with read-only scoped agent tokens. Create or select an existing harmless receipt in workspace A, then run:

```sh
POSTSTEWARD_ORIGIN=https://staging.example \
POSTSTEWARD_AGENT_TOKEN_A='...' \
POSTSTEWARD_AGENT_TOKEN_B='...' \
POSTSTEWARD_FOREIGN_DELIVERY_ID='...' \
  node scripts/hosted-acceptance.mjs cross-tenant
```

Required result: the tokens resolve to distinct workspace fingerprints, workspace A can read its receipt, workspace B receives 404 for the same identifier, and hostile Origin replay is denied. Follow with targeted token replay, grant expiry, source-authority drift and external-link host checks using the existing runtime/security tests plus hosted observations.

## Production edge acceptance

Production must use a custom HTTPS origin. `workers.dev` is acceptable for restricted staging but is not production acceptance.

The read-only verifier requires Cloudflare authority that can read Zone, DNS and Rulesets configuration. It does not mutate Cloudflare:

```sh
POSTSTEWARD_PRODUCTION_ORIGIN=https://service.example.com \
CLOUDFLARE_ZONE_NAME=example.com \
CLOUDFLARE_API_TOKEN='...' \
  node scripts/production-edge-check.mjs
```

Required evidence:

- exact custom HTTPS origin;
- pinned 40-character release SHA from the hosted service;
- restricted signup until public admission is separately approved;
- Advanced and MPP disabled until their own acceptance gates pass;
- HSTS and CSP on the public origin;
- proxied DNS for the service hostname;
- at least one active custom or managed WAF rule;
- at least one active Cloudflare rate-limit rule.

OAuth/provider callback registrations must be checked against the exact production origin. Do not reuse staging callbacks.

## GitHub release governance

The repository returned an empty server-side ruleset inventory again on 14 September 2026. This remains a genuine repository-admin action.

After admin configuration, verify with:

```sh
GITHUB_REPOSITORY=AyobamiH/poststeward \
GITHUB_TOKEN='...' \
  node scripts/github-main-protection-check.mjs
```

The accepted main ruleset must apply to the default/main branch, block deletion, block non-fast-forward updates, require at least one approving review and require the `Verify` status check. Administrative bypasses should be limited to deliberate break-glass actors and reviewed separately.

## Root encryption-key replacement — accepted staging evidence

The protected staging cutover has already traversed the complete registered workspace inventory, used conditional rewrap/readback, activated `ENCRYPTION_ROOT_WRITE=next`, verified current inventory and retained the legacy root. Later deployments have re-read the current inventory without changing credentials.

This acceptance does **not** authorise deleting `ENCRYPTION_KEY`. The retained legacy root is part of the recovery boundary while snapshots/rollback may still reference old ciphertext. Any future root replacement is a new cryptographic event and must receive a new explicit inventory/rollback review rather than replaying the 13 September cutover.

Never place either root key in a GitHub issue, PR, Actions log, ChatGPT transcript or acceptance receipt.

## Workspace erasure

The prepare-only PITR diagnostic has live evidence that its synthetic workspace could be erased through the normal lifecycle route with local and identity cleanup reported complete. That is useful partial evidence, not the full production anti-resurrection journey.

For full acceptance, use a disposable staging workspace only: export it, record a non-secret canary, invoke owner deletion, confirm the completed tombstone and authority purge, prove the old session is unusable, then sign in again and prove a new workspace is created rather than resurrecting the old one. Do not erase the primary owner acceptance workspace merely to close the checklist.

## Advanced and MPP

Stripe sandbox billing is accepted, but `ADVANCED_ENABLED` remains false until the Advanced **product path** itself is proven live: source change -> source snapshot/inventory -> bounded spaced allocation -> provider effect/readback -> scheduled metrics.

MPP is separately disabled and optional. It does not block Free or subscription-based Advanced unless deliberately added to launch scope.

## Public signup/support/abuse gate

Before changing signup from restricted to public, assign and exercise ownership for:

- support intake and response target;
- abuse/rate-limit escalation;
- privacy/data request handling;
- customer deletion and provider/repository revocation guidance;
- incident commander and backup;
- payment/refund/dispute support if Advanced is enabled;
- provider outage communications;
- admission suspension / emergency publishing pause.

Public signup is a separate decision after production infrastructure is accepted. It is not implied by a successful production deployment.
