# PostSteward agent guide

Free includes direct reviewed publishing and explicit future scheduling. Advanced is USD 5 per workspace per month when the deployed rollout policy enables it and adds bounded continuing operation around reviewed sources. Payment never grants posting authority by itself. Read `/help.json` and `/readiness.json` for the deployment's actual availability.

## Connect

1. The owner signs in at `/auth/login` and connects an account in `/app`. Provider OAuth is implemented for X, Threads and LinkedIn, but a provider remains unavailable until its deployed application and negotiated capabilities are actually configured. Manual import remains a fallback for an already-authorised user token. For X manual import, the owner explicitly confirms use of their own funded developer application.
2. PostSteward stores OAuth access/refresh material encrypted server-side. Threads supports long-lived refresh; X OAuth uses refresh authority when issued; LinkedIn can require reauthorisation when a usable refresh token is not available. Connection identity is revalidated before refreshed credentials can replace captured authority.
3. The owner selects verified account aliases and creates explicit project bindings.
4. The owner issues a scoped, expiring agent token. Store it in the agent's secret manager. Recommended publishing scopes are `read`, `campaign:write`, `publish` and `schedule`. Add `billing` or `automation` only when those actions are intended. Admin cannot be delegated through this endpoint.
5. Connect an MCP client supporting Bearer headers to `https://YOUR_HOST/mcp`, or use HTTP with `Authorization: Bearer YOUR_TOKEN`. Browser WebMCP is additive and uses the owner browser session/CSRF boundary when the browser exposes the native API. HTTP and remote MCP remain the stable agent surfaces.

Private GitHub source access is separate from agent publication authority. When needed, the owner can install the PostSteward GitHub App for selected repositories with read-only Contents access. Public repositories do not require that authority.

## First publication

Use `accounts_list` and `projects_list` to resolve explicit destinations. Create a campaign using exact approved text per account alias:

```json
{"project":"product","text":{"product_x":"The exact approved update."},"idempotencyKey":"campaign-2026-001"}
```

Call `campaign_validate` with the returned campaign ID, then `publish_now` or `schedule_create`. For HTTP, each is `POST /api/operations/{name}` with a JSON body. Every consequential mutation has an idempotency key: reuse the same key and identical inputs only when recovering transport delivery.

```json
{"campaign":"RETURNED_CAMPAIGN_ID","at":"2026-10-01T12:00:00Z","timezone":"UTC","idempotencyKey":"schedule-2026-001"}
```

The response reserves deliveries. Durable alarms execute future work after the agent disconnects. Poll `receipt_get` or `receipts_list` for provider evidence. PostSteward does not promise exactly-once behaviour inside an external provider; it prevents blind repetition when a provider write may already have happened.

## Receipt states

| State | Meaning |
| --- | --- |
| scheduled | Durable reservation; no provider publication claimed |
| waiting_container | Threads container created; readiness checks pending |
| executing | Dispatch owns the durable execution claim |
| published_verified | Provider ID, stable author and exact content independently read back |
| published_unverified | Creation evidence stored; exact readback not established |
| ambiguous_effect | Provider may have accepted the write; blind retry blocked |
| drift_blocked | Captured account, payload or authority no longer matches reviewed work |
| failed | Known rejection or pre-effect failure |
| cancelled | Unclaimed reservation cancelled |

Cancellation cannot undo an in-flight provider effect. `schedule_replace` requires a reviewed replacement campaign. On-demand metrics report unavailable data explicitly; unavailable provider metrics are never represented as a fabricated zero. LinkedIn member readback and analytics depend on the actual deployed LinkedIn application permissions.

## Continuing operation

When Advanced is enabled for the workspace and a verified entitlement is active, configure a reviewed repository profile using `automation_configure`. Specify exact destination templates, a repository/branch/path, family and spacing. The service supports `{repository}`, `{commit}` and `{source_url}` substitutions. Template text is the approved claim boundary; repository content is treated as source data, never as instructions.

Use `automation_preview` to inspect the next action, then explicitly call `automation_enable`. The first observation establishes a baseline. Subsequent reviewed source-path changes can create deterministic inventory, withdraw stale unclaimed work and reserve spaced deliveries. Profiles start paused and stop when entitlement/authority is unavailable. Payment alone never starts posting.

Public repositories can be read anonymously. Selected private repositories can use the owner-authorised GitHub App path. `/help.json` distinguishes implemented operations from runtime-enabled capabilities. Advanced also has an independent master switch and deterministic rollout policy, so repository implementation is not evidence that execution is enabled for a workspace.

## Stripe purchases

`billing_quote` binds actor, workspace, amount, currency, period and renewal mode. `billing_checkout` creates Stripe-hosted recurring checkout when the deployment enables it. A returned Checkout URL does not grant access; verified server payment state does.

Where merchant MPP support is separately enabled, quote `mode: "pass"` and POST to `/payments/{quote}` with the agent Bearer token plus the wallet's `Payment-Authorization` header. Do not send payment credentials through ordinary operation inputs.

An active entitlement or pending purchase blocks overlapping purchases. Inspect `billing_status` after timeouts instead of starting another charge to repair uncertainty. Provider/API charges are separate from PostSteward's service price.

## Limits, retention and erasure

Hosted configuration currently bounds delivery reservations, active schedules, request admission and retained workspace storage. Treat `/help.json`, operation errors and `Retry-After` as authoritative for the deployed limits.

The owner Data surface supports workspace export, bounded retention archive/pruning and explicit workspace erasure. Provider credentials and payment tokens are excluded from workspace export. Erasure durably fences the old workspace before recoverable local authority is cleared; provider-side grants may still need revocation at the provider.

Disconnecting an account blocks unclaimed future work. Revoking an agent token blocks future execution under that grant. Neither action can undo an external effect that already crossed the provider write boundary.

## Recover safely

Exact recovery checkpoints are the primary recovery path. Preparing a restore quarantines new publication. Execution requires the signed-in owner and exact typed confirmation. After the object restarts, restored authority must be reconciled against external-effect evidence before publication can resume. A reconciled plan can expose an exact undo path while its stored undo bookmark remains valid.

Approximate timestamp-to-bookmark recovery is a separate compatibility path and may be unavailable even when exact checkpoints are healthy. Inspect receipts and provider accounts for ambiguous effects; never delete the ledger to enable another attempt.

## Request limits

A 429 means admission was rejected; wait for `Retry-After` before retrying an HTTP request. Protected routes have edge admission limits and each workspace has an atomic request allowance across transports and grants. MCP failures report the operation error in the tool result. Keep the same idempotency key for an identical consequential request and inspect existing receipts after an ambiguous outcome.

Owner signup may be restricted even when technical production infrastructure exists. Provider OAuth applications, public signup, Advanced rollout and optional browser capabilities each have separate release evidence. Use `/readiness.json`, `/help.json` and the generated release-gate ledger rather than inferring availability from repository code.
