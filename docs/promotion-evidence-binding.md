# Exact-release promotion evidence

## Purpose

The release-promotion controller is a read-only decision report. A green diagnostic job, configured integration, supplied JSON flag, or passing observation is not permission to promote a release. This contract completes the controller integration after the hosted observation pipelines were added in PRs #84–#89.

The engineering pattern follows reproducible, evaluated releases and reversible changes rather than an incident-time manual checklist:

- Google SRE, Canarying Releases: https://sre.google/workbook/canarying-releases/
- AWS Builders' Library, Ensuring rollback safety during deployments: https://aws.amazon.com/builders-library/ensuring-rollback-safety-during-deployments/

The exact evidence schema and freshness limits below are PostSteward policy decisions, not claims that those companies prescribe these particular values.

## Two different results

`promotion.evidenceReady` means the currently required observations pass evaluation and are suitable for review. `promotion.reviewRequired` identifies those observations whose canonical gates still need a reviewed change.

`promotion.ready` requires both the exact healthy runtime context and already-reviewed acceptance of every required gate. Observations do not silently replace the reviewed ledger. `promotion.authorized` is always `false`; no report grants OAuth, public admission, billing authority, global Advanced rollout, publication or recovery authority.

Production and public launch remain separate stages. A staging observation cannot prove a production environment. Public admission remains a later decision even after technical production gates are reviewed.

## Binding and provenance

The controller requires:

1. `POSTSTEWARD_EXPECTED_RELEASE`, or the workflow's `GITHUB_SHA`, to equal the hosted release exactly. An explicitly empty value is invalid, not an instruction to fall back.
2. The complete hosted gate map to match the reviewed generated `public/release-gates.json`. Missing core/stage rows, unknown states, duplicate identifiers and mis-scoped required gates fail closed.
3. A healthy runtime policy with an explicitly empty violation list and the correct environment for the target.
4. New observations labelled `hosted_observation`, identifying the same release and environment. An origin, when supplied, must match as well.
5. New observation timestamps no older than 24 hours and no more than five minutes ahead of the evaluation clock. Missing, empty and malformed timestamps are rejected.

These checks validate trusted operator/collector inputs; they are not cryptographic attestation that an arbitrary uploaded JSON document is genuine. Independent source review remains required. Previously accepted historical gates are not expired by these new-observation freshness rules and must not be replayed merely to produce another receipt.

The SLO adapter consumes the actual `promotion.ready` returned by the SLO evaluator, not a nonexistent top-level flag. Its event-window timestamp comes from `cohort.observedAt` when no top-level timestamp exists. A caller-supplied `ready: true` never overrides a failed evaluation.

Read-only receipt isolation can now bind its before/after hosted context and both authenticated workspace-status responses to an exact release. The legacy unbound inspection interface remains compatible, but an unbound result is not promotion evidence. The controller never calls the separately authorised state-mutating isolation rehearsal.

The edge verifier independently reports the observed production environment, release, origin and timestamp, and rejects a changed runtime during its readback. Capacity observations preserve durable sample timestamps rather than refreshing old evidence merely by downloading it again. Capacity rows are restricted to the named last-observed release; daily maxima and analytics may still span earlier revisions in the requested window, which the output explicitly states. That report does not claim an isolated per-release load test or replace provider-quota and reviewed-cost evidence.

## Report versus enforcement

The daily status workflow retains `contents: read` only and no provider, Cloudflare or agent credentials. It explicitly uses `POSTSTEWARD_PROMOTION_MODE=report`. Ordinary blocked stages, absent production deployment, or a pending newer deployment are reported without turning an informational job into a false incident. Runtime policy contradictions and malformed input still fail.

Protected staging deployment uses `POSTSTEWARD_PROMOTION_MODE=enforce` after hosted smoke/lifecycle checks and before recording the accepted canary boundary. It evaluates `restricted_staging`, pins the exact workflow SHA and deployed origin, and has no observation credentials. A mismatched revision, ledger drift, unhealthy policy or required unaccepted core gate stops this deployment acceptance step.

This does not bypass GitHub review/signature/CI rules and does not start a canary. Existing failed-start rollback remains unchanged.

## Local commands

Run from a checked-out reviewed revision with Node 24. Supply the expected SHA explicitly outside GitHub Actions:

```sh
POSTSTEWARD_EXPECTED_RELEASE="$(git rev-parse HEAD)" \
POSTSTEWARD_PROMOTION_TARGET=restricted_staging \
POSTSTEWARD_PROMOTION_MODE=enforce \
npm run promotion:status
```

The default origin is restricted staging. For production inspection, explicitly supply `POSTSTEWARD_ORIGIN` for the actual production deployment; changing the target alone does not relabel staging evidence.

`npm run capacity:observe` is restored as the command for the existing capacity collector. `POSTSTEWARD_CAPACITY_OUTPUT` can write its report as JSON for `POSTSTEWARD_CAPACITY_OBSERVATION`; protected Cloudflare read authority and an exact expected release are required. A missing observation window, provider quota, pricing evidence or alert acknowledgement must remain missing, not be fabricated.

## Evidence still external

This implementation does not close Meta's callback gate, native authenticated WebMCP, X/LinkedIn application consent, live Advanced product/SLO acceptance, production edge application, alert delivery/escalation, capacity/cost acceptance, live two-workspace acceptance or public admission. Those existing gates retain their current reviewed states.
