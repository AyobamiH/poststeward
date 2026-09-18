# PostSteward

Reliable social publishing for AI agents, hosted on Cloudflare. PostSteward turns approved content into publication, explicit schedules and inspectable receipts. Advanced adds continuing campaign management for USD 5 per workspace/month.

**Status: restricted staging. Live-accepted evidence now includes owner Google sign-in, one controlled Threads publication with independent provider readback, Inspect-only HTTP/remote-MCP grant plus post-revocation denial, the Stripe sandbox subscription/refund/cancellation/webhook lifecycle, protected encryption-root cutover, and selected-private-repository GitHub grant/read/provider-revocation/fail-closed behaviour. Threads OAuth callback completion is blocked by Meta's callback-save failure. Hosted Cloudflare PITR is parked because `getBookmarkForTime()` fails in the reviewed staging runtime before any restore is armed. Native WebMCP still needs one authenticated invocation in a browser that exposes the API. X and LinkedIn applications/grants remain external setup gates. Advanced, MPP and public signup remain disabled.**

Staging is live at [poststeward-staging.woeinvests.workers.dev](https://poststeward-staging.woeinvests.workers.dev). The current authoritative acceptance ledger is [live external gates](docs/live-external-gates-2026-09-13.md). Historical receipts and procedures remain useful evidence, but must not be used to repeat already accepted external effects.

## What runs

- TypeScript Cloudflare Worker, D1 identity and one SQLite-backed Durable Object per workspace.
- Google OIDC code/PKCE owner sign-in, browser sessions, CSRF and revocable scoped agent tokens. Admin authority cannot be delegated through the grant endpoint.
- X, Threads and LinkedIn provider adapters with stable identity checks. Threads uses long-lived token refresh handling. LinkedIn supports member identities plus explicitly reviewed organization/page actors; controlled readback is capability-gated.
- One generated operation catalogue shared by HTTP, remote MCP and native browser WebMCP. Owner acceptance, provider OAuth, private-source, recovery and lifecycle APIs remain owner-browser controls rather than delegated agent tools.
- Owner-controlled `/pilot`: fresh provider identity, expiring immutable review, explicit approval, thirty-second cancellation boundary, session/account/release fencing and bounded independent readback.
- Fingerprint dedupe, operation idempotency, stale-claim recovery and ambiguous-effect preservation. A lost or uncertain write is inspected, never blindly retried with a fresh key.
- Private GitHub source-path monitoring with owner-authorised selected-repository access, read-only Contents/Metadata authority, encrypted rotating user credentials and per-read revalidation. Its real staging grant/read/revoke path is accepted; do not reconnect merely to repeat evidence.
- Owner-only PITR coordination, quarantine, reconciliation and undo fencing. The current hosted blocker is Cloudflare target-bookmark resolution, not missing PostSteward recovery state-machine code.
- Advanced source monitoring, deterministic reviewed templates, first-observation baselines, stale-work withdrawal, bounded replenishment/spacing and independently scheduled metrics.
- Owner Advanced inventory view at `/advanced-inventory.html`. The reviewed profile `family` is the category identity; the view groups categories, source snapshots, reserved automatic deliveries and metric evidence without a second drifting category store.
- Stripe recurring Checkout, Portal and signed-webhook reconciliation plus a separately disabled MPP foundation. The staging sandbox lifecycle is accepted; that does not by itself enable paid production automation.
- Protected mixed-root credential reads and bounded rewrap/cutover support. The active staging writer is `next`; the legacy root is deliberately retained for recovery and must not be retired merely for another receipt.

## Free and Advanced

Free includes verified account routing, immutable campaigns, publish-now dispatch, explicit schedules, cancellation/replacement, durable receipts, export, available on-demand metrics and all agent transports.

Advanced is priced at USD 5 per workspace/month and adds continuing source monitoring, replenishment, rolling allocation, spacing controls and scheduled metrics. `ADVANCED_ENABLED` remains false until the Advanced product path itself has live source-to-allocation-to-provider/readback-to-metrics evidence and the chosen production controls are accepted. Stripe sandbox acceptance is already preserved and must not be repeated merely to unblock this product test. MPP remains a separate optional gate.

## Agent contract

Remote agents authenticate with owner-issued, scoped, expiring, revocable Bearer tokens. HTTP and remote MCP use the same operation catalogue and workspace authority checks. OAuth-compatible MCP authorization discovery/bootstrap is **not** claimed for the current release; discovery must never mint or broaden authority.

PostSteward does **not** promise a separately packaged CLI binary. The supported command-line workflow is shell/cURL over the documented HTTP operation surface.

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

Automatic CI/deploy is deliberately non-destructive. It must not sign in, publish, grant/revoke owner authority, execute PITR, erase a customer workspace, rotate a protected root secret or settle a payment.

## Hosted acceptance helpers

Read-only staging readiness:

```sh
POSTSTEWARD_ORIGIN=https://poststeward-staging.woeinvests.workers.dev \
  node scripts/hosted-acceptance.mjs readiness
```

The historical Inspect-only HTTP/remote-MCP grant/revoke proof is already accepted. Do not issue another token merely to repeat it. Production-side read-only helpers cover capacity observations, hosted cross-tenant isolation, Cloudflare DNS/TLS/WAF/rate evidence and GitHub main-ruleset state; see [production readiness acceptance](docs/production-readiness-acceptance.md).

## Repository and deployment

[AyobamiH/poststeward](https://github.com/AyobamiH/poststeward) is the standalone product repository with its own deployment lifecycle. Post Once remains separate.

See [deployment](docs/deployment.md), [private GitHub source authority](docs/private-github-sources.md), [provenance](docs/provenance.md), [operating runbook](docs/operations-runbook.md), [security model](docs/security.md) and the [current external-gate ledger](docs/live-external-gates-2026-09-13.md).

## Current execution order

1. Preserve every accepted live receipt. Do not repeat Google sign-in acceptance, Threads publication/readback, Inspect-only grant/revoke, Stripe sandbox lifecycle, private GitHub grant/read/revoke or protected root cutover merely for fresh evidence.
2. Close authenticated native WebMCP in a supporting browser with one read-only `workspace_status` round trip bound to the current workspace and exact hosted release.
3. Configure and accept the X provider application/grant if X is in release scope.
4. Configure and accept the LinkedIn provider application/grant. Restricted staging now targets the verified PostSteward Page `urn:li:organization:146607525` with organization publish/read scopes and exact actor verification. Member-profile readback remains a separate restricted capability.
5. Keep Threads OAuth parked until Meta accepts the exact callback. Keep PITR parked until Cloudflare target-bookmark resolution succeeds or an explicit owner-visible recovery fallback is deliberately implemented and verified.
6. Before enabling Advanced, run its live source-change -> inventory/allocation -> provider/readback -> scheduled-metrics path without repeating the already accepted Stripe lifecycle.
7. Production/public launch remains separate: capacity/cost, alert delivery, hosted cross-tenant evidence, custom production DNS/TLS/WAF/rate policy, GitHub main protection and public signup/support/abuse ownership must be accepted deliberately.

MPP does not block Free or subscription-based Advanced unless it is deliberately added to launch scope.
