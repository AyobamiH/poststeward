# Research basis for PostSteward distributed runtime

**Purpose:** explain why the PostSteward cloud/local architecture is tenable and which
external designs informed the implementation.

This document is not a claim that PostSteward is identical to any cited system. It
extracts specific design patterns that fit PostSteward's constraints.

## OpenClaw insights

OpenClaw's current installer and pairing architecture provides four especially useful
patterns.

### 1. The installer is a product entrypoint, not a download shortcut

OpenClaw serves canonical installers from its product domain, supports macOS/Linux/WSL
and Windows, can skip onboarding, supports dry-run/verification, and can target exact
Git revisions. The installer verifies the replacement before treating installation as
successful and preserves an existing installation when replacement verification fails.

**PostSteward implication:** `https://poststeward.com/install.sh` should install a
real runtime, verify it, then launch onboarding. Exact revisions, `--no-onboard`,
`--dry-run` and post-install verification are product requirements rather than
debug-only conveniences.

Source: OpenClaw `docs/install/installer.md`.

### 2. Bootstrap authority is short-lived; durable device authority is separate

OpenClaw uses bounded setup/bootstrap credentials for pairing, then issues a separate
device token after approval. Device tokens can be rotated or revoked. A pairing-required
response carries structured recovery guidance rather than silently widening authority.

**PostSteward implication:** the installer must not ask a user to paste a long-lived
agent token as the machine identity. Pairing gets a short-lived one-time claim; owner
approval then returns a separate installation-bound runtime credential.

Source: OpenClaw `docs/gateway/protocol/auth.md`, pairing setup-code implementation,
and device-token flows.

### 3. Pairing is not admin authority

OpenClaw separates ordinary pairing from owner/admin privilege and makes the first-owner
bootstrap an explicit, constrained exception.

**PostSteward implication:** pairing a runtime proves that the workspace owner accepts
that machine. It must not automatically grant billing, recovery, provider-OAuth or
executor-switch authority to an agent running on that machine.

### 4. Exact runtime diagnostics remain available even when full operation is blocked

OpenClaw keeps bounded diagnostics available on unsupported runtimes and uses doctor/
verification paths during installation and upgrades.

**PostSteward implication:** `poststeward doctor`, `runtime status`, setup status
and evidence inspection must remain available when publishing authority is closed or a
cloud lease is unavailable.

## Kubernetes insight: renewable leases plus fencing

Kubernetes uses Lease objects for node heartbeat and leader election. The useful idea is
that liveness is represented as renewable authority with a bounded time window rather
than as a permanent boolean.

A lease alone is insufficient for PostSteward because a previously active local process
could wake after losing network connectivity. Therefore PostSteward combines a lease
with the A-K runtime's monotonically advancing `authority_generation`.

**PostSteward invariant:**

```text
current executor == local installation X
AND lease is not expired
AND request generation == workspace authority_generation
AND local marker generation == workspace authority_generation
```

A stale runtime can retain old state, but the cloud provider relay rejects its old
generation. This is the fencing-token property.

Source: Kubernetes Lease API / leader election design.

## Cloudflare insight: one Durable Object per coordination atom

Cloudflare recommends using Durable Objects around the logical unit that needs
coordination, not as a global singleton. A Durable Object has globally unique identity,
strongly consistent storage and single-threaded coordination semantics.

PostSteward already has one `Workspace` Durable Object per workspace. That is the
correct serialization point for executor ownership, lease renewals and transition state.

**Why this is tenable:**

- executor transitions for one workspace serialize through one object;
- unrelated workspaces scale independently;
- the hosted Worker remains stateless for ordinary edge routing;
- D1 remains the durable cross-recovery/effect ledger where restore of workspace state
  must not erase provider-write history;
- no global executor coordinator becomes a throughput bottleneck.

Sources: Cloudflare Durable Objects architecture and rules/best-practices documentation.

## Tailscale insight: machine identity is not user identity

Tailscale separates device/node cryptographic identity from the user identity that
approved or provisioned the device. Device approval can be revoked later without
changing the user's identity.

**PostSteward implication:** each local runtime generates its own installation keypair.
The public key participates in pairing. Owner authentication approves the installation;
the server binds workspace + installation + key, rather than treating a copied bearer
secret as machine identity.

This also gives us a clean future path to signed heartbeats and effect requests.

Sources: Tailscale node-key and device-approval documentation.

## Existing PostSteward insight: D1 effect fencing is already the right consequence ledger

PostSteward's `external_effects` / `external_containers` ledger already writes an
intent fence before calling a social provider and preserves uncertainty instead of
blindly retrying. Recovery quarantine is also stored outside the workspace Durable
Object so restoring workspace-local state cannot erase provider-effect evidence.

The provider relay should **reuse this path**, not invent a second idempotency system.

The new executor-generation check belongs immediately before effect-intent acquisition.
That way:

```text
stale executor request
-> rejected before external_effects intent
-> zero provider consequence

current executor request
-> executor/generation/lease validated
-> existing external-effect fence acquired
-> provider call
-> created/uncertain/verified evidence
```

## Resulting architecture decision

The design is tenable because every distributed concern has one explicit authority:

| Concern | Authority |
| --- | --- |
| Human identity / billing / provider consent | PostSteward Cloud owner session |
| Machine identity | installation key + approved pairing record |
| Which executor may schedule/publish | workspace executor state + generation + lease |
| Local planning/schedules | active PostSteward local runtime |
| Provider credentials | PostSteward Cloud |
| Final provider write | executor-fenced provider relay |
| Duplicate/ambiguous provider effect | existing D1 external-effect ledger |
| Local unattended execution | A-K local automation marker |
| Recovery/migration | A-K setup/recovery state + cloud executor transition |

No layer is asked to infer another layer's authority.

## Implementation consequences

1. Replace token-paste onboarding with one-time machine pairing.
2. Generate a local installation keypair before pairing.
3. Add workspace executor generation and lease state.
4. Serialize executor transitions through the workspace Durable Object.
5. Persist an identity/audit projection in D1.
6. Bind the local runtime token to workspace + installation + scopes + key identity.
7. Add a dedicated provider-relay route whose first check is executor fencing.
8. Reuse the existing `EffectLedgerProviders` consequence fence.
9. Make hosted `publish_now` / schedule creation fail closed while local execution
   owns the workspace generation.
10. Keep diagnostics/read-only operations available regardless of executor mode.
