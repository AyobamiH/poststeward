# Release completion control plane

PostSteward separates code-complete capability from externally proven capability. A green build or staging deployment must never silently turn an external gate into a product claim, and an older checklist must never silently reopen an already accepted high-consequence effect.

The current detailed evidence ledger is [live external gates](live-external-gates-2026-09-13.md).

## Implemented and deployable

- Restricted Google OIDC owner sign-in and session-bound controlled publication approval.
- X, Threads and LinkedIn publishing adapters with explicit uncertain-outcome handling and durable external-effect fences.
- Provider OAuth architecture for X, Threads and LinkedIn, encrypted access/refresh storage, background refresh and identity-drift blocking.
- Capability-gated LinkedIn member-post readback. It remains disabled unless the LinkedIn app actually has `r_member_social`.
- Owner-authorised private GitHub sources with selected-repository/read-only enforcement, encrypted rotating user authority, per-read revalidation, owner controls and deletion cleanup.
- Workspace PITR coordination, quarantine, restored-authority invalidation, exact undo and D1 effect fences outside restored Durable Object state.
- Free explicit publishing/scheduling/receipts and Advanced source-monitoring foundations, including independently scheduled metrics.
- Stripe subscription Checkout/Portal/webhook/refund/dispute/cancellation handling with staging-only sandbox controls and a separately disabled MPP foundation.
- Mixed-root protected credential reads, complete protected inventory traversal, bounded conditional rewrap/cutover and readback.
- Blocking production and full dependency audits.
- Automatic staging deployment requires the exact `main` revision to be associated with a merged pull request. GitHub server-side main protection remains a separate repository-admin control.

## Accepted external evidence

These gates are closed for restricted staging and must be preserved rather than replayed:

1. real owner Google sign-in;
2. one controlled Threads provider publication and independent exact readback;
3. Inspect-only agent grant over HTTP and remote MCP plus denial after owner revocation;
4. selected-private-repository GitHub installation/OAuth/read/provider-revocation/fail-closed journey;
5. Stripe sandbox subscription Checkout, paid application state, refund/revoked entitlement, operator cancellation and completed webhook ledger;
6. protected encryption-root cutover with `next` as active writer and legacy root retained for recovery.

## External/browser/platform gates still open

1. **Threads OAuth callback:** parked until Meta accepts the exact callback allowlist. This is not permission to weaken redirect validation or publish again.
2. **Cloudflare PITR:** parked because the hosted runtime can read the current bookmark but fails target resolution at `getBookmarkForTime()` before any restore is armed.
3. **Native WebMCP:** one authenticated read-only invocation in a browser that actually implements the native API. The owner's ordinary authenticated browser has already been observed to lack native WebMCP support.
4. **X provider app/grant:** real application registration, protected credentials, owner consent, stable identity/capability and refresh evidence.
5. **LinkedIn app/grant:** real application registration and owner consent; member readback remains separately gated on actual restricted permission approval.

## Paid product and production are separate

Stripe sandbox acceptance does not automatically enable Advanced. `ADVANCED_ENABLED` remains false until one controlled live source-change -> inventory/allocation -> provider/readback -> scheduled-metrics path is accepted.

MPP remains optional and disabled.

Production/public release also requires real operational evidence: custom production origin and Cloudflare edge controls, capacity/cost calibration, alert delivery/escalation, hosted cross-tenant checks, GitHub main protection and public-signup/support/abuse ownership. The 14 September repository ruleset inventory was empty, so deployment provenance is not yet a substitute for server-side branch protection.

The workspace UI exposes current readiness, provider connection health, private GitHub source state, reviewed Advanced profile configuration and owner recovery status/actions. Automatic deployment must remain non-destructive and cannot manufacture provider consent, private-repository authority, a PITR restore, a native browser invocation or a production admin control.
