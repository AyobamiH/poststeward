# Hosted capacity observation

PostSteward measures capacity in two layers instead of relying on a hand-maintained spreadsheet.

## Workspace high-water telemetry

Each Durable Object maintains a bounded local daily counter for workspace requests and alarm cycles. The established hourly schedule asks every registered workspace for a read-only capacity snapshot and writes only a truncated SHA-256 workspace fingerprint plus aggregate high-water values to D1.

The D1 observation contains:

- record count and encoded record bytes;
- largest encoded record value;
- deliveries created that UTC day;
- active scheduled/executing deliveries;
- source-profile count;
- workspace request count for that UTC day;
- alarm-cycle count for that UTC day.

Raw workspace identifiers are never written to `workspace_capacity_observations`. Thirty-one days of daily high-water observations are retained. The collector is bounded to 500 registered workspaces per hourly pass and reports incomplete coverage instead of silently claiming a complete sample.

Alarm cycles are deliberately labelled as an upper-bound proxy for provider polling cycles. They are useful for conservative cost estimation but are not represented as exact provider API request counts.

## Cloudflare usage observation

`scripts/capacity-observe.mjs` reads the high-water table through the D1 API and combines it with Cloudflare GraphQL analytics for:

- Worker requests, subrequests and errors;
- Worker CPU p50/p99;
- D1 read/write queries, rows read/written, response bytes and p90 query time;
- operational-alert volume from D1.

The resulting JSON is compatible with `scripts/capacity-calibration.mjs` and the release promotion controller.

Run only with read-authorised Cloudflare credentials:

```sh
CLOUDFLARE_ACCOUNT_ID='...' \
D1_ID='...' \
CLOUDFLARE_API_TOKEN='...' \
POSTSTEWARD_WORKER_NAME=poststeward-staging \
  npm run capacity:observe
```

## Evidence that remains explicitly external

Cloudflare metrics cannot prove third-party provider quotas or the commercial prices chosen for a production forecast. Those are separate reviewed evidence inputs:

- `POSTSTEWARD_PROVIDER_QUOTA_EVIDENCE`: JSON with `evidenceClass: provider_observation`, an observation timestamp and one or more provider observations.
- `POSTSTEWARD_PRICING_EVIDENCE`: JSON with `evidenceClass: reviewed_pricing` and a non-negative `monthlyEstimate`.

The capacity report remains `ready: false` if either is absent. This is intentional: PostSteward does not fabricate quotas or bake transient vendor pricing into runtime code.

The production `capacity_cost_calibration` gate closes only after the product headroom evaluator reports at least 30% headroom and the Cloudflare, provider-quota, alert-volume and reviewed-pricing evidence are all present.
