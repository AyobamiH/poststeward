# Advanced rollout

Advanced is never enabled by a single global feature flag alone. PostSteward uses two layers:

1. `ADVANCED_ENABLED` is the global emergency kill switch. When it is not exactly `true`, no workspace can use Advanced even if it has paid entitlement.
2. `ADVANCED_ROLLOUT_MODE` controls admission. The only deployable enabled state today is `canary`; `global` exists in runtime semantics for a future reviewed promotion but deployment policy rejects it.

A canary uses `ADVANCED_CANARY_BPS` (basis points) and `ADVANCED_CANARY_SEED`. Workspace membership is deterministic from the opaque workspace identifier plus the stable non-secret seed. No customer identifier list is shipped to the Worker, no membership is logged, and changing the seed or percentage creates a new reviewed cohort rather than changing billing records.

Current deployment policy is deliberately narrow:

- ordinary staging and all production deployments default to `ADVANCED_ENABLED=false`, mode `disabled`, 0 basis points;
- an enabled rollout is staging-only;
- mode must be `canary`;
- the cohort must be between 1 and 1000 basis points (0.01% to 10%);
- the seed must be explicit and bounded;
- global rollout is rejected until canary product-path and SLO evidence are reviewed;
- MPP remains separately disabled.

Within a Durable Object, the existing Engine sees Advanced as enabled only when its workspace is in the deterministic cohort. The shared Worker environment is never mutated, so one tenant's canary decision cannot leak to another tenant. Existing entitlement checks remain mandatory: cohort membership does not grant paid access by itself.

The canary mechanism is deployment capability, not acceptance evidence. Turning it on later requires protected staging variables and deliberate live product-path observation. Deployment of this code with the default disabled values does not publish, schedule, charge, grant entitlement, or repeat any previously accepted external effect.
