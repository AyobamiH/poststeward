# PostSteward

Reliable social publishing for AI agents, hosted on Cloudflare. PostSteward turns approved content into publication, explicit schedules and inspectable receipts. Advanced adds continuing campaign management for USD 5 per workspace/month.

**Status: restricted admission on staging and production. Live-accepted evidence includes owner Google sign-in, one controlled Threads publication with independent provider readback, a real Threads OAuth callback/code exchange with a current healthy long-lived owner connection, Inspect-only HTTP/remote-MCP grant plus post-revocation denial, the Stripe sandbox lifecycle, protected encryption-root cutover, selected-private-repository GitHub authority, production edge/main-protection/alert/capacity controls and hosted cross-tenant isolation. X application credentials are configured but no owner X connection is accepted. LinkedIn application credentials and owner connection are absent; the verified PostSteward Page identity is not OAuth authority. Native WebMCP execution, approximate-time PITR, Advanced, MPP and public signup remain open or disabled.**

Staging is live at [poststeward-staging.woeinvests.workers.dev](https://poststeward-staging.woeinvests.workers.dev). The current authoritative generated ledger is [current release gates](docs/current-readiness.md). The dated [live external gates](docs/live-external-gates-2026-09-13.md) and other historical receipts remain useful evidence, but must not be used to repeat already accepted external effects.

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

See [deployment](docs/deployment.md), [private GitHub source authority](docs/private-github-sources.md), [provenance](docs/provenance.md), [operating runbook](docs/operations-runbook.md), [security model](docs/security.md), the [provider connection audit](docs/provider-connection-audit-2026-09-19.md) and the generated [current release gates](docs/current-readiness.md).

## Current execution order

1. Preserve every accepted live receipt. Do not repeat Google sign-in, Threads OAuth, Threads publication/readback, Inspect-only grant/revoke, Stripe sandbox, private GitHub, recovery, production-edge, alert, capacity or cross-tenant effects merely for newer evidence.
2. If X is in release scope, complete one real owner X OAuth grant and inspect stable identity, scopes and refresh authority. A configured X application is not a connected account.
3. If LinkedIn is in release scope, configure the application and complete owner consent for the exact Page `urn:li:organization:146607525` with organization publish/read scopes. Member-profile readback is a separate optional capability.
4. Keep the existing Threads owner connection healthy. A combined OAuth-connection-to-publication receipt is optional strengthening, not a reason to repeat an unnecessary public post.
5. Close native WebMCP only when the authenticated page's read-only `workspace_status` execution succeeds. Keep approximate-time PITR parked while the hosted primitive remains unavailable.
6. Before enabling Advanced, run its live source-change -> inventory/allocation -> provider/readback -> scheduled-metrics path without repeating the accepted Stripe lifecycle.
7. Public launch remains separately blocked by restricted signup and support/abuse/incident policy, regardless of production infrastructure gates already marked `live_verified`.

MPP does not block Free or subscription-based Advanced unless it is deliberately added to launch scope.
