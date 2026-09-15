# Hosted cross-tenant acceptance

PostSteward keeps the production isolation claim separate from ordinary unit/runtime tests. The live staging proof uses two **disposable** workspaces and two short-lived agent grants. It does not add a staging authentication bypass or write grant records directly to D1.

## Protected inputs

Create one agent grant in each disposable staging workspace with exactly `read` and `publish` scopes and a short lifetime. Store the one-time token values directly in the protected `staging` GitHub environment as:

- `CROSS_TENANT_AGENT_TOKEN_A`
- `CROSS_TENANT_AGENT_TOKEN_B`

Never paste those tokens into source, issues, PRs, logs, chat or acceptance documents. The grants must resolve to two distinct disposable workspaces. Revoke them after the acceptance run if they have not already expired.

## Acceptance workflow

Run `.github/workflows/staging-cross-tenant-acceptance.yml` from `main` with the explicit confirmation `RUN_CROSS_TENANT_ACCEPTANCE` only after the exact `main` revision is deployed to protected staging.

The workflow first runs the full repository verifier, then `scripts/hosted-acceptance.mjs cross-tenant-state`. The live proof requires:

- both grants authenticate on the exact reviewed release;
- the grants resolve to distinct workspace identities;
- both disposable workspaces begin unpaused;
- workspace A accepts a reversible `publishing_pause=true` canary;
- workspace B remains unpaused while A is paused;
- a hostile Origin replay is rejected;
- workspace A is restored to `publishing_pause=false` in a `finally` cleanup path.

The report exposes only truncated workspace fingerprints. It never includes raw workspace IDs or token values.

## Boundaries

This acceptance performs no social publication, provider OAuth mutation, billing/payment action, recovery action or customer-data read. The existing receipt-ID cross-tenant checker remains available as an additional object-ID test when a harmless authorised receipt already exists; the reversible pause-canary path exists so production isolation can be demonstrated without manufacturing an external provider effect merely for evidence.

A successful workflow run is live evidence to review. It does not automatically edit `src/release-gates.ts`; the `hosted_cross_tenant` gate moves to `live_verified` only in a reviewed evidence PR citing the successful exact-release run.
