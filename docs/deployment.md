# Deploy PostSteward to Cloudflare

PostSteward is hosted centrally on the service operator’s Cloudflare account. Customers use the hosted service. Run these instructions from [AyobamiH/poststeward](https://github.com/AyobamiH/poststeward). Deployment has not yet been performed.

## Provision staging

1. Check out the reviewed PostSteward revision from `AyobamiH/poststeward`. Record the commit SHA for the deployment.
2. Create a Cloudflare D1 database named `poststeward-identity-staging`; record its actual ID in a staging copy of `wrangler.jsonc`. Use a different Worker name and D1 database for production. Durable Objects are scoped to the separate Worker.
3. Set `PUBLIC_ORIGIN` to the real HTTPS origin. Register `/auth/callback` with an OIDC provider supporting authorization code, PKCE, nonce, `openid profile` and confidential client authentication.
4. Set Worker secrets using `wrangler secret put`: `ENCRYPTION_KEY` (base64-encoded random 32 bytes), `OIDC_CLIENT_SECRET`. Configure `OIDC_ISSUER` and `OIDC_CLIENT_ID`. Keep every credential out of source control.
5. Apply `migrations/0001_identity.sql` with `wrangler d1 migrations apply poststeward-identity-staging --remote --config <staging-config>`.
6. Run `npm ci`, `npm run verify`, then `wrangler deploy --config <staging-config>`. Record the returned deployment URL and version. Check `/health` and `/help.json` at that exact URL, then complete a new owner sign-in and two isolated customer workspaces.

The default database ID and origin are placeholders. `npm run deploy:check` rejects them before `npm run deploy`. Default Advanced and MPP flags are false. Do not advertise or charge for deployment-disabled functionality.

## Stripe sandbox

Configure secrets `STRIPE_SECRET_KEY`, `STRIPE_WEBHOOK_SECRET` and `MPP_SECRET`; configure `STRIPE_PRICE_ID` and `STRIPE_PROFILE_ID` for the same environment. The Price must be active USD 5, monthly, quantity one. The fixed checkout total is USD 5; this first version does not add automatic checkout taxes. Merchant tax configuration must be reviewed before live billing.

Register `/webhooks/stripe` for checkout, subscription, invoice, payment-intent, charge-refund and dispute changes. The service verifies signatures over raw bytes, stores events, and reconciles current Stripe objects. It never trusts a success redirect. Initial reconciliation of a lost checkout response scans a bounded recent session list; high-volume recovery/pagination remains a release validation task.

Before enabling Advanced, complete the outstanding automation work and sandbox acceptance. Then set `ADVANCED_ENABLED=true` in staging. Before enabling MPP, verify merchant SPT availability and an eligible agent wallet; set `MPP_ENABLED=true` only in that sandbox. Test challenge binding, invalid credentials, settlement, response loss, replay, refund/dispute and exact non-renewing month coverage. The installed SDK uses `Payment-Authorization` for the payment credential so `Authorization` remains the agent's identity token.

The endpoint wraps Stripe PaymentIntent creation with a stable purchase-level idempotency key and persists the attempt. A payment timeout requires reconciliation; it never authorizes another charge. A failed MPP verification must not be treated as proof that no financial request happened.

No live payment test has been performed. A live test needs an exact authorised purchase and an eligible wallet. Do not run live MPP validation casually: it can spend real funds.

## Release

Use separate production bindings, secrets, webhook signing key and Price. Set a real release SHA. Check actual provider permissions with a fresh user, native WebMCP in a supported browser and both HTTP/MCP clients. Record provider receipt URLs and payment receipts separately. Use the runbook to rehearse pause and restore. Publish calibrated limits and retention/deletion policy before opening public signup.

The included manual GitHub workflow verifies and bundles. Configure a protected `staging` or `production` GitHub environment and the listed variables/secrets before triggering deployment. This repository does not automatically deploy on every merge.
