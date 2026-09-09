# Owner acceptance engineering receipt: 9 September 2026

**Engineering outcome: implemented, deployed to restricted staging and verified. Live acceptance outcome: pending real owner Google consent and the first explicitly approved provider publication.**

This receipt does not claim that a real owner signed in or that a real social post exists. No owner session, provider credential, destination or publication approval was manufactured to make a test pass.

## Exact runtime and verification identity

| Field | Value |
| --- | --- |
| Repository | `AyobamiH/poststeward` |
| Active runtime SHA | `00544af2ae1333357c4f6b427541007cbd2206ba` |
| Worker | `poststeward-staging` |
| Acceptance page | `https://poststeward-staging.woeinvests.workers.dev/pilot` |
| Cloudflare version | `5afe3331-bfe8-4c7b-9215-684dc0bf98ba` |
| Application PR | [#8](https://github.com/AyobamiH/poststeward/pull/8) |
| Readiness/verification PR | [#9](https://github.com/AyobamiH/poststeward/pull/9) |
| Application candidate CI | [34398644597](https://github.com/AyobamiH/poststeward/actions/runs/34398644597), job 102624695456: 96 passed |
| Final verification candidate CI | [34399698258](https://github.com/AyobamiH/poststeward/actions/runs/34399698258), job 102628237086: 99 passed |
| Migration/upload run | [34399141159](https://github.com/AyobamiH/poststeward/actions/runs/34399141159), job 102626827735 |
| Accepted existing-runtime run | [34399839232](https://github.com/AyobamiH/poststeward/actions/runs/34399839232), job 102628706573: success |
| Verification-code revision | `821829d1bf1dfb4348795153f4dd8279e3c0b22d` |
| Original hosted suite observation | `2026-09-09T20:14:46.527Z` |
| Pilot hosted suite observation | `2026-09-09T20:14:48.816Z` |
| Browser observation | `2026-09-09T20:14:58.731Z` |

The verification-code revision is newer than the active runtime: PR #9 changed verification, not application code. Its workflow constructed expectations for the explicit deployed SHA and checked `/health` and `/help.json` against that SHA. It neither migrated nor uploaded a second time. Documentation-only commits after this receipt likewise do not change the active runtime.

## Deployment result and resolved readiness failure

The application deployment validated saved secrets/database identity, applied additive migration `0003_owner_proofs.sql`, uploaded the Worker and assets and preserved the existing encryption key and resources. The new schema adds `login_return_paths` and `owner_proofs`; it leaves the original four-column login-state table unchanged. This avoids breaking the older Worker during the schema/code transition.

The deployment run then passed all 17 original live checks and eleven of twelve new pilot checks. `/pilot.css` returned HTTP 404 immediately after upload, so the run correctly failed and did not execute the browser step. It must not be relabelled as an entirely successful deployment workflow.

PR #9 introduced bounded static GET/404 readiness, retained persistent-failure rejection and added a separate existing-runtime verifier. The accepted verification run returned HTTP 200 for `/pilot.css` on its first attempt, along with every other expected result, without re-uploading the application. The initial absence was therefore transient; this observation does not establish the provider's precise internal cause. No authentication or content check was weakened to turn the run green.

## Automated verification: 99 passed

The final candidate ran a clean dependency install, production-only audit, TypeScript check, generated-document drift check, Worker bundle and the full test suite. The result was 99 passed, zero failed, zero skipped and zero cancelled.

Coverage includes the actual Workers/D1/SQLite/Assets runtime, signed callback processing, state and token-claim rejection, proof/session transaction rollback, expired/logout proof cleanup, old-schema SQL compatibility, owner-only API admission, CSRF, concurrent approval, exact-account capture, one-shot cross-transport/fingerprint enforcement, cancellation redaction, failed transaction rollback, alarm response recovery, stale execution claim fencing, late provider creation evidence, body byte/deadline limits and bounded readback.

The complete callback-to-receipt runtime test waits for a real thirty-second alarm without polling during that interval. It observes one simulated provider write and a separate matching simulated provider GET. Its Google and provider responses are controlled fixtures. This proves the tested implementation path, not the live owner's consent, actual provider permission or existence of a real post.

The production-only audit found zero known vulnerabilities. The full install still reported three high-severity advisories when development/tooling dependencies were included. Those findings are not fixed or fully characterised here and remain a production-review item.

## Hosted acceptance: 29 HTTP checks passed

The accepted run passed the original 17 checks for the exact runtime, disabled payments, landing/workspace HTML, discovery/assets, unauthenticated/forged Bearer/cross-origin/MCP denial, invalid callback-state denial and Google initiation.

It also passed all twelve owner-flow checks:

| Check | Status |
| --- | --- |
| `/pilot`: HTML with explicit approval and credential-entry controls | 200 |
| `/pilot.js` | 200 |
| `/pilot-client.js` | 200 |
| `/pilot.css` | 200 |
| Unauthenticated `/api/pilot/status`, uncached | 401 |
| Unauthenticated pilot prepare | 401 |
| Unauthenticated pilot confirm | 401 |
| Unauthenticated pilot cancel | 401 |
| Unauthenticated pilot recheck | 401 |
| Cross-origin pilot status | 403 |
| External post-login return destination | 400 |
| Fixed `/pilot` return: Google code flow initiation | 302 |

No redirect was followed. Only static GET/200 expectations can retry a 404, for at most five attempts. POSTs, authentication denial, redirects and failed content assertions cannot use that retry. The probe does not approve or submit a publication. Google initiation creates an expiring login-state record only, not an authenticated session.

## Browser acceptance: two unauthenticated contexts passed

Fresh packaged Chromium contexts were requested at 1280×900 and 390×844 window sizes. In both, the page's JavaScript completed its session check, the owner workbench stayed hidden, the confirmation button stayed disabled and the correct Google sign-in link remained available.

The browser processes received only a fresh profile, ordinary executable paths and locale/temp configuration, not deployment, Actions, Google or provider credentials. Their DOM was inspected internally and not printed into logs. This is a functional unauthenticated browser smoke test, not a full visual/accessibility assessment, mobile-device emulation certification, authenticated owner journey or native WebMCP acceptance.

## Owner-flow safeguards deployed

The owner proof is created only after validated OIDC callback processing and committed with the session. A legacy session without a proof must sign in again for pilot approval. A permitted identity must still be in the current restricted allowlist. The pilot accepts a sign-in completion within fifteen minutes and an exact review within ten minutes; it does not claim a new password or MFA challenge occurred.

The review binds the stable provider ID, account alias and binding version, exact copy and digests, owner proof, runtime revision and review expiry. Preparation performs identity reads but allocates no publication. Confirmation explicitly approves that captured review and atomically consumes one pilot slot while creating its immutable campaign and single existing-engine delivery.

The acknowledged reservation has a thirty-second cancellation window. Dispatch rechecks the captured session authority, current binding, pause state, runtime revision and execution claim before the public write. A stale claimant cannot continue writing. A late creation ID from the same claim is retained rather than discarded. An uncertain write is never automatically repeated.

A consumed pilot cannot be reset using another transport, replacing its delivery, reusing the campaign, or cloning its exact account/content fingerprint after cancellation. This restriction is enforced in the canonical engine, not just in a disabled button. Private status/cancellation/export responses exclude the internal session authority and tokens.

Independent acceptance readback reads only the durable provider post ID, at most eight times with at least thirty seconds between attempts. It checks the exact post ID, stable author ID and exact text. Threads uses `owner.id`; a matching username alone is not proof. Returned links require exact trusted provider hosts. The first successful observation and later observations are distinct; a later edit/removal cannot be ruled out by an earlier successful read.

## Required live owner action and remaining boundary

At `/pilot`, the owner must complete Google consent, explicitly select or connect an authorised X/Threads account, prepare and review the exact destination/text, then approve one publication. The page then recovers the same delivery/receipt and performs bounded readback. Credentials are entered only into the authenticated product, never into chat or repository content. The draft text is not pre-approved.

LinkedIn remains a general provider adapter but is intentionally excluded from this exact-readback milestone because its member-post readback is not implemented. This restricted pilot imports existing authorised user tokens; it does not claim completed self-service provider OAuth onboarding or refresh.

No real sign-in proof or provider publication receipt has been collected for this milestone. There was no social account import, public post, payment or production deployment in the verification run. Advanced and MPP remain disabled; signup remains restricted.

Before downgrading application code, pause and inspect active/uncertain controlled deliveries. Pre-pilot code does not understand the new session/review fields. Database expansion compatibility is not a guarantee that downgrading with pending work is safe. Preserve provider evidence and resolve or safely terminalise pending work before a reviewed rollback. Disaster recovery, full operational alerts, public signup, native browser WebMCP, real provider OAuth/refresh and payment settlement remain separate release gates.
