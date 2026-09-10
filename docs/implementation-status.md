# PostSteward implementation status: 10 September 2026

PostSteward is deployed to restricted staging in [AyobamiH/poststeward](https://github.com/AyobamiH/poststeward). The owner sign-in and controlled-publication acceptance workflow is implemented and hosted verification passed. **Real owner Google consent and the first real controlled publication have not been recorded.** Public customer acceptance and public charging remain outstanding.

## Current engineering evidence

| Evidence                      | Recorded result                                                                                                       |
| ----------------------------- | --------------------------------------------------------------------------------------------------------------------- |
| Owner acceptance page         | `https://poststeward-staging.woeinvests.workers.dev/pilot`                                                            |
| Active runtime revision       | `28579527895955651b2f4b7ea61227ce194113a6` (PR #13 merge)                                                             |
| Cloudflare version            | `464a8dc4-f3ae-4965-94df-2259bc7c0339`                                                                                |
| PR #13 merge deployment       | Run `34448815060`: migrations `0005` and `0006`, exact revision upload and all hosted checks succeeded                |
| PR #13 candidate verification | Run `34413334443`: 130 tests passed, zero failed/skipped/cancelled; type checking, generated docs and bundling passed |
| Hosted verification           | 17 base HTTP checks, 12 controlled-publication checks and two fresh unauthenticated Chromium contexts passed          |
| Dependency audits             | Production and full high-severity audits both report zero known vulnerabilities                                       |
| Provider app configuration    | X, Threads and LinkedIn OAuth client IDs are currently absent in staging; implementation remains fail-closed          |
| Public release                | Restricted signup; Advanced and MPP disabled                                                                          |

The active Worker is no longer the older owner-acceptance revision recorded in the 9 September receipt. PR #11 added provider OAuth/readback, PR #12 closed the dependency audit findings and PR #13 added restore-safe effect fencing/PITR. This document supersedes the stale 99-test/advisory status while preserving those historical receipts.

## Implemented

| Area                        | Current implementation                                                                                                                                                                                            |
| --------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Hosted foundation           | TypeScript Worker, D1 identity, SQLite Durable Objects, alarms, deployment configuration and CI                                                                                                                   |
| Customer authority          | OIDC owner sign-in, browser sessions, CSRF protection, scoped revocable agent tokens and workspace isolation                                                                                                      |
| Owner completion proof      | Minimal proof committed atomically with a validated session; expiry/logout cascade; fixed post-login return paths; legacy sessions cannot approve the pilot without signing in again                              |
| Controlled acceptance       | Owner-only `/pilot` UI/API, fresh stable provider identity, immutable expiring review, explicit approval, one durable delivery and private receipt/export                                                         |
| Controlled execution        | Thirty-second cancellation window, captured session/account/runtime checks, stale-claim fencing, late creation-ID preservation and no one-shot reset through other publishing transports                          |
| Controlled readback         | At most eight separate reads of the known post ID, thirty seconds apart; exact ID/author/text matching; no publication retry to repair missing evidence; LinkedIn is capability-gated by approved member readback |
| Free publishing             | Verified account aliases, explicit project routing, immutable approved copy and X/Threads/LinkedIn API adapters                                                                                                   |
| Free schedules and evidence | Publish-now reservations, explicit schedules, cancellation/replacement, receipts, export, on-demand metrics and duplicate/uncertain-effect protections                                                            |
| Agent surfaces              | 26 shared operations across HTTP, remote MCP and browser WebMCP; generated help/reference/OpenAPI and public discovery files                                                                                      |
| Provider OAuth              | X PKCE OAuth, Threads long-lived tokens and LinkedIn OAuth with encrypted refresh handling, owner/session binding and identity-drift blocking                                                                     |
| Recovery safety             | D1 external-effect/container fences, global quarantine, owner-only PITR plans, exact undo and restored-authority invalidation                                                                                     |
| Advanced foundation         | Public source-path monitoring, deterministic reviewed templates, first-observation baselines, stale-work withdrawal, spacing and scheduled metrics                                                                |
| Stripe                      | USD 5 monthly Checkout, Portal, signed webhooks, entitlement reconciliation and SDK-backed MPP for a non-renewing calendar-month pass                                                                             |

Direct publishing and explicit scheduling remain Free; continuing campaign management is the proposed paid boundary. Advanced and MPP are disabled in the active deployment. LinkedIn controlled acceptance is implemented but remains available only when the deployment and connection prove approved member-post readback authority.

## Verification interpretation

The current 130-test suite includes actual Workers/D1/SQLite/Assets execution, validated signed-token callback processing, proof/session transaction rollback, migration compatibility, expiry/logout and replay handling, malformed or wrong claims, CSRF/Bearer rejection, workspace isolation, parallel approval, lost response recovery, stale execution claims, immutable account routing, cancellation, cloned-fingerprint bypass prevention and bounded readback. The end-to-end runtime acceptance uses a real thirty-second alarm and exactly one simulated provider write followed by independent simulated readback. **Google and provider responses in CI are fixtures, not live acceptance.**

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

1. Complete real Google owner consent and a real provider grant, then record exactly one controlled publication plus independent provider readback.
2. Supply/approve provider application credentials. The OAuth, refresh and LinkedIn-readback code is implemented; staging currently exposes those providers as unavailable because no provider app IDs are configured.
3. Finish private GitHub source installation plus richer Advanced category management. The owner review/configuration UI is being promoted into the main workspace before purchases are enabled.
4. Run Stripe sandbox settlement, renewal, refund/dispute and eligible-wallet MPP acceptance before enabling Advanced or MPP.
5. Perform an explicitly approved staging PITR rehearsal, native WebMCP acceptance in a supporting browser, capacity/retention calibration, account erasure, encryption-key rotation, operational alerts, cross-tenant hosted attack testing and production-domain/WAF acceptance.
6. Enable an actual GitHub main ruleset with required Verify checks. The repository currently has no ruleset; the deployment path now adds its own merged-PR provenance gate but that is not a substitute for server-side branch protection.

Pilot delivery limits remain 20 reservations per UTC day, 100 active schedules and 10 source profiles per workspace. They are initial limits, not proven unit economics. PostSteward is hosted centrally on the service operator's Cloudflare account; customers connect to the product rather than provision their own Cloudflare stack.
