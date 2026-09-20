# PostSteward implementation status — 20 September 2026

## Executive state

PostSteward's restricted-staging product is substantially implemented. The remaining work is no longer a general product rewrite: it is a small set of genuine browser/provider/platform gates plus a separate production/paid-launch acceptance layer.

The reconciled staging and production release before the LinkedIn correction branch is `80e3af5529cbe96163e7baed0a53c180cba2187b`. Exact current runtime identity should always be read from `/readiness.json` rather than inferred from this document. Provider application state, owner connection state and publication/readback evidence are deliberately separate; see the [provider connection audit](provider-connection-audit-2026-09-19.md).

The authoritative generated ledger is [current release gates](current-readiness.md). The dated [live external gates](live-external-gates-2026-09-13.md) and other completion documents remain historical evidence and must not reopen accepted effects.

## Live evidence already accepted

Do not repeat any of these merely to create a newer receipt:

- **Owner Google sign-in:** accepted from the real owner browser journey.
- **Threads controlled publication and independent readback:** accepted from the existing owner receipt.
- **Threads owner connection:** accepted from a real production OAuth callback/code exchange and a current healthy long-lived connection. The current production workspace has no delivery receipt, so connection and publication remain separate evidence.
- **X owner connection and publication:** accepted for the dedicated application, real `@poststeward` Page identity, production OAuth callback, exact controlled publication/readback and natural token refresh rotation.
- **Inspect-only agent authority:** accepted over HTTP and remote MCP, including denial of the same token after owner revocation.
- **Stripe sandbox lifecycle:** accepted for subscription Checkout, paid application state, full refund with entitlement revocation, operator cancellation and completed signed-webhook ledger evidence.
- **Protected root cutover:** accepted. The active writer is `next`; the legacy root remains intentionally retained for recovery and is not to be retired merely for another receipt.
- **Private GitHub sources:** accepted from a real selected-repository staging App journey: owner OAuth, harmless private read, provider-side App uninstall, stale-authority detection and fail-closed follow-up read without anonymous fallback.

## Live gates still open

| Gate                     | Current state                                                                                                                                                                                    | Correct next action                                                                                                                                                                                                                            |
| ------------------------ | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Cloudflare PITR          | **Blocked at hosted target-bookmark resolution.** `getCurrentBookmark()` succeeds; `getBookmarkForTime()` returns the bounded `RECOVERY_PITR_TARGET_BOOKMARK_FAILED`. No restore has been armed. | Park destructive rehearsal. Retry only after the hosted primitive works, or after an explicit owner-visible fallback is deliberately designed and verified.                                                                                    |
| Native WebMCP            | **Engineering deployed; live execution open.** A supporting browser advertised 27 tools, but the authenticated page's `workspace_status` check reported the tool was not registered.             | Diagnose the registration/execution boundary and accept only a successful read-only round trip bound to the current workspace and release.                                                                                                     |
| LinkedIn Page connection | Dedicated Community Management credentials and owner Page grant are absent. The OpenID-only application is intentionally not reused because LinkedIn makes these products mutually exclusive.    | Complete the dedicated app/provider review when the owner is ready to provide the required organisation information, then configure protected credentials and complete the exact Page grant. Member-profile readback is separate and optional. |

## Implemented product foundation

| Area                      | Current implementation                                                                                                                                                                                                     |
| ------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Hosted foundation         | TypeScript Cloudflare Worker, D1 identity, SQLite Durable Objects, alarms, explicit deployment configuration and CI                                                                                                        |
| Owner identity            | Google OIDC code/PKCE, browser sessions, CSRF, restricted admission and atomic owner proof                                                                                                                                 |
| Agent authority           | Scoped, expiring, revocable Bearer grants with admin non-delegation and workspace isolation                                                                                                                                |
| Controlled publication    | Owner-only `/pilot`, stable provider identity, immutable expiring review, explicit approval, one-shot reservation and 30-second cancellation boundary                                                                      |
| Effect safety             | Idempotency, fingerprint dedupe, claim fencing, ambiguous-effect preservation, known creation-ID retention and no blind retry across the write boundary                                                                    |
| Readback                  | Separate bounded provider GET with exact post ID, stable author ID and exact text verification, plus ordinary `receipt_recheck` recovery without republishing                                                              |
| Provider OAuth            | X PKCE, Threads long-lived/refresh flow, LinkedIn member OAuth and an exact state-bound LinkedIn organisation/Page path with separate capability-gated readback                                                            |
| Free publishing           | Explicit project routing, immutable campaigns, publish now, explicit schedules, cancel/replace, receipts, export and on-demand metrics                                                                                     |
| Agent transports          | One generated operation catalogue shared by HTTP, remote MCP and browser WebMCP, with generated help/OpenAPI/reference/discovery                                                                                           |
| Private GitHub            | Owner-only selected-repository authority, read-only Contents/Metadata, encrypted rotating user credential, per-read revalidation, unlink/revoke/lifecycle cleanup and fail-closed private reads; live acceptance preserved |
| Recovery                  | Owner-only PITR plan, quarantine, restore/reconcile/resume/undo state machine, restored-authority invalidation and external-effect fences; hosted target resolution currently blocked upstream                             |
| Advanced foundation       | Source monitoring, reviewed deterministic templates, baseline/change detection, stale-work withdrawal, bounded replenishment/spacing and independently scheduled metrics                                                   |
| Advanced management model | `Profile.family` is the category identity. `/advanced-inventory.html` groups source snapshots, reserved automatic deliveries and metric evidence without a second drifting category store                                  |
| Billing                   | USD 5 monthly Stripe Checkout/Portal, signed-webhook reconciliation, entitlement/refund/dispute/cancellation logic and separately disabled MPP foundation; staging sandbox lifecycle accepted                              |
| Root replacement          | Mixed-root reads, authenticated root IDs, complete protected inventory traversal, bounded conditional rewrap/cutover and readback; staging cutover accepted with old root intentionally retained                           |
| Acceptance tooling        | Readiness, agent/revocation, private-source, recovery, lifecycle, Advanced inventory, capacity, cross-tenant, Cloudflare edge and GitHub main-protection verifiers                                                         |

## Explicit product decisions

### CLI

PostSteward's supported command-line contract is shell/cURL over the documented HTTP operations. There is no separately packaged PostSteward binary/update channel promise.

### Remote MCP authorization

Remote MCP uses owner-issued, scoped, expiring, revocable Bearer tokens. Standards-based OAuth-compatible MCP authorization bootstrap is not part of the current release contract. Discovery must never mint or broaden authority.

### Advanced categories and inventory

The reviewed profile `family` remains the category boundary used by spacing decisions. A second category entity would create drift without additional authority value.

## Paid product and public launch remain separate

Restricted staging being healthy does **not** mean public/paid production is complete.

- **Advanced execution remains disabled.** Before enabling it, prove one live source change -> snapshot/inventory -> spaced automatic allocation -> provider effect/readback -> scheduled metrics cycle. Stripe sandbox does not need to be repeated for this.
- **MPP remains disabled and optional.** It does not block Free or subscription-based Advanced unless deliberately added to launch scope.
- **Production infrastructure controls are accepted.** Production edge, GitHub main protection, operational alert delivery, capacity/cost calibration and hosted cross-tenant isolation are `live_verified` for their own stated scopes.
- **Public signup remains a separate decision:** support, abuse, privacy/deletion, provider outage and incident ownership must be exercised before unrestricted admission.

See [production readiness acceptance](production-readiness-acceptance.md).

## Evidence rule

Automated tests and Miniflare scenarios can prove PostSteward code paths; they cannot manufacture a real provider grant, browser capability, Cloudflare PITR restore, repository-admin rule or delivered operational alert. Conversely, already accepted external effects must not be repeated merely because an older document still describes them as pending.

Automatic CI/deploy remains non-destructive: it must not sign in, publish, grant/revoke owner authority, execute PITR, erase a customer workspace, rotate a protected root secret or settle a payment.
