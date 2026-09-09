# Owner sign-in and first controlled publication

## Scope and completion rule

Continue the accepted restricted staging deployment. Do not recreate Cloudflare resources, rotate the stored encryption key, enable billing, open public signup, import another product's credentials, or publish to a guessed account. This milestone is one owner-approved, text-only publication to one verified X or Threads identity through PostSteward's existing durable engine. LinkedIn is excluded from this acceptance path because its current adapter cannot independently read back a member post. General product support is unchanged.

Completion requires four separate records: a successful OIDC callback and usable owner session; a fresh provider identity read; the owner's exact destination/content approval; and a separate provider GET matching the creation ID, stable author ID and exact text. Deployment, mocked provider responses, a login redirect, an HTTP 2xx write, or a screenshot alone cannot substitute for these records.

## Implementation sequence

1. Persist a minimal sign-in proof atomically with the session, only after state, PKCE, nonce, signature, issuer/audience and invited verified-email checks. Store a hash rather than the email or Google tokens. Cascade proof deletion when its session is deleted. Permit only fixed /app and /pilot return paths.
2. Expose an authenticated /pilot screen and browser-owner-only API. Agent Bearer credentials cannot create or approve pilot work. Existing origin, CSRF, body limits, rate limits and tenant isolation remain in front of this API. Legacy sessions without completion proof must sign in again.
3. Revalidate the chosen provider identity without writing a post. Freeze one account alias, stable provider ID, binding version, exact copy, hashes, owner proof, runtime revision and ten-minute review expiry. Replacing an unapproved preview invalidates the prior preview. Nothing is scheduled during preview.
4. Confirm exactly that review with a sign-in completed within fifteen minutes. Atomically commit one delivery, the immutable campaign and the consumed pilot slot using the existing engine's reservation/limit logic. Set a thirty-second cancellation window. Double clicks, concurrent confirmations, response loss and process restarts must not allocate a second delivery. The slot cannot be reset by this API after reservation, even after uncertainty or cancellation.
5. Bind this delivery's authority to its actual owner session. Before each public write, recheck that authority, account version, pause state, approved runtime and execution claim. Logout, expiry, account drift or a lost claim must block a not-yet-started publication. No promise of cancellation once a provider write has started.
6. Use independent, explicitly bounded readback attempts on the recorded provider post ID. Never retry a publication to recover a missing response. Unknown outcomes remain unknown; there is no latest-post guessing or automatic deletion. Validate Threads ownership by stable owner.id and restrict returned links to exact provider hosts.
7. Show and export the private acceptance receipt without cookies, session hashes, Google/provider tokens, client secrets or email hashes. Preserve the first successful readback observation and subsequent observations separately. Polling and reload recovery inspect existing work, not create new work.

## Verification matrix

Use the real Workers/D1/SQLite Durable Object runtime for signed-token callback, proof/session atomicity and expiry, replay/forgery, wrong issuer/audience/nonce, uninvited identities, CSRF/Bearer denial and a simulated provider end-to-end path. Use deterministic engine tests for simultaneous confirmation, frozen routing, expiry, pause, logout/authority loss, transaction rollback, restart, post-write response loss, stale execution and bounded readback. Provider tests must cover stable-author mismatch, wrong text/ID, body-read failure after acceptance and hostile permalink hosts. Simulated tests are never recorded as live acceptance.

Run existing CI, the production dependency audit, new tests and hosted checks before accepting a staged revision. Preserve the separate, still-open full-development-dependency advisory finding rather than describing the entire dependency graph as clean.

## Live run

Open /pilot. Complete Google consent in the owner's browser. Connect an authorised provider credential only through the authenticated application, or explicitly choose an already connected account. Review the stable account ID and exact text, then approve one publication. No account or copy is silently selected. The browser can be closed after reservation; the durable alarm owns dispatch. Returning to /pilot recovers the same receipt. Cancellation before dispatch and post-ID readback are available there.

Google consent and the choice/approval of a real destination are owner actions, not deployment credentials. Do not manufacture a session or grant through D1 to bypass consent. No credential has been provided for this publication merely because another connected product can post socially. Provider token import is the existing pilot mechanism, not a claim that self-service provider OAuth/refresh is complete.

## Operational interpretation

- Prepared: no provider write or delivery exists.
- Reserved/scheduled: approved once; cancellation may still win.
- Executing/container waiting: inspect the captured phase; never create another job as a retry.
- Published unverified: preserve the durable provider ID and run readback only.
- Ambiguous effect: do not retry. Investigate with the provider; this path has no reset or inferred-success action.
- Independently verified: the separate GET matched ID, stable owner and exact content. This is an observation at the recorded time, not a guarantee a post cannot later be edited or removed.

The pilot receipt does not establish public-launch readiness, native browser WebMCP compatibility, payment settlement, long-term token refresh, disaster recovery or organisation-wide production security review.
