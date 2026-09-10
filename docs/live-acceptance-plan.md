# Five live acceptance workstreams

## Objective and current authority

Complete five restricted-staging acceptance journeys: owner sign-in plus one controlled provider publication, private GitHub sources, Durable Object PITR recovery, native browser WebMCP, and Stripe sandbox payments. Production launch controls are a separate milestone. Code, CI, hosted boundary checks and real external observations are different evidence classes.

Baseline main: fb8379e0940732e9361aa76fdac08141f7f50f0d. Baseline runtime: e48a1e7ef9ed6388f8c5934aae5426fe40a0ed6c. Origin: https://poststeward-staging.woeinvests.workers.dev. See [the deployment receipt](private-github-staging-2026-09-10.md).

The owner explicitly approved Google owner sign-in in the current cloud-browser session and selected the connected Stripe account in test mode. Those approvals remain valid and must not be requested again.

Google sign-in opened through PostSteward; Google displayed the existing configured OAuth application's branding. After the secure sign-in handoff, the browser URL policy blocked the PostSteward /auth/callback page. The owner's screenshot independently showed Chromium ERR_BLOCKED_BY_CLIENT on that callback. A separate read-only visit to PostSteward /app still reported unauthenticated access. The remaining authentication blocker is the cloud-browser callback policy, not missing owner approval. Do not replay callback parameters, rewrite the callback route to evade the policy, or fabricate an owner session.

Stripe account targeting now works. The test catalogue was empty. A dedicated PostSteward staging product and active USD 5 monthly Price were created and independently retrieved with livemode=false. No customer, Checkout Session, subscription or payment was created. The webhook inventory was empty. Creating the endpoint remains pending a secure destination for its one-time signing secret and the restricted test API key. The available Stripe API discovery exposes no key-creation operation; the GitHub integration excludes environment-secret administration. No credentials from unrelated products are authorised substitutes.

## Dependency order and completion records

| Workstream | Implementation and configuration | Live sequence | Required acceptance evidence |
| --- | --- | --- | --- |
| Owner + provider publication | Existing verified Google callback, provider OAuth, /pilot review, exact approval, cancellation window and durable readback. Configure one provider's own staging application. | Resolve the cloud-browser callback policy using the existing sign-in approval; verify invited owner; complete provider OAuth; prepare one account/text review; approve that exact review; inspect reservation and independent readback. | Owner callback proof, stable provider identity, approved digest/release, one delivery/provider ID, separate GET matching ID/author/text. |
| Private GitHub source | Existing dedicated GitHub App installation, selected-only Contents read, encrypted refresh leases. New free owner-only source probe. | Configure staging App triple; owner selects one private repository; run probe on a harmless path; revoke selection; probe again; reconnect only if wanted. | Selected repository identity, private commit SHA/time/release, next check denied after revocation, no anonymous fallback or credential output. |
| Recovery | Existing owner prepare/execute/reconcile/resume/undo with D1 quarantine and effect fences. No fake local PITR implementation. | Inspect workspace first; perform rehearsal before provider/payment grants where possible; choose an explicit target; review returned plan/digest; authorise execute; reconcile after restart; verify invalidated authority and preserved fences; resume explicitly. | Actual Cloudflare restore/restart plus reconciled plan, quarantine transitions, canary restored, accounts/profiles/billing invalidated, no duplicate effect. Undo is a separate exact-plan action. |
| Native WebMCP | Current Document API registration, cleanup on partial failure and a read-only native round-trip button. Workspace remains usable after registration failure. | Sign in using a supported native browser; run Check native WebMCP; then invoke workspace_status through that browser's agent tool interface; verify logout makes captured authority unusable. | Browser/version and release, registration and API round-trip observation, separate browser-agent tool invocation/result. A fixture or HTTP fallback never closes this gate. |
| Stripe sandbox | Explicit staging-only sandbox configuration, test-key/test-price preflight, mode checks before Checkout, verified-webhook reconciliation. Advanced automation and MPP remain disabled. | Reuse the created USD 5 monthly test Price in the selected account; configure the signed webhook and protected credentials; configure protected environment; deploy; create one quote/Checkout; complete with Stripe test details; reconcile; replay same quote/event; refund; separately exercise dispute; cancel sandbox subscriptions. | Test account and livemode=false, quote/session/payment/invoice mapping, one settlement, bounded paid entitlement, no duplicate charge on retry, refund/dispute revocation, cleanup record. |

## Implementation changes

1. /api/sources/github/probe accepts only repository/branch/path, requires a fresh owner browser and CSRF, and checks an existing private link before any source request. It uses the same refresh lease and installation/repository revalidation as automation. The UI clears old success evidence before every new attempt.
2. WebMCP registration failure aborts all registrations made in that attempt and no longer prevents ordinary workspace loading. The native check selects only this window's workspace_status tool, invokes it through document.modelContext and verifies the returned workspace. workspace_status now includes workspace and release.
3. STRIPE_SANDBOX_ENABLED is an explicit staging-only opt-in with restricted signup. Test Checkout does not turn on Advanced automation or MPP. Protected deployment validates a test credential and retrieves the exact active USD 5/month test Price before any Cloudflare mutation. Checkout validates mode before reservation; failed price validation cannot strand a purchase attempt.
4. New Checkout quotes persist an integration identifier so retries retain identical Stripe parameters; pre-upgrade quotes keep their original payload. Verified dispute/refund events resolve their charge when customer/workspace is omitted, then reconcile through the existing customer mapping. A failed lookup returns a retryable response and never marks the event complete.
5. Hosted probes include unauthenticated denial of the private source check and the exact sandbox-enabled state. Automatic deployment never signs in, posts, restores, settles a payment or removes a workspace.
6. Operator documents reconcile provider OAuth, conditional LinkedIn readback and the cleared dependency advisory.

## Sandbox configuration contract

Protected staging variables:

- STRIPE_SANDBOX_ENABLED=true only for the deliberate sandbox run.
- STRIPE_SANDBOX_PRICE_ID: the actual test Price, USD 5.00, monthly, interval_count=1.
- Existing restricted owner/OIDC configuration remains required.

Protected staging secrets:

- STRIPE_SANDBOX_SECRET_KEY: an sk_test_ or rk_test_ key for the selected sandbox with the operations needed by billing.
- STRIPE_SANDBOX_WEBHOOK_SECRET: the signing secret for this sandbox's https://poststeward-staging.woeinvests.workers.dev/webhooks/stripe endpoint.

The workflow maps these to Worker STRIPE_SECRET_KEY and STRIPE_WEBHOOK_SECRET only in the deploy step. Never put values in PRs, chat, logs or receipts. Use the exact event selection and staged activation sequence in [Stripe sandbox setup](stripe-sandbox-acceptance.md). Verify delivery and reconciliation in the chosen sandbox; a whsec_ prefix alone does not prove correct endpoint configuration.

Use Stripe test payment details only, following [Stripe testing documentation](https://docs.stripe.com/testing). Sandbox settlement is simulated money and must never be described as real-money settlement. MPP needs a separately eligible merchant profile and agent wallet; this change does not activate it. Disabling STRIPE_SANDBOX_ENABLED and redeploying closes new Checkout access; cancel sandbox subscriptions separately.

## Recovery run detail

Use the existing [recovery procedure](recovery.md) and exact UI confirmation text. Preparation itself quarantines the workspace; explain this before selecting a target. Record the pre-rehearsal business canary, effect counts and authority inventory without credentials. Confirm that the returned target time is intentional, the plan belongs to the owner and its ten-minute expiry has not passed. Do not treat a dropped connection during execute as success. Reconcile after restart and inspect recovered state before resume. If execution is uncertain, keep quarantine and inspect the existing plan; never create a fresh restore to guess the outcome.

A safe first rehearsal can restore an owner-created draft canary in a workspace with no external publication or payment in flight. That is only a proposal until the owner chooses the workspace and target. Do not seed owner sessions or fabricate PITR success through D1.

## Native browser evidence boundary

The [current WebMCP draft](https://webmachinelearning.github.io/webmcp/) places the API on document.modelContext. The new button exercises getTools and executeTool directly; it does not install a polyfill. Its observation is an in-page native round trip, not proof that a separate browser agent discovered/invoked the tools. Preserve both observations for full acceptance. Browsers without the API report unsupported and retain normal HTTP/MCP workspace access.

## Stop and recovery conditions

- Authentication denial: distinguish missing owner approval from browser URL-policy rejection. The owner approval is already recorded; resolving the callback policy is the current requirement. Do not bypass the denied action.
- Public-write uncertainty: keep the one captured delivery and use readback only.
- GitHub refresh uncertainty or revoked access: reconnect deliberately; no reuse of a possibly consumed refresh credential.
- Restore uncertainty: keep quarantine; reconcile the existing plan.
- Payment uncertainty: inspect the existing quote/session and provider record; never create a new key to bypass the pending attempt.
- Missing native browser or Stripe account authority: retain a blocked evidence state. CI simulations do not close these gates.

After the code is merged and staged, append the exact head, CI, deployment run, runtime SHA and observed capability flags. Mark each live gate passed only when its own external evidence exists.

## Deployed evidence

[The 10 September acceptance-path receipt](live-acceptance-staging-2026-09-10.md) records PR #22 and the deployed PR #24 follow-up at `2dc0bfee59a441bbcedd95a59d6877a89c4845f0`: 188 passing tests, 58 hosted assertions, existing owner/account approvals, the callback URL-policy block, successful test Product/Price creation and the five still-open live gates. The Stripe test account also has no Portal configuration; configure cancellation before claiming the Portal journey.
