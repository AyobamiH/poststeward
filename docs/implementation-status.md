# PostSteward implementation status: 9 September 2026

PostSteward is deployed to restricted staging in its independent repository, [AyobamiH/poststeward](https://github.com/AyobamiH/poststeward). The deployment and all 17 hosted checks passed on 9 September 2026. Public customer acceptance and public charging remain outstanding. This is not a production-launch claim.

## Verified staging deployment

| Evidence | Recorded result |
| --- | --- |
| Origin | `https://poststeward-staging.woeinvests.workers.dev` |
| Deployed runtime revision | `d4fe795cea5e7216692f4d433ce807782dc31292` |
| Successful deployment run | [34391992401](https://github.com/AyobamiH/poststeward/actions/runs/34391992401) |
| Cloudflare version | `49a1c0bf-21fd-4a36-9146-53247df65c24` |
| Live checks observed | `2026-09-09T18:57:13.505Z` |
| Automated verification | 71 passed, zero failed, skipped or cancelled; TypeScript, generated documentation and bundling passed |
| Hosted verification | 17 passed; exact revision, landing/workspace HTML, discovery/assets, access rejection and Google login initiation |
| Signup and billing | Verified invited owners only; Advanced and MPP disabled |

The [deployment receipt](staging-deployment-2026-09-09.md) links the verification and deployment jobs, records the failure/fix sequence, and distinguishes real hosted checks from simulated-provider tests. Documentation-only commits may follow the deployed revision without redeploying it. The receipt's runtime SHA, not an assumed latest main SHA, identifies the tested service.

D1 database `poststeward-identity-staging` (`d68d73f6-a7b3-4530-82e7-28c2028c050a`) is bound to the Worker. Both identity migrations were applied during the first successful upload; the final deployment found no pending migrations. The required Google and encryption settings were validated during deployment. No D1 permission change, resource recreation or key regeneration remains necessary for this staging deployment.

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

Free and Advanced retain the agreed boundary: direct publishing and explicit scheduling are free; continuing campaign management is paid. Advanced purchases and MPP are disabled in the staging deployment.

## Verification and its boundaries

`npm run verify` passed in [deployment verification job 102602294236](https://github.com/AyobamiH/poststeward/actions/runs/34391992401/job/102602294236): TypeScript checking, generated-document drift checks, Wrangler deployment bundling and **71 automated tests**. CI separately passed on the deployed main revision in [run 34391992198](https://github.com/AyobamiH/poststeward/actions/runs/34391992198).

The runtime integration uses Cloudflare's actual Workers runtime, D1 and SQLite Durable Objects. It verifies authenticated account connection, isolated workspaces, HTTP/MCP operation agreement, alarm-driven dispatch after reservation, duplicate prevention and a native SDK MPP challenge. Provider responses are simulated; these checks did not publish to real accounts.

The added Workers Assets regression uses the real asset implementation and repository HTML, not an asset-response stub. It verifies `/app`, query strings and canonical redirects. It covers the live redirect loop fixed in [PR #6](https://github.com/AyobamiH/poststeward/pull/6). Workflow-contract and hosted-check tests cover the explicit staging request, named secret forwarding, readiness bounds and safe diagnostic output.

OIDC runtime tests use locally signed test tokens and check state replay, forged signatures, verified-email restrictions, session CSRF and grant caps. The hosted check additionally reached real Google issuer discovery, stored login state in deployed D1 and produced the expected Google authorization redirect with PKCE, nonce and a secure cookie. It did not complete Google consent, exchange a real authorization code or establish an owner session. Correct initiation alone does not prove the client secret and registered redirect work end to end.

Payment tests exercise SDK challenge verification, altered credentials/challenges, exact USD 5 input, one-month entitlement, replay prevention and simulated refund revocation. Stripe responses are simulated. No merchant sandbox settlement, real charge or wallet eligibility was verified.

Browser WebMCP contract tests verify scoped registration, consequential annotations and forwarding to the shared handlers. Native supported-browser execution on the deployed origin remains unverified.

The checked-in deployment configuration remains deliberately unsuitable for direct deployment. Preflight rejects placeholder origins/databases, missing OIDC configuration and missing release revisions. The protected deployment workflow generates and validates real environment configuration before any migration.

## Cloudflare configuration and security implementation

Separate staging/production configuration, full-SHA-pinned CI actions, main-only environment deployments, remote D1 identity verification, secret validation and temporary secret-file cleanup are implemented. The explicit staging request forwards only four named repository-level secret fallbacks; selected environment secrets take precedence. Secrets are mapped into process environment variables only for the configuration/deployment steps, not dependency installation, audit, tests or hosted checks. The Cloudflare token is not uploaded to the Worker.

The workflow leaves billing disabled and signup restricted to verified invited owners. Runtime protections include strict host/origin checks, OIDC signature verification, CSRF, malformed-auth rejection, edge and durable workspace request limits, streamed-body deadlines, bounded active grants/login state and expiry cleanup. The hosted checks verified unauthenticated and forged-token rejection, cross-origin rejection, invalid callback-state rejection, no-store session responses and security headers. Live two-owner isolation and grant-revocation acceptance remain separate work.

The production-dependency audit (`npm audit --omit=dev --audit-level=high`) reported zero known vulnerabilities in the accepted deployment verification. The full dependency install still reported **three high-severity advisories** when development/tooling dependencies were included. Those findings were not remediated or characterised here and require triage; the narrower audit result is not a claim that the entire dependency graph is clean or that any dependency is vulnerability-free.

No real owner consent, hosted WAF policy, operational alert delivery or restore rehearsal has been verified. See [setup](deployment.md) and [security boundaries](security.md).

## What remains before launch

1. Complete hosted owner sign-in and session acceptance against Google, then verify invited/uninvited identities, two-owner isolation and grant revocation on staging. Infrastructure setup is complete; do not repeat the previous credential-entry work.
2. Complete provider OAuth onboarding and token refresh/rotation. The current pilot accepts authorised user tokens and verifies account identity. Fresh-customer and controlled live-provider acceptance are still required.
3. Validate native WebMCP in a supported browser and authenticated HTTP/MCP clients against the same live workspace. Remote MCP currently uses scoped Bearer headers; OAuth-compatible MCP authorization discovery remains separate work.
4. Complete richer Advanced profile/category management, private GitHub installation and customer review UI before enabling purchases.
5. Configure Stripe sandbox and merchant MPP eligibility; complete recurring invoice, payment recovery, refund/dispute and eligible-wallet acceptance. Billing remains disabled; no live purchase is authorised by this deployment receipt.
6. Finish production traffic/retention limits, erasure tooling, alerts, restore validation and development/tooling advisory triage. Initial limits are 20 reservations per UTC day, 100 active schedules and 10 source profiles per workspace; these are pilot limits, not proven unit economics. Configure production resources separately after acceptance.

PostSteward runs centrally on the service operator's Cloudflare account. Customers connect accounts and agents to the hosted service. The staging setup inspector can discover existing resource IDs and report credential presence without exposing their values or modifying resources; it is distinct from the completed deployment and hosted acceptance checks above.
