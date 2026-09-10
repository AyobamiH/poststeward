# Data lifecycle and workspace erasure

PostSteward keeps operational state only while it is useful to the workspace, subject to explicit safety ceilings. Erasure is a separate owner authority operation because provider publication history and recovery evidence must not be silently confused with a normal record delete.

## Retained workspace state

Each workspace Durable Object enforces the following hard SQLite record-store ceilings:

- 20,000 retained application records.
- 16 MiB aggregate serialized key/value data.
- 128 KiB for any one serialized value.

The quota is enforced by SQLite triggers inside the Durable Object rather than by a client-supplied estimate. Existing records can be reduced while the workspace is at capacity; new retained records fail with `WORKSPACE_STORAGE_LIMIT` instead of consuming unbounded account storage. Provider payloads and request bodies have separate network limits.

These are initial safety ceilings, not a claim of proven unit economics. Capacity/load calibration remains required before unrestricted traffic.

## Credential encryption lifecycle

Credential envelopes are versioned. Version 1 remains readable using the original AES-GCM root-key format. New writes use key-schedule version 2, which derives independent AES-256-GCM material from the backed-up root with HKDF-SHA256 and includes the version and workspace/account context in the authenticated envelope contract.

This permits cryptographic key-schedule rotation without making existing credentials unreadable. It is not the same thing as replacing the backed-up root secret. A root-secret replacement requires an explicit keyring/rewrap rehearsal and must not be performed as part of an ordinary deployment.

## Owner workspace deletion

The owner-facing lifecycle surface is `/lifecycle`. Deletion requires a current owner browser session, a fresh owner completion proof, CSRF/origin checks and the exact typed phrase `DELETE <workspace-id>`.

Deletion proceeds as a fail-closed state machine:

1. Verify that point-in-time recovery is not currently prepared or armed.
2. In one D1 batch, create an irreversible `pending` workspace-deletion tombstone and enable publication quarantine.
3. Reuse the recovery external-effect settlement gate. If a provider or Threads-container request acquired an effect intent immediately before quarantine, deletion remains pending until its bounded two-minute write window is no longer live. No external-effect ledger is erased while a provider write may still be executing.
4. Delete the workspace Durable Object's application records and alarms.
5. In one D1 batch, remove the workspace's sessions/owner proofs, agent grants, provider OAuth states, Stripe mappings/events, recovery coordinator rows and external-effect rows, then change the deletion tombstone to `completed`.
6. Clear the current browser session cookie. The completed D1 tombstone remains permanently as a minimal anti-resurrection marker.

A pending deletion has no cancel path. If the local clear or D1 purge is interrupted, the workspace remains fenced and the same owner may retry deletion; ordinary product work is not re-enabled between attempts.

The Durable Object class checks the D1 deletion tombstone before normal internal requests and before alarm execution. Restoring an old Durable Object snapshot therefore cannot resurrect a deleted workspace's publication, automation, billing or provider-token activity.

Provider-side authorization grants live outside PostSteward. The service deletes locally usable credential material, but the owner may still need to revoke the application grant at the provider according to that provider's controls. The service must not claim provider-side revocation when the provider did not confirm it.

## Evidence boundary

Deployment smoke checks `/lifecycle`, the browser script, unauthenticated lifecycle status/deletion rejection and cross-origin rejection. It never supplies a real workspace deletion phrase and never creates a deletion tombstone. The destructive owner path is verified in the real Workers/D1/SQLite Durable Object test runtime, but a real customer's deletion remains an owner operation.
