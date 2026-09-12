# PostSteward

Reliable social publishing for AI agents, hosted on Cloudflare. PostSteward turns approved content into publication, explicit schedules and inspectable receipts. Advanced adds continuing campaign management for USD 5 per workspace/month.

**Status: restricted staging. Owner Google sign-in is live-accepted. Inspect-only HTTP/remote MCP grant and revocation passed in an owner-executed live run. Threads app credentials are deployed, but saving the Meta redirect allowlist is blocked; the real Threads owner grant, first controlled live publication and independent provider readback are the current P0 external evidence gates. X and LinkedIn applications remain unconfigured. Private GitHub authority, PITR coordination, native WebMCP integration and Stripe billing are engineered but still require their separate live acceptance journeys. Advanced, MPP and public signup remain disabled.**

Staging is live at [poststeward-staging.woeinvests.workers.dev](https://poststeward-staging.woeinvests.workers.dev). The [owner acceptance page](https://poststeward-staging.woeinvests.workers.dev/pilot) takes the owner through provider connection, exact-content review, one durable publication reservation and separate provider readback. Opening it does not publish anything.

The current completion boundary is documented in [implementation status](docs/implementation-status.md), [live acceptance plan](docs/live-acceptance-plan.md), [private hosted completion gap closure](docs/completion-gap-closure-2026-09-12.md) and [production readiness acceptance](docs/production-readiness-acceptance.md).

## What runs

- TypeScript Cloudflare Worker, D1 identity and one SQLite-backed Durable Object per workspace.
- Google OIDC code/PKCE owner sign-in, browser sessions, CSRF and revocable scoped agent tokens. Admin authority cannot be delegated through the grant endpoint.
- X, Threads and LinkedIn provider adapters with stable identity checks. Threads uses long-lived token refresh handling. LinkedIn controlled readback is capability-gated.
- 26 operations shared by HTTP, remote MCP and native browser WebMCP. Additional owner acceptance, provider OAuth, private-source, recovery and lifecycle APIs remain owner-browser controls rather than delegated agent tools.
- Owner-controlled `/pilot`: fresh provider identity, expiring immutable review, explicit approval, thirty-second cancellation boundary, session/account/release fencing and bounded independent readback.
- Fingerprint dedupe, operation idempotency, stale-claim recovery and ambiguous-effect preservation. A lost/uncertain write is inspected, never blindly retried with a fresh key.
- Private GitHub source-path monitoring with optional owner-authorised selected-repository access, read-only Contents/Metadata authority, encrypted rotating user credentials and per-read revalidation.
- Advanced source monitoring, deterministic reviewed templates, first-observation baselines, stale-work withdrawal, bounded replenishment/spacing and independently scheduled metrics.
- Owner Advanced inventory view at `/advanced-inventory.html`. The reviewed profile `family` is the category identity; the view groups categories, source snapshots, reserved automatic deliveries and metric evidence without creating a second drifting category store.
- Stripe recurring Checkout, Portal and signed-webhook reconciliation plus a separately disabled MPP foundation. Public charging is not approved until live sandbox acceptance is complete.

## Free and Advanced

Free includes verified account routing, immutable campaigns, publish-now dispatch, explicit schedules, cancellation/replacement, durable receipts, export, available on-demand metrics and all agent transports.

Advanced is proposed at USD 5 per workspace/month and adds continuing source monitoring, replenishment, rolling allocation, spacing controls and scheduled metrics. `ADVANCED_ENABLED` remains false until the product and Stripe acceptance gates are complete. MPP has its own independent gate.

## Agent contract

Remote agents authenticate with owner-issued, scoped, expiring, revocable Bearer tokens. HTTP and remote MCP use the same operation catalogue and workspace authority checks. OAuth-compatible MCP authorization discovery/bootstrap is **not** claimed for the current release; discovery must never mint or broaden authority.

PostSteward does **not** promise a separately packaged CLI binary. The supported command-line workflow is shell/cURL over the documented HTTP operation surface. Avoid creating a package/update channel that adds no product value.

Generated operation documentation comes from `src/operations/catalog.ts`:

- [Agent guide](public/docs/agent-guide.md)
- [Operation reference](docs/operations.md)
- `/help.json`
- `/openapi.json`
- remote `/mcp`

## Develop and verify

Use Node 24. Dependencies and the Workers runtime are pinned in `package-lock.json`.

```sh
npm ci
npm run verify
```

`verify` checks TypeScript, generated documentation, deployment bundling and unit/integration scenarios. Runtime tests use actual Workers/D1/SQLite/Assets execution under Miniflare while Google, GitHub, Stripe and social-provider responses are simulated. Fixtures prove code paths, not real external acceptance.

Automatic CI/deploy is deliberately non-destructive. It must not sign in, publish, grant/revoke owner authority, restore, erase a workspace, rotate a real root secret or settle a payment.

## Hosted acceptance helpers

Read-only Threads-first staging readiness:

```sh
POSTSTEWARD_ORIGIN=https://poststeward-staging.woeinvests.workers.dev \
  node scripts/hosted-acceptance.mjs readiness
```

A real least-privilege token can be exercised over HTTP and remote MCP with `scripts/hosted-acceptance.mjs agent`, then verified denied after owner revocation with `revoked`. The harness receives tokens only through environment variables and emits a workspace fingerprint rather than the raw workspace identifier.

Production-side read-only verification helpers cover capacity observations, hosted cross-tenant isolation, Cloudflare DNS/TLS/WAF/rate evidence and GitHub main ruleset state. See [production readiness acceptance](docs/production-readiness-acceptance.md).

## Repository and deployment

[AyobamiH/poststeward](https://github.com/AyobamiH/poststeward) is the standalone product repository with its own deployment lifecycle. Post Once remains separate.

See [deployment](docs/deployment.md), [private GitHub source authority](docs/private-github-sources.md), [provenance](docs/provenance.md), [operating runbook](docs/operations-runbook.md) and [security model](docs/security.md). Ordinary application/documentation changes do not automatically establish live provider, payment, recovery or browser acceptance.

## Immediate release sequence

1. Keep Threads as the only P0 provider. Complete fresh owner Threads consent and confirm the stable identity stored by PostSteward.
2. In `/pilot`, approve one exact destination/text review. Preserve exactly one provider creation ID and require the separate Threads GET to match ID, stable owner and exact text.
3. Preserve the completed Inspect-only HTTP/remote MCP grant/revoke proof in [P0 live evidence](docs/p0-live-evidence-2026-09-12.md). Do not repeat it or treat it as delegated publishing acceptance.
4. Close native browser WebMCP separately in a supporting authenticated browser.
5. If private sources are part of release scope, configure the staging GitHub App and complete selected private grant/read/revoke acceptance.
6. Rehearse PITR, disposable workspace erasure and root-key replacement against non-production state.
7. Complete Advanced source-to-allocation-to-metrics acceptance plus Stripe webhook/Portal/Checkout/settlement/renewal/cancel/refund/dispute before enabling paid automation.
8. Calibrate capacity/cost, prove alert delivery, run hosted cross-tenant checks, validate the production custom domain/DNS/TLS/WAF/rate policies, enable a real GitHub main ruleset, then decide when public signup/support/abuse controls are ready.

X, LinkedIn and MPP do not block the Threads-first Free launch unless they are deliberately added to launch scope.
