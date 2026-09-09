# PostSteward

Reliable social publishing for AI agents, hosted on Cloudflare. PostSteward turns approved content into publication, explicit schedules and inspectable receipts. Advanced adds continuing campaign management for USD 5 per workspace/month.

**Status: deployed to restricted staging; 17 hosted checks passed. Public customer acceptance and public charging are not approved.**

Staging is live at [poststeward-staging.woeinvests.workers.dev](https://poststeward-staging.woeinvests.workers.dev). The [deployment receipt](docs/staging-deployment-2026-09-09.md) records the exact runtime revision, successful Actions run and verification boundaries. Google login initiation passed; completed owner sign-in and real social publication remain unverified.

Free includes verified account routing, immutable campaigns, publish-now dispatch, explicit schedules, cancellation/replacement, durable receipts, export and available metrics on demand. Advanced is USD 5 per workspace/month and adds continuing management. Initial source monitoring, deterministic replenishment, spacing and daily metrics are implemented behind a disabled flag; the complete Advanced release still needs the validation and remaining work below.

## What runs

- TypeScript Cloudflare Worker, D1 identity and one SQLite-backed Durable Object per workspace.
- Maintained OIDC code/PKCE flow, browser sessions, CSRF checks and revocable scoped agent tokens.
- X, Threads and LinkedIn provider adapters. Threads resumes readiness checks using alarms. No blind retry after an uncertain publication.
- 26 operations shared by HTTP, remote MCP and native browser WebMCP. Generated help, OpenAPI, agent guide, `llms.txt` and crawlable discovery.
- Stripe recurring Checkout, Portal, signed-webhook reconciliation and an SDK-backed MPP endpoint for a non-renewing USD 5 calendar-month pass. Both require real account validation before enablement.
- Public GitHub source-path monitoring, first-observation baselines, exact approved templates, stale-work withdrawal, bounded scheduling and metrics timers.

## Develop and verify

Use Node 24. Dependencies and the Workers runtime are pinned in `package-lock.json`.

```sh
npm ci
npm run verify
```

`verify` checks TypeScript, generated documentation, deployment bundling and meaningful unit/integration scenarios. The integration tests use the actual Workers runtime with SQLite Durable Objects, D1 and Workers Assets; social provider responses are simulated. They do not post publicly or spend money. The browser adapter has automated contract checks; native WebMCP still needs supported-browser validation on the hosted origin.

Documentation comes from `src/operations/catalog.ts`. Run `npm run docs` when changing an operation. Do not hand-edit generated references. All effectful operations declare their consequence class, scope and safe inspection operation.

## Repository and deployment

[AyobamiH/poststeward](https://github.com/AyobamiH/poststeward) is the standalone product repository, with its own deployment and release lifecycle. Post Once remains a separate project. See [deployment](docs/deployment.md) for the exact configuration and workflow, including [explicit staging requests](docs/staging-deployment-request.md). Production resources are deliberately unconfigured; dry-run bundling works without credentials. Ordinary documentation and application merges do not automatically deploy.

## Remaining release work

1. Complete the hosted owner sign-in and fresh-customer acceptance flow, including invited/uninvited identities, workspace isolation and grant revocation. Staging infrastructure and required settings are already configured; do not recreate resources or regenerate keys. Select and configure the production origin separately before public launch.
2. Complete provider OAuth onboarding and token refresh/rotation. The initial usable pilot connection accepts authorised user tokens and verifies identities. Validate a fresh customer's account and provider permissions.
3. Complete private repository installation, richer inventory category balancing, automation profile review UI and scheduled-metrics coverage. Advanced remains disabled meanwhile.
4. Run Stripe sandbox lifecycle and eligible-wallet MPP settlement tests, including refund/dispute linkage and uncertain-payment reconciliation. No real Stripe merchant capability or charge was verified here.
5. Validate browser WebMCP on a supported hosted browser, controlled real publications and the full fresh-customer flow. Remote MCP currently uses scoped Bearer headers; OAuth-compatible MCP authorization discovery is separate remaining work.
6. Calibrate traffic/storage retention limits; implement account erasure, credential key rotation and operational alerts; rehearse state restore with external-effect reconciliation. Triage the full-install development/tooling advisories separately from the passing production-dependency audit.

Read [provenance](docs/provenance.md), the [agent guide](public/docs/agent-guide.md), the generated [operation reference](docs/operations.md), the [operating runbook](docs/operations-runbook.md) and the [security model](docs/security.md).
