# Live external acceptance gates — 13 September 2026

This ledger is the canonical continuation after the engineering-gap reconciliation and protected root-cutover integration. It separates evidence already accepted from live provider/browser/service gates that still require the external system itself. Do not recreate completed effects merely to obtain another receipt.

Last reconciled restricted-staging release: `93b526bb31f6de39c2386b51063532e2bf4282b9`, deployed successfully on 14 September 2026. Exact current runtime identity must still be read from `/readiness.json` rather than inferred from this document.

## Evidence that is already accepted and must be preserved

- Owner Google sign-in has already been accepted from the owner's real browser journey.
- Threads controlled publication and independent provider readback are accepted from the existing owner receipt. Do not publish again merely to close OAuth.
- Inspect-only agent authority over HTTP and remote MCP is accepted, including denial of the same token after owner revocation. Do not mint/revoke another token merely to repeat this proof.
- Stripe sandbox acceptance already includes completed subscription Checkout, paid application state, full refund with revoked entitlement, operator cancellation, and the persisted completed webhook ledger evidence. Do not create another payment/refund/cancellation merely for acceptance.
- Protected encryption-root cutover is accepted. The active writer remains `next`; the legacy root is retained for recovery and must not be retired merely to obtain another receipt.
- Private GitHub source authority is accepted from the real staging App journey on release `074931bfab5cb492dc391e44347efca5bd949042`: selected-repository installation, owner OAuth, successful private probe, provider-side App uninstall, stale-authority detection, and a subsequent probe that failed closed without anonymous fallback.

Automated tests, configured flags, redirects and screenshots remain supporting evidence only. A live gate closes only when the external system produced the relevant effect and PostSteward independently observed the required state.

## Gate 1 — Threads OAuth callback

**State: blocked upstream, not a PostSteward code gap.**

The staging Threads client and secret are present. The exact PostSteward callback is:

`https://poststeward-staging.woeinvests.workers.dev/connections/oauth/threads/callback`

Meta's dashboard currently rejects saving the callback allowlist. The owner already reproduced the failure in more than one browser/app context. The request reached Meta's Threads settings save route and returned its own not-found response. Meta's public sample issue tracker also contains an open 2026 report for the same class of Threads callback-URL save failure:

- https://github.com/fbsamples/threads_api/issues/73
- https://github.com/fbsamples/threads_api

Do not weaken redirect validation, reuse another product's credential, manufacture a callback, or create another publication. Close this gate only after Meta accepts the exact callback and PostSteward completes its own code exchange and stable-identity persistence.

## Gate 2 — private GitHub source authority

**State: accepted on real staging provider journey.**

The dedicated staging GitHub App is configured with the required contract:

- Repository access: selected repositories only.
- Repository permission: Contents read-only; Metadata read-only as supplied by GitHub.
- User-to-server token expiration: enabled.
- Request user authorization (OAuth) during installation: disabled.
- Setup URL: `https://poststeward-staging.woeinvests.workers.dev/sources/github/setup`
- Callback URL: `https://poststeward-staging.woeinvests.workers.dev/sources/github/callback`
- No webhook and no write permission.

GitHub Actions reserves the `GITHUB_` prefix for its own names. Protected staging storage is therefore all-or-nothing under:

- variable `POSTSTEWARD_GITHUB_APP_CLIENT_ID`
- variable `POSTSTEWARD_GITHUB_APP_SLUG`
- secret `POSTSTEWARD_GITHUB_APP_CLIENT_SECRET`

The deployment workflow maps those protected names to the application's existing runtime `GITHUB_APP_*` contract. Do not create user-defined GitHub Actions variables or secrets whose names start with `GITHUB_`.

Accepted live evidence:

1. PR #54 fixed the protected-environment naming incompatibility and was merged after exact-head verification.
2. Protected staging deployment `34770161726` completed successfully on release `074931bfab5cb492dc391e44347efca5bd949042`; the hosted report showed `githubPrivateSources: true`.
3. The owner installed `poststeward-staging-ayobamih` for selected private repository `AyobamiH/april-codewars` only and completed the GitHub user OAuth journey.
4. A harmless owner probe of branch `master`, path `solution001.js`, returned private-repository metadata and commit SHA `e51cc5e66ab1dd6e18b2803f01a141c6c7609506` on the exact hosted release.
5. The owner then uninstalled the GitHub App at GitHub, without first unlinking it in PostSteward.
6. PostSteward independently detected the provider-side revocation as stale authority and the next private-source probe failed closed with `GitHub repository authorization is stale. Reconnect GitHub.` No anonymous fallback or stale private read occurred.

Do not reconnect or repeat the private-read/revoke journey merely to obtain another receipt. Reconnection is only needed if staging later needs private-source functionality for another purpose.

Primary GitHub contract:
https://docs.github.com/en/apps/creating-github-apps/registering-a-github-app/about-the-user-authorization-callback-url

## Gate 3 — real Cloudflare PITR and disposable workspace erasure

**State: PITR target resolution is blocked at Cloudflare's hosted `getBookmarkForTime()` primitive; restore has never been armed. The live synthetic erasure path executed successfully, but the full PITR-plus-erasure gate remains open.**

Workflow: `.github/workflows/staging-disposable-pitr-erasure.yml`

The workflow is manual, staging-only, restricted to `AyobamiH` on `main`, and requires the explicit confirmation `RUN_DISPOSABLE_PITR_ERASURE`. It verifies the exact reviewed release and creates a random synthetic owner/workspace in the protected staging environment. It never uses the existing owner acceptance workspace.

Three live manual runs failed closed during **recovery preparation**, before `/api/recovery/execute` could arm a restore:

1. The first run exposed an unsafe assumption around a newly-created synthetic object's very young PITR history. No recovery plan or restore was claimed.
2. Run `34777210583` on release `d04bb4af81db7fa34b893921c78134c9c38769fe` still failed during `/api/recovery/prepare`; restore execution never started.
3. Run `34779491079` on release `615894758934509b69d95353cb162f08e6524e01` used durable-status-aware preparation. It made 27 bounded attempts across the full eight-minute window. After every uncertain response, PostSteward independently verified that no recovery plan was active and quarantine had safely released before another non-destructive prepare attempt. No restore was armed.

PR #57 then split the hosted PITR bookmark acquisition into individually classified primitives and added a one-shot, **prepare-only** staging diagnostic. The diagnostic is regression-fenced from `/api/recovery/execute`: it can only initialise a synthetic workspace, synchronise storage, test preparation, cancel a prepared plan if one exists, and erase its own synthetic workspace.

Protected deployment `34780704136` on release `acd3e679a84a5dacf2d8a0bbfc944293de5a3fe5` passed its exact-revision deployment and hosted boundaries. Its automatic PITR diagnostic produced:

- `getCurrentBookmark()` succeeded.
- `getBookmarkForTime()` failed with the bounded classification `RECOVERY_PITR_TARGET_BOOKMARK_FAILED`.
- no restore was executed;
- no provider effect or payment was attempted;
- the synthetic workspace lifecycle deletion completed and the diagnostic reported `cleanupVerified: true`.

This is now a hosted platform boundary, not a reason to keep blindly changing wait times or replaying the destructive workflow. Cloudflare's published SQLite-backed Durable Object contract documents `getBookmarkForTime(number | Date)` as the supported way to resolve an approximate point in the previous 30 days. PostSteward keeps the failure fail-closed and does not substitute a different recovery time silently.

Do **not** ask the owner to run the manual PITR workflow again until either:

- Cloudflare's target-bookmark primitive succeeds in the reviewed staging runtime; or
- PostSteward deliberately implements and verifies an explicit recovery-checkpoint fallback whose semantics are visible to the owner rather than silently changing the requested restore point.

The disposable-erasure execution is useful live evidence, but the original end-to-end gate also requires the restore/reconcile/resume journey and independent post-erasure assertions. Therefore do not mark the complete gate accepted yet.

Primary Cloudflare PITR contract:
https://developers.cloudflare.com/durable-objects/api/sqlite-storage-api/#pitr-point-in-time-recovery-api

## Gate 4 — authenticated native WebMCP

**State: live verified on 20 September 2026 in an authenticated supporting browser.**

The follow-up evidence is recorded in [native WebMCP live evidence](native-webmcp-live-evidence-2026-09-20.md). Native `workspace_status`, `accounts_list`, `projects_list` and `receipts_list` calls succeeded and matched exact production release `7a7f0fa698b1f21b01c5ad2971f13174d4249def`. This closes read-only native registration/execution only; it does not imply provider writes or other live gates.

Evidence on 14 September 2026:

- the owner was authenticated in the real staging workspace and saw restricted signup, Free publishing and the expected current workspace state;
- that browser explicitly reported `Remote MCP and HTTP are available. Native WebMCP is not available in this browser.`;
- this is a browser-capability result, not a PostSteward registration failure;
- PR #59 hardened the native path so registered tools abort on page teardown and the read-only native `workspace_status` check must match both the authenticated workspace and the exact current `/readiness.json` release;
- protected deployment `34851168519` successfully deployed release `93b526bb31f6de39c2386b51063532e2bf4282b9`, including the updated `/webmcp.js`; hosted smoke remained green and did not manufacture an owner session or native invocation.

The earlier supporting cloud browser observation showed that native WebMCP can be exposed by a suitable browser. The required authenticated read-only invocation has now completed; do not repeat it merely to obtain a newer receipt.

Do not substitute the already accepted HTTP/remote-MCP proof, a JavaScript shim, a signed-out capability observation or another browser that explicitly reports native WebMCP unavailable.

## Gate 5 — X provider application and grant

**State: application client/secret absent in protected staging.**

Callback:
`https://poststeward-staging.woeinvests.workers.dev/connections/oauth/x/callback`

The implementation uses OAuth 2.0 Authorization Code + PKCE and requires `tweet.read`, `tweet.write`, `users.read`, and `offline.access`. `offline.access` is required for refresh authority. Protected configuration is `X_OAUTH_CLIENT_ID` plus secret `X_OAUTH_CLIENT_SECRET`.

Close with the real registered application, exact callback, customer-funded API authority where required by X, owner consent, stored stable identity/capability evidence and refresh authority. A manually imported access token does not substitute for this OAuth acceptance.

Primary X contract:
https://docs.x.com/fundamentals/authentication/oauth-2-0/authorization-code

## Gate 6 — LinkedIn provider application and grant

**State: PostSteward Page identity is independently known; provider application/grant and live organisation OAuth acceptance remain open.**

Callback:
`https://poststeward-staging.woeinvests.workers.dev/connections/oauth/linkedin/callback`

Verified Page:

- URL: `https://www.linkedin.com/company/poststeward/`
- organisation ID: `146607525`
- actor URN: `urn:li:organization:146607525`

This Page is administered by the same LinkedIn member account that administers the owner's other Pages. The member remains the OAuth subject; the Page URN is a separately reviewed organisation actor.

For a member-profile connection, the existing base scopes remain `openid`, `profile`, and `w_member_social`; restricted `r_member_social` is never inferred.

For the reviewed PostSteward Page actor, the OAuth journey requests `w_organization_social` and `r_organization_social` through a dedicated Community Management-only application. The exact actor URN is stored in one-use OAuth state before redirect, then the callback performs LinkedIn's permission-gated author finder for that exact Page before the connection can be stored. The member remains the interactive OAuth subject, but OpenID scopes are neither requested nor used on this Page path. A denied Page read remains a provider authority gate and is never converted into member authority.

Protected Page configuration is `LINKEDIN_ORGANIZATION_OAUTH_CLIENT_ID` plus secret `LINKEDIN_ORGANIZATION_OAUTH_CLIENT_SECRET`. The separate `LINKEDIN_OAUTH_CLIENT_ID` pair is reserved for member-profile/OpenID authority. The independently verified Page ID does **not** establish that the Page application has been granted the required organisation permissions.

Close this gate only after the real LinkedIn application is configured, the owner completes OAuth for `urn:li:organization:146607525`, PostSteward stores that exact actor identity, and an ordinary controlled Page publication/readback produces live evidence. Do not substitute the member profile or another Page merely to obtain a receipt.

Primary LinkedIn contracts:

- Posts API: https://learn.microsoft.com/en-us/linkedin/marketing/community-management/shares/posts-api
- Organization access control: https://learn.microsoft.com/en-us/linkedin/marketing/community-management/organizations/organization-access-control-by-role

## Execution order and stop conditions

Continue without reopening completed acceptance. Threads OAuth, X OAuth/publication and authenticated native WebMCP are complete and must not be repeated merely for evidence. PITR remains parked until the hosted target-bookmark primitive succeeds or a deliberate, owner-visible checkpoint fallback is implemented and verified. The remaining provider application/grant path is LinkedIn organisation approval and exact Page consent.

Advanced live product-path acceptance and production/public readiness remain separate from these external gates. Stripe sandbox and protected root cutover are already accepted and are not reasons to replay payment or cryptographic effects.

Stop only for owner/provider actions that cannot be delegated safely: accepting provider terms, creating or revealing provider client secrets, approving OAuth consent, running an explicitly destructive PITR workflow after its prerequisite becomes healthy, or completing browser sign-in. Secrets must go directly to protected environment storage and must never be pasted into chat, source, logs or evidence documents.
