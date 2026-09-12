# Stripe sandbox setup and acceptance

## Verified progress

The owner selected the connected account in test mode on 10 September 2026. A dedicated PostSteward staging Product and USD 5 monthly Price were created and independently retrieved: active, `livemode=false`, currency=usd, `unit_amount=500`, interval=month, interval_count=1 and `tax_behavior=inclusive`. Reuse that resource; do not create another Price on retry. The verified Price is `price_1UEH75GbPfXt7ec560fJXX5U` for product `prod_poststeward_staging_advanced_v1`.

On 12 September 2026 the active Billing Portal configuration inventory was still empty. The protected staging preflight proved that the GitHub `staging` environment did not yet contain the sandbox enable/Price variables, runtime Stripe key or webhook secret. A dedicated Portal setup/operator key had not yet been introduced or configured at the time of that run. No customer, Checkout Session, subscription or payment has been created by the acceptance pass.

PR #34 added a protected staging preflight. The follow-up billing hardening makes Portal acceptance deterministic instead of relying on the account default: the setup preflight creates or repairs exactly one active test-mode Portal configuration tagged `application=poststeward, environment=staging`, and sandbox Portal session creation lists the active configurations, requires exactly that one reviewed configuration and passes its ID explicitly to Stripe. A missing, duplicate, live-mode or weakened configuration fails closed.

## Protected configuration

Use separate least-privilege test keys for runtime payment work and one-time Portal setup. Neither key belongs in source, PR text, logs, chat or evidence documents. The Portal setup key is consumed only by the protected GitHub `staging` workflow and is never deployed to the Worker. The runtime key is the only Stripe API key deployed to the Worker.

| Protected staging setting | Required value |
| --- | --- |
| Variable `STRIPE_SANDBOX_ENABLED` | `true` only for the deliberate sandbox run |
| Variable `STRIPE_SANDBOX_PRICE_ID` | `price_1UEH75GbPfXt7ec560fJXX5U` |
| Secret `STRIPE_SANDBOX_SECRET_KEY` | Dedicated restricted `rk_test_` runtime key for the selected account |
| Secret `STRIPE_SANDBOX_OPERATOR_KEY` | Separate restricted `rk_test_` setup key with Billing Portal configuration read/write only |
| Secret `STRIPE_SANDBOX_WEBHOOK_SECRET` | Signing secret for the exact endpoint below |

The runtime application requires Price reads, Checkout Session creation/list/retrieval, subscription reads including expanded invoice/payment/charge data, charge reads for dispute/refund routing, Billing Portal configuration list/read and Billing Portal session creation. It does not need Billing Portal configuration write, webhook-administration or refund-write authority. Setup/refunds/cancellation use operator authority separately. MPP stays disabled.

The `Staging external acceptance preflight` workflow uses `STRIPE_SANDBOX_OPERATOR_KEY` to list/create/update the dedicated Portal configuration and requires a restricted `rk_test_` key. It receives only booleans for runtime/webhook secret presence and therefore cannot accidentally use the runtime payment key to mutate Portal configuration. Remove the operator key after acceptance if no further configuration repair is authorised.

Create the account-level webhook endpoint with the following reviewed parameters only when its one-time signing secret can be stored directly into `STRIPE_SANDBOX_WEBHOOK_SECRET` during the same controlled operation. Immediately disable the new endpoint while deploying the configured receiver; enable it only after the exact staging release reports sandbox billing configured. If setup fails, keep it disabled and reuse the existing endpoint. Never silently rotate the secret or create a replacement endpoint after an uncertain response.

```json
{
  "url": "https://poststeward-staging.woeinvests.workers.dev/webhooks/stripe",
  "api_version": "2026-08-26.dahlia",
  "connect": false,
  "description": "PostSteward restricted staging test acceptance",
  "enabled_events": [
    "checkout.session.completed",
    "checkout.session.expired",
    "checkout.session.async_payment_succeeded",
    "checkout.session.async_payment_failed",
    "invoice.paid",
    "invoice.payment_failed",
    "customer.subscription.updated",
    "customer.subscription.deleted",
    "charge.refunded",
    "charge.refund.updated",
    "refund.created",
    "refund.failed",
    "refund.updated",
    "charge.dispute.created",
    "charge.dispute.updated",
    "charge.dispute.closed"
  ],
  "metadata": {
    "application": "poststeward",
    "environment": "staging"
  }
}
```

The receiver verifies the signature and event mode before charge lookup. A dispute/refund without a workspace/customer resolves its charge, verifies charge identity/mode and uses the stored customer mapping. Retrieval failure returns 503; it does not acknowledge or complete the event. A quarantined workspace retains a retryable incomplete event.

## Exact acceptance sequence

1. Save the reviewed staging variables, runtime key and Portal operator key directly in the repository `staging` environment. Do not paste either key into an issue, PR, shell transcript or chat. Run `Staging external acceptance preflight`; it must report both keys present and exactly one active reviewed test Portal configuration.
2. Create the webhook only through a path that lets the one-time signing secret be written directly to the protected `STRIPE_SANDBOX_WEBHOOK_SECRET`. Do not create it if that atomic secret-placement path is unavailable.
3. Deploy the reviewed main revision through the normal staging workflow. Verify the deployed runtime SHA, restricted signup, `sandbox=true`, `Advanced=false`, `MPP=false` and the configured USD 5 test Price before creating any quote.
4. In the authenticated owner browser, create one subscription quote. Capture quote ID, amount, currency, expiry and auto-renewal terms. Start Checkout from that quote and retain its single Session ID. New quotes persist their integration identifier; repeated requests and reconstructed Workers must use identical parameters and idempotency keys.
5. Complete hosted Checkout using Stripe test payment details. Observe the signed webhook and server reconciliation. Record Session, subscription, invoice, PaymentIntent/charge and workspace mapping with `livemode=false`. A success URL or session creation alone grants nothing.
6. Retry the same quote and redeliver the same event. Confirm one settlement and no second Checkout purchase. Confirm the entitlement ends at the verified paid period and does not activate Advanced automation. Open the Billing Portal and verify the explicitly selected reviewed configuration, payment-method management and at-period-end cancellation path.
7. Issue a sandbox refund of the captured test charge through authorised Stripe operator access. Observe the signed refund event and entitlement revocation without relying on a manual status read to repair the state. Cancel the captured subscription.
8. Exercise a separate dispute using Stripe's documented dispute test method and an explicitly identified test purchase. Confirm the charge-only event maps to the workspace, is completed after reconciliation, and revokes coverage. Record the separate test objects; do not reuse or describe a refunded charge as the dispute test.
9. Cancel all subscriptions created by the rehearsal, expire abandoned open Checkout Sessions where applicable, disable sandbox Checkout and redeploy unless another test has been authorised. Retain the dedicated Product/Price for a later rerun.

Record actual observations and exact object IDs in an appropriate private acceptance record. Public receipts may state outcomes without account/customer/financial identifiers. Tests with Workers/D1 and mocked Stripe responses remain engineering evidence. MPP requires separate merchant/wallet eligibility and remains outside this Checkout run.
