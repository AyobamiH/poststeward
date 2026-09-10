# Stripe sandbox setup and acceptance

## Verified progress

The owner selected the connected account in test mode on 10 September 2026. Inventory was empty. A dedicated PostSteward staging Product and USD 5 monthly Price were created and independently retrieved: active, livemode=false, currency=usd, unit_amount=500, interval=month, interval_count=1, tax_behavior=inclusive. Reuse that resource; do not create another Price on retry. Its identifiers are available in the private owner session.

No customer, Checkout Session, subscription or payment has been created. Hosted sandbox billing remains disabled. Automatic tax remains disabled for this test run.

## Protected configuration

Use a dedicated restricted test API key in the platform's protected secrets, never source, PR text, logs or chat. The working Stripe integration cannot create API keys, and the working GitHub integration does not administer environment secrets. Establish an authorised secure configuration path before creating the endpoint, because its signing secret is returned once.

| Protected staging setting | Required value |
| --- | --- |
| Variable STRIPE_SANDBOX_ENABLED | true only for the deliberate sandbox run |
| Variable STRIPE_SANDBOX_PRICE_ID | Existing verified test Price |
| Secret STRIPE_SANDBOX_SECRET_KEY | Dedicated rk_test_ key for the selected account |
| Secret STRIPE_SANDBOX_WEBHOOK_SECRET | Signing secret for the exact endpoint below |

The application requires Price reads, Checkout Session creation/list/retrieval, subscription reads including expanded invoice/payment/charge data, charge reads for dispute/refund routing, and Billing Portal session creation. Check the restricted key against these operations. Test setup/refunds/cancellation use operator authority separately; the application does not need webhook-administration or refund-write authority. MPP stays disabled.

Create the account-level endpoint with the following reviewed parameters only when its one-time signing secret can be stored securely. Immediately disable the new endpoint while deploying the configured receiver; enable it only after the exact staging release reports sandbox billing configured. If setup fails, keep it disabled and reuse the existing endpoint. Never silently rotate the secret or create a replacement endpoint after an uncertain response.

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

1. Restore owner browser access by resolving the cloud-browser /auth/callback URL-policy restriction. Existing Google approval remains valid.
2. Verify the deployed runtime SHA, restricted signup, sandbox=true, Advanced=false and MPP=false. Validate the configured test Price before creating any quote.
3. Create one subscription quote in the owner workspace. Capture quote ID, amount, currency, expiry and auto-renewal terms. Start Checkout from that quote and retain its single Session ID. New quotes persist their integration identifier; repeated requests and reconstructed Workers must use identical parameters and idempotency keys.
4. Complete the hosted Checkout using [Stripe's test details](https://docs.stripe.com/testing). Observe the signed webhook and server reconciliation. Record Session, subscription, invoice, PaymentIntent/charge and workspace mapping with livemode=false. A success URL or session creation alone grants nothing.
5. Retry the same quote and redeliver the same event. Confirm one settlement and no second Checkout purchase. Confirm the entitlement ends at the verified paid period and does not activate Advanced automation.
6. Issue a sandbox refund of the captured test charge through the authorised Stripe integration. Observe the signed refund event and entitlement revocation without relying on a manual status read to repair the state. Cancel the captured subscription.
7. Exercise a separate dispute using Stripe's documented dispute test method and an explicitly identified test purchase. Confirm the charge-only event maps to the workspace, is completed after reconciliation, and revokes coverage. Record the separate test objects; do not reuse or describe a refunded charge as the dispute test.
8. Verify Billing Portal cancellation and the subscription state. Cancel all subscriptions created by this rehearsal, expire abandoned open Checkout Sessions where applicable, then disable sandbox Checkout and redeploy unless another test has been authorised. Retain the dedicated Product/Price for a later rerun.

Record actual observations and exact IDs in an appropriate private acceptance record. Public receipts may state outcomes without account/customer/financial identifiers. Tests with Workers/D1 and mocked Stripe responses remain engineering evidence. MPP requires separate merchant/wallet eligibility and remains outside this Checkout run.
