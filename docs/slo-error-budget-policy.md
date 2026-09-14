# SLO and error-budget policy

PostSteward separates **correctness invariants** from **service-level objectives**. An invariant is not something the service is allowed to spend an error budget on. Duplicate external publication effects are therefore zero-tolerance: one observed duplicate is an immediate incident signal and blocks rollout.

SLOs describe reliability over real hosted observations. They do not become true because unit tests pass, and an evaluator report does not itself authorise a wider rollout. Promotion still requires a reviewed release-gate change after the evidence is inspected.

## Advanced canary promotion evidence

`scripts/slo-evaluate.mjs` accepts one JSON observation produced from the hosted staging canary. Promotion evidence must identify:

- `schemaVersion: 1`;
- `evidenceClass: hosted_observation`;
- staging environment and exact 40-character release SHA;
- deterministic canary basis points, start/end timestamps, workspace count and Advanced operation count;
- duplicate external-effect count;
- publication, scheduling, OAuth refresh, provider-reconciliation and webhook-reconciliation observations;
- evidence that the complete Advanced path occurred: source change, inventory snapshot, spaced allocation, provider effect, verified readback and scheduled metrics capture.

The current initial promotion policy requires at least 24 hours of canary observation, at least one canary workspace and at least 20 Advanced operations. The canary must remain at or below 10% while this policy is in force.

## Reliability objectives

The first reviewed objectives are deliberately simple and machine-evaluable:

- **Duplicate external effects:** exactly 0. This is an invariant, not an SLO.
- **Verified publication:** at least 99% of readback-capable publication attempts, minimum 20 samples.
- **Schedule timeliness:** at least 99% of due deliveries begin within five minutes, minimum 20 samples.
- **OAuth refresh:** at least 99% successful refresh attempts, minimum 1 observed refresh.
- **Provider reconciliation:** p95 at or below five minutes, minimum 1 observation.
- **Webhook reconciliation:** p95 at or below two minutes, minimum 1 observation.

For ratio SLOs the evaluator reports the consumed error budget as observed bad events divided by the number of bad events permitted by the target. A value above 1 means the budget for the evaluated window is exhausted. Insufficient sample size is reported separately from budget exhaustion.

## Recovery SLI

Recovery remains observable even though the exact-checkpoint rehearsal is already accepted independently. When recovery observations exist, the initial monitoring targets are p95 RTO at or below five minutes and maximum RPO at or below six hours, matching the bounded automatic checkpoint cadence.

A canary promotion does **not** require manufacturing another recovery rehearsal merely to create samples. An observed recovery breach is still operationally significant and should feed incident review.

## Alerting boundary

The evaluator does not page on every individual noncritical failure. It emits a hard `page` severity only for the duplicate-external-effect invariant. Ratio/latency SLO failures block promotion and expose error-budget consumption; production alert routing should use real telemetry and multi-window burn-rate policy rather than converting every application error into a page.

## Running the evaluator

```sh
npm run slo:evaluate -- observations.json
```

Exit codes:

- `0`: observation is valid and satisfies the current promotion policy;
- `2`: observation is valid but promotion is blocked by evidence, SLO or product-path conditions;
- `1`: observation itself is invalid or cannot be treated as reviewed hosted evidence.

A successful report is **necessary but not sufficient** for global Advanced rollout. Deployment policy continues to reject `global` until a later reviewed change explicitly advances the release gate.
