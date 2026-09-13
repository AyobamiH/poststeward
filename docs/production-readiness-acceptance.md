# Production readiness acceptance contract

This runbook defines the evidence required after the restricted-staging product loop is complete. It is intentionally separate from the Threads-first P0 journey.

## Operational alert ownership

Before production is accepted, the operator must configure and exercise alerts for all of the following classes:

- Worker uncaught errors and 5xx rate;
- edge/workspace/login rate-limit pressure;
- owner authentication failures above baseline;
- provider OAuth refresh failure, provider API failure and ambiguous external effects;
- Durable Object alarm/queue lag and D1 errors;
- recovery quarantine or unreconciled PITR state;
- Stripe signature failures, retryable webhook reconciliation, invoice/payment failure, refund and dispute reconciliation;
- storage/capacity thresholds and retained-state quota failures.

Each alert acceptance record must contain only: alert class, environment, test timestamp, release SHA, delivery destination label, received timestamp and acknowledgement/escalation result. Do not put access tokens, customer IDs, provider credentials, webhook signatures, raw request bodies or workspace exports in alert evidence.

A production claim requires at least one delivered test alert for every configured delivery path and one deliberately failed notification path demonstrating escalation. Source code that can emit an error is not proof an operator receives it.

## Capacity and retention

Hard ceilings are safety bounds, not customer economics. Run a representative staging/load scenario and record the input observations expected by `scripts/capacity-calibration.mjs`:

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

Use two disposable, explicitly authorised workspaces. Give each a read-only scoped agent token. Create or select an existing harmless receipt in workspace A, then run:

```sh
POSTSTEWARD_ORIGIN=https://staging.example \
POSTSTEWARD_AGENT_TOKEN_A='...' \
POSTSTEWARD_AGENT_TOKEN_B='...' \
POSTSTEWARD_FOREIGN_DELIVERY_ID='...' \
  node scripts/hosted-acceptance.mjs cross-tenant
```

Required result: the two tokens resolve to distinct workspace fingerprints, workspace A can read its receipt, workspace B receives 404 for the same identifier, and a hostile Origin replay is denied. Follow with targeted token replay, grant expiry, source-authority drift and external-link host checks using the existing runtime/security tests plus hosted observations.

## Production edge acceptance

Production must use a custom HTTPS origin. `workers.dev` remains acceptable for restricted staging but is not production acceptance.

The read-only verifier requires a Cloudflare token that can read Zone, DNS and Rulesets configuration. It does not mutate Cloudflare:

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

OAuth/provider callback registrations must then be checked against the exact production origin. Do not reuse staging callbacks.

## GitHub release governance

At the start of the 12 September completion pass, the repository returned no server-side rulesets. Merged-PR provenance in the deployment workflow is useful but is not equivalent to main protection.

After repository-admin configuration, verify with:

```sh
GITHUB_REPOSITORY=AyobamiH/poststeward \
GITHUB_TOKEN='...' \
  node scripts/github-main-protection-check.mjs
```

The accepted main ruleset must be active for the default/main branch, block deletion, block non-fast-forward updates, require at least one approving review and require the `Verify` status check. Administrative bypasses should be limited to deliberate break-glass actors and reviewed separately.

## Root encryption-key replacement rehearsal

Key-schedule version rotation and replacement of the backed-up root secret are different events. Root replacement must use an exact inventory of every encrypted credential store before the current root is changed.

`src/root-rotation.ts` provides a narrow primitive that rewraps one explicitly supplied envelope from old root to new root while preserving its authenticated context. It does not discover credentials and does not mutate storage. This is deliberate: broad credential discovery is authority that belongs to the owning store and its existing transaction/CAS rules.

A staging rehearsal must:

1. pause publication and Advanced automation;
2. inventory account credentials, provider OAuth refresh secrets and private-GitHub credentials without printing them;
3. back up the old root through the existing secure owner process;
4. generate the new root outside source control and logs;
5. rewrap every inventoried envelope using its exact authenticated context and the owning store's atomic/CAS update rule;
6. verify every rewrapped envelope reads with the new root and rejects the old root;
7. keep a bounded rollback window until all credential classes are proven;
8. replace the protected root secret only after the inventory count and rewrap count match;
9. exercise one credential read/refresh per class after restart;
10. record only counts, envelope versions, release SHA and pass/fail evidence.

Never place either root key in a GitHub issue, PR, Actions log, ChatGPT transcript or acceptance receipt.

## Workspace erasure

Use a disposable staging workspace only. Export it, record a non-secret canary, invoke owner deletion, confirm the tombstone/authority purge, then sign in again and prove a new workspace is created rather than resurrecting the old one. Do not perform this rehearsal against the owner's primary acceptance workspace.

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
