# Operating and recovering the service

## Inspect

Use `/health` for revision/configuration status and authenticated `workspace_status`, `receipts_list`, `billing_status` and `automation_inspect` for customer state. A health response does not prove successful publication or payment. Logs must contain only correlation IDs and outcome codes, never provider tokens, authorization headers or payment credentials.

## Pause

`publishing_pause` blocks new workspace claims; `PUBLISHING_PAUSED=true` blocks them globally. `automation_pause` cancels unclaimed automatic work for one profile. Already executing writes may complete. These controls do not delete history or undo provider effects. Agent grant revocation is checked again before future delivery.

## An uncertain post

Do not delete a receipt, overwrite its fingerprint pointer or create a revised campaign to bypass duplicate protection. Inspect the provider account and recorded container/post ID. `ambiguous_effect` requires evidence-based reconciliation. A later readback failure keeps the provider creation ID and `published_unverified` status.

An expired executing claim is handled by phase: after the publish boundary it becomes ambiguous; with a durable provider ID it remains unverified; before publication it becomes failed. No post is automatically replayed. Threads readiness retries are reads of the existing container, not repeated publication.

## Payment interruption

Inspect `billing_status`; retain the original quote and attempt. Reconciliation looks up the original session or PaymentIntent. Never create another financial attempt merely because the response or entitlement write was lost. Webhook acknowledgement occurs after durable event storage and current-state reconciliation; failed processing remains retryable by Stripe. Manual investigation is needed when bounded reconciliation cannot locate an attempt.

## Subscription expiry

Free publishing, manually scheduled work, receipt inspection and export remain available. Unclaimed automated work is cancelled and profiles are paused. Restored paid coverage requires explicit automation resume. A failed renewal never extends the last confirmed paid-through time.

## Restore or migration

Pause publishing and automation before restoring workspace state. Provider and Stripe effects may have happened after the snapshot. Reconcile every potentially lost effect before enabling writes, because a restored ledger cannot know about later external actions. Use supported Cloudflare backups; validate restore against isolated staging first. Never run destructive schema changes in the deployment workflow.

## Current limits

The initial pilot reserves at most 20 deliveries per UTC day and 100 active schedules per workspace. Source scans and dispatch batches are bounded. Automatic source observations run every 15 minutes; the initial metrics task checks up to 10 recent profile receipts daily. Validate real infrastructure use before publishing broader allowances. No automatic customer overage charges exist.

Before public release, finish operational alerting, credential key rotation, provider token refresh, retention/erasure and richer reconciliation tooling. The software is not a claim that these operational requirements have already been met.
