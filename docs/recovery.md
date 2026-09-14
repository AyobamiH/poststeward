# Workspace recovery

PostSteward treats Durable Object point-in-time recovery as a change of authority, not as a storage-only operation. A restored object can contain credentials, schedules, automation settings and billing state that were valid at the target time but revoked or changed later. It can also forget that an external provider write already happened. Recovery therefore remains fail-closed until those two classes of risk are reconciled.

## Safety model

The workspace Durable Object is recoverable. The recovery coordinator, exact-checkpoint catalogue and external-effect fences are stored separately in D1 so a Durable Object restore cannot roll them back.

Before any X, Threads or LinkedIn publication request, PostSteward creates a D1 effect fence keyed by workspace and immutable publication fingerprint. Threads container creation has its own fence. If a restored workspace attempts the same external effect again, existing provider evidence is reused when a durable provider ID exists. If the previous outcome is uncertain, the new write is refused rather than repeated.

Recovery quarantine is also stored in D1. While quarantined, background alarms are suppressed, payments and connection mutations are blocked, new publication authority is blocked, and ordinary operations are limited to inspection plus risk-reducing cancellation/pause/disconnect actions.

After the restored object starts, locally restored authority is invalidated before reconciliation completes: provider accounts are made inactive, their restored encrypted access-token ciphertext is replaced with a non-usable tombstone, OAuth refresh metadata is discarded, automation profiles are disabled, unclaimed schedules are drift-blocked, and restored entitlement/checkout/customer state is cleared. Content and historical receipts remain. This deliberately requires fresh authority after a restore instead of trusting credentials or billing facts from the past.

## Exact checkpoints are the primary recovery contract

PostSteward captures the Durable Object runtime's exact current bookmark and stores it only in protected D1. The owner sees checkpoint metadata, never the raw bookmark:

- immutable checkpoint ID;
- capture time;
- exact release SHA;
- active encryption-root writer (`legacy` or `next`);
- source (`release`, bounded automatic capture or explicit owner capture);
- a digest of a non-secret operational state summary.

The digest intentionally does not include credential material and is not represented as a byte-for-byte authentication of the entire Durable Object snapshot.

Active workspaces opportunistically capture at most one checkpoint every six hours. A new deployed release gets a `release` checkpoint on the first eligible workspace activity. Failed or in-flight-effect captures are deferred for a bounded retry and never fail the customer operation that triggered the opportunity. Checkpoints are retained for at most 30 days and capped at 128 per workspace.

`GET /api/recovery/checkpoints` lists safe metadata. `POST /api/recovery/checkpoints` with `{ "capture": true }` performs an explicit fresh-owner capture. `/recovery` is the owner UI for selecting and preparing an exact checkpoint.

Raw bookmarks are recovery capabilities. They are never returned by the browser API, logs, receipts or generated evidence, and workspace erasure deletes their D1 rows.

## Owner-only exact recovery flow

Recovery endpoints require the signed-in owner browser, current owner proof, the exact workspace, and CSRF protection supplied by the normal browser session.

1. Select a stored checkpoint on `/recovery` and `POST /api/recovery/prepare` with its checkpoint ID plus a concise reason. The workspace is quarantined before the current pre-restore bookmark is captured. The target bookmark comes from the immutable server-side checkpoint row, not from browser input.
2. Review the returned plan ID, target time, `targetMode: exact_checkpoint`, checkpoint ID and digest. The checkpoint ID and target mode are included in the immutable recovery-plan digest. Raw PITR bookmarks are not returned.
3. `POST /api/recovery/execute` with the same plan ID/digest and `execute: true`. The Durable Object restores the exact stored bookmark, stores the runtime-provided undo bookmark in D1, then intentionally aborts the current object session with alarm retry disabled so the next session starts from the selected point.
4. `POST /api/recovery/reconcile` after the object has restarted. The restored workspace is probed, resurrected authority is invalidated, and the D1 plan becomes `reconciled` only after that post-restore boundary succeeds.
5. Inspect receipts, external-effect counts, accounts, automation and recovered business state. A fresh `intent` fence blocks PITR execution/resume for two minutes because the provider call may still be live. After that bounded window it becomes `uncertain`: the exact fingerprint stays permanently fenced against replay, but unrelated recovered work is not wedged by an old ambiguous effect.
6. `POST /api/recovery/resume` with the exact plan ID/digest and `resume: true` only after reconciliation. This removes global quarantine. Recovered provider connections and automation authority remain invalid and must be deliberately re-established; old captured schedules do not regain authority.

Before execute, `POST /api/recovery/cancel` may cancel the prepared plan if no unresolved external write exists. After reconciliation, `POST /api/recovery/undo` uses the exact undo bookmark returned by Durable Object PITR. Undo re-enters quarantine and is reconciled through the same path before another resume.

## Approximate timestamp compatibility path

The existing `{ at, reason }` form of `POST /api/recovery/prepare` remains available as an explicitly approximate compatibility path. It asks Cloudflare to resolve the requested time with `getBookmarkForTime()`.

It is deliberately not a hidden fallback for exact checkpoint recovery. If Cloudflare cannot resolve a timestamp, PostSteward returns that failure and does not silently choose a nearby checkpoint. Conversely, exact checkpoint preparation uses `getCurrentBookmark()` plus the already-stored exact target and does not call `getBookmarkForTime()`.

This separation means a failure of the provider's timestamp-to-bookmark convenience no longer removes PostSteward's exact recovery capability.

## Failure behaviour

Preparation never calls the destructive restore primitive until a plan can be produced. If preparation fails, PostSteward removes quarantine only when it can prove there is no prepared/armed recovery and no unresolved external write; otherwise quarantine remains.

Once a restore or undo is armed, any uncertain coordinator outcome is treated as a recovery incident. D1 plan state and quarantine are authoritative. The system does not infer success from a dropped connection or from the Durable Object restart itself.

D1 unavailability fails closed before provider writes and recovery state changes. Provider-side network uncertainty is preserved as an `uncertain` external-effect fence. That fingerprint can never be blindly retried, while unrelated work can resume after restored authority has been invalidated and no fresh external write remains in flight.

## Verification boundary

Unit and Miniflare tests verify the D1 fences, checkpoint catalogue, raw-bookmark privacy, replay prevention, quarantine, recovery-plan state machine, restored-authority invalidation, runtime schema and current Workers type contracts. Cloudflare Durable Object restore itself is not available as a local Miniflare simulation, so tests do not manufacture a successful restore.

A staging deployment may verify migrations, compilation, exact revision, owner/rejection controls and static checkpoint surfaces. It must not call the restore primitive automatically. A real restore remains an explicit owner operation because it intentionally changes stored customer state.

Advanced billing remains deliberately fail-closed after a PITR restore until current Stripe provider-of-record evidence is reconciled again. Recovery never treats historical entitlement state as proof of current paid access.
