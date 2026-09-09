# PostSteward agent operation reference

Generated from src/operations/catalog.ts. Do not edit by hand.

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

Create an exact USD 5 workspace purchase quote: recurring subscription or non-renewing calendar month.

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
