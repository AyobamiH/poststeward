# PostSteward

Reliable social publishing for AI agents, hosted on Cloudflare. PostSteward turns approved content into publication, explicit schedules and inspectable receipts. Advanced adds continuing campaign management for USD 5 per workspace/month.

**Status: deployed to restricted staging. Owner acceptance, self-service provider OAuth architecture, exact provider readback, dependency-audit closure and restore-safe external-effect recovery are implemented. The PR #13 candidate passed 130 automated tests and its exact merge revision deployed successfully. Real owner/provider consent, a controlled live publication, a real PITR rehearsal, native WebMCP acceptance and payment settlement remain external evidence gates. Public charging is not approved.**

Staging is live at [poststeward-staging.woeinvests.workers.dev](https://poststeward-staging.woeinvests.workers.dev). The [owner acceptance page](https://poststeward-staging.woeinvests.workers.dev/pilot) takes the owner through Google sign-in, explicit account choice, exact-content review, one durable publication reservation and separate provider readback. Opening it does not publish anything. The [current acceptance engineering receipt](docs/owner-acceptance-deployment-2026-09-09.md) records the exact deployed runtime and verification boundaries; the [earlier infrastructure receipt](docs/staging-deployment-2026-09-09.md) remains historical evidence.

Free includes verified account routing, immutable campaigns, publish-now dispatch, explicit schedules, cancellation/replacement, durable receipts, export and available metrics on demand. Advanced is USD 5 per workspace/month and adds continuing management. Initial source monitoring, deterministic replenishment, spacing and daily metrics are implemented behind a disabled flag; the complete Advanced release still needs the validation and remaining work below.

## What runs

- TypeScript Cloudflare Worker, D1 identity and one SQLite-backed Durable Object per workspace.
- Maintained OIDC code/PKCE flow, browser sessions, CSRF checks and revocable scoped agent tokens. Owner acceptance records a minimal completion proof atomically with a validated session.
- X, Threads and LinkedIn provider adapters. Threads resumes readiness checks using alarms. No blind retry after an uncertain publication.
- 26 operations shared by HTTP, remote MCP and native browser WebMCP. Generated help, OpenAPI, agent guide, `llms.txt` and crawlable discovery. The additional pilot APIs are browser-owner-only, not delegated agent tools.
- Owner-controlled `/pilot` acceptance for one text-only X, Threads or capability-approved LinkedIn publication: fresh sign-in, expiring immutable review, one-shot reservation, thirty-second cancellation window, session-bound dispatch and bounded independent post-ID readback.
- Stripe recurring Checkout, Portal, signed-webhook reconciliation and an SDK-backed MPP endpoint for a non-renewing USD 5 calendar-month pass. Both require real account validation before enablement.
- Public GitHub source-path monitoring, first-observation baselines, exact approved templates, stale-work withdrawal, bounded scheduling and metrics timers.

## Develop and verify

Use Node 24. Dependencies and the Workers runtime are pinned in `package-lock.json`.

```sh
npm ci
npm run verify
```

`verify` checks TypeScript, generated documentation, deployment bundling and unit/integration scenarios. The integration tests use the actual Workers runtime with SQLite Durable Objects, D1 and Workers Assets; Google and social provider responses are simulated. The owner acceptance test waits for a real thirty-second Durable Object alarm without polling during that wait, observes one simulated public write, and then separately reads it back. This does not prove real Google consent or a live social post. Native WebMCP and authenticated real-user browser acceptance remain separate checks.

Documentation comes from `src/operations/catalog.ts`. Run `npm run docs` when changing an operation. Do not hand-edit generated references. All effectful operations declare their consequence class, scope and safe inspection operation.

## Repository and deployment

[AyobamiH/poststeward](https://github.com/AyobamiH/poststeward) is the standalone product repository, with its own deployment and release lifecycle. Post Once remains a separate project. See [deployment](docs/deployment.md) and [explicit staging requests](docs/staging-deployment-request.md). Ordinary application/documentation merges do not automatically deploy. Production resources are deliberately unconfigured; dry-run bundling works without credentials.

The active runtime is recorded separately from main. The existing-runtime verification workflow can recheck a specified deployed SHA without rerunning migrations, uploading code or receiving deployment credentials. Its login-initiation probe creates an expiring state only; it never fabricates an owner session or approves a publication.

## Remaining release work

1. Complete the real owner journey at `/pilot`: Google consent, an authorised account connection, exact destination/content approval and independent readback of the single resulting post. No real owner session, provider credential or approval was manufactured by CI or deployment. Record the private receipt only after these actually happen. Staging settings/resources are already configured; do not recreate them or regenerate keys.
2. Configure and approve at least one real X/Threads/LinkedIn provider application and validate the implemented OAuth/refresh path with a real owner grant. Provider OAuth code exists, but staging correctly reports all provider apps unavailable until their credentials are supplied.
3. Complete private repository installation, richer inventory category balancing, automation profile review UI and scheduled-metrics coverage. Advanced remains disabled meanwhile.
4. Run Stripe sandbox lifecycle and eligible-wallet MPP settlement tests, including refund/dispute linkage and uncertain-payment reconciliation. No real Stripe merchant capability or charge was verified here.
5. Validate native browser WebMCP, full authenticated browser interaction, and fresh-customer isolation/revocation on the hosted service. Remote MCP currently uses scoped Bearer headers; OAuth-compatible MCP authorization discovery remains separate work.
6. Calibrate traffic/storage retention limits; implement account erasure, credential key rotation and operational alerts; perform a real staging restore rehearsal with external-effect reconciliation. Both production and full high-severity dependency audits are clean. Choose and validate the production origin before public launch.

Read the [owner acceptance plan](docs/owner-publication-acceptance.md), [current receipt](docs/owner-acceptance-deployment-2026-09-09.md), [provenance](docs/provenance.md), [agent guide](public/docs/agent-guide.md), generated [operation reference](docs/operations.md), [operating runbook](docs/operations-runbook.md) and [security model](docs/security.md).
