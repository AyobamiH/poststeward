# Advanced canary operations

Advanced is controlled by two independent boundaries:

1. the normal entitlement check remains authoritative for every workspace;
2. a deployment-level master switch and deterministic workspace cohort decide whether an entitled workspace may exercise Advanced.

The rollout control never grants an entitlement and never creates a payment. It only changes the bounded staging feature cohort.

## Operator workflow

Use `.github/workflows/staging-advanced-canary.yml` from reviewed `main`. The workflow is restricted to `AyobamiH`, protected staging, and the explicit confirmation `APPLY_ADVANCED_CANARY_CHANGE`.

Allowed actions are deliberately finite:

- `start_100bps` — 1% deterministic canary;
- `start_500bps` — 5% deterministic canary;
- `start_1000bps` — 10% deterministic canary, the current hard maximum;
- `stop` — master off, rollout disabled and 0 basis points.

There is no global option. Production deployment validation also rejects Advanced enablement regardless of workflow input.

A start requires the protected staging variable `ADVANCED_CANARY_SEED` to contain a pre-reviewed stable value of at least eight bounded non-whitespace characters. The seed is preserved on stop so a later restart selects the same workspace cohort. Do not rotate the seed during a measurement window because that changes the cohort and invalidates the observation.

## Behaviour

The workflow calls the same reviewed deployment path used by normal staging releases. Before deployment, `scripts/advanced-rollout-request.mjs` converts the finite operator action into the four runtime rollout values. Unknown actions, non-staging use, non-main use, another repository or another actor fail closed.

A canary start does not itself publish anything. An eligible workspace still needs a real active Advanced entitlement and normal operation authority. Existing provider effect fences, idempotency, readback and recovery boundaries remain unchanged.

Use `stop` when any hard invariant or SLO requires rollback. It redeploys the same reviewed `main` release with the Advanced master switch off. Normal deploy verification remains in the path so a canary configuration cannot bypass the repository's required release controls.

## Promotion evidence

Do not promote from a successful deployment alone. The canary must remain on one exact release/cohort long enough to meet `scripts/slo-evaluate.mjs` requirements: at least 24 hours, at least 20 Advanced operations, zero duplicate external effects, the required publication/schedule/OAuth ratios and reconciliation latency, plus every Advanced product-path stage.

A passing SLO report is evidence for review, not authority to enable global rollout. Global Advanced remains forbidden until a separate reviewed gate change explicitly permits it.
