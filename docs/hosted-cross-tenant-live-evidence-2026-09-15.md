# Hosted cross-tenant live evidence — 15 September 2026

This record closes the `hosted_cross_tenant` production-readiness gate from the existing protected staging acceptance run. It does not infer isolation from unit tests or configuration.

## Accepted run

GitHub Actions run `35029954430`, job `104585873370`, completed successfully against hosted release `59a265edb092c76d7fc5220bbbf2492bf3987f80`.

The acceptance harness created two distinct disposable staging workspaces with short-lived protected grants and returned:

- two distinct workspace fingerprints;
- `workspaceLocalStateIsolation: true` after a reversible `publishing_pause` canary was applied only to workspace A while workspace B remained unchanged;
- hostile-Origin replay denied with HTTP 403;
- `providerEffectAttempted: false`;
- `paymentAttempted: false`;
- `recoveryAttempted: false`;
- no token values or raw workspace identifiers emitted;
- ephemeral grant cleanup verified after the test.

The run also pinned the hosted `/readiness.json` release to the exact reviewed workflow SHA before exercising tenant state.

## Boundary

This evidence proves the reviewed PostSteward application-level hosted isolation acceptance required by the production runbook. It does not claim that every future provider integration is cross-tenant safe without its own scoped authority checks, and it does not enable public signup or Advanced rollout.

No social publication, provider credential creation, payment, recovery restore or customer workspace was used to obtain this evidence.
