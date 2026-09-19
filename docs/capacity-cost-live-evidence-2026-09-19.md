# Capacity and cost live evidence — 19 September 2026

The `capacity_cost_calibration` production gate is accepted from the protected, read-only GitHub Actions observation in workflow run `35442767724`.

## Evidence boundary

- Hosted runtime release: `6f7f2ce95949a420496e1b4f9d7349359f47d442`.
- Observation controller revision: `4d26c857361119d18165f6ddc878f8a676d67b6b`.
- Environments: protected `staging` and `production`.
- Projection target: the reviewed first 100 workspaces.
- Evidence sources: fingerprinted workspace high-water rows in each environment's D1 database, Cloudflare Workers and D1 analytics, reviewed Threads quota evidence, operational-alert volume, and reviewed Cloudflare pricing.
- No customer identifiers, workspace identifiers, credentials, request bodies, provider tokens or secret values are part of the acceptance record.

The observation controller revision is newer than the hosted runtime release because it contains only read-only analytics corrections. Runtime capacity snapshots themselves came from the exact hosted release named above. Both protected deployments for that runtime passed their release and access-boundary checks in runs `35441619702` and `35441619688`.

## Accepted results

| Environment | Sampled workspaces | Projected workspaces | Product verdict | Projected monthly Worker requests | Projected CPU at observed p99 | Projected D1 rows read | Projected D1 rows written | Pricing envelope | Ready |
| --- | ---: | ---: | --- | ---: | ---: | ---: | ---: | --- | --- |
| staging | 4 | 100 | `calibrated_with_30pct_headroom` | 24,000 | 385,032 ms | 86,048.61 | 4,631.02 | within reviewed $5 envelope | yes |
| production | 1 | 100 | `calibrated_with_30pct_headroom` | 108,000 | 976,860 ms | 85,136.71 | 16,236.81 | within reviewed $5 envelope | yes |

Both reports also recorded:

- Cloudflare observation present;
- provider quota observation present;
- at least 30% provider quota headroom;
- operational-alert volume observation present;
- reviewed pricing present;
- no Worker errors in the seven-day analytics window; and
- `ready: true` from the complete evaluator.

## Investigation and corrections

The first hosted run did not pass, and its failures were investigated rather than accepted from workflow colour:

1. UUID workspace IDs were rejected by a double-escaped character class in the internal snapshot endpoint.
2. The snapshot queried a nonexistent `record_usage.singleton` column instead of the canonical `id=1` row.
3. The Cloudflare D1 analytics query requested latency from the unsupported `sum` field instead of `quantiles.queryBatchTimeMsP90`.
4. Cloudflare Workers CPU quantiles were reported in microseconds but initially compared directly with a millisecond pricing allowance, overstating projected CPU by 1,000 times.

The runtime faults were repaired and deployed through PR `139`. The read-only analytics contract and unit corrections were verified through PRs `141` and `142`. Workflow `35442767724` is the first complete post-correction evidence run and both matrix jobs passed their enforcing summary step.

## Decision

The accepted observations project the reviewed first-100 workload with more than 30% product/storage headroom and inside the reviewed Cloudflare and provider envelope. The `capacity_cost_calibration` gate may therefore move to `live_verified` and cease blocking technical production readiness.

This decision does not authorise public signup, Advanced global rollout, MPP, provider consent, publication, billing, recovery, or deletion. Public admission remains a separate reviewed support/abuse policy decision.
