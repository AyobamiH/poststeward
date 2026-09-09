# Workspace recovery

PostSteward treats Durable Object point-in-time recovery as a change of authority, not as a storage-only operation. A restored object can contain credentials, schedules, automation settings and billing state that were valid at the target time but revoked or changed later. It can also forget that an external provider write already happened. Recovery therefore remains fail-closed until those two classes of risk are reconciled.

## Safety model

The workspace Durable Object is recoverable. The recovery coordinator and external-effect fences are stored separately in D1 so a Durable Object restore cannot roll them back.

Before any X, Threads or LinkedIn publication request, PostSteward creates a D1 effect fence keyed by workspace and immutable publication fingerprint. Threads container creation has its own fence. If a restored workspace attempts the same external effect again, existing provider evidence is reused when a durable provider ID exists. If the previous outcome is uncertain, the new write is refused rather than repeated.

Recovery quarantine is also stored in D1. While quarantined, background alarms are suppressed, payments and connection mutations are blocked, new publication authority is blocked, and ordinary operations are limited to inspection plus risk-reducing cancellation/pause/disconnect actions.

After the restored object starts, locally restored authority is invalidated before reconciliation completes: provider accounts are made inactive, OAuth refresh metadata is discarded, automation profiles are disabled, unclaimed schedules are drift-blocked, and restored entitlement/checkout/customer state is cleared. Content and historical receipts remain. This deliberately requires fresh authority after a restore instead of trusting credentials or billing facts from the past.

## Owner-only recovery flow

Recovery endpoints require the signed-in owner browser, current owner proof, the exact workspace, and CSRF protection supplied by the normal browser session.

1. `POST /api/recovery/prepare` with an explicit offset timestamp and concise reason. The target must be in the previous 30 days. The workspace is quarantined before PITR bookmarks are read. The prepared plan is bound to the owner and an immutable digest and expires after ten minutes.
2. Review the returned plan ID, target time and digest. Raw PITR bookmarks are not returned in public recovery status.
3. `POST /api/recovery/execute` with the same plan ID/digest and `execute: true`. The Durable Object arms the exact target bookmark, stores the pre-restore undo bookmark in D1, then intentionally aborts the current object session with alarm retry disabled so the next session starts from the selected point.
4. `POST /api/recovery/reconcile` after the object has restarted. The restored workspace is probed, resurrected authority is invalidated, and the D1 plan becomes `reconciled` only after that post-restore boundary succeeds.
5. Inspect receipts, external-effect counts, accounts, automation and recovered business state. Unresolved publication or Threads-container write fences (`intent`/`uncertain`) block resume.
6. `POST /api/recovery/resume` with the exact plan ID/digest and `resume: true` only after reconciliation. This removes global quarantine. Recovered provider connections and automation authority remain invalid and must be deliberately re-established; old captured schedules do not regain authority.

Before execute, `POST /api/recovery/cancel` may cancel the prepared plan if no unresolved external write exists. After reconciliation, `POST /api/recovery/undo` uses the exact undo bookmark returned by Durable Object PITR. Undo re-enters quarantine and is reconciled through the same path before another resume.

## Failure behaviour

Preparation never calls the destructive restore primitive until a plan can be produced. If bookmark preparation fails, PostSteward removes quarantine only when it can prove there is no prepared/armed recovery and no unresolved external write; otherwise quarantine remains.

Once a restore or undo is armed, any uncertain coordinator outcome is treated as a recovery incident. D1 plan state and quarantine are authoritative. The system does not infer success from a dropped connection or from the Durable Object restart itself.

D1 unavailability fails closed before provider writes and recovery state changes. Provider-side network uncertainty is preserved as an `uncertain` external-effect fence and requires reconciliation rather than retry.

## Verification boundary

Unit and Miniflare tests verify the D1 fences, replay prevention, quarantine, recovery-plan state machine, restored-authority invalidation, runtime schema and current Workers type contracts. Cloudflare Durable Object PITR itself is not available as a local Miniflare simulation, so tests do not manufacture a successful restore.

A staging deployment may verify migrations, compilation, current revision, access controls and non-destructive recovery surfaces. It must not call the restore primitive automatically. A real PITR restore remains an explicit owner operation because it intentionally changes stored customer state.
