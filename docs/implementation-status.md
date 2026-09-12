# PostSteward implementation status: 12 September 2026

## Executive state

PostSteward is substantially implemented. The remaining work is primarily live external acceptance and productionisation, not another product rewrite.

The latest owner-observed restricted-staging release is `ae4831ef368c5e3361f053e4ea28f4b2faa0a4f8`, also recorded by successful [deployment run 34679445845](https://github.com/AyobamiH/poststeward/actions/runs/34679445845). See [P0 live evidence](p0-live-evidence-2026-09-12.md) for the owner-executed token proof and the Meta callback-save blocker. The earlier PR #31/#32 deployment and its 58 hosted checks remain historical evidence in [the deployment receipt](hosted-completion-deployment-2026-09-12.md).

The prior PR #30 revision `e8a95086548a23541e89e2f33d56c0abd274d9cc` remains historical evidence for the owner Google sign-in acceptance and the first deployed Threads configuration. It is no longer the current deployed revision.

Current hosted truth:

- Owner Google sign-in: **live accepted** from the earlier owner journey. The latest deployment deliberately did not manufacture or repeat owner consent.
- Threads provider application: **configured**. PostSteward OAuth consent/callback remains open. The manual-token publication and independent readback are accepted from the owner receipt.
- X and LinkedIn applications: **not configured** and outside the Threads-first P0 path.
- Scoped agent grants + HTTP/remote MCP: **live accepted for Inspect-only scope from owner-supplied transcript**. Both transports accepted the same workspace/release, then denied the same token after owner revocation. See [P0 evidence and limits](p0-live-evidence-2026-09-12.md); delegated publishing remains unproven.
- Private GitHub sources: **engineered and deployed**. Staging App configuration and real grant/read/revoke acceptance are still open.
- Durable Object PITR: recovery/quarantine/effect fencing is **implemented**. A real restore/reconcile/resume rehearsal remains open.
- Native browser WebMCP: registration and native round-trip engineering is **implemented**. Authenticated browser-agent invocation remains open.
- Stripe subscription billing: **implemented**. The test Product/Price exists; protected sandbox webhook/key/Portal configuration and the test lifecycle remain open.
- Advanced, MPP and public signup: **disabled**.
- GitHub main protection: deploy provenance exists, but server-side ruleset protection was still absent when this completion pass began and has not been claimed as activated.

See [hosted completion deployment receipt](hosted-completion-deployment-2026-09-12.md) for historical staging evidence, [private hosted completion gap closure](completion-gap-closure-2026-09-12.md) for the gap-by-gap disposition and [production readiness acceptance](production-readiness-acceptance.md) for the remaining production evidence contract.

## Implemented product foundation

| Area | Current implementation |
| --- | --- |
| Hosted foundation | TypeScript Cloudflare Worker, D1 identity, SQLite Durable Objects, alarms, explicit deployment configuration and CI |
| Owner identity | Google OIDC code/PKCE, browser sessions, CSRF, restricted admission and atomic owner proof |
| Agent authority | Scoped, expiring, revocable Bearer grants with admin non-delegation and workspace isolation |
| Controlled publication | Owner-only `/pilot`, stable provider identity, immutable expiring review, explicit approval, one-shot reservation and 30-second cancellation boundary |
| Effect safety | Idempotency, fingerprint dedupe, claim fencing, ambiguous-effect preservation, known creation-ID retention and no blind retry across the write boundary |
| Readback | Separate bounded provider GET with exact post ID, stable author ID and exact text verification |
| Provider OAuth | X PKCE, Threads long-lived/refresh flow and LinkedIn OAuth with capability-gated member readback |
| Free publishing | Explicit project routing, immutable campaigns, publish now, explicit schedules, cancel/replace, receipts, export and on-demand metrics |
| Agent transports | 26 shared operations over HTTP, remote MCP and browser WebMCP; generated help/OpenAPI/reference/discovery |
| Private GitHub | Owner-only selected-repository authority, read-only Contents/Metadata, encrypted rotating user credential, per-read revalidation, unlink/revoke/lifecycle cleanup and fail-closed private reads |
| Recovery | Owner-only PITR plan, quarantine, restore/reconcile/resume/undo state machine, restored-authority invalidation and external-effect fences |
| Advanced foundation | Source monitoring, reviewed deterministic templates, baseline/change detection, stale-work withdrawal, bounded replenishment/spacing and scheduled metrics |
| Advanced management model | `Profile.family` is the category identity. `/advanced-inventory.html` provides an owner view of category groups, source snapshots, reserved automatic deliveries and metric evidence without introducing a second drifting category store |
| Billing | Staging-only Stripe sandbox gate, USD 5 monthly Checkout/Portal, signed webhook reconciliation, entitlement logic and separately disabled MPP foundation |
| Root-rotation support | Narrow old-root -> new-root envelope rewrap/verification primitive with adversarial tests. Real storage enumeration and staging replacement rehearsal remain external |
| Acceptance tooling | Read-only Threads-first readiness, Advanced inventory asset verification, HTTP/MCP grant, revoked-grant, cross-tenant, capacity, Cloudflare edge and GitHub ruleset verification harnesses |

## Explicit product decisions

### CLI

PostSteward's supported command-line contract is shell/cURL over the documented HTTP operations. There is no promise of a separately packaged PostSteward binary, updater or package distribution channel. Do not add a CLI package merely to satisfy old wording.

### Remote MCP authorization

Remote MCP uses owner-issued, scoped, expiring, revocable Bearer tokens. Standards-based OAuth-compatible MCP authorization discovery/bootstrap is not part of the current release contract. Discovery may describe tools and consequence classes, but it must not mint or broaden authority.

### Advanced categories and inventory

The existing reviewed profile `family` is the category boundary used by spacing decisions. A second category entity would create drift without additional authority value. Inventory is represented by the profile's current observed source snapshot and any automatic deliveries reserved from that snapshot. The owner-facing Advanced inventory view groups and exposes that state while profile changes continue to require the existing reviewed, paused configuration path.

## Evidence interpretation

Automated tests and Miniflare runtime scenarios exercise real Worker/D1/SQLite/Assets code, tenant isolation, Bearer/CSRF rejection, idempotency, effect fencing, provider OAuth, private GitHub authority, recovery coordination, billing reconciliation and browser registration logic. Provider/Google/GitHub/Stripe responses in CI are fixtures unless a receipt explicitly says otherwise.

The following cannot be inferred from code or CI and remain separate evidence classes:

- real provider consent;
- a real public provider write;
- independent provider readback;
- a real private-repository grant/read/revoke;
- a Cloudflare PITR restore;
- workspace deletion against real staging state;
- native browser-agent WebMCP invocation;
- Stripe test payment lifecycle or MPP settlement;
- root-secret replacement across real encrypted state;
- alert delivery, Cloudflare WAF/rate policy configuration or GitHub ruleset activation.

A success redirect, configured flag, screenshot, fixture or HTTP 2xx alone does not close those gates.

## Immediate P0 sequence

1. Preserve the completed manual-token publication/readback and Inspect-only revoke proof.
2. Finish PostSteward OAuth callback/code exchange when Meta allows the exact callback to persist.
3. Execute remaining external gates only with the required protected configuration and disposable targets.

See [P0 evidence](p0-live-evidence-2026-09-12.md). The offline receipt checker performs no network operation.

## Next safety/production milestones

After P0:

1. Private GitHub staging App/grant/read/revoke, if private sources are in release scope.
2. PITR rehearsal, disposable workspace erasure and root-key replacement/rewrap rehearsal.
3. Advanced source-to-inventory-to-spaced-allocation-to-metrics live acceptance before paid automation is enabled.
4. Stripe webhook/Portal/Checkout/settlement/renewal/cancel/refund/dispute sandbox lifecycle. Keep MPP separate unless launch requires it.
5. Capacity/cost calibration, operational alert delivery, hosted cross-tenant attack run, custom production domain/DNS/TLS/WAF/rate policies, GitHub main ruleset and public signup/support/abuse controls.

Automatic CI/deploy must remain non-destructive throughout: it must not sign in, publish, grant/revoke owner authority, restore, delete a workspace, rotate a real secret or settle a payment.
