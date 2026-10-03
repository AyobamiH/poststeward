# PostSteward agent operation reference

Generated from src/operations/catalog.ts. Do not edit by hand.

## autonomy_request

Ask the standing producer to fill an actual stock deficit using owner-selected sources and context. Reuses an existing durable request. Agent model spending still requires owner consent.

- Tier: advanced
- Required scope: campaign:write
- Effects: STATE_WRITE, FINANCIAL_EFFECT, FUTURE_CONSEQUENCE
- Inspect with: autonomy_list
- Retry: Reuse the same idempotencyKey and exact inputs. Inspect status after disconnection; never create a fresh key to bypass an uncertain result.

Example:

```json
{
  "project": "product",
  "idempotencyKey": "autonomy-request-001"
}
```

## autonomy_list

Inspect standing project authority, stock request and editorial holds.

- Tier: free
- Required scope: read
- Effects: READ_ONLY
- Inspect with: autonomy_list
- Retry: Safe to repeat.

Example:

```json
{}
```

## autonomy_configure

Owner setup consent for recurring evidence-grounded preparation and paced publishing. Clean checks proceed without per-copy approval; existing manual deliveries remain unchanged.

- Tier: advanced
- Required scope: admin
- Effects: STATE_WRITE, AUTHORITY_CHANGE, FUTURE_CONSEQUENCE, FINANCIAL_EFFECT
- Inspect with: autonomy_list
- Retry: Reuse the same idempotencyKey and exact inputs. Inspect status after disconnection; never create a fresh key to bypass an uncertain result.

Example:

```json
{
  "project": "product",
  "selection": {
    "repository": "owner/product",
    "releaseTag": "v1.0",
    "documentationPaths": [
      "README.md"
    ],
    "allowPrivate": false,
    "allowUnreleased": false
  },
  "context": {
    "audience": "Builders delegating release communications",
    "objective": "Explain useful documented capabilities to potential users",
    "brandVoice": "Specific British English",
    "productContext": "A source-grounded publishing product",
    "exclusions": "No unsupported outcomes",
    "callToAction": "Read the documentation"
  },
  "enabled": true,
  "intervalMinutes": 240,
  "stockFloor": 2,
  "maxDailyDeliveries": 3,
  "idempotencyKey": "autonomy-setup-001"
}
```

## autonomy_pause

Owner revocation of standing publishing and generation authority. Captured deliveries stop before further provider writes.

- Tier: free
- Required scope: admin
- Effects: STATE_WRITE, AUTHORITY_CHANGE
- Inspect with: autonomy_list
- Retry: Reuse the same idempotencyKey and exact inputs. Inspect status after disconnection; never create a fresh key to bypass an uncertain result.

Example:

```json
{
  "project": "product",
  "idempotencyKey": "autonomy-pause-001"
}
```

## model_status

Inspect this workspace’s own model connection and bounded usage. No key is returned.

- Tier: free
- Required scope: read
- Effects: READ_ONLY
- Inspect with: model_status
- Retry: Safe to repeat.

Example:

```json
{}
```

## model_connect

Owner-only: connect workspace-owned OpenAI or Cloudflare credentials and explicit routing/spend policy. Cloudflare validation reads metadata only; no inference, top-up or shared gateway mutation occurs.

- Tier: free
- Required scope: admin
- Effects: AUTHORITY_CHANGE
- Inspect with: model_status
- Retry: Reuse the same idempotencyKey and exact inputs. Inspect status after disconnection; never create a fresh key to bypass an uncertain result.

Example:

```json
{
  "apiKey": "OWNER_SUPPLIED_API_KEY",
  "maxJobsPerDay": 2,
  "allowAgents": false,
  "inputUsdPerMillion": null,
  "outputUsdPerMillion": null,
  "maxDailyUsd": null,
  "idempotencyKey": "model-connect-001"
}
```

## model_disconnect

Owner-only: disconnect workspace model access and fence queued/in-flight generation.

- Tier: free
- Required scope: admin
- Effects: AUTHORITY_CHANGE
- Inspect with: model_status
- Retry: Reuse the same idempotencyKey and exact inputs. Inspect status after disconnection; never create a fresh key to bypass an uncertain result.

Example:

```json
{
  "idempotencyKey": "model-disconnect-001"
}
```

## preparations_list

Inspect private preparation strategy, evidence, drafts, review states and model usage; never provider/model credentials.

- Tier: free
- Required scope: read
- Effects: READ_ONLY
- Inspect with: preparations_list
- Retry: Safe to repeat.

Example:

```json
{}
```

## preparation_export

Export a private preparation and the exact digest required for reviewed removal. No credential or publication effect.

- Tier: free
- Required scope: read
- Effects: READ_ONLY
- Inspect with: preparations_list
- Retry: Safe to repeat.

Example:

```json
{
  "id": "RETURNED_PREPARATION_ID"
}
```

## preparation_archive

Owner-only: remove an exported terminal preparation from the active library. Retains immutable campaigns, receipts, spending and existing operation history.

- Tier: free
- Required scope: admin
- Effects: STATE_WRITE
- Inspect with: preparations_list
- Retry: Reuse the same idempotencyKey and exact inputs. Inspect status after disconnection; never create a fresh key to bypass an uncertain result.

Example:

```json
{
  "id": "RETURNED_PREPARATION_ID",
  "revision": 1,
  "reviewDigest": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
  "idempotencyKey": "preparation-archive-001"
}
```

## preparation_project_put

Create cloud editorial project-to-account context for AI preparation in either executor mode. This never creates local runtime projects or schedules.

- Tier: free
- Required scope: campaign:write
- Effects: STATE_WRITE
- Inspect with: workspace_status
- Retry: Reuse the same idempotencyKey and exact inputs. Inspect status after disconnection; never create a fresh key to bypass an uncertain result.

Example:

```json
{
  "id": "product",
  "name": "Product preparation",
  "accounts": [
    "product_x"
  ],
  "idempotencyKey": "preparation-project-001"
}
```

## preparation_create

Queue original AI release interpretation, campaign strategy, drafting and editorial checks using the workspace’s own bounded model account. Never publishes or schedules.

- Tier: free
- Required scope: campaign:write
- Effects: STATE_WRITE, FINANCIAL_EFFECT
- Inspect with: preparations_list
- Retry: Reuse the same idempotencyKey and exact inputs. Inspect status after disconnection; never create a fresh key to bypass an uncertain result.

Example:

```json
{
  "project": "launch",
  "selection": {
    "repository": "owner/repository",
    "releaseTag": "v1.0",
    "documentationPaths": [
      "README.md"
    ],
    "allowPrivate": false,
    "allowUnreleased": false
  },
  "context": {
    "audience": "Operators maintaining release communications",
    "objective": "Explain the practical value of this release",
    "brandVoice": "Clear British English",
    "productContext": "A product for reviewed release communications",
    "exclusions": "No unsupported availability claims",
    "callToAction": "Read the release notes"
  },
  "idempotencyKey": "prepare-release-001"
}
```

## preparation_edit

Edit or remove selected draft variants and strategy. Edits invalidate checking/approval and never change an immutable handed-off campaign.

- Tier: free
- Required scope: campaign:write
- Effects: STATE_WRITE
- Inspect with: preparations_list
- Retry: Reuse the same idempotencyKey and exact inputs. Inspect status after disconnection; never create a fresh key to bypass an uncertain result.

Example:

```json
{
  "id": "RETURNED_PREPARATION_ID",
  "revision": 1,
  "text": {
    "product_x": "Owner-edited text"
  },
  "idempotencyKey": "preparation-edit-001"
}
```

## preparation_regenerate

Explicitly spend bounded model allowance to regenerate strategy, all/one draft, or check edited drafts. Uncertain calls are never retried automatically.

- Tier: free
- Required scope: campaign:write
- Effects: STATE_WRITE, FINANCIAL_EFFECT
- Inspect with: preparations_list
- Retry: Reuse the same idempotencyKey and exact inputs. Inspect status after disconnection; never create a fresh key to bypass an uncertain result.

Example:

```json
{
  "id": "RETURNED_PREPARATION_ID",
  "revision": 1,
  "stage": "draft",
  "alias": "product_x",
  "idempotencyKey": "preparation-regenerate-001"
}
```

## preparation_reject

Reject private preparation and fence any in-flight output; no publication or schedule is created.

- Tier: free
- Required scope: campaign:write
- Effects: STATE_WRITE
- Inspect with: preparations_list
- Retry: Reuse the same idempotencyKey and exact inputs. Inspect status after disconnection; never create a fresh key to bypass an uncertain result.

Example:

```json
{
  "id": "RETURNED_PREPARATION_ID",
  "revision": 1,
  "idempotencyKey": "preparation-reject-001"
}
```

## preparation_approve

Signed-in owner reviews exact revision/digest, current account bindings and evidence, then freezes variants into an immutable campaign. Scheduling remains a separate explicit action.

- Tier: free
- Required scope: admin
- Effects: STATE_WRITE
- Inspect with: preparations_list
- Retry: Reuse the same idempotencyKey and exact inputs. Inspect status after disconnection; never create a fresh key to bypass an uncertain result.

Example:

```json
{
  "id": "RETURNED_PREPARATION_ID",
  "revision": 1,
  "digest": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
  "idempotencyKey": "preparation-approve-001"
}
```

## workspace_status

Inspect workspace, entitlement, limits and publication pause.

- Tier: free
- Required scope: read
- Effects: READ_ONLY
- Inspect with: workspace_status
- Retry: Safe to repeat.

Example:

```json
{}
```

## publishing_capabilities

Inspect implemented publication formats, live provider-app configuration, connected stable identities, owner-approval behavior and the one-shot acceptance URL.

- Tier: free
- Required scope: read
- Effects: READ_ONLY
- Inspect with: publishing_capabilities
- Retry: Safe to repeat.

Example:

```json
{}
```

## accounts_list

Read verified account identities and binding versions. Credentials are never returned.

- Tier: free
- Required scope: read
- Effects: READ_ONLY
- Inspect with: accounts_list
- Retry: Safe to repeat.

Example:

```json
{}
```

## account_disconnect

Revoke a connection and block its future unclaimed deliveries.

- Tier: free
- Required scope: connections
- Effects: AUTHORITY_CHANGE, FUTURE_CONSEQUENCE
- Inspect with: accounts_list
- Retry: Reuse the same idempotencyKey and exact inputs. Inspect status after disconnection; never create a fresh key to bypass an uncertain result.

Example:

```json
{
  "alias": "product_x",
  "idempotencyKey": "disconnect-001"
}
```

## project_put

Create or replace explicit project-to-account routing. Existing deliveries retain their captured bindings.

- Tier: free
- Required scope: campaign:write
- Effects: STATE_WRITE
- Inspect with: projects_list
- Retry: Reuse the same idempotencyKey and exact inputs. Inspect status after disconnection; never create a fresh key to bypass an uncertain result.

Example:

```json
{
  "id": "product",
  "name": "Product",
  "accounts": [
    "product_x"
  ],
  "idempotencyKey": "project-001"
}
```

## projects_list

List project routing.

- Tier: free
- Required scope: read
- Effects: READ_ONLY
- Inspect with: projects_list
- Retry: Safe to repeat.

Example:

```json
{}
```

## campaign_create

Store immutable, exact text per account alias. No copy is generated or truncated.

- Tier: free
- Required scope: campaign:write
- Effects: STATE_WRITE
- Inspect with: campaign_get
- Retry: Reuse the same idempotencyKey and exact inputs. Inspect status after disconnection; never create a fresh key to bypass an uncertain result.

Example:

```json
{
  "project": "product",
  "text": {
    "product_x": "A reviewed product update."
  },
  "idempotencyKey": "campaign-001"
}
```

## campaign_get

Inspect exact content and its immutable digest.

- Tier: free
- Required scope: read
- Effects: READ_ONLY
- Inspect with: campaign_get
- Retry: Safe to repeat.

Example:

```json
{
  "campaign": "campaign-id"
}
```

## campaign_validate

Dry-run routing and provider text validation without publication.

- Tier: free
- Required scope: read
- Effects: READ_ONLY
- Inspect with: campaign_get
- Retry: Safe to repeat.

Example:

```json
{
  "campaign": "campaign-id"
}
```

## publish_now

Reserve each campaign delivery once and dispatch through durable alarms. Returns receipt IDs immediately; inspect receipts for actual outcome.

- Tier: free
- Required scope: publish
- Effects: STATE_WRITE, EXTERNAL_PROVIDER_EFFECT
- Inspect with: receipts_list
- Retry: Reuse the same idempotencyKey and exact inputs. Inspect status after disconnection; never create a fresh key to bypass an uncertain result.

Example:

```json
{
  "campaign": "campaign-id",
  "idempotencyKey": "publish-001"
}
```

## schedule_create

Schedule exact immutable campaign content at an explicit time with UTC offset.

- Tier: free
- Required scope: schedule
- Effects: STATE_WRITE, FUTURE_CONSEQUENCE
- Inspect with: receipts_list
- Retry: Reuse the same idempotencyKey and exact inputs. Inspect status after disconnection; never create a fresh key to bypass an uncertain result.

Example:

```json
{
  "campaign": "campaign-id",
  "at": "2026-10-01T12:00:00Z",
  "timezone": "UTC",
  "idempotencyKey": "schedule-001"
}
```

## schedule_cancel

Cancel one unclaimed delivery. Reports already executing if dispatch won the race.

- Tier: free
- Required scope: schedule
- Effects: STATE_WRITE, FUTURE_CONSEQUENCE
- Inspect with: receipt_get
- Retry: Reuse the same idempotencyKey and exact inputs. Inspect status after disconnection; never create a fresh key to bypass an uncertain result.

Example:

```json
{
  "delivery": "delivery-id",
  "idempotencyKey": "cancel-001"
}
```

## delivery_approve

Owner-review an agent-created delivery and release its immutable account, identity, text and schedule reservation. The original agent authority is still rechecked at dispatch.

- Tier: free
- Required scope: publish
- Effects: AUTHORITY_CHANGE, FUTURE_CONSEQUENCE
- Inspect with: receipt_get
- Retry: Reuse the same idempotencyKey and exact inputs. Inspect status after disconnection; never create a fresh key to bypass an uncertain result.

Example:

```json
{
  "delivery": "delivery-id",
  "idempotencyKey": "approve-001"
}
```

## delivery_reject

Owner-reject an agent-created delivery before any provider write.

- Tier: free
- Required scope: publish
- Effects: AUTHORITY_CHANGE, FUTURE_CONSEQUENCE
- Inspect with: receipt_get
- Retry: Reuse the same idempotencyKey and exact inputs. Inspect status after disconnection; never create a fresh key to bypass an uncertain result.

Example:

```json
{
  "delivery": "delivery-id",
  "idempotencyKey": "reject-001"
}
```

## schedule_replace

Atomically cancel an unclaimed delivery and reserve a reviewed replacement campaign for the same account.

- Tier: free
- Required scope: schedule
- Effects: STATE_WRITE, FUTURE_CONSEQUENCE
- Inspect with: receipt_get
- Retry: Reuse the same idempotencyKey and exact inputs. Inspect status after disconnection; never create a fresh key to bypass an uncertain result.

Example:

```json
{
  "delivery": "delivery-id",
  "campaign": "replacement-id",
  "at": "2026-10-01T13:00:00Z",
  "timezone": "UTC",
  "idempotencyKey": "replace-001"
}
```

## receipt_get

Inspect provider evidence, status and reason for one delivery.

- Tier: free
- Required scope: read
- Effects: READ_ONLY
- Inspect with: receipt_get
- Retry: Safe to repeat.

Example:

```json
{
  "delivery": "delivery-id"
}
```

## receipt_recheck

Verify the recorded post ID, stable author and exact text without publishing again. At most eight recovery reads, at least sixty seconds apart. Already verified receipts return unchanged.

- Tier: free
- Required scope: read
- Effects: STATE_WRITE
- Inspect with: receipt_get
- Retry: Reuse the same idempotencyKey and exact inputs. Inspect status after disconnection; never create a fresh key to bypass an uncertain result.

Example:

```json
{
  "delivery": "delivery-id",
  "idempotencyKey": "readback-001"
}
```

## receipts_list

Read delivery history. An unverified ID and an ambiguous effect are distinct from verified publication.

- Tier: free
- Required scope: read
- Effects: READ_ONLY
- Inspect with: receipts_list
- Retry: Safe to repeat.

Example:

```json
{
  "limit": 50
}
```

## workspace_export

Export project, campaign and receipt records; excludes credentials and payment tokens.

- Tier: free
- Required scope: read
- Effects: READ_ONLY
- Inspect with: workspace_export
- Retry: Safe to repeat.

Example:

```json
{}
```

## metrics_capture

Capture available provider metrics on demand. Unsupported or inaccessible metrics are returned as unavailable, never fabricated zeros.

- Tier: free
- Required scope: read
- Effects: STATE_WRITE
- Inspect with: receipt_get
- Retry: Reuse the same idempotencyKey and exact inputs. Inspect status after disconnection; never create a fresh key to bypass an uncertain result.

Example:

```json
{
  "delivery": "delivery-id",
  "idempotencyKey": "metrics-001"
}
```

## publishing_pause

Pause or resume all new workspace publication claims. In-flight effects may still finish.

- Tier: free
- Required scope: publish
- Effects: AUTHORITY_CHANGE, FUTURE_CONSEQUENCE
- Inspect with: workspace_status
- Retry: Reuse the same idempotencyKey and exact inputs. Inspect status after disconnection; never create a fresh key to bypass an uncertain result.

Example:

```json
{
  "paused": true,
  "idempotencyKey": "pause-001"
}
```

## automation_configure

Store an explicitly reviewed repository profile and exact approved templates. Configuration starts paused. Repository input never grants authority.

- Tier: advanced
- Required scope: automation
- Effects: STATE_WRITE
- Inspect with: automation_inspect
- Retry: Reuse the same idempotencyKey and exact inputs. Inspect status after disconnection; never create a fresh key to bypass an uncertain result.

Example:

```json
{
  "id": "release",
  "project": "product",
  "repository": "owner/product",
  "branch": "main",
  "path": "README.md",
  "templates": {
    "product_x": "Development update: reviewed source changed in https://github.com/owner/product."
  },
  "family": "development",
  "intervalMinutes": 60,
  "minSpacingMinutes": 60,
  "idempotencyKey": "profile-001"
}
```

## automation_inspect

Inspect profiles, source snapshots, decisions and pending automated deliveries. Inspection remains free after subscription expiry.

- Tier: free
- Required scope: read
- Effects: READ_ONLY
- Inspect with: automation_inspect
- Retry: Safe to repeat.

Example:

```json
{}
```

## automation_preview

Check the selected source and show the next permitted allocation without storing inventory or schedules.

- Tier: advanced
- Required scope: automation
- Effects: READ_ONLY
- Inspect with: automation_inspect
- Retry: Safe to repeat.

Example:

```json
{
  "id": "release"
}
```

## automation_enable

Enable bounded continuing authority for this reviewed profile. Payment alone does not grant posting authority.

- Tier: advanced
- Required scope: automation
- Effects: AUTHORITY_CHANGE, FUTURE_CONSEQUENCE
- Inspect with: automation_inspect
- Retry: Reuse the same idempotencyKey and exact inputs. Inspect status after disconnection; never create a fresh key to bypass an uncertain result.

Example:

```json
{
  "id": "release",
  "idempotencyKey": "enable-001"
}
```

## automation_pause

Pause a profile and cancel its unclaimed automated deliveries. Always available, including after expiry.

- Tier: free
- Required scope: automation
- Effects: AUTHORITY_CHANGE, FUTURE_CONSEQUENCE
- Inspect with: automation_inspect
- Retry: Reuse the same idempotencyKey and exact inputs. Inspect status after disconnection; never create a fresh key to bypass an uncertain result.

Example:

```json
{
  "id": "release",
  "idempotencyKey": "autopause-001"
}
```

## billing_status

Inspect confirmed paid-through access and payment method availability.

- Tier: free
- Required scope: read
- Effects: READ_ONLY
- Inspect with: billing_status
- Retry: Safe to repeat.

Example:

```json
{}
```

## billing_quote

Create an exact configured GBP workspace purchase quote: recurring subscription or non-renewing calendar month.

- Tier: free
- Required scope: billing
- Effects: STATE_WRITE
- Inspect with: billing_status
- Retry: Reuse the same idempotencyKey and exact inputs. Inspect status after disconnection; never create a fresh key to bypass an uncertain result.

Example:

```json
{
  "mode": "subscription",
  "idempotencyKey": "quote-001"
}
```

## billing_checkout

Create or retrieve Stripe-hosted subscription checkout for an unexpired quote. Checkout creation does not grant access.

- Tier: free
- Required scope: billing
- Effects: FINANCIAL_EFFECT
- Inspect with: billing_status
- Retry: Reuse the same idempotencyKey and exact inputs. Inspect status after disconnection; never create a fresh key to bypass an uncertain result.

Example:

```json
{
  "quote": "quote-id",
  "idempotencyKey": "checkout-001"
}
```

## billing_portal

Open the Stripe customer portal to inspect invoices or manage renewal.

- Tier: free
- Required scope: billing
- Effects: STATE_WRITE
- Inspect with: billing_status
- Retry: Reuse the same idempotencyKey and exact inputs. Inspect status after disconnection; never create a fresh key to bypass an uncertain result.

Example:

```json
{
  "idempotencyKey": "portal-001"
}
```

## runtime_inspect

Queue bounded inspection of the active local executor. Read runtime_command_get for the result; this is not hosted workspace state.

- Tier: free
- Required scope: read
- Effects: READ_ONLY
- Inspect with: runtime_command_get
- Retry: Safe to repeat.

Example:

```json
{
  "view": "schedules",
  "idempotencyKey": "local-inspect-001"
}
```

## runtime_schedule_create

Queue an exact local campaign/provider schedule on the reviewed local executor. Original agent authority is rechecked before provider effects.

- Tier: free
- Required scope: schedule
- Effects: STATE_WRITE, FUTURE_CONSEQUENCE
- Inspect with: runtime_command_get
- Retry: Reuse the same idempotencyKey and exact inputs. Inspect status after disconnection; never create a fresh key to bypass an uncertain result.

Example:

```json
{
  "campaign": "PRODUCT-001",
  "provider": "threads",
  "at": "2026-10-01T12:00:00Z",
  "idempotencyKey": "local-schedule-001"
}
```

## runtime_schedule_cancel

Queue cancellation of one unclaimed local schedule; never changes executor or owner authority.

- Tier: free
- Required scope: schedule
- Effects: STATE_WRITE
- Inspect with: runtime_command_get
- Retry: Reuse the same idempotencyKey and exact inputs. Inspect status after disconnection; never create a fresh key to bypass an uncertain result.

Example:

```json
{
  "scheduleId": "sch_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
  "idempotencyKey": "local-cancel-001"
}
```

## runtime_command_get

Read a durable command receipt for this actor. Claimed work is never automatically redispatched; provider receipts remain separate.

- Tier: free
- Required scope: read
- Effects: READ_ONLY
- Inspect with: runtime_command_get
- Retry: Safe to repeat.

Example:

```json
{
  "commandId": "11111111-1111-4111-8111-111111111111"
}
```
