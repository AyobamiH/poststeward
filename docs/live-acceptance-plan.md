# Live acceptance plan

> **Current control plane:** this plan is reconciled to the 19 September 2026 deployed release. The generated ledger is [current release gates](current-readiness.md), and provider connection state is recorded in the [provider connection audit](provider-connection-audit-2026-09-19.md). Older procedures are evidence/history, not instructions to repeat accepted external effects.

## Preserve completed evidence

The following are accepted and must not be repeated merely for a fresher receipt:

- real owner Google sign-in;
- one controlled Threads publication and independent exact provider readback;
- one real production owner Threads OAuth callback/code exchange and a current healthy long-lived connection;
- Inspect-only owner-to-agent grant over HTTP and remote MCP plus denial after owner revocation;
- Stripe sandbox subscription Checkout, paid state, refund/revoked entitlement, operator cancellation and completed webhook ledger;
- protected encryption-root cutover with `next` as the active writer and the legacy root intentionally retained;
- private GitHub selected-repository installation/OAuth/read, provider-side uninstall and fail-closed subsequent private read.

A dated document that still describes any of those as pending is historical, not a request to recreate it.

## Current execution order

### 1. Authenticated native WebMCP

This remains a browser gate independent of provider connections.

An earlier authenticated staging browser reported:

`Remote MCP and HTTP are available. Native WebMCP is not available in this browser.`

On 19 September, a supporting production browser advertised 27 PostSteward tools, but the page's live check still reported that `workspace_status` was not registered. Registration display alone is not execution proof.

That result is not a PostSteward failure and must not be substituted with another HTTP/remote-MCP token proof. Use a browser that genuinely exposes `document.modelContext`, sign in as the existing owner, allow the page to register its authorised tools, then run **Check native WebMCP** once.

Acceptance requires:

- `workspace_status` is discovered as a tool belonging to the current window;
- native execution returns the authenticated workspace;
- the returned release equals the exact current `/readiness.json` release;
- the observation is read-only;
- browser tools are revoked on page teardown/sign-out lifecycle rather than being treated as durable authority.

No publication, payment, provider connection or new agent token is required.

### 2. X owner connection

X application credentials are configured in staging and production. Do not register a second application merely because the older ledger said the client was absent. The remaining work is the real owner grant using the existing exact callback:

`https://poststeward-staging.woeinvests.workers.dev/connections/oauth/x/callback`

Acceptance requires the real owner consent path, OAuth 2.0 Authorization Code + PKCE, the required read/write/user/offline scopes, stable identity persistence and refresh authority. A manually imported token is not a substitute for this OAuth grant.

### 3. LinkedIn provider application and grant

Staging has no approved LinkedIn Community Management application client/secret yet. Configure a dedicated Page application with exact callback:

`https://poststeward-staging.woeinvests.workers.dev/connections/oauth/linkedin/callback`

The intended PostSteward Page path uses only `w_organization_social` and `r_organization_social` for the exact actor `urn:li:organization:146607525`. The same member authorises as Page administrator and PostSteward must verify Page access before binding it. LinkedIn's portal requires Community Management to be the only product on this application, so member OpenID credentials and Page credentials remain separate. `r_member_social` and `LINKEDIN_MEMBER_READBACK=true` concern member-profile posts only and must remain absent until LinkedIn actually approves that separate capability.

### 4. Threads connection — accepted and preserved

The Threads application credentials and callback are accepted, including a real production owner journey that completed code exchange and stable-identity persistence:

`https://poststeward-staging.woeinvests.workers.dev/connections/oauth/threads/callback`

The current production workspace has one healthy long-lived Threads connection and no delivery receipts. Preserve that connection. The earlier controlled publication/readback evidence remains valid and separate; do not publish again merely to combine the two receipts.

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

## Production and public-launch separation

Production edge, GitHub main protection, operational alert delivery, capacity/cost calibration and hosted cross-tenant isolation are accepted on the reviewed release. That does not make public signup safe or enabled. Public admission remains restricted until support, abuse, privacy/deletion, provider-outage and incident ownership are explicitly accepted.

## Evidence rule

A live gate passes only when the external system itself produced the required effect and PostSteward independently observed the required state. Code, CI, a configuration flag, setup screenshot or success redirect can support diagnosis but does not substitute for a provider grant, browser-native invocation, PITR restore, admin rule or delivered operational alert.

Equally important: once a high-consequence live effect is accepted, do not recreate it merely because an older checklist still calls it pending.
