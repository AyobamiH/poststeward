# Deploy PostSteward to Cloudflare

PostSteward runs centrally on the service operator’s Cloudflare account. Customers connect to the hosted product. All repository work belongs in [AyobamiH/poststeward](https://github.com/AyobamiH/poststeward).

The configuration and workflow are implemented. Cloudflare resources, credentials and real deployment have not yet been verified. Do not paste secrets into chat, issues, source files or workflow inputs.

## 1. Create isolated resources

Open [Cloudflare Workers & Pages](https://dash.cloudflare.com/?to=/:account/workers-and-pages) and record the account ID and your account's workers.dev subdomain. The subdomain variable is the bare account label, without `.workers.dev`.

Open [Cloudflare D1](https://dash.cloudflare.com/?to=/:account/workers/d1). Create `poststeward-identity-staging` first; record its database UUID. Create `poststeward-identity-production` separately when preparing production. Keep read replication disabled for identity data. The deployment checks each database's actual name and UUID through Cloudflare before migrating it.

| Environment | Worker | D1 database | Default origin |
| --- | --- | --- | --- |
| staging | `poststeward-staging` | `poststeward-identity-staging` | `https://poststeward-staging.YOUR_SUBDOMAIN.workers.dev` |
| production | `poststeward` | `poststeward-identity-production` | `https://poststeward.YOUR_SUBDOMAIN.workers.dev` |

Durable Objects and secrets are created with each Worker deployment. Do not deploy a temporary public Worker merely to add secrets first. The workflow uploads code and required secrets together.

A custom domain can be supplied using `APP_ORIGIN`, for example `https://publish.your-domain.com`. It must be an exact HTTPS origin without a trailing slash. The configuration creates a Worker custom-domain route and disables workers.dev; the domain must belong to the intended Cloudflare zone. Preview URLs are always disabled. Use a custom domain and reviewed zone WAF policies before unrestricted signup.

## 2. Create a scoped deployment token

Open [Cloudflare API tokens](https://dash.cloudflare.com/profile/api-tokens). Create a custom token named `poststeward-staging-deploy`, limited to the selected Cloudflare account:

- Account / Workers Scripts / Edit.
- Account / D1 / Edit.
- Account / Account Settings / Read for Wrangler's account/subdomain discovery.

For custom-domain routing, include Zone / Workers Routes / Edit and Zone / Zone / Read, limited to the selected domain's zone. Do not grant DNS edit, billing, API-token administration, R2, KV or access to every account. Use an expiry and a separate production token. If Cloudflare's displayed permission names differ, use the corresponding Workers Scripts and D1 write permissions from its current [permission catalogue](https://developers.cloudflare.com/fundamentals/api/reference/permissions/).

These permissions are account-scoped, not restricted to one Worker/database. Separate resource names prevent accidental cross-environment wiring; they do not prevent a compromised deployment token from accessing other permitted resources in the same account.

## 3. Configure owner sign-in

Use an OIDC provider supporting code flow, PKCE S256, confidential-client basic authentication, signed ID tokens and `openid profile email`. Use separate web OAuth clients for staging and production.

For Google, open [Google Auth Platform clients](https://console.cloud.google.com/auth/clients), configure the application's branding/audience, and create a **Web application** OAuth client. The issuer is `https://accounts.google.com`. Register the exact chosen origin plus `/auth/callback` as its authorised redirect URI. For example, staging on workers.dev uses `https://poststeward-staging.YOUR_SUBDOMAIN.workers.dev/auth/callback`. Add the invited owner as a test user while the OAuth application is in testing. Configure the production consent screen and domain requirements before public use. Follow [Google's OIDC documentation](https://developers.google.com/identity/openid-connect/openid-connect).

Store the client ID as a GitHub environment variable and the client secret as an environment secret. `ALLOWED_OWNER_EMAILS` is a comma-separated list of invited owners; their ID tokens must contain `email_verified: true`. Initial deployment rejects all other owners. This control does not paywall agent documentation or Free publishing.

## 4. Enter GitHub environment settings

Open [repository environments](https://github.com/AyobamiH/poststeward/settings/environments). Create **staging**. Restrict deployment branches to **main**. Repeat separately for **production**, with its own resources and credentials. Use required-reviewer protection where available on the repository's GitHub plan. Protect main through [repository rules](https://github.com/AyobamiH/poststeward/settings/rules), requiring the Verify check and reviewed changes to deployment code. Avoid repository-wide deployment secrets that unreviewed branch workflows could request.

Environment **variables**:

| Name | Value |
| --- | --- |
| `CLOUDFLARE_ACCOUNT_ID` | Your 32-character Cloudflare account ID |
| `D1_ID` | The UUID of this environment's dedicated database |
| `WORKERS_SUBDOMAIN` | Your bare workers.dev account subdomain |
| `OIDC_ISSUER` | Identity issuer URL; Google: `https://accounts.google.com` |
| `OIDC_CLIENT_ID` | This environment's OAuth client ID |
| `APP_ORIGIN` | Optional custom HTTPS origin; leave absent for the generated workers.dev origin |

Environment **secrets**:

| Name | Value |
| --- | --- |
| `CLOUDFLARE_API_TOKEN` | This environment's scoped deployment token |
| `ENCRYPTION_KEY` | Random 32-byte base64 key, generated once and backed up securely |
| `OIDC_CLIENT_SECRET` | This environment's OAuth client secret |
| `ALLOWED_OWNER_EMAILS` | Comma-separated invited owner email addresses |

Generate each encryption key once in a trusted local terminal with `openssl rand -base64 32`, store it in your password manager and the correct environment, then clear its terminal display. Never regenerate it on each deployment: previously stored provider tokens would become unreadable. Key rotation requires versioned re-encryption and a restoration rehearsal. The deploy workflow deliberately has no auto-generate/overwrite key step.

No Stripe key or social provider token is needed for the initial infrastructure deployment. Owners import provider credentials through their authenticated workspace after deployment; the credentials never go into GitHub variables.

## 5. Deploy and verify

1. Merge the reviewed implementation/configuration into main. The manual workflow must be present on the default branch before it can be dispatched.
2. Open [Deploy reviewed configuration](https://github.com/AyobamiH/poststeward/actions/workflows/deploy.yml). Choose main and the **staging** environment, then run it.
3. Verification runs in a separate job with no deployment credentials. The deploy job installs the lockfile with lifecycle scripts disabled and no shared build cache. It validates configuration and secrets, checks the database identity, applies additive migrations, and deploys the exact workflow SHA. Credentials are scoped to that single deployment step; the Cloudflare token is never uploaded to the Worker.
4. The smoke check verifies the deployed revision, help catalogue, disabled payments, HSTS and unauthenticated/cross-origin rejection. Read the workflow summary for the actual origin and revision. It does not prove customer sign-in or a successful publication.
5. Sign in as an invited owner. Verify an uninvited identity is rejected, two owner workspaces are isolated, grant revocation works and a controlled approved publication returns its provider receipt. Exercise supported-browser WebMCP and an HTTP/MCP client against the same hosted origin.
6. Configure platform error/usage alerts, validate restore and key handling, complete remaining release work in `implementation-status.md`, then deploy the accepted main revision separately to production. Public signup and purchases require a reviewed configuration release; they remain disabled in this initial deployment workflow.

The checked-in config contains a placeholder origin/database for dry-run verification. `npm run deploy:check` rejects it. Real deployment must use `scripts/configure-deploy.mjs` and the protected GitHub workflow; running `wrangler deploy` directly bypasses the repository's preflight controls.

## Stripe sandbox

Configure secrets `STRIPE_SECRET_KEY`, `STRIPE_WEBHOOK_SECRET` and `MPP_SECRET`; configure `STRIPE_PRICE_ID` and `STRIPE_PROFILE_ID` for the same environment. The Price must be active USD 5, monthly, quantity one. The fixed checkout total is USD 5; this first version does not add automatic checkout taxes. Merchant tax configuration must be reviewed before live billing.

Register `/webhooks/stripe` for checkout, subscription, invoice, payment-intent, charge-refund and dispute changes. The service verifies signatures over raw bytes, stores events, and reconciles current Stripe objects. It never trusts a success redirect. Initial reconciliation of a lost checkout response scans a bounded recent session list; high-volume recovery/pagination remains a release validation task.

Before enabling Advanced, complete the outstanding automation work and sandbox acceptance. Then set `ADVANCED_ENABLED=true` in staging. Before enabling MPP, verify merchant SPT availability and an eligible agent wallet; set `MPP_ENABLED=true` only in that sandbox. Test challenge binding, invalid credentials, settlement, response loss, replay, refund/dispute and exact non-renewing month coverage. The installed SDK uses `Payment-Authorization` for the payment credential so `Authorization` remains the agent's identity token.

The endpoint wraps Stripe PaymentIntent creation with a stable purchase-level idempotency key and persists the attempt. A payment timeout requires reconciliation; it never authorizes another charge. A failed MPP verification must not be treated as proof that no financial request happened.

No live payment test has been performed. A live test needs an exact authorised purchase and an eligible wallet. Do not run live MPP validation casually: it can spend real funds.

## Release

Use separate production bindings, secrets, webhook signing key and Price. Set a real release SHA. Check actual provider permissions with a fresh user, native WebMCP in a supported browser and both HTTP/MCP clients. Record provider receipt URLs and payment receipts separately. Use the runbook to rehearse pause and restore. Publish calibrated limits and retention/deletion policy before opening public signup.

The current initial-deployment preflight intentionally rejects enabling Advanced/MPP. Enabling billing requires a reviewed release that extends the secret manifest and deployment workflow, after the acceptance above. This repository does not automatically deploy on every merge.
