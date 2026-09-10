# PostSteward implementation status: 10 September 2026

PostSteward is deployed to restricted staging in [AyobamiH/poststeward](https://github.com/AyobamiH/poststeward). The active deployed runtime remains the PR #13 merge revision recorded below until the current private-GitHub-source candidate is reviewed, merged and deployed. **Real owner Google consent and the first real controlled publication have not been recorded. A real private GitHub repository grant/read has also not been recorded.** Public customer acceptance and public charging remain outstanding.

The current release candidate implements the previously missing private GitHub source authority and owner workspace controls. At checkpoint `1d3b8cbfc5f2e1ba43515b8e8f3d900096137ed7`, Verify run `34499989830` passed **169/169 tests**, TypeScript, generated-document checks, dry-run bundling and both production/full high-severity dependency audits. Later commits in this branch are documentation-only unless otherwise noted; the final PR head must receive its own green Verify result before merge.

## Current engineering evidence

| Evidence | Recorded result |
| --- | --- |
| Owner acceptance page | `https://poststeward-staging.woeinvests.workers.dev/pilot` |
| Active deployed runtime | `28579527895955651b2f4b7ea61227ce194113a6` (PR #13 merge) |
| Cloudflare version | `464a8dc4-f3ae-4965-94df-2259bc7c0339` |
| PR #13 merge deployment | Run `34448815060`: migrations `0005` and `0006`, exact revision upload and its hosted checks succeeded |
| Private-source candidate verification | Run `34499989830` at checkpoint `1d3b8cb…`: 169 tests passed, zero failed/skipped/cancelled; typecheck/docs/build passed |
| Candidate hosted verifier | 25 non-destructive base surfaces, including static private-source UI plus unauthenticated denial of all five GitHub source authority routes |
| Dependency audits | Production and full high-severity audits both report zero known vulnerabilities at the candidate checkpoint |
| Provider app configuration | X, Threads and LinkedIn OAuth client IDs were absent in the last recorded staging evidence; implementation remains fail-closed until configured |
| Private GitHub staging evidence | GitHub App code/config contract is implemented; real staging App credentials, owner installation and private repository read remain external evidence |
| Public release | Restricted signup; Advanced and MPP disabled |

The historical 9 September deployment/acceptance receipts remain evidence for the active deployed runtime. This document distinguishes that live revision from the newer candidate instead of presenting CI fixtures as deployed behaviour.

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
| Agent surfaces | 26 shared operations across HTTP, remote MCP and browser WebMCP; generated help/reference/OpenAPI and public discovery files |
| Provider OAuth | X PKCE OAuth, Threads long-lived tokens and LinkedIn OAuth with encrypted refresh handling, owner/session binding and identity-drift blocking |
| Private GitHub sources | Owner-only GitHub App setup + user OAuth/PKCE; selected repositories only; read-only Contents/Metadata; maximum 50 repositories; encrypted expiring user access/refresh authority with CAS rotation; per-read installation/permission/repository revalidation; explicit removal/rename/revocation handling; anonymous public fallback only when no private link exists |
| Private-source UI | Dedicated owner module shows non-secret installation/repository state, constrains navigation to exact `github.com`, supports unlink/reconnect and populates Advanced repository suggestions without removing public free-text input |
| Lifecycle | Pending deletion globally fences GitHub authority routes; completed erasure purges pending GitHub state, encrypted GitHub credentials and repository links alongside other workspace authority |
| Recovery safety | D1 external-effect/container fences, global quarantine, owner-only PITR plans, exact undo and restored-authority invalidation |
| Advanced foundation | Source-path monitoring, deterministic reviewed templates, first-observation baselines, stale-work withdrawal, spacing and independently scheduled metrics |
| Stripe | USD 5 monthly Checkout, Portal, signed webhooks, entitlement reconciliation and SDK-backed MPP for a non-renewing calendar-month pass |

Direct publishing and explicit scheduling remain Free; continuing campaign management is the proposed paid boundary. Advanced and MPP are disabled in the active deployment. LinkedIn controlled acceptance is implemented but remains available only when the deployment and connection prove approved member-post readback authority.

## Private GitHub source trust boundary

Private-source installation is deliberately not an agent/MCP/WebMCP operation. A setup-returned `installation_id` is treated only as a candidate. PostSteward binds it to a fresh owner browser session/state and then independently verifies, through GitHub App user OAuth, that the user can access that exact installation. The installation must match the configured app slug, be unsuspended, use selected repositories and have no active permission beyond read-level Contents/Metadata.

PostSteward retains an encrypted expiring GitHub App user credential rather than switching monitoring to broad installation-token authority. Refresh tokens rotate under D1 compare-and-swap. Before every linked private source commit read, the current user-accessible installation and complete bounded repository inventory are re-read. Permission expansion, `all`-repository expansion, suspension, owner/repository access loss or repository removal therefore fails closed before the requested source path is read. A linked private repository is never retried anonymously.

See [private GitHub source authority](private-github-sources.md) for the full contract and exact staging callback/setup URLs.

## Verification interpretation

The 169-test candidate checkpoint includes actual Workers/D1/SQLite/Assets execution, signed-token callback processing, proof/session transaction rollback, migration compatibility, expiry/logout/replay handling, CSRF/Bearer rejection, workspace isolation, parallel publication approval, lost response recovery, external-effect fencing, provider OAuth, scheduled metrics and private GitHub authority tests. The GitHub tests cover spoofed installation IDs, broad/write permission rejection, encrypted refreshable credential retention, per-read privilege-drift checks, public anonymous compatibility, deletion fencing and erasure.

Google, GitHub and social-provider responses in CI are fixtures. The source tests do not prove that a real GitHub App was created, that an owner granted a private repository, or that the hosted service successfully read that repository. Hosted checks are deliberately non-destructive: they verify the static private-source module and unauthenticated denial of status/start/unlink/setup/callback rather than creating authority.

The provider client bounds both response bytes and time through the response body. A successful social write status followed by incomplete/unreadable evidence remains ambiguous. Threads readback uses `owner.id`, not a username that can change or be reused. Permalinks and external browser navigation are restricted to exact provider/GitHub hosts.

Payment tests still use simulated Stripe/SDK challenge responses. No merchant settlement, real charge, refund/dispute lifecycle or eligible wallet has been verified. Native browser WebMCP remains unverified.

## Deployment and operation

The current candidate adds additive `0009_github_sources.sql` for pending GitHub setup state, encrypted installation/user authority and selected repository links. Workspace erasure explicitly removes all three. The existing staging deployment path remains main-only, exact-SHA, D1-identity-checked and secret-minimised.

GitHub private-source deployment settings are optional but all-or-nothing: `GITHUB_APP_CLIENT_ID` and `GITHUB_APP_SLUG` are non-secret environment variables; `GITHUB_APP_CLIENT_SECRET` is a deploy-step secret. Partial configuration fails before Cloudflare mutation. The verification job receives no application secret.

Before considering an application downgrade, pause publication and inspect active/uncertain publication, recovery and source-authority state. Additive database compatibility alone is not a safe rollback rehearsal. Resolve pending high-consequence state and preserve provider creation evidence before a reviewed rollback.

## Next live milestones

For controlled publication, complete `/pilot`: Google sign-in, authorised provider connection, exact text/destination review, one durable publication reservation and separate provider readback. A prepared review, scheduled job, HTTP 2xx write, screenshot, CI fixture or login redirect alone does not satisfy the evidence gate.

For private sources, create/configure the staging GitHub App using the exact `poststeward-staging.woeinvests.workers.dev` setup/callback URLs, selected repositories and read-only Contents permission. Save the protected client ID/slug/secret, deploy the reviewed main revision, then as the invited owner connect one harmless private repository. Confirm only the selected repository appears, perform one read-only source observation, remove/revoke access and prove the next check fails closed. Do not enable paid Advanced execution merely to prove repository access.

## Remaining public-release work

1. Complete real Google owner consent and a real provider grant, then record exactly one controlled publication plus independent provider readback.
2. Supply/approve provider application credentials. The OAuth, refresh and LinkedIn-readback code is implemented; unavailable providers must remain fail-closed until configured.
3. Configure the real staging GitHub App and complete one owner-authorised private-repository grant/read/revocation acceptance. Private-source authority engineering and owner UI are implemented; richer Advanced inventory/category management remains product work.
4. Run Stripe sandbox settlement, renewal, refund/dispute and eligible-wallet MPP acceptance before enabling Advanced or MPP.
5. Perform an explicitly approved staging PITR rehearsal, native WebMCP acceptance in a supporting browser, capacity/retention calibration, encryption-key rotation, operational alerts, cross-tenant hosted attack testing and production-domain/WAF acceptance. The account-erasure path is implemented and must be exercised with real staging data rather than described as missing code.
6. Enable an actual GitHub main ruleset with required Verify checks. The deployment path has merged-PR provenance enforcement, but that is not a substitute for server-side branch protection.

Pilot delivery limits remain 20 reservations per UTC day, 100 active schedules and 10 source profiles per workspace. Private GitHub installations are additionally bounded to 50 selected repositories. These are initial limits, not proven unit economics. PostSteward is hosted centrally on the service operator's Cloudflare account; customers connect to the product rather than provision their own Cloudflare stack.
