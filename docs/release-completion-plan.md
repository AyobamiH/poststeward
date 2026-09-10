# Release completion control plane

PostSteward separates code-complete capability from externally proven capability. A green build or staging deployment must never silently turn an external gate into a product claim.

## Implemented and deployable

- Restricted Google OIDC owner sign-in and session-bound controlled publication approval.
- X, Threads and LinkedIn publishing adapters with explicit uncertain-outcome handling and durable external-effect fences.
- Provider OAuth architecture for X, Threads and LinkedIn, encrypted access/refresh storage, background refresh and identity-drift blocking. Each provider remains unavailable until its own application credentials are configured.
- Capability-gated LinkedIn member-post readback. It remains disabled unless the LinkedIn app actually has `r_member_social`.
- Workspace PITR coordination, quarantine, restored-authority invalidation, exact undo and D1 effect fences outside restored Durable Object state.
- Free explicit publishing/scheduling/receipts and Advanced source-monitoring foundations. Advanced/MPP stay disabled until payment acceptance is proven.
- Blocking production and full dependency audits.
- Automatic staging deployment additionally requires the exact main revision to be associated with a merged pull request. GitHub branch rules remain a separate repository-admin control.

## External evidence gates

1. Real owner Google consent and authenticated-browser acceptance.
2. At least one real provider application/grant, exact controlled publication and independent readback.
3. A deliberate staging PITR rehearsal with owner approval and post-restore reconciliation.
4. Native WebMCP acceptance in a browser that actually implements the API.
5. Stripe sandbox settlement/refund/dispute acceptance and, separately, eligible MPP merchant/wallet validation.
6. GitHub main ruleset/required-check enforcement, Cloudflare custom-domain/WAF/alerts and production origin/resources.

The workspace UI exposes current readiness, provider connection health, reviewed Advanced profile configuration and owner recovery status/actions. Destructive recovery never runs from deployment automation.
