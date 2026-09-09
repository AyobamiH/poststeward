# PostSteward implementation status — 9 September 2026

The first standalone PostSteward service milestone is implemented and tested. Its independent repository is [AyobamiH/poststeward](https://github.com/AyobamiH/poststeward), created on 9 September 2026. This document accompanies the implementation review; deployment to Cloudflare and public customer acceptance remain outstanding.

## Implemented

| Area | Current implementation |
| --- | --- |
| Hosted foundation | TypeScript Worker, D1 identity, SQLite Durable Objects, alarms, deployment configuration and CI |
| Customer authority | OIDC owner sign-in, browser sessions and CSRF protection, scoped revocable agent tokens, workspace isolation |
| Free publishing | Verified account aliases, explicit project routing, immutable approved copy, real X/Threads/LinkedIn API adapters |
| Free schedules and evidence | Publish-now reservations, explicit schedules, cancellation/replacement, receipts, export, on-demand metrics, duplicate/uncertain-effect protections |
| Agent surfaces | 26 shared operations across HTTP, remote MCP and browser WebMCP; generated help/reference/OpenAPI; public agent guide and discovery files |
| Advanced foundation | Public repository source-path monitoring, deterministic reviewed templates, first-observation baselines, stale-work withdrawal, profile/project/family spacing and scheduled metrics |
| Stripe | USD 5 monthly Checkout, Portal, signed webhooks, entitlement reconciliation and SDK-backed MPP for a non-renewing calendar-month pass |

Free and Advanced retain the agreed boundary: direct publishing and explicit scheduling are free; continuing campaign management is paid. Advanced purchases and MPP are disabled in the default deployment configuration.

## Verification

`npm run verify` passed: TypeScript checking, generated-document drift checks, Wrangler deployment bundling and **55 automated tests**.

The runtime integration uses Cloudflare's actual Workers runtime, D1 and SQLite Durable Objects. It verifies authenticated account connection, isolated workspaces, HTTP/MCP operation agreement, alarm-driven dispatch after reservation, duplicate prevention and a native SDK MPP challenge. Provider responses are simulated; these checks did not publish to real accounts.

Payment tests exercise SDK challenge verification, altered credentials/challenges, exact USD 5 input, one-month entitlement, replay prevention and simulated refund revocation. Stripe responses are simulated. No merchant sandbox settlement, real charge or wallet eligibility was verified.

Browser WebMCP contract tests verify scoped registration, consequential annotations and forwarding to the shared handlers. Native supported-browser execution on a deployed origin remains unverified.

The deploy preflight correctly rejects the current placeholder origin/database, missing OIDC configuration and missing release revision. The deployment bundle is generated and checked by Wrangler; exact size is reported by CI.

## Cloudflare configuration and security implementation

Separate staging/production configuration, full-SHA-pinned CI actions, main-only environment deployments, isolated deployment secrets, remote D1 identity verification, secret validation and temporary secret-file cleanup are implemented. The workflow leaves billing disabled and signup restricted to verified invited owners. Runtime protections include strict host/origin checks, OIDC signature verification, CSRF, malformed-auth rejection, edge and durable workspace request limits, streamed-body deadlines, bounded active grants/login state and expiry cleanup.

The 55 tests include actual Workers OIDC processing using locally signed test tokens, state replay and forged-signature rejection, verified-email restrictions, session CSRF, grant caps and Cloudflare rate bindings. The production-dependency audit reported zero known advisories on 9 September 2026; this does not establish that dependencies are vulnerability-free. A clean dependency install with lifecycle scripts disabled also produced the Worker bundle. No real identity provider consent, Cloudflare account operation, hosted WAF policy or alert delivery has been verified. See [setup](deployment.md) and [security boundaries](security.md).

## What remains before launch

1. Choose the hosted domain and validate the implementation through GitHub CI and review.
2. Cloudflare resources, OIDC configuration and staging secrets. This session has no Cloudflare account/token configured.
3. Provider OAuth onboarding and token refresh/rotation. The current pilot accepts authorised user tokens and verifies account identity. Fresh-customer and live provider acceptance are still required.
4. Complete the richer Advanced profile/category-management work, private GitHub installation and customer review UI before enabling purchases.
5. Configure Stripe sandbox and merchant MPP eligibility; complete recurring invoice, payment recovery, refund/dispute and eligible-wallet acceptance. This session has no Stripe key configured.
6. Finish production traffic/retention limits, erasure tooling, alerts and restore validation. Initial delivery limits are 20 reservations per UTC day, 100 active schedules and 10 source profiles per workspace; these are pilot limits, not proven unit economics.

PostSteward runs centrally on the service operator’s Cloudflare account. Customers connect accounts and agents to the hosted service.
