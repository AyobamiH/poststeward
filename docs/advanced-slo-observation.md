# Hosted Advanced SLO observation

Advanced promotion evidence is generated from durable hosted state. Operators do not hand-author the JSON consumed by `scripts/slo-evaluate.mjs`.

## Evidence boundary

`src/canary-edge.ts` observes only workspaces selected by the reviewed staging canary policy. Evidence writes use `src/slo-telemetry.ts`, which is best-effort and fail-open for the product path: a D1 telemetry outage can make promotion evidence incomplete, but it cannot make a publication, owner operation or automation fail.

The SLO ledger stores only:

- workspace identifier internally for aggregation;
- bounded event type;
- event time;
- count/value and optional duration;
- exact release SHA;
- canary basis points.

Stable event dedupe keys are hashed before storage. The ledger never stores content, OAuth tokens, provider credentials, provider post IDs or the raw canary seed.

## Event-time sources

The evidence deliberately uses the first durable source for each fact rather than mutable receipt timestamps:

- source changes and inventory snapshots: source-backed campaign creation time;
- spaced allocations: automatic delivery creation time;
- provider effect and schedule latency: `external_effects.created_at`, written before the provider call;
- readback and provider reconciliation latency: terminal `external_effects.updated_at` for verified/unverified effects;
- scheduled metrics: profile `lastMetricsSuccess`;
- OAuth refresh attempts/success: persisted OAuth refresh metadata;
- webhook reconciliation: `stripe_events.completed_at - received_at`;
- duplicate external effects: direct grouped read of the external-effect ledger.

Because event rows keep their original observed time, a newly started canary cannot accidentally import historical automation state into its observation window.

## Canary run boundary

After a staging deployment has passed the normal hosted smoke/recovery/lifecycle checks, `scripts/advanced-canary-run.mjs` records the active canary release, basis points, start time and a hash of the stable seed in D1. A normal disabled deployment or explicit stop closes any active run.

A start is recorded only after hosted checks pass. If a manually requested canary start fails those checks, the reusable deploy workflow resolves `stop` and attempts to redeploy the same reviewed SHA with the Advanced master switch disabled before recording the stopped boundary.

## Observation

`npm run slo:observe` builds the current or most recent canary observation from D1. `.github/workflows/staging-advanced-slo-observe.yml` performs the same read-only observation every four hours and on demand using the protected staging Cloudflare read authority.

If no canary has ever started, the scheduled workflow is idle and green. During a canary, insufficient sample size is also an expected accumulating state: valid evidence may return the evaluator's not-ready status without turning the observation workflow into an infrastructure failure. Invalid or contradictory evidence still fails.

The resulting JSON is the exact input contract for `scripts/slo-evaluate.mjs`. Promotion still requires the reviewed minimum window, operation count, zero duplicate effects, ratio/latency budgets and complete Advanced product path. A passing report is evidence for a later reviewed gate change; it is never authority to enable global Advanced automatically.
