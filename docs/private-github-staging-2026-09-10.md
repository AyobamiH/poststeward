# Private GitHub sources: restricted-staging receipt

Observed 10 September 2026. This records actual GitHub Actions deployment evidence, with external owner acceptance kept separate.

## Exact revision and provenance

- Implementation PR: [#20](https://github.com/AyobamiH/poststeward/pull/20), merged by `AyobamiH`.
- Final reviewed head: `45db0e2e31ec07cb16951aa4cde5d82a766b10e7`.
- Merged and deployed runtime: `e48a1e7ef9ed6388f8c5934aae5426fe40a0ed6c`.
- Final PR verification: [run 34534352457](https://github.com/AyobamiH/poststeward/actions/runs/34534352457), 176/176 tests, zero failed/skipped/cancelled; TypeScript, generated docs, Wrangler dry-run build and production/full dependency audits passed.
- Deployment: [run 34534537780](https://github.com/AyobamiH/poststeward/actions/runs/34534537780), successful merged-PR preflight, verification, migration, upload and all hosted gates.
- Additive migration `0009_github_sources.sql` applied successfully.
- Cloudflare Version ID: `06ac1748-140f-4bd0-8286-fd9697fdcc63`.
- Staging origin: `https://poststeward-staging.woeinvests.workers.dev`.

Documentation-only reconciliation after this deployment does not change the deployed runtime. A later source revision requires its own explicit staging request and receipt.

## Hosted observations

| Check group | Observed result |
| --- | --- |
| Base HTTP checks | 25/25 passed; exact revision observed at 21:54:51 UTC |
| Owner acceptance surfaces | 12/12 passed |
| Browser rendering | Desktop 1280×900 and mobile 390×844 passed; unauthenticated owner controls blocked |
| Recovery/provider OAuth boundaries | 13/13 passed |
| Lifecycle boundaries | 5/5 passed; no deletion attempted |
| Private GitHub source configuration | `false` |
| X / Threads / LinkedIn OAuth configuration | `false` / `false` / `false` |
| Access and charging | Restricted signup; Advanced and MPP disabled |

These are 57 hosted assertions across five reports. Readiness took four bounded read-only attempts to observe the exact release; the subsequent 25 base checks passed without retries.

The cloud browser in the continuation session could read the shared chat but returned `ERR_BLOCKED_BY_CLIENT` for this workers.dev origin. Hosted browser evidence above comes from the deployment's fresh Chromium contexts, not a completed owner session in the cloud browser.

## Resumed fixes

The shared chat stopped after adding refresh-lease database columns. Its runtime still sent rotating refresh tokens before acquiring exclusive ownership. The continuation implemented and tested:

1. Exclusive pre-refresh D1 leases plus exact credential/revision/lease commits.
2. Retryable competing reads and no replay of uncertain or abandoned refreshes.
3. Protection against stale responses overwriting or invalidating owner reconnection; unlink cannot be undone by a late refresh.
4. Persistent stale links after repository removal/rename, preventing an unintended later anonymous fallback.
5. Atomic callback persistence guarded by a live owner session and absence of the workspace-deletion tombstone.
6. Safe, allow-listed configuration booleans in deployment reports.

Seven added regression tests use controlled interleavings, including OAuth callbacks paused across pending deletion, completed erasure and logout. GitHub HTTP responses in CI are fixtures; D1/SQLite/Workers execution is real.

## Remaining external gates

Private-source code, migration, UI and denial checks are deployed. Live private-source use is **not activated or proven**: the deployed GitHub App configuration is absent.

Next requires a dedicated staging GitHub App, its client ID/slug/secret in the protected environment, and owner consent for one explicitly selected private repository. Then verify a harmless source read and access-revocation failure. The exact registration contract is in [private GitHub sources](private-github-sources.md). Do not repurpose another product's App or widen repository access.

X/Threads/LinkedIn OAuth are also unconfigured. A real owner sign-in, provider grant and exact controlled publication/readback remain separate acceptance gates. The hosted reports neither create owner sessions nor publish content.

The account-management connector was unavailable in this continuation; existing GitHub repository tools did not expose App registration or protected-environment secret management. No credentials were invented, exposed or replaced.

PITR rehearsal, native WebMCP, Stripe/MPP settlement and production release remain the separately listed gates in [release completion](release-completion-plan.md). This receipt is not public-release approval.
