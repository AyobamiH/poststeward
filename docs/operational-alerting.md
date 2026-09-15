# Operational alerting

PostSteward separates platform telemetry from application-semantic incidents.

## Platform-owned signals

Worker 5xx rate, edge/login rate-limit pressure, request/runtime failures and D1/platform availability are observed through Cloudflare's platform telemetry and notification layer. PostSteward does not attempt to report a D1 outage by first writing another record to the same unavailable D1 database.

## Application-semantic alerts

PostSteward owns durable semantic evidence for conditions the platform cannot infer safely:

- ambiguous provider effects and OAuth authority failures;
- recovery plans that remain quarantined/prepared/armed/reconciled beyond their bounded operating window;
- stale Stripe reconciliation state;
- capacity threshold observations supplied by the capacity/calibration path.

These alerts use the D1 `operational_alerts` outbox. A condition is recorded before delivery is attempted. The outbox uses:

- stable alert IDs and an `Idempotency-Key` header;
- severity-aware deduplication windows, so a critical escalation cannot disappear behind a warning;
- a 60-second sending lease and safe lease reclamation;
- bounded exponential retry for network failures, 408, 425, 429 and 5xx responses;
- `Retry-After` support for 429;
- a maximum of eight attempts followed by durable `dead` state;
- no raw workspace ID, provider token, webhook signature, request body or customer identifier in the webhook payload. Subject identity is a truncated SHA-256 fingerprint only.

A five-minute Worker schedule sweeps persisted semantic state and flushes the outbox. The established hourly identity-expiry schedule remains separate.

## Delivery authority

The webhook destination is optional and protected:

- `OPERATIONAL_ALERT_WEBHOOK_URL`
- `OPERATIONAL_ALERT_WEBHOOK_TOKEN` (optional bearer authority)

If no URL is configured, alerts remain pending and no outbound request is attempted. Runtime readiness reports whether delivery is configured, but configuration alone never advances `operational_alert_delivery` from `external_setup_required`.

## Acceptance

Production acceptance still requires the live evidence contract in `scripts/operational-alert-evidence.mjs`:

- every reviewed alert class delivered and acknowledged;
- every configured notification path exercised;
- one deliberately failed path demonstrating escalation to a different fallback path.

This prevents “the code can send an alert” from being confused with “an operator actually received and acknowledged the alert”.
