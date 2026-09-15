# Release promotion controller

PostSteward uses one fail-closed promotion controller to reconcile reviewed release gates, hosted runtime state and optional live observations. It is a control-plane decision surface, not an effect executor.

## Design contract

The controller follows four rules:

1. **Reviewed evidence is authoritative.** Runtime configuration never upgrades a gate from `external_setup_required`, `blocked_external` or `disabled_policy` to an accepted state.
2. **Safe probes may be automated.** Hosted readiness, production edge inspection and existing cross-tenant read-only acceptance can be evaluated automatically when their protected inputs are supplied.
3. **External authority stays explicit.** Provider registration, provider consent, public signup, global Advanced rollout and other externally consequential actions are never executed by the controller.
4. **Ambiguous effects are not retried.** Existing provider-effect ledgers and recovery fences remain authoritative. The controller only reports the next action or evidence that is ready for review.

## Promotion stages

- `restricted_staging` evaluates blocking restricted-staging gates only.
- `advanced_canary` evaluates whether the bounded Advanced canary has the required hosted SLO/product-path evidence. It does not enable the canary or promote it globally.
- `production` evaluates production infrastructure, operational alert delivery, capacity/cost evidence and hosted cross-tenant isolation. Production may remain restricted signup.
- `public_launch` requires the production evidence plus the separate public-admission/support/abuse gate.

Separating `production` from `public_launch` is deliberate. A service can be technically production-ready while admission remains restricted.

## Baseline use

The scheduled GitHub workflow runs a read-only production evaluation once per day and whenever the controller or gate model changes. It has `contents: read` only and receives no provider, payment or Cloudflare mutation authority.

Local/read-only status:

```sh
POSTSTEWARD_PROMOTION_TARGET=production \
POSTSTEWARD_ORIGIN=https://poststeward-staging.woeinvests.workers.dev \
  npm run promotion:status
```

The report contains:

- exact hosted release;
- runtime policy health;
- required gates for the selected stage;
- live observations supplied for review;
- provider callback/scope/capability contract;
- ordered next actions;
- promotion blockers.

A report with `promotion.ready: true` is evidence for review. It is never permission to make an external change automatically.

## Optional live observations

### Advanced SLO observation

Set `POSTSTEWARD_SLO_OBSERVATION` to a JSON file accepted by `scripts/slo-evaluate.mjs`. The observation must be real hosted canary evidence, cover the minimum window/sample sizes and preserve the zero-duplicate-external-effects invariant.

### Capacity and cost

Set `POSTSTEWARD_CAPACITY_OBSERVATION` to the existing capacity observation format plus:

```json
{
  "costEvidence": {
    "cloudflareObserved": true,
    "providerQuotaObserved": true,
    "alarmWebhookVolumeObserved": true,
    "monthlyEstimate": 0
  }
}
```

The existing 30% product/storage headroom requirement still applies. The controller refuses to treat a spreadsheet-only estimate as complete evidence without the three real operational observations.

### Alert delivery

Set `POSTSTEWARD_ALERT_EVIDENCE` to a hosted observation validated by `npm run alerts:evaluate`. It requires delivery and acknowledgement for every reviewed alert class, every configured delivery path, and one deliberately failed path that proves escalation to a different fallback path. Evidence contains labels and timestamps only, never provider tokens, webhook signatures, customer identifiers or request bodies.

### Production edge

When `POSTSTEWARD_PRODUCTION_ORIGIN`, `CLOUDFLARE_ZONE_NAME` and a read-authorised `CLOUDFLARE_API_TOKEN` are supplied, the controller calls the existing read-only production edge inspector. It does not create DNS, WAF or rate-limit rules.

### Cross-tenant isolation

When the two existing read-only agent tokens and an existing workspace-A delivery ID are supplied, the controller runs the existing hosted isolation check: distinct workspaces, owner read accepted, object-ID swap denied and hostile Origin replay denied. It creates no token and no publication.

## Provider gates

The controller reports the exact staged callback for X, Threads and LinkedIn and reads the deployed required/optional scope and capability contract. If an application is absent it asks for application setup. If the application is configured it asks only for the real owner OAuth grant. It never manufactures a grant or treats configured credentials as live acceptance.

Threads callback persistence, native WebMCP browser support and restricted LinkedIn member-readback permission remain legitimate external capability states. They do not become generic engineering failures and they do not block unrelated promotion stages unless the reviewed gate model explicitly says they do.
