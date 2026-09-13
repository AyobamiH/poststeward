# Protected root cutover

The runtime now supports the retained legacy root and one next root. This is a staged credential migration, not permission to destroy the old root. [Google Cloud KMS](https://docs.cloud.google.com/kms/docs/key-rotation) distinguishes key rotation from data re-encryption and retirement. [Cloudflare PITR](https://developers.cloudflare.com/durable-objects/api/sqlite-storage-api/) can restore historical ciphertext, so the old protected root must remain available.

## Deployment integration

Use the existing protected `staging` environment and reviewed main deployment workflow:

1. Retain and back up `ENCRYPTION_KEY`. Store a distinct backed-up random 32-byte base64 value as `ENCRYPTION_KEY_NEXT`. Its presence alone enables compatible reads, not next-root writes.
2. Set staging variable `ENCRYPTION_ROOT_WRITE=next` when authorising the cutover. The deployment validates both roots, pins their non-secret identifiers and refuses subsequent root removal/replacement or a switch back to legacy writes.
3. Deployment creates a random, expiring capability internally, deploys the exact reviewed revision, allows bounded prior requests to settle, then runs `scripts/root-cutover.mjs`. No credential needs to enter chat, a command argument or a public artifact.
4. The driver enumerates the durable workspace registry, inspects each current workspace, rehearses a batch, submits that exact inventory digest, and independently inspects the completed result. `POSTSTEWARD_ROOT_CUTOVER` is emitted only after the complete traversal succeeds. The subsequent deployment smoke checks still have to pass.

Ordinary deployments with `ENCRYPTION_ROOT_WRITE=legacy` do not perform root migration. Missing roots, changed inventory, unknown credential families, GitHub refresh leases, expired operator authority and mismatched releases stop the operation. A failed run can leave committed batches: both roots remain readable, and a later reviewed run discovers the remaining ciphertext instead of replaying provider operations.

## Authority and data boundary

`/internal/root-cutover` accepts only the temporary staging workflow capability, an exact release and narrow list/inspect/migrate actions. Browser cookies and Origin-bearing requests are rejected. It does not accept keys, tokens, plaintext, arbitrary storage paths or arbitrary operations. It is absent from the agent catalogue. Capabilities expire within 45 minutes and are replaced on the next cutover deployment.

Workspace enumeration is seeded from existing identity, provider, billing, lifecycle and effect records. Future owner creation records the workspace in the same D1 transaction. Erasure does not delete this minimal registry entry. Account and OAuth credentials, including inactive accounts, are inventoried from all current workspace records; GitHub credentials include every installation status. Unknown top-level credential families fail closed.

Each workspace batch is fenced by Durable Object `blockConcurrencyWhile` and full-record comparison. GitHub writes additionally compare credential revision, exact ciphertext and absence of a refresh lease. No routing version, account active state, token scope, subscription or delivery receipt is changed. A post-write read decrypts the stored replacement. Workspace checkpoints and workflow receipts contain counts and digests, never credential material.

Limits are 25 replacements per batch, 200 credentials per workspace, 100 workspaces per page and 100 pages per run. Exceeding a limit fails rather than claiming a complete inventory. Normal edge rate limits also apply. Results describe the traversal's observation interval, not an atomic global snapshot. Restore paths invalidate restored credentials and write tombstones using the configured active root; historical ciphertext is not silently rewritten in Cloudflare's recovery log.

## Evidence

`tests/root-cutover.test.ts` exercises mixed roots, authenticated root metadata, explicit activation, batch limits, resumption, stale local commits and unknown families. `tests/runtime-root-cutover.test.ts` exercises the actual Workers/D1/SQLite route across a runtime reconfiguration, including account and refresh credentials, GitHub conditional updates, independent readback and operator rejection. Outbound identity reads are fixtures, not live provider grants. The driver and deployment guard have separate failure tests.

Live acceptance requires a successful protected run against its deployed revision. Neither these tests nor adding this document claims that the next protected key exists, the live migration ran, or the old key can be retired.

## Other remaining acceptance gates

The protected external preflight now reports X, Threads and LinkedIn client/secret presence separately from live grants. X's [PKCE flow](https://docs.x.com/fundamentals/authentication/oauth-2-0/authorization-code) requires its own registered application; `offline.access` requests refresh authority. LinkedIn's [Posts API](https://learn.microsoft.com/en-us/linkedin/marketing/community-management/shares/posts-api?view=li-lms-2026-08) restricts `r_member_social` to approved users. A local test or manually setting the readback variable does not grant that authority.

On 13 September, the cloud browser's PostSteward page reported WebMCP available but required owner sign-in. That supersedes the earlier observation of unsupported browser capability; authenticated tool execution remains unproven until the owner session is restored. The existing Threads publication, agent-token revocation and Stripe sandbox receipts must not be repeated to close these gates.
