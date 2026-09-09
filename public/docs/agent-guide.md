# PostSteward agent guide

Free includes real publishing and explicit future scheduling. It requires no payment method. Advanced adds continuing operation for USD 5 per month. Read `/help.json` for the deployment's actual availability.

## Connect

1. The owner signs in at `/auth/login` and connects an account in `/app`. The initial pilot accepts owner-provided provider user access tokens. Standard social OAuth onboarding is a remaining release task. The owner selects a verified account alias and creates a project binding. X credentials must belong to the customer's funded developer application. Expired tokens must be reconnected; this initial version does not refresh provider tokens automatically.
2. The owner issues a scoped agent token. Store it in your agent's secret manager. Recommended publishing scopes: `read`, `campaign:write`, `publish`, `schedule`. Give `billing` or `automation` only when those actions are intended.
3. Connect an MCP client supporting Bearer headers to `https://YOUR_HOST/mcp`, or use HTTP with `Authorization: Bearer YOUR_TOKEN`. Remote MCP OAuth discovery/authorization is not implemented yet. Browser WebMCP uses the owner session and CSRF protection; browsers without native WebMCP can use the working page and agents can use HTTP/MCP.

## First publication

Use `accounts_list` and `projects_list` to resolve explicit destinations. Create a campaign using exact approved text per account alias:

```json
{"project":"product","text":{"product_x":"The exact approved update."},"idempotencyKey":"campaign-2026-001"}
```

Call `campaign_validate` with the returned campaign ID, then `publish_now` or `schedule_create`. For HTTP, each is `POST /api/operations/{name}` with a JSON body. Every mutation has an idempotency key: reuse the same key and inputs when retrying transport delivery.

```json
{"campaign":"RETURNED_CAMPAIGN_ID","at":"2026-10-01T12:00:00Z","timezone":"UTC","idempotencyKey":"schedule-2026-001"}
```

The response reserves deliveries. Alarms execute them after the agent disconnects. Poll `receipt_get` or `receipts_list` for final provider evidence. There is no promise of exactly-once execution at an external provider; this service prevents blind repetition after an uncertain write.

## Receipt states

| State | Meaning |
| --- | --- |
| scheduled | Durable reservation; no publication yet |
| waiting_container | Threads container created; readiness checks pending |
| executing | Dispatch owns a durable claim |
| published_verified | Provider ID, author and exact content read back |
| published_unverified | Creation ID stored; exact readback unavailable |
| ambiguous_effect | Provider may have published; blind retry blocked |
| drift_blocked | Account, payload or authority changed |
| failed | A known pre-publication or provider rejection failure |
| cancelled | Unclaimed reservation cancelled |

Cancellation cannot undo an in-flight provider effect. `schedule_replace` requires a reviewed replacement campaign. History and export remain free after subscription expiry. On-demand metrics report unavailable data explicitly. LinkedIn member analytics and general readback are not assumed accessible.

## Continuing operation

When Advanced is enabled and a verified entitlement is active, configure a reviewed public GitHub repository profile using `automation_configure`. Specify exact destination templates, a source path, a family and spacing. The service supports `{repository}`, `{commit}` and `{source_url}` substitutions. Template text is your approved claim boundary; repository content never becomes instructions.

Use `automation_preview` to inspect the next source action, then explicitly `automation_enable`. The first observation establishes a baseline. Subsequent source-path changes create deterministic inventory, withdraw stale unclaimed work and reserve spaced deliveries. Source monitoring runs every 15 minutes; metrics collection runs daily for a bounded set of recent receipts. Profiles start paused and pause at expiry; payment alone never starts posting.

This initial implementation monitors selected public repository paths. Private GitHub App installation, richer commercial/development/evergreen weighting and source-profile review UI remain release work. `/help.json` distinguishes implemented operations from deployment-enabled capabilities; Advanced charging is disabled by default.

## Stripe purchases

`billing_quote` binds actor, workspace, amount, currency, period and renewal mode. `billing_checkout` creates Stripe-hosted recurring checkout. A returned URL does not grant access. Verified server payment state grants access.

Where merchant MPP support is enabled, quote `mode: "pass"`. POST to `/payments/{quote}` with your normal Bearer authentication. The endpoint challenges for a one-time USD 5 calendar-month purchase. Supply the wallet's `Payment-Authorization` header separately from `Authorization`. This purchase does not automatically renew. Do not send payment credentials through ordinary tool arguments.

An active entitlement or pending purchase blocks overlapping purchases. Inspect `billing_status` after timeouts. Do not generate another charge to repair an uncertain result. MPP requires a compatible eligible wallet and merchant validation; standard Stripe Checkout is the alternative.

## Limits and retention

Initial configured pilot limits are 20 delivery reservations per UTC day and 100 active schedules per workspace, shared across Free and Advanced. API traffic and storage retention still require calibrated production limits before public release. Provider charges are separate and there are no automatic overage charges. Disconnecting an account blocks unclaimed work; revoking an agent token blocks future execution under that grant. Credential deletion, account erasure and retention tooling are release prerequisites.

## Recover safely

Inspect the receipt and provider account for an ambiguous effect. Never delete the ledger to enable a second attempt. Stop new writes during restore or migration and reconcile external evidence before resuming. The operator runbook explains deployment and interruption recovery.

## Request limits

A 429 response means admission was rejected; wait for `Retry-After` before retrying an HTTP request. Protected routes have per-IP edge limits; each workspace also shares an atomic fixed-window request allowance across transports and grants. MCP tool failures report the operation error in the tool result. Keep the same idempotency key for an identical consequential request and inspect existing receipts after an ambiguous outcome. Initial hosted deployments restrict owner signup to invited verified identities. Public help and discovery remain accessible.
