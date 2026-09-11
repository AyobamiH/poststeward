# PostSteward implementation status: 10 September 2026

## New owner sign-in failure — 11 September 2026

A manual Google sign-in in the owner's normal browser reached the application callback and returned `INTERNAL_ERROR`. This is distinct from the Cloud Browser URL-policy block. Owner sign-in and all five live journeys remain unverified. See [the diagnosis and evidence boundary](owner-signin-diagnosis-2026-09-11.md); the callback now has redacted stage diagnostics and staging runs a synthetic-code client probe, neither of which is sign-in acceptance.

PostSteward PR #24 is merged and deployed to restricted staging at `2dc0bfee59a441bbcedd95a59d6877a89c4845f0`. [Deployment run 34541570597](https://github.com/AyobamiH/poststeward/actions/runs/34541570597) passed every hosted gate. See the [five-workstream receipt](live-acceptance-staging-2026-09-10.md) and [execution plan](live-acceptance-plan.md).

**Engineering is deployed; all five live gates remain open.** Google sign-in was rejected by automatic approval review. Provider OAuth, private GitHub and Stripe sandbox are unconfigured. WebMCP API exposure was observed, but authenticated execution remains unverified. Stripe is connected and account discovery succeeded; explicit test-account selection remains pending.

The owner has now approved this browser's Google sign-in and selected Stripe test mode. Stripe Product/Price provisioning and independent readback succeeded. The cloud-browser URL policy blocks /auth/callback (owner screenshot: ERR_BLOCKED_BY_CLIENT); /app remains unauthenticated. Secure Stripe key/webhook configuration is still unavailable through the working integrations. See [the updated receipt](live-acceptance-staging-2026-09-10.md) and [sandbox execution sequence](stripe-sandbox-acceptance.md). These approvals must not be requested again.

## Current engineering evidence

| Evidence | Result |
| --- | --- |
| Runtime | `93215b0467986e4f17f2c6777c1bb69551d66bd9` (PR #22 merge) |
| Cloudflare version | `2b789a06-8c56-47c0-a9be-dfb35dc3b419` |
| Final reviewed-head CI | Run `34537062391`: 188/185 tests, TypeScript, generated docs and Worker build passed; final push CI also passed |
| Dependency audits | Production and full audits: zero known vulnerabilities |
| Deployment | Run `34541570597` passed; migrations already current |
| Hosted assertions | 26 HTTP + 12 owner surfaces + 2 browser viewports + 13 recovery/OAuth + 5 lifecycle = 58 passed |
| Private GitHub | Owner-only free probe deployed; configured `false` |
| X / Threads / LinkedIn OAuth | All configured `false` |
| Stripe sandbox | Staging-only opt-in deployed; configured `false` |
| Public release | Restricted signup; Advanced and MPP disabled |

Earlier receipts remain historical evidence. Documentation-only commits after PR #22 do not imply a new runtime deployment.

## Implemented

| Area | Current implementation |
| --- | --- |
| Hosted foundation | TypeScript Worker, D1 identity, SQLite Durable Objects, alarms, deployment configuration and CI |
| Customer authority | Google OIDC owner sign-in, browser sessions, CSRF protection, scoped revocable agent tokens and workspace isolation |
| Owner completion proof | Minimal proof committed atomically with a validated session; expiry/logout cascade; fixed post-login return paths; legacy sessions cannot approve high-consequence owner flows without signing in again |
| Controlled acceptance | Owner-only `/pilot` UI/API, fresh stable provider identity, immutable expiring review, explicit approval, one durable delivery and private receipt/export |
| Controlled execution | Thirty-second cancellation window, captured session/account/runtime checks, stale-claim fencing, late creation-ID preservation and no one-shot reset through other publishing transports |
| Controlled readback | At most eight separate reads of the known post ID, thirty seconds apart; exact ID/author/text matching; no publication retry to repair missing evidence; LinkedIn is capability-gated by approved member readback |
| Free publishing | Verified account aliases, explicit project routing, immutable approved copy and X/Threads/LinkedIn API adapters |
| Free schedules/evidence | Publish-now reservations, explicit schedules, cancellation/replacement, receipts, export, on-demand metrics and duplicate/uncertain-effect protections |
| Agent surfaces | Native round-trip check and partial-registration cleanup; 26 shared operations across HTTP, remote MCP and browser WebMCP; generated help/reference/OpenAPI and public discovery files |
| Provider OAuth | X PKCE OAuth, Threads long-lived tokens and LinkedIn OAuth with encrypted refresh handling, owner/session binding and identity-drift blocking |
| Private GitHub sources | Owner-only GitHub App setup + user OAuth/PKCE; selected repositories only; read-only Contents/Metadata; maximum 50 repositories; encrypted expiring user access/refresh authority with exclusive pre-refresh leases and CAS rotation; per-read installation/permission/repository revalidation; explicit removal/rename/revocation handling; anonymous public fallback only when no private link exists |
| Private-source UI | Free owner-only probe with commit SHA/time/release and no anonymous fallback; dedicated owner module shows non-secret installation/repository state, constrains navigation to exact `github.com`, supports unlink/reconnect and populates Advanced repository suggestions without removing public free-text input |
| Lifecycle | Pending deletion globally fences GitHub authority routes; completed erasure purges pending GitHub state, encrypted GitHub credentials and repository links alongside other workspace authority |
| Recovery safety | D1 external-effect/container fences, global quarantine, owner-only PITR plans, exact undo and restored-authority invalidation |
| Advanced foundation | Source-path monitoring, deterministic reviewed templates, first-observation baselines, stale-work withdrawal, spacing and independently scheduled metrics |
| Stripe | Staging-only sandbox opt-in with test-key/test-price preflight while Advanced stays disabled; USD 5 monthly Checkout, Portal, signed webhooks, entitlement reconciliation and SDK-backed MPP for a non-renewing calendar-month pass |

Direct publishing and explicit scheduling remain Free; continuing campaign management is the proposed paid boundary. Advanced and MPP are disabled in the active deployment. LinkedIn controlled acceptance is implemented but remains available only when the deployment and connection prove approved member-post readback authority.

## Private GitHub source trust boundary

Private-source installation is deliberately not an agent/MCP/WebMCP operation. A setup-returned `installation_id` is treated only as a candidate. PostSteward binds it to a fresh owner browser session/state and then independently verifies, through GitHub App user OAuth, that the user can access that exact installation. The installation must match the configured app slug, be unsuspended, use selected repositories and have no active permission beyond read-level Contents/Metadata.

PostSteward retains an encrypted expiring GitHub App user credential rather than switching monitoring to broad installation-token authority. Refresh tokens rotate only after an exclusive D1 lease is acquired and commit under credential/revision/lease compare-and-swap. Abandoned or uncertain refreshes require reconnection and cannot replay the old credential. Before every linked private source commit read, the current user-accessible installation and complete bounded repository inventory are re-read. Permission expansion, `all`-repository expansion, suspension, owner/repository access loss or repository removal therefore fails closed before the requested source path is read. A linked private repository is never retried anonymously.

See [private GitHub source authority](private-github-sources.md) for the full contract and exact staging callback/setup URLs.

## Verification interpretation

The 188-test merged implementation checkpoint includes actual Workers/D1/SQLite/Assets execution, signed-token callback processing, proof/session transaction rollback, migration compatibility, expiry/logout/replay handling, CSRF/Bearer rejection, workspace isolation, parallel publication approval, lost response recovery, external-effect fencing, provider OAuth, scheduled metrics and private GitHub authority tests. The GitHub tests cover spoofed installation IDs, broad/write permission rejection, encrypted refreshable credential retention, per-read privilege-drift checks, public anonymous compatibility, deletion fencing and erasure. The PR #20 regressions cover exclusive rotation, interrupted refresh without replay, expired leases, reconnect and unlink races, persistent removal/rename fences, and callbacks delayed across logout/deletion.

Google, GitHub and social-provider responses in CI are fixtures. The source tests do not prove that a real GitHub App was created, that an owner granted a private repository, or that the hosted service successfully read that repository. Hosted checks are deliberately non-destructive: they verify the static private-source module and unauthenticated denial of status/start/probe/unlink/setup/callback rather than creating authority.

The provider client bounds both response bytes and time through the response body. A successful social write status followed by incomplete/unreadable evidence remains ambiguous. Threads readback uses `owner.id`, not a username that can change or be reused. Permalinks and external browser navigation are restricted to exact provider/GitHub hosts.

Payment tests still use simulated Stripe/SDK challenge responses. The PR #24 Workers/D1 regression verifies charge-only dispute routing, signature-before-read, retryable lookup failure, mode rejection, quarantine, event completion and deduplication. Checkout retry tests cover Worker reconstruction and pre-upgrade quotes. No merchant settlement, real charge, refund/dispute lifecycle or eligible wallet has been verified. Native browser WebMCP remains unverified.

## Deployment and operation

PR #20 deployed additive `0009_github_sources.sql` for pending GitHub setup state, encrypted installation/user authority and selected repository links. Workspace erasure explicitly removes all three. The existing staging deployment path remains main-only, exact-SHA, D1-identity-checked and secret-minimised.

GitHub private-source deployment settings are optional but all-or-nothing: `GITHUB_APP_CLIENT_ID` and `GITHUB_APP_SLUG` are non-secret environment variables; `GITHUB_APP_CLIENT_SECRET` is a deploy-step secret. Partial configuration fails before Cloudflare mutation. The verification job receives no application secret.

Before considering an application downgrade, pause publication and inspect active/uncertain publication, recovery and source-authority state. Additive database compatibility alone is not a safe rollback rehearsal. Resolve pending high-consequence state and preserve provider creation evidence before a reviewed rollback.

## Next live milestones

For controlled publication, complete `/pilot`: Google sign-in, authorised provider connection, exact text/destination review, one durable publication reservation and separate provider readback. A prepared review, scheduled job, HTTP 2xx write, screenshot, CI fixture or login redirect alone does not satisfy the evidence gate.

For private sources, create/configure the staging GitHub App using the exact `poststeward-staging.woeinvests.workers.dev` setup/callback URLs, selected repositories and read-only Contents permission. Save the protected client ID/slug/secret, deploy the reviewed main revision, then as the invited owner connect one explicitly selected private repository. Confirm only the selected repository appears, perform one read-only source observation, remove/revoke access and prove the next check fails closed. Do not enable paid Advanced execution merely to prove repository access.

## Remaining public-release work

1. Complete real Google owner consent and a real provider grant, then record exactly one controlled publication plus independent provider readback.
2. Supply/approve provider application credentials. The OAuth, refresh and LinkedIn-readback code is implemented; unavailable providers must remain fail-closed until configured.
3. Configure the real staging GitHub App and complete one owner-authorised private-repository grant/read/revocation acceptance. Private-source authority engineering and owner UI are implemented; richer Advanced inventory/category management remains product work.
4. Run Stripe sandbox settlement, renewal, refund/dispute and eligible-wallet MPP acceptance before enabling Advanced or MPP.
5. Perform an explicitly approved staging PITR rehearsal, native WebMCP acceptance in a supporting browser, capacity/retention calibration, encryption-key rotation, operational alerts, cross-tenant hosted attack testing and production-domain/WAF acceptance. The account-erasure path is implemented and must be exercised with real staging data rather than described as missing code.
6. Enable an actual GitHub main ruleset with required Verify checks. The deployment path has merged-PR provenance enforcement, but that is not a substitute for server-side branch protection.

Pilot delivery limits remain 20 reservations per UTC day, 100 active schedules and 10 source profiles per workspace. Private GitHub installations are additionally bounded to 50 selected repositories. These are initial limits, not proven unit economics. PostSteward is hosted centrally on the service operator's Cloudflare account; customers connect to the product rather than provision their own Cloudflare stack.
