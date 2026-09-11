# Live acceptance plan

## Current baseline

Restricted staging remains the acceptance environment. The latest deployed baseline before this completion pass is main `e8a95086548a23541e89e2f33d56c0abd274d9cc` from PR #30 at `https://poststeward-staging.woeinvests.workers.dev`.

Current facts:

- Owner Google sign-in has been accepted live in the owner's normal browser.
- Threads provider OAuth is configured in staging.
- A real Threads owner grant has not yet been accepted.
- No owner-approved real Threads publication/readback receipt has been recorded.
- X and LinkedIn provider applications remain unconfigured.
- Private GitHub engineering is deployed; staging App configuration and real grant/read/revoke are open.
- Stripe engineering is deployed; protected sandbox configuration and payment lifecycle acceptance are open.
- Advanced, MPP and public signup are disabled.

Do not recreate working Google or Threads application credentials. Do not broaden to X or LinkedIn until the single Threads path is complete.

## Phase 1: close the hosted product loop

### 1. Readiness

Run the repository's read-only verifier against staging:

```sh
POSTSTEWARD_ORIGIN=https://poststeward-staging.woeinvests.workers.dev \
  node scripts/hosted-acceptance.mjs readiness
```

It must report Threads OAuth configured, X/LinkedIn unavailable, restricted signup and Advanced/MPP disabled.

### 2. Threads owner grant

From the owner's normal browser:

1. open the hosted workspace or `/pilot`;
2. choose the intended Threads alias;
3. start Threads OAuth;
4. approve the requested Threads permissions;
5. return to PostSteward and verify the stable provider identity shown by the service.

Acceptance requires the code exchange to complete and the verified stable Threads identity/capabilities to be stored. A provider success screen without PostSteward's stored identity is not acceptance. Never copy the provider token into chat, source, logs or an evidence document.

### 3. Exact controlled publication

1. In `/pilot`, select the verified Threads destination.
2. Enter the exact text intended for the one acceptance post.
3. Review the destination, stable identity, text, digest and current release.
4. Approve that exact immutable review.
5. Preserve the resulting PostSteward delivery ID and provider creation ID.

The existing owner-pilot path supplies a 30-second cancellation boundary before the claimed external effect. If the request disconnects or the provider response becomes uncertain, inspect the existing delivery. Do not create a fresh campaign/review/key to bypass uncertainty.

### 4. Independent readback

The controlled path must perform a separate provider GET after the creation result. Acceptance requires the same provider post ID, stable owner ID and exact text. `published_verified` is the successful terminal state. `published_unverified` and `ambiguous_effect` are not substitutes.

## Phase 2: close agent delegation

Create a least-privilege agent grant from the owner workspace. A `read`-only grant is sufficient for the first transport proof; add another scope only if a separately reviewed workflow requires it.

Put the shown-once token directly into the agent secret environment, not a shell history file or acceptance document, then run:

```sh
POSTSTEWARD_ORIGIN=https://poststeward-staging.woeinvests.workers.dev \
POSTSTEWARD_AGENT_TOKEN='...' \
  node scripts/hosted-acceptance.mjs agent
```

The harness calls `workspace_status` through HTTP and remote MCP and requires both to resolve to the same release/workspace. It emits only a workspace fingerprint.

Revoke that grant in the owner UI. With the same token, run:

```sh
POSTSTEWARD_ORIGIN=https://poststeward-staging.woeinvests.workers.dev \
POSTSTEWARD_AGENT_TOKEN='...' \
  node scripts/hosted-acceptance.mjs revoked
```

Both HTTP and remote MCP must return denial. This closes agent delegation acceptance only when the real grant was issued and revoked; a unit test does not.

## Phase 3: safety and recovery

### Private GitHub, when in release scope

Configure the dedicated staging GitHub App with the existing setup/callback contract, selected repositories only and read-only Contents/Metadata. The invited owner selects one deliberately small private repository set. Prove only the selected repository is visible, perform one harmless path probe, revoke/remove access and prove the next private read fails closed without anonymous fallback.

### Durable Object PITR

Use a disposable/non-production workspace with an explicit restore target. Record a pre-rehearsal canary, authority inventory and effect state without credentials. Execute the existing prepare -> execute -> reconcile -> resume state machine using one immutable plan/digest. Verify restored authority is invalidated and no external effect replays. Treat undo as a separate exact-plan action.

### Workspace erasure

Use a disposable staging workspace. Export it, delete it through the owner lifecycle UI, prove the tombstone prevents resurrection, then sign in again and prove a new workspace is created. Do not erase the primary owner acceptance workspace simply to close the checklist.

### Root-key replacement

Follow `docs/production-readiness-acceptance.md`. Key-schedule version rotation is already implemented; root-secret replacement must enumerate and rewrap real encrypted state. `src/root-rotation.ts` provides the narrow per-envelope primitive and tests, but only an actual staging rewrap/restart/rollback rehearsal closes the gate.

## Phase 4: Advanced and Stripe

The Advanced product boundary remains disabled until both product and payment acceptance are complete.

Advanced inventory/category decision: the reviewed profile `family` is the category identity. The owner can inspect grouped categories, current source snapshots, reserved automatic deliveries and metrics evidence at `/advanced-inventory.html`. A second category store is intentionally not introduced. Profile changes still use the existing paused/reviewed configuration path.

Before enabling Advanced, validate a real source change -> source snapshot -> deterministic campaign inventory -> spaced reservation -> provider effect/readback -> scheduled metrics cycle.

For Stripe, use the already created USD 5 monthly test Product/Price. Do not create a webhook until its one-time signing secret can be stored directly in protected staging secret storage. Configure a restricted test API key, signed webhook and Billing Portal, then perform one quote -> Checkout -> test payment -> webhook reconciliation -> entitlement journey. Replay the same quote/event and prove no duplicate charge or entitlement. Separately exercise renewal/payment-method change, cancellation/expiry, refund and dispute, then clean up the sandbox subscriptions and disable sandbox access.

MPP remains a separate acceptance stream and must not block the Free Threads launch.

## Phase 5: productionise

Use `docs/production-readiness-acceptance.md` for:

- real capacity/cost observations and customer-facing retention/limit calibration;
- operational alert delivery and escalation ownership;
- hosted cross-tenant attack checks;
- custom production origin, DNS/TLS, WAF and rate-policy evidence;
- GitHub main ruleset activation/verification;
- public signup, abuse, support, deletion/revocation and incident ownership.

Production and public signup are separate gates. A successful production deployment does not imply unrestricted admission.

## Evidence rule

Mark a live gate passed only when the external system itself produced the required effect and PostSteward independently observed the required evidence. Configuration flags, fixtures, source assertions, screenshots of a setup form and successful redirects may support diagnosis, but they do not replace provider readback, restore state, payment reconciliation, browser-agent invocation or post-revocation denial.
