# Private hosted completion gap closure

Date: 12 September 2026

This document reconciles the private hosted completion audit against the repository state that was current when this work began: main `e8a95086548a23541e89e2f33d56c0abd274d9cc`, the PR #30 merge. It deliberately separates repository engineering from live external acceptance. A provider consent, public write, payment, restore, secret rotation, repository grant or browser capability is never marked complete merely because code or a harness exists.

## Current hosted truth

- Owner Google sign-in: **live accepted** in the owner browser.
- Threads application: **configured in restricted staging**. A real owner Threads grant, one controlled publication and independent provider readback remain open.
- X and LinkedIn applications: **not configured** and intentionally outside the Threads-first P0 path.
- Private GitHub authority: **engineered and deployed**; staging App configuration plus real grant/read/revoke acceptance remain open.
- Stripe subscription billing: **engineered**; the test Product/Price exists, while protected sandbox configuration and the real test lifecycle remain open.
- Advanced, MPP and public signup: **disabled**.
- Automatic deploy/CI remains non-destructive. It does not sign in, publish, restore, delete, grant authority or settle payment.

## Gap-by-gap disposition

| Audit item | Disposition after this change | Closure boundary |
| --- | --- | --- |
| P0-1 real Threads owner grant | External live-acceptance blocker | Complete fresh owner consent and store the stable provider identity. No token may appear in logs or source. |
| P0-2 first controlled real publication | External live-acceptance blocker | Owner must approve one exact `/pilot` review. Exactly one provider creation ID is permitted; ambiguous write outcomes are inspected, never retried with a fresh key. |
| P0-3 independent Threads readback | External live-acceptance blocker | Separate provider GET must match creation ID, stable owner ID and exact text. |
| P0-4 scoped agent delegation | Repository-side acceptance tooling added; live run still required | `scripts/hosted-acceptance.mjs agent` proves HTTP + remote MCP on the same workspace/release without printing the raw workspace or token. `revoked` proves both transports deny the same token after owner revocation. |
| Private GitHub grant/read/revoke | External live-acceptance blocker | Existing selected-only/read-only authority remains the product boundary. Real staging App setup and owner grant are not fabricated by CI. |
| Durable Object PITR | External destructive rehearsal | Existing recovery coordinator/effect fences remain authoritative. A real prepare -> execute -> reconcile -> resume run against an explicit non-production target is still required. |
| Native browser WebMCP | External browser-capability acceptance | Existing native registration/round-trip remains. A supported browser-agent invocation and post-logout/revoke denial are still required. |
| Stripe sandbox lifecycle | External financial-provider acceptance | Do not create a webhook until its one-time signing secret can be written directly to protected staging secret storage. Checkout/renewal/cancel/refund/dispute remain live sandbox gates. |
| MPP | External settlement gate | Remains disabled and must not block Free Threads completion. |
| Advanced inventory/category management | Repository-side product decision closed | The existing reviewed `Profile.family` is the category identity; no second drifting category entity is introduced. `/advanced-inventory.html` groups families, source snapshots, reserved automatic deliveries and metrics evidence. Profile authority changes remain on `/app`; preview is read-only and does not reserve work. |
| Standalone CLI definition | Explicit product decision | PostSteward supports shell/cURL over the documented HTTP operation surface. There is no separately packaged CLI binary promise, so no extra package/update channel is created. |
| MCP authorization discovery | Explicit product decision | Remote MCP supports owner-issued, scoped, expiring, revocable Bearer tokens. Standards-based OAuth bootstrap is not claimed for this release. Discovery must never mint or broaden authority. |
| Root encryption-key replacement | Repository-side rewrap primitive + rehearsal coverage added; real rotation remains external | `src/root-rotation.ts` rewraps one explicitly inventoried envelope old-root -> new-root while preserving authenticated context. Tests prove the rewrapped envelope works only with the new root. A real staging credential inventory/rewrap/rollback rehearsal is still required before changing the backed-up root secret. |
| Capacity/retention calibration | Repository-side calibration harness added; live observations still required | `scripts/capacity-calibration.mjs` evaluates measured workload against the existing 20k-record, 16 MiB, 128 KiB/value and product ceilings with 30% minimum headroom. Provider/Cloudflare costs must come from a real load run. |
| Operational alerts | Production acceptance contract documented; provider configuration still external | Alert delivery cannot be proven from source. Production must demonstrate error/auth/rate/provider/queue/D1-DO/webhook/billing alert delivery to the named operator before launch. |
| Cross-tenant hosted security | Repository-side adversarial harness added; two-workspace live run required | `scripts/hosted-acceptance.mjs cross-tenant` verifies foreign receipt-ID denial and hostile Origin replay denial with two real scoped tokens. |
| Production domain/DNS/TLS/WAF/rate policies | Repository-side read-only verifier added; production configuration remains external | `scripts/production-edge-check.mjs` requires a custom HTTPS origin, release-pinned readiness, HSTS/CSP, proxied DNS, WAF evidence and rate-limit rules. It performs no Cloudflare mutation. |
| GitHub main protection | Repository-side verifier added; repository-admin mutation remains external | `scripts/github-main-protection-check.mjs` requires active main rules covering deletion, non-fast-forward, one approving review and the `Verify` status check. At the start of this work the repository ruleset inventory was empty. |
| Real staging workspace erasure | External destructive acceptance | Implementation remains complete; use a disposable workspace and prove anti-resurrection. Do not run against the owner workspace merely to close a checklist. |
| Public signup/support/abuse readiness | Production acceptance contract documented | Signup remains restricted until support, deletion/revocation, abuse and incident ownership are exercised. |

## Threads-first completion sequence

1. Run the read-only readiness check and confirm Threads OAuth is configured while X/LinkedIn, Advanced and MPP remain outside P0.
2. In the owner browser, complete fresh Threads consent and verify the stable account identity.
3. Review the exact destination and exact text in `/pilot`, then approve that single review.
4. Preserve the one provider creation ID. If the response is lost or ambiguous, inspect existing evidence only. Do not create a fresh publication attempt.
5. Require the separate provider readback to match ID, stable owner ID and exact text.
6. Create a least-privilege agent grant. Run the HTTP + remote MCP acceptance. Revoke it from the owner UI and run the denial check.
7. Close native WebMCP separately. X/LinkedIn remain unrelated to this P0 chain.

## Acceptance tooling

Read-only deployment/configuration check:

```sh
POSTSTEWARD_ORIGIN=https://poststeward-staging.woeinvests.workers.dev \
  node scripts/hosted-acceptance.mjs readiness
```

Scoped agent acceptance, with the token provided only through the process environment:

```sh
POSTSTEWARD_ORIGIN=https://poststeward-staging.woeinvests.workers.dev \
POSTSTEWARD_AGENT_TOKEN='...' \
  node scripts/hosted-acceptance.mjs agent
```

After owner revocation, run the same token through the denial check:

```sh
POSTSTEWARD_ORIGIN=https://poststeward-staging.woeinvests.workers.dev \
POSTSTEWARD_AGENT_TOKEN='...' \
  node scripts/hosted-acceptance.mjs revoked
```

The harness never prints the token and fingerprints the workspace before emitting evidence.

Cross-tenant hosted acceptance requires two deliberately created test workspaces and an existing harmless receipt ID from workspace A. It performs read-only object-ID swapping plus a hostile Origin replay check; it does not publish or mutate tenant state.

## Production evidence still required

The repository can define and verify boundaries, but these facts require the real external systems:

- one actual Threads consent/publication/readback;
- one actual least-privilege agent grant/revoke journey;
- private GitHub App/grant/read/revoke if private sources are in release scope;
- Cloudflare PITR and disposable-workspace erasure rehearsals;
- root-secret replacement using an exact credential inventory and rollback plan;
- native WebMCP browser-agent invocation;
- Stripe test webhook/Portal/Checkout/renewal/cancel/refund/dispute;
- MPP only if separately enabled later;
- real capacity/cost observations, alert delivery, cross-tenant hosted run, production DNS/TLS/WAF/rate policy evidence and GitHub main ruleset activation.

None of those is converted to “complete” by a fixture, dry run, screenshot of configuration, success redirect or source-code assertion.
