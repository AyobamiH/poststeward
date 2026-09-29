# Engineering insights behind the PostSteward convergence

This document records the external engineering patterns used to validate the
PostSteward distributed-runtime design. It is explanatory guidance; the normative
product invariants remain in [ARCHITECTURE.md](ARCHITECTURE.md).

## OpenClaw: the installer is a front door to a real runtime

OpenClaw's public install flow is useful because the shell installer is not the
product. It resolves and installs a real runtime, supports unattended/headless
installation, exact versions or revisions, verification, and then hands the user to
guided onboarding.

Relevant public references:

- https://docs.openclaw.ai/install/installer
- https://docs.openclaw.ai/start/getting-started
- https://github.com/openclaw/openclaw

### PostSteward application

PostSteward follows the same useful separation:

```text
curl installer
  -> exact release
  -> prove local runtime
  -> install canonical command
  -> pair machine
  -> guided owner onboarding
  -> activate only after authority review
```

The installer must stay replaceable and boring. Product intelligence belongs in the
versioned runtime and hosted owner surface.

This works because failed/interrupted onboarding can resume without making the
installer itself an authority-bearing state machine.

## Kubernetes: leases are coordination, not ownership history

Kubernetes uses Lease objects for node heartbeats and leader election. Coordinated
leader election uses a holder identity, lease duration/renewal and optimistic
concurrency so only one contender wins an update.

References:

- https://kubernetes.io/docs/concepts/architecture/leases/
- https://kubernetes.io/docs/concepts/cluster-administration/coordinated-leader-election/

### PostSteward application

`workspace_executors` is the authoritative cloud coordination record:

```text
workspace
executor_mode
active_installation_id
authority_generation
lease_expires_at
```

The local runtime renews a bounded lease. A missed lease closes future relay
authority; it does not delete or rewrite historical receipts.

The cloud's clock evaluates the lease. A customer's local wall clock is not trusted
to decide who owns publishing authority.

This is tenable because PostSteward Cloud is already the workspace identity and
provider-credential authority, so it is a natural single coordination point.

## Kafka: generation/epoch fences stale writers

Kafka's idempotent/transactional producer model uses producer identity plus an epoch.
A broker rejects a stale epoch even if an old producer process wakes up later.

Reference:

- https://kafka.apache.org/documentation/#producerconfigs_enable.idempotence

The exact Kafka internals are not copied; the transferable idea is monotonically
increasing fencing generations.

### PostSteward application

Every executor transition increments `authority_generation`.

A provider relay write therefore needs both:

```text
active_installation_id == caller installation
authority_generation == caller generation
lease is live
```

A stale machine cannot regain authority merely by having an old runtime token or
coming back online.

This is stronger than lease expiry alone: expiry handles liveness; generation handles
ordering and stale-process fencing.

## AWS / large-scale distributed systems: idempotency belongs at the effect boundary

Reliable distributed systems assume requests and responses can be lost. Retrying a
write with a new identity is therefore dangerous.

PostSteward already had the right foundation: an external-effect ledger,
idempotency/fingerprint fencing and explicit ambiguous-effect states.

### PostSteward application

The local runtime supplies a deterministic `effectId` and exact text digest to the
cloud relay. The relay uses the existing hosted effect ledger.

```text
same effectId + same exact payload
    -> same result / no second provider write

same effectId + changed payload
    -> reject

uncertain provider result
    -> preserve ambiguity
    -> never mint retry authority
```

This makes cloud relay practical without creating a second scheduling system.

## TUF: a convenient installer still needs pinned release truth

The Update Framework separates roles such as targets, snapshot and timestamp metadata
to defend against rollback, freeze and mix-and-match attacks. Target metadata binds
artifacts to hashes/sizes; short-lived freshness metadata prevents a client from
accepting stale update state forever.

References:

- https://theupdateframework.io/overview/
- https://theupdateframework.github.io/specification/latest/

### PostSteward application

The first PostSteward release channel is intentionally smaller than a full TUF
implementation, but adopts the immediately useful properties:

- channel manifest resolves to one exact Git commit;
- manifest contains a deterministic embedded-runtime tree SHA-256;
- manifest has generation and expiry metadata;
- installer validates manifest shape and freshness;
- downloaded runtime is hashed before promotion;
- installed release SHA is passed into runtime attestation;
- exact explicit SHA installs remain possible for controlled testing.

Do **not** call this full TUF until PostSteward also has independent signing keys,
threshold/role separation and rollback metadata. It is TUF-informed release metadata,
not a TUF implementation.

## Google/SRE style: acceptance must include recovery, not only happy-path CI

A system is not production-ready because the nominal path passes. PostSteward's A-K
lineage deliberately added migration, dead-host recovery, activation failure drills,
adversarial setup tests and a real owner canary.

### PostSteward application

The convergence keeps those tests rather than replacing them with Cloudflare-only
tests. The final acceptance matrix includes:

- install and onboarding;
- pairing replay/expiry;
- stale generation;
- effect idempotency and ambiguity;
- local activation/deactivation;
- upgrade/rollback;
- migration/recovery;
- X, Threads and LinkedIn controlled external evidence.

## Why the combined architecture is tenable

The architecture avoids the most expensive failure mode: two independent systems
believing they may create the same social effect.

```text
Local runtime:
  owns planning / schedule / local durable intent

PostSteward Cloud:
  owns identity / OAuth secrets / executor fence / exact provider relay

Provider:
  sees one fenced effect path
```

This division works because each plane owns a non-overlapping source of truth:

- local state decides **what local work is due**;
- cloud state decides **which executor may cause effects**;
- provider receipts decide **what actually happened externally**.

No plane is allowed to infer another plane's fact.

## Implementation checklist derived from these insights

- [x] Real local runtime behind the installer.
- [x] Machine pairing with short bootstrap credential.
- [x] Cloud-side executor lease.
- [x] Monotonic authority generation.
- [x] Optimistic transition fencing.
- [x] Existing effect-ledger reuse for relay idempotency.
- [x] Hosted operation blocking while local executor is active.
- [x] Provider secrets remain cloud-side.
- [ ] Stable/beta release manifests with exact revision, runtime hash and expiry.
- [ ] Installer verifies channel metadata and runtime hash before promotion.
- [ ] Runtime activation requires matching cloud generation as well as local marker.
- [ ] Clean-machine external acceptance across X, Threads and LinkedIn.
