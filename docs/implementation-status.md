# PostSteward implementation status: 9 September 2026

PostSteward is deployed to restricted staging in [AyobamiH/poststeward](https://github.com/AyobamiH/poststeward). The owner sign-in and controlled-publication acceptance workflow is implemented and hosted verification passed. **Real owner Google consent and the first real controlled publication have not been recorded.** Public customer acceptance and public charging remain outstanding.

## Current engineering evidence

| Evidence | Recorded result |
| --- | --- |
| Owner acceptance page | `https://poststeward-staging.woeinvests.workers.dev/pilot` |
| Active runtime revision | `00544af2ae1333357c4f6b427541007cbd2206ba` |
| Cloudflare version | `5afe3331-bfe8-4c7b-9215-684dc0bf98ba` |
| Upload and additive migration run | [34399141159](https://github.com/AyobamiH/poststeward/actions/runs/34399141159): migration/upload succeeded; initial stylesheet readiness check failed |
| Accepted existing-runtime verification | [34399839232](https://github.com/AyobamiH/poststeward/actions/runs/34399839232), job 102628706573: success |
| Automated verification | [34399698258](https://github.com/AyobamiH/poststeward/actions/runs/34399698258), job 102628237086: 99 tests passed, zero failed/skipped/cancelled; type checking, generated docs and bundling passed |
| Hosted verification | 17 existing HTTP checks plus 12 owner acceptance checks: all passed |
| Browser verification | Two fresh unauthenticated Chromium contexts, requested wide/narrow window sizes: JavaScript settled and owner controls stayed blocked |
| Last hosted observation | `2026-09-09T20:14:58.731Z` |
| Production dependency audit | Zero known vulnerabilities in the production-only audit |
| Full-install audit | Three high-severity advisories with development/tooling dependencies included; still open |

Read the [current engineering receipt](owner-acceptance-deployment-2026-09-09.md) for exact run identities and boundaries. The [earlier infrastructure receipt](staging-deployment-2026-09-09.md) is historical. Main contains later verification/documentation changes and need not equal the deployed runtime SHA. No redeployment was needed to resolve the initial static-asset 404: the same runtime subsequently served the stylesheet with HTTP 200.

## Implemented

| Area | Current implementation |
| --- | --- |
| Hosted foundation | TypeScript Worker, D1 identity, SQLite Durable Objects, alarms, deployment configuration and CI |
| Customer authority | OIDC owner sign-in, browser sessions, CSRF protection, scoped revocable agent tokens and workspace isolation |
| Owner completion proof | Minimal proof committed atomically with a validated session; expiry/logout cascade; fixed post-login return paths; legacy sessions cannot approve the pilot without signing in again |
| Controlled acceptance | Owner-only `/pilot` UI/API, fresh stable provider identity, immutable expiring review, explicit approval, one durable delivery and private receipt/export |
| Controlled execution | Thirty-second cancellation window, captured session/account/runtime checks, stale-claim fencing, late creation-ID preservation and no one-shot reset through other publishing transports |
| Controlled readback | At most eight separate reads of the known post ID, thirty seconds apart; exact ID/author/text matching; no publication retry to repair missing evidence |
| Free publishing | Verified account aliases, explicit project routing, immutable approved copy and X/Threads/LinkedIn API adapters |
| Free schedules and evidence | Publish-now reservations, explicit schedules, cancellation/replacement, receipts, export, on-demand metrics and duplicate/uncertain-effect protections |
| Agent surfaces | 26 shared operations across HTTP, remote MCP and browser WebMCP; generated help/reference/OpenAPI and public discovery files |
| Advanced foundation | Public source-path monitoring, deterministic reviewed templates, first-observation baselines, stale-work withdrawal, spacing and scheduled metrics |
| Stripe | USD 5 monthly Checkout, Portal, signed webhooks, entitlement reconciliation and SDK-backed MPP for a non-renewing calendar-month pass |

Direct publishing and explicit scheduling remain Free; continuing campaign management is the proposed paid boundary. Advanced and MPP are disabled in the active deployment. Pilot acceptance supports X or Threads only because the current LinkedIn member adapter cannot independently read back a post; this does not remove general LinkedIn support.

## Verification interpretation

The 99-test suite includes actual Workers/D1/SQLite/Assets execution, validated signed-token callback processing, proof/session transaction rollback, migration compatibility, expiry/logout and replay handling, malformed or wrong claims, CSRF/Bearer rejection, workspace isolation, parallel approval, lost response recovery, stale execution claims, immutable account routing, cancellation, cloned-fingerprint bypass prevention and bounded readback. The end-to-end runtime acceptance uses a real thirty-second alarm and exactly one simulated provider write followed by independent simulated readback. **Google and provider responses in CI are fixtures, not live acceptance.**

The provider client bounds both response bytes and time through the response body. A successful write status followed by incomplete/unreadable evidence remains ambiguous. Threads readback uses `owner.id`, not a username that can change or be reused. Permalinks are restricted to exact provider hosts. These changes have automated coverage; they do not establish a real provider publication until the owner completes the hosted acceptance.

The hosted HTTP suites check exact runtime identity, original public assets/discovery, invitation/authentication boundaries, pilot HTML/JavaScript/styles, unauthenticated pilot action rejection, cross-origin rejection, fixed return-path rejection and Google login initiation. The browser check uses fresh unauthenticated Chromium profiles at two requested window sizes, without deployment credentials; it verifies settled JavaScript and blocked owner controls. It is not a full accessibility audit, mobile-device certification, authenticated user journey or native WebMCP test.

Payment tests still use simulated Stripe/SDK challenge responses. No merchant settlement, real charge, refund/dispute lifecycle or eligible wallet has been verified. Native browser WebMCP remains unverified.

## Deployment and operation

PR #8 introduced the application and additive `0003_owner_proofs.sql` expansion. The migration leaves the old login-state table shape intact, adding dependent return-path and owner-proof tables. Existing login-state SQL can run during deployment or schema-compatible rollback; a regression proves that database contract, not arbitrary safe application downgrade with active jobs.

PR #9 added bounded static GET/404 readiness and a credential-free existing-runtime verification workflow. Persistent 404s, redirects, authentication failures and failed content assertions still fail. Verification does not repeat a deployment or publish a post. Login probes create expiring state records but not authenticated owner sessions.

Existing keys, D1 and Durable Object resources were retained. Main-only staging requests, named secret forwarding, separate verification jobs, exact database/release checks, invite-only signup and disabled billing remain in place. The deployment workflow never writes an owner session directly to bypass Google consent.

Before considering an application downgrade, pause publication and inspect all active/uncertain pilot deliveries. Do not run pre-pilot code with pending session-bound work: older code does not enforce the new review/session fields. Additive database compatibility alone is not a safe rollback rehearsal. Resolve or safely terminalise pending work and preserve any provider creation evidence before a reviewed rollback.

## Next live milestone

The implemented owner journey is `/pilot`: complete Google sign-in, explicitly choose or connect an authorised X/Threads account, prepare the exact text, review the stable provider ID and approve one publication. The page recovers its existing receipt after reload and offers bounded post-ID readback and private export. Once the reservation is acknowledged, closing the browser does not cancel it; the Durable Object alarm owns execution. The thirty-second cancellation window precedes dispatch. No cancellation guarantee is made after a provider write starts.

A successful live milestone requires the owner proof, captured approval, durable creation ID and separate exact provider readback in the receipt. A prepared review, scheduled job, HTTP 2xx write, screenshot, CI fixture or login redirect alone does not satisfy it. The pilot slot remains consumed after reservation, including cancellation or uncertainty; there is no reset/republication escape in this API.

## Remaining public-release work

1. Complete and record real Google consent, authenticated browser interaction, invited/uninvited-owner isolation/revocation and the first controlled provider publication/readback. These are still pending, not claimed completed by deployment.
2. Complete self-service provider OAuth onboarding and token refresh/rotation. The restricted pilot currently imports an authorised user token over its authenticated application. Do not borrow another product's credentials or call token import a completed OAuth onboarding product.
3. Complete richer Advanced profile/category-management, private GitHub installation and customer review UI before purchases.
4. Configure Stripe sandbox and merchant MPP eligibility; finish recurring invoice, recovery, refund/dispute and eligible-wallet acceptance before any live charge.
5. Finish production traffic/retention limits, account erasure, rotation, operational alerts, restore rehearsal and development/tooling advisory triage. Choose and verify the production domain and its separate resources.

Pilot delivery limits remain 20 reservations per UTC day, 100 active schedules and 10 source profiles per workspace. They are initial limits, not proven unit economics. PostSteward is hosted centrally on the service operator's Cloudflare account; customers connect to the product rather than provision their own Cloudflare stack.
