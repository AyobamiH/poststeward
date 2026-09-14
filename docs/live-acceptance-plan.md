# Live acceptance plan

> **Current control plane:** this plan is reconciled to the 14 September 2026 state. The detailed evidence ledger is [live external gates](live-external-gates-2026-09-13.md). Older procedures are evidence/history, not instructions to repeat already accepted external effects.

## Preserve completed evidence

The following are accepted and must not be repeated merely for a fresher receipt:

- real owner Google sign-in;
- one controlled Threads publication and independent exact provider readback;
- Inspect-only owner-to-agent grant over HTTP and remote MCP plus denial after owner revocation;
- Stripe sandbox subscription Checkout, paid state, refund/revoked entitlement, operator cancellation and completed webhook ledger;
- protected encryption-root cutover with `next` as the active writer and the legacy root intentionally retained;
- private GitHub selected-repository installation/OAuth/read, provider-side uninstall and fail-closed subsequent private read.

A dated document that still describes any of those as pending is historical, not a request to recreate it.

## Current execution order

### 1. Authenticated native WebMCP

This is the first actionable browser gate while Threads and PITR are parked.

The owner has already authenticated successfully in an ordinary browser on staging, but that browser reported:

`Remote MCP and HTTP are available. Native WebMCP is not available in this browser.`

That result is not a PostSteward failure and must not be substituted with another HTTP/remote-MCP token proof. Use a browser that genuinely exposes `document.modelContext`, sign in as the existing owner, allow the page to register its authorised tools, then run **Check native WebMCP** once.

Acceptance requires:

- `workspace_status` is discovered as a tool belonging to the current window;
- native execution returns the authenticated workspace;
- the returned release equals the exact current `/readiness.json` release;
- the observation is read-only;
- browser tools are revoked on page teardown/sign-out lifecycle rather than being treated as durable authority.

No publication, payment, provider connection or new agent token is required.

### 2. X provider application and grant

Staging has no X application client/secret yet. Register the real provider application with exact callback:

`https://poststeward-staging.woeinvests.workers.dev/connections/oauth/x/callback`

Store `X_OAUTH_CLIENT_ID` as protected configuration and `X_OAUTH_CLIENT_SECRET` as a protected secret. Never paste either secret into chat, source, logs or evidence documents.

Acceptance requires the real owner consent path, OAuth 2.0 Authorization Code + PKCE, the required read/write/user/offline scopes, stable identity persistence and refresh authority. A manually imported token is not a substitute for this OAuth grant.

### 3. LinkedIn provider application and grant

Staging has no LinkedIn application client/secret yet. Register the real app with exact callback:

`https://poststeward-staging.woeinvests.workers.dev/connections/oauth/linkedin/callback`

Base acceptance uses the application's supported `openid`, `profile` and `w_member_social` path. `r_member_social` and `LINKEDIN_MEMBER_READBACK=true` must remain absent until LinkedIn actually approves that restricted capability. Do not turn a configuration flag into a false provider-permission claim.

### 4. Threads OAuth callback — parked upstream

The Threads application credentials are already in protected staging. The remaining callback is:

`https://poststeward-staging.woeinvests.workers.dev/connections/oauth/threads/callback`

Meta currently rejects saving the callback allowlist in its own dashboard. Do not change PostSteward's redirect validation, use another product's credentials or publish again. Resume only when Meta accepts the exact callback and PostSteward can complete its own code exchange/stable-identity persistence.

### 5. Cloudflare PITR — parked at hosted platform boundary

Do not run the destructive disposable PITR workflow again merely to see the same failure.

Live staging evidence has already narrowed the blocker:

- `getCurrentBookmark()` succeeds;
- `getBookmarkForTime()` fails as `RECOVERY_PITR_TARGET_BOOKMARK_FAILED`;
- no restore has ever been armed by the failed acceptance runs;
- the prepare-only diagnostic erased its own synthetic workspace successfully and attempted no provider effect or payment.

Resume the full prepare -> execute -> reconcile -> resume rehearsal only when Cloudflare target-bookmark resolution succeeds in the reviewed runtime, or after a deliberate owner-visible recovery-checkpoint fallback is designed and independently verified. Never silently change the requested restore point.

## Advanced product acceptance

Advanced remains disabled even though the Stripe sandbox lifecycle is already accepted. Before enabling paid automation, prove the **product path**, not another payment:

1. a reviewed source changes;
2. the change becomes the current source snapshot/inventory;
3. deterministic reviewed content is allocated with the configured family/spacing rules;
4. one authorised provider effect reaches a receipt/readback outcome without bypassing idempotency/effect fences;
5. the independent scheduled-metrics clock captures or honestly records unavailable evidence;
6. pause/expiry still prevents new automatic authority while inspection remains available.

Use a deliberately controlled test target. Do not repeat the already accepted Stripe refund/cancellation lifecycle merely because Advanced is still disabled.

MPP remains separate and optional.

## Production readiness after restricted-staging acceptance

Public/production launch requires its own evidence from [production readiness acceptance](production-readiness-acceptance.md):

- representative capacity/cost observations and retention/limit calibration;
- operational alert delivery plus a failed-notification escalation path;
- hosted two-workspace cross-tenant checks;
- custom production origin, DNS/TLS, WAF and rate-limit policy evidence;
- real GitHub main protection/required checks;
- public-signup support, abuse, privacy/deletion and incident ownership if unrestricted admission is chosen.

The 14 September repository ruleset inventory is empty, so automatic merged-PR provenance must not be confused with server-side main protection.

## Evidence rule

A live gate passes only when the external system itself produced the required effect and PostSteward independently observed the required state. Code, CI, a configuration flag, setup screenshot or success redirect can support diagnosis but does not substitute for a provider grant, browser-native invocation, PITR restore, admin rule or delivered operational alert.

Equally important: once a high-consequence live effect is accepted, do not recreate it merely because an older checklist still calls it pending.
