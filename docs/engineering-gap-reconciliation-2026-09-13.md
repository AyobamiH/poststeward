# Engineering gap reconciliation — 13 September 2026

This records the implementation sequence following the owner's 12 September discovery. CI evidence is separate from live provider acceptance. No publication, payment, refund, subscription cancellation or agent-token revocation was repeated.

| Gap | Implementation | Evidence and remaining boundary |
| --- | --- | --- |
| Connection concurrency | PR42: conditional commits for provider connect, refresh, disconnect and manual replacement | Exact-head CI passed; stale asynchronous work cannot overwrite a newer connection in covered races. |
| Ordinary receipt readback | PR43: scoped receipt_recheck, bounded attempts and owner UI | Exact-head CI passed; recorded IDs are read without another publication. |
| Billing-safe erasure | PR44: expire open Checkout, enumerate/cancel recurring subscriptions and verify cancellation before erasure | Exact-head CI passed with Workers/SQLite fixtures. Live paid disposable-workspace deletion still needs the runtime key's subscription list/read/cancel permissions. |
| Billing reconstruction after restore | PR45: recover customer, Checkout and subscription relationships from bounded Stripe inventory | Exact-head CI passed. Incomplete inventory, multiple live subscriptions and machine-payment recovery fail closed. Live PITR remains unproven. |
| Metrics capability and failures | PR46: optional Threads insights scope, unknown capability without granted-scope evidence, unavailable results count as failures, binding checks across reads | Exact-head CI passed. No live Threads insights grant claimed. |
| Retention | PR47: fresh-owner export, digest-confirmed bounded cleanup, unused campaign deletion and compact operation fences | Exact-head CI passed, including Workers/SQLite routes. All receipts and uncertain outcomes remain. Legacy operation results without timestamps and permanent fences still consume capacity; this is not unlimited cold storage. |
| Root replacement | PR48: complete supplied-inventory validation and rewrap rehearsal for account, OAuth and GitHub credentials | Tests cover partial inventories, leases, unknown families, context/key failures and empty inventories. Protected live enumeration, fenced rollout/CAS integration and actual root cutover remain unfinished. Rehearsal explicitly does not claim independently verified live inventory. |
| Public owner authority | PR49: verified Google owner proof can serve public workspace controls while controlled acceptance remains restricted | Runtime test exercises real signed OIDC fixture, owner recovery status and pilot denial. Deployment signup mode remains restricted. |

## Provider insight used

- [Google Cloud Storage request preconditions](https://docs.cloud.google.com/storage/docs/request-preconditions): reject stale state changes.
- [AWS idempotent API retries](https://aws.amazon.com/builders-library/making-retries-safe-with-idempotent-APIs/): distinguish retrying observation from repeating external effects.
- [Meta Threads insights](https://developers.facebook.com/documentation/threads/insights): insights require their own permission.
- [AWS S3 lifecycle interaction](https://docs.aws.amazon.com/AmazonS3/latest/userguide/lifecycle-and-other-bucket-config.html): unresolved external work must not be erased by ordinary retention.
- [Google Cloud KMS key rotation](https://docs.cloud.google.com/kms/docs/key-rotation): rotation does not itself re-encrypt existing data.
- [Google OIDC validation](https://developers.google.com/identity/openid-connect/openid-connect): validated issuer, client audience, signature and expiry precede owner authority.

## Existing live acceptance evidence retained

The owner supplied exact Threads publication/readback evidence for delivery f1d100ca-5ed3-4eb5-970b-d8a324204cf2, and HTTP plus remote MCP acceptance followed by denial of the same revoked Inspect token.

Stripe sandbox evidence includes the completed subscription Checkout, application paid status, full refund with revoked entitlement, operator cancellation, and seven completed webhook ledger events including the previously incomplete event after resend. This does not additionally prove duplicate webhook handling or portal-initiated period-end cancellation.

## Still requiring live service or browser evidence

Threads OAuth callback completion; X application configuration, funded API authority and grant; LinkedIn application configuration, approved member-post readback and grant; private GitHub registration/credential/grant/read/revoke; disposable recovery/erasure; protected full root rollout; and authenticated native WebMCP remain distinct live gates.

Stripe hosted Portal layout remains Stripe-controlled. The application billing receipt follows PostSteward's UI; a wholly custom billing management experience and account branding isolation are not claimed complete.

## Deployment reconciliation

The [failed run 34742154507](https://github.com/AyobamiH/poststeward/actions/runs/34742154507) deployed revision `a58826e0f48157a4890566ca34780ebba3060526`, then failed its catalogue smoke assertion: the assertion still expected 26 operations after receipt_recheck made the catalogue contain 27. A successful upload did not make that run an accepted deployment; subsequent smoke steps were skipped.

[PR50](https://github.com/AyobamiH/poststeward/pull/50) corrected the stale catalogue contract. The [successful successor run 34742429407](https://github.com/AyobamiH/poststeward/actions/runs/34742429407) verified revision `14db538fd8774df93fc5ea5d0514ec5af06b7f13`: 261 tests and the deployment smoke steps passed. The earlier red run remains historical evidence, not an outstanding request to repeat deployment or live acceptance.

At this revision, Stripe sandbox and Threads OAuth configuration are present; private GitHub, X OAuth and LinkedIn OAuth configuration remain absent. Configuration is not proof of a completed provider grant. Public signup, Advanced execution and real-money billing remain disabled.

## Research before the next implementation

Checked against primary documentation on 13 September 2026. These are implementation decisions and acceptance requirements, not claims that the remaining live gates have passed.

| Area | Primary-source insight | Consequence for PostSteward |
| --- | --- | --- |
| Root replacement | [Google Cloud KMS](https://docs.cloud.google.com/kms/docs/key-rotation) separates creating a new key version from re-encrypting data and retiring old versions. | Current single-root decryption and supplied-inventory rehearsal are insufficient for a partial rollout. Implement explicit root identification and compatible decryption before migration; then protected complete enumeration, fenced conditional writes, resumable checkpoints and readback. Include recoverable historical data in retirement decisions. This sequence is our design inference, not a claim that PostSteward uses Google KMS. |
| Recovery and erasure | [Cloudflare SQLite storage](https://developers.cloudflare.com/durable-objects/api/sqlite-storage-api/) provides 30-day PITR, unavailable in local development. | Local fixtures cannot close the hosted restore gate. Use a disposable hosted workspace, record the recovery bookmark and verify restored authority and external-effect state before erasure. Preserve the existing acceptance workspace. |
| Native WebMCP | The [10 September WebMCP draft](https://webmachinelearning.github.io/webmcp/) exposes `Document.modelContext` and `executeTool(tool, inputObject, options)`. It is a draft, not proof of installed browser support. | Existing `document.modelContext` and object arguments match this contract. Do not replace them with older navigator examples or label a shim native acceptance. The outstanding check is an authenticated supporting browser and agent observation. |
| X OAuth | [X authorisation-code documentation](https://docs.x.com/fundamentals/authentication/oauth-2-0/authorization-code) specifies PKCE and the offline.access scope for refresh tokens. | Verify the registered callback, configured client and granted scopes. Manual access-token publication cannot substitute for the callback and refresh proof. |
| Private GitHub | [GitHub user-token documentation](https://docs.github.com/en/apps/creating-github-apps/authenticating-with-a-github-app/generating-a-user-access-token-for-a-github-app) limits access to the intersection of user and app authority; [app permissions](https://docs.github.com/en/apps/creating-github-apps/registering-github-apps/choosing-permissions-for-a-github-app) are explicit. | App installation alone does not establish the owner's repository authority. Retain selected-repository and current-user checks; close registration, private read and revoke with live receipts. |

LinkedIn member-post readback approval and Meta's failing dashboard callback-save request remain unresolved; this research establishes no supported bypass. Stripe's completed sandbox evidence remains retained, and no new charge or publication is required by this reconciliation.
