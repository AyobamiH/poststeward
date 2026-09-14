# PostSteward implementation status — 14 September 2026

## Executive state

PostSteward's restricted-staging product is substantially implemented. The remaining work is no longer a general product rewrite: it is a small set of genuine browser/provider/platform gates plus a separate production/paid-launch acceptance layer.

The repository audit on 14 September found no open pull requests or open issues. The protected staging release immediately before this reconciliation was `5abe810b86d0fc4984c2b2ceed6ab4d5c70a6b42`; deployment run `34781441361` completed successfully with the normal PITR diagnostic disabled. Exact current runtime identity should always be read from `/readiness.json` rather than inferred from this document.

The authoritative live-gate ledger is [live external gates](live-external-gates-2026-09-13.md). Dated completion documents remain historical evidence and must not reopen accepted effects.

## Live evidence already accepted

Do not repeat any of these merely to create a newer receipt:

- **Owner Google sign-in:** accepted from the real owner browser journey.
- **Threads controlled publication and independent readback:** accepted from the existing owner receipt. The remaining Threads problem is OAuth callback completion, not publication proof.
- **Inspect-only agent authority:** accepted over HTTP and remote MCP, including denial of the same token after owner revocation.
- **Stripe sandbox lifecycle:** accepted for subscription Checkout, paid application state, full refund with entitlement revocation, operator cancellation and completed signed-webhook ledger evidence.
- **Protected root cutover:** accepted. The active writer is `next`; the legacy root remains intentionally retained for recovery and is not to be retired merely for another receipt.
- **Private GitHub sources:** accepted from a real selected-repository staging App journey: owner OAuth, harmless private read, provider-side App uninstall, stale-authority detection and fail-closed follow-up read without anonymous fallback.

## Live gates still open

| Gate | Current state | Correct next action |
| --- | --- | --- |
| Threads OAuth callback | **Blocked upstream.** Meta still rejects saving the exact callback allowlist. | Park it. Do not weaken redirect validation or publish again. Resume only when Meta accepts the exact callback. |
| Cloudflare PITR | **Blocked at hosted target-bookmark resolution.** `getCurrentBookmark()` succeeds; `getBookmarkForTime()` returns the bounded `RECOVERY_PITR_TARGET_BOOKMARK_FAILED`. No restore has been armed. | Park destructive rehearsal. Retry only after the hosted primitive works, or after an explicit owner-visible fallback is deliberately designed and verified. |
| Native WebMCP | **Engineering complete; live browser invocation open.** The owner's authenticated ordinary browser reports native WebMCP unavailable. | Use a supporting authenticated browser and perform one read-only `workspace_status` native round trip. Do not substitute HTTP/remote MCP. |
| X provider application/grant | Client/secret and real owner grant absent. | Register the real X app, exact callback and funded authority as required; store secrets only in protected staging; complete OAuth/identity/refresh proof. |
| LinkedIn provider application/grant | Client/secret absent; restricted member-readback approval absent. | Register app and complete base grant; request/use member readback only after LinkedIn actually grants the capability. |

## Implemented product foundation

| Area | Current implementation |
| --- | --- |
| Hosted foundation | TypeScript Cloudflare Worker, D1 identity, SQLite Durable Objects, alarms, explicit deployment configuration and CI |
| Owner identity | Google OIDC code/PKCE, browser sessions, CSRF, restricted admission and atomic owner proof |
| Agent authority | Scoped, expiring, revocable Bearer grants with admin non-delegation and workspace isolation |
| Controlled publication | Owner-only `/pilot`, stable provider identity, immutable expiring review, explicit approval, one-shot reservation and 30-second cancellation boundary |
| Effect safety | Idempotency, fingerprint dedupe, claim fencing, ambiguous-effect preservation, known creation-ID retention and no blind retry across the write boundary |
| Readback | Separate bounded provider GET with exact post ID, stable author ID and exact text verification, plus ordinary `receipt_recheck` recovery without republishing |
| Provider OAuth | X PKCE, Threads long-lived/refresh flow and LinkedIn OAuth with capability-gated member readback |
| Free publishing | Explicit project routing, immutable campaigns, publish now, explicit schedules, cancel/replace, receipts, export and on-demand metrics |
| Agent transports | One generated operation catalogue shared by HTTP, remote MCP and browser WebMCP, with generated help/OpenAPI/reference/discovery |
| Private GitHub | Owner-only selected-repository authority, read-only Contents/Metadata, encrypted rotating user credential, per-read revalidation, unlink/revoke/lifecycle cleanup and fail-closed private reads; live acceptance preserved |
| Recovery | Owner-only PITR plan, quarantine, restore/reconcile/resume/undo state machine, restored-authority invalidation and external-effect fences; hosted target resolution currently blocked upstream |
| Advanced foundation | Source monitoring, reviewed deterministic templates, baseline/change detection, stale-work withdrawal, bounded replenishment/spacing and independently scheduled metrics |
| Advanced management model | `Profile.family` is the category identity. `/advanced-inventory.html` groups source snapshots, reserved automatic deliveries and metric evidence without a second drifting category store |
| Billing | USD 5 monthly Stripe Checkout/Portal, signed-webhook reconciliation, entitlement/refund/dispute/cancellation logic and separately disabled MPP foundation; staging sandbox lifecycle accepted |
| Root replacement | Mixed-root reads, authenticated root IDs, complete protected inventory traversal, bounded conditional rewrap/cutover and readback; staging cutover accepted with old root intentionally retained |
| Acceptance tooling | Readiness, agent/revocation, private-source, recovery, lifecycle, Advanced inventory, capacity, cross-tenant, Cloudflare edge and GitHub main-protection verifiers |

## Explicit product decisions

### CLI

PostSteward's supported command-line contract is shell/cURL over the documented HTTP operations. There is no separately packaged PostSteward binary/update channel promise.

### Remote MCP authorization

Remote MCP uses owner-issued, scoped, expiring, revocable Bearer tokens. Standards-based OAuth-compatible MCP authorization bootstrap is not part of the current release contract. Discovery must never mint or broaden authority.

### Advanced categories and inventory

The reviewed profile `family` remains the category boundary used by spacing decisions. A second category entity would create drift without additional authority value.

## Paid-product and production layer still separate

Restricted staging being healthy does **not** mean public/paid production is complete.

- **Advanced execution remains disabled.** Before enabling it, prove one live source change -> snapshot/inventory -> spaced automatic allocation -> provider effect/readback -> scheduled metrics cycle. Stripe sandbox does not need to be repeated for this.
- **MPP remains disabled and optional.** It does not block Free or subscription-based Advanced unless deliberately added to launch scope.
- **GitHub main protection remains open.** The 14 September repository ruleset read returned an empty inventory, so merged-PR deployment provenance is not yet equivalent to server-side main protection.
- **Production edge remains open:** custom production origin, DNS/TLS, WAF and rate-policy evidence.
- **Operational acceptance remains open:** capacity/cost calibration, alert delivery/escalation and hosted two-workspace cross-tenant evidence.
- **Public signup remains a separate decision:** support, abuse, privacy/deletion, provider outage and incident ownership must be exercised before unrestricted admission.

See [production readiness acceptance](production-readiness-acceptance.md).

## Evidence rule

Automated tests and Miniflare scenarios can prove PostSteward code paths; they cannot manufacture a real provider grant, browser capability, Cloudflare PITR restore, repository-admin rule or delivered operational alert. Conversely, already accepted external effects must not be repeated merely because an older document still describes them as pending.

Automatic CI/deploy remains non-destructive: it must not sign in, publish, grant/revoke owner authority, execute PITR, erase a customer workspace, rotate a protected root secret or settle a payment.
