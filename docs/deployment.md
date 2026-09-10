# Deploy PostSteward to Cloudflare

PostSteward runs centrally on the service operator's Cloudflare account. Customers connect to the hosted product. All repository work belongs in [AyobamiH/poststeward](https://github.com/AyobamiH/poststeward).

**Restricted staging is deployed.** The [9 September deployment receipt](staging-deployment-2026-09-09.md) records the successful historical run, exact runtime revision, Cloudflare version and the 17 hosted checks that existed at that deployment. D1 access and required owner/encryption settings are configured. The current private-GitHub-source candidate expands the non-destructive hosted verifier to 25 surfaces but is not live evidence until its reviewed main revision is deployed. Do not recreate existing staging resources, regenerate its encryption key or repeat credential entry. The setup procedure below remains relevant for a new environment and eventual production. Do not paste secrets into chat, issues, source files or workflow inputs.

## 1. Create isolated resources

Open [Cloudflare Workers & Pages](https://dash.cloudflare.com/?to=/:account/workers-and-pages) and record the account ID and your account's workers.dev subdomain. The subdomain variable is the bare account label, without `.workers.dev`. Deployment reads the account's actual subdomain from Cloudflare and uses that verified value, so a stale saved subdomain cannot misroute the Worker or OAuth callback.

Open [Cloudflare D1](https://dash.cloudflare.com/?to=/:account/workers/d1). For a new setup, create `poststeward-identity-staging` first and record its database UUID. The current staging database already exists. Create `poststeward-identity-production` separately when preparing production. Keep read replication disabled for identity data. The deployment checks each database's actual name and UUID through Cloudflare before migrating it.

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

## 3A. Configure optional private GitHub sources

Public GitHub source profiles require no GitHub App. To allow an owner to monitor a private repository, create a dedicated GitHub App for that PostSteward environment and keep its permissions minimal. The application must support GitHub App user OAuth with expiring/refreshable user credentials because private reads are bound to the owner's continuing GitHub access rather than to a broad long-lived installation token.

Configure the app with:

- **Repository access at installation:** selected repositories only. PostSteward rejects `all`-repository installations.
- **Repository permissions:** Contents = Read-only. Metadata remains Read-only. Do not grant write access or other active repository permissions.
- **Setup URL:** the exact PostSteward origin plus `/sources/github/setup`.
- **User authorisation callback URL:** the exact PostSteward origin plus `/sources/github/callback`.

For the current staging workers.dev origin those URLs are:

- `https://poststeward-staging.woeinvests.workers.dev/sources/github/setup`
- `https://poststeward-staging.woeinvests.workers.dev/sources/github/callback`

If `APP_ORIGIN` later changes, change both GitHub App URLs before enabling private-source acceptance on the new origin. Do not use a wildcard callback, repository write permission or an installation covering every repository.

Record the app's client ID and slug as environment variables and its client secret as an environment secret. PostSteward accepts these three values only as an all-or-nothing set. A partial/invalid GitHub App configuration fails deployment before Cloudflare mutation. The client secret is mapped only into the deploy step, never into CI verification.

The owner connection remains a separate post-deployment action. Deployment and hosted smoke never install the GitHub App, create an owner GitHub credential or read a private repository. See [private GitHub source authority](private-github-sources.md).

## 4. Enter GitHub environment settings

Open [repository environments](https://github.com/AyobamiH/poststeward/settings/environments). Create **staging** for a new setup. Restrict deployment branches to **main**. Repeat separately for **production**, with its own resources and credentials. Use required-reviewer protection where available on the repository's GitHub plan. Protect main through [repository rules](https://github.com/AyobamiH/poststeward/settings/rules), requiring the Verify check and reviewed changes to deployment code. Prefer environment-scoped deployment secrets over repository-wide secrets that unreviewed branch workflows could request.

Environment **variables**:

| Name | Value |
| --- | --- |
| `CLOUDFLARE_ACCOUNT_ID` | Your 32-character Cloudflare account ID |
| `D1_ID` | The UUID of this environment's dedicated database |
| `WORKERS_SUBDOMAIN` | Your bare workers.dev account subdomain |
| `OIDC_ISSUER` | Identity issuer URL; Google: `https://accounts.google.com` |
| `OIDC_CLIENT_ID` | This environment's OAuth client ID |
| `APP_ORIGIN` | Optional custom HTTPS origin; leave absent for the generated workers.dev origin |
| `GITHUB_APP_CLIENT_ID` | Optional private-source GitHub App client ID; configure with slug + secret |
| `GITHUB_APP_SLUG` | Optional private-source GitHub App slug; configure with client ID + secret |

Environment **secrets**:

| Name | Value |
| --- | --- |
| `CLOUDFLARE_API_TOKEN` | This environment's scoped deployment token |
| `ENCRYPTION_KEY` | Random 32-byte base64 key, generated once and backed up securely |
| `OIDC_CLIENT_SECRET` | This environment's OAuth client secret |
| `ALLOWED_OWNER_EMAILS` | Comma-separated invited owner email addresses |
| `GITHUB_APP_CLIENT_SECRET` | Optional private-source GitHub App client secret; configure with client ID + slug |

Generate each encryption key once in a trusted local terminal with `openssl rand -base64 32`, store it in your password manager and the correct environment, then clear its terminal display. Never regenerate it on each deployment: previously stored provider and private-source credentials would become unreadable. Key rotation requires versioned re-encryption and a restoration rehearsal. The deploy workflow deliberately has no auto-generate/overwrite key step.

No Stripe key or social provider token is needed for the initial infrastructure deployment. Private GitHub App settings are also optional unless private-source acceptance is being enabled. Owners establish GitHub and provider user authority through their authenticated workspace after deployment; user access/refresh credentials never go into GitHub environment variables.

## 5. Deploy and verify

1. Merge the reviewed implementation/configuration into main. The manual workflow must be present on the default branch before it can be dispatched.
2. Deployment can be requested through [Deploy reviewed configuration](https://github.com/AyobamiH/poststeward/actions/workflows/deploy.yml), choosing main and the configured environment. Alternatively, an intentional change to `.github/workflows/deploy-staging-request.yml`, merged to main by `AyobamiH`, invokes the same-commit deployment with environment fixed to staging. See [explicit staging requests](staging-deployment-request.md). Ordinary source/documentation merges do not deploy, and production remains manual.
3. Verification runs in a separate job with no deployment secrets mapped into its process environment. It runs the production-dependency audit and full verification suite. The deploy job installs the lockfile with lifecycle scripts disabled and no shared build cache. It validates configuration and secrets, checks the database identity, applies additive migrations, and deploys the exact workflow SHA. Application secrets are scoped to that single deployment step. The Cloudflare token is also supplied to the preceding read-only subdomain discovery step; it is never uploaded to the Worker. The reusable call forwards only explicitly named repository-level secret fallbacks; selected environment secrets take precedence. It does not inherit every repository secret or require Actions write permission.
4. The current candidate smoke check waits a bounded number of read-only attempts for the exact deployed revision and checks 25 hosted surfaces: health/catalogue/readiness, disabled payments, landing/workspace HTML without redirects, public assets/discovery including the private-source browser module, security headers, D1-backed forged-token denial, unauthenticated/cross-origin rejection, all five GitHub source authority routes denied without a session, invalid OIDC callback state and Google login initiation with PKCE/nonce/secure cookie. It never follows redirects or repeats a deployment, GitHub installation, private-repository read, social write or charge. The report records fixed check names and status/boolean results, not OAuth state, cookies or secret values. The historical 9 September receipt records the 17-surface verifier that existed at that deployed revision.
5. Complete sign-in as an invited owner. Verify an uninvited identity is rejected, two owner workspaces are isolated, grant revocation works and a controlled approved publication returns its provider receipt. Exercise supported-browser WebMCP and an HTTP/MCP client against the same hosted origin. If private GitHub sources are configured, explicitly install the app for a small selected repository set, confirm the workspace exposes only those repositories, prove a harmless private path source read and then exercise access removal/fail-closed behaviour.
6. Configure platform error/usage alerts, validate restore and key handling, complete remaining release work in `implementation-status.md`, then deploy the accepted main revision separately to production. Public signup and purchases require a reviewed configuration release; they remain disabled in this initial deployment workflow.

The checked-in config contains a placeholder origin/database for dry-run verification. `npm run deploy:check` rejects it. Real deployment must use `scripts/configure-deploy.mjs` and the protected GitHub workflow; running `wrangler deploy` directly bypasses the repository's preflight controls.

## Stripe sandbox

Configure secrets `STRIPE_SECRET_KEY`, `STRIPE_WEBHOOK_SECRET` and `MPP_SECRET`; configure `STRIPE_PRICE_ID` and `STRIPE_PROFILE_ID` for the same environment. The Price must be active USD 5, monthly, quantity one. The fixed checkout total is USD 5; this first version does not add automatic checkout taxes. Merchant tax configuration must be reviewed before live billing.

Register `/webhooks/stripe` for checkout, subscription, invoice, payment-intent, charge-refund and dispute changes. The service verifies signatures over raw bytes, stores events, and reconciles current Stripe objects. It never trusts a success redirect. Initial reconciliation of a lost checkout response scans a bounded recent session list; high-volume recovery/pagination remains a release validation task.

Before enabling Advanced, complete the outstanding automation work and sandbox acceptance. Then set `ADVANCED_ENABLED=true` in staging. Before enabling MPP, verify merchant SPT availability and an eligible agent wallet; set `MPP_ENABLED=true` only in that sandbox. Test challenge binding, invalid credentials, settlement, response loss, replay, refund/dispute and exact non-renewing month coverage. The installed SDK uses `Payment-Authorization` for the payment credential so `Authorization` remains the agent's identity token.

The endpoint wraps Stripe PaymentIntent creation with a stable purchase-level idempotency key and persists the attempt. A payment timeout requires reconciliation; it never authorizes another charge. A failed MPP verification must not be treated as proof that no financial request happened.

No live payment test has been performed. A live test needs an exact authorised purchase and an eligible wallet. Do not run live MPP validation casually: it can spend real funds.

## Release

Use separate production bindings, secrets, webhook signing key, GitHub App and Price. Set a real release SHA. Check actual provider and private-repository permissions with a fresh user, native WebMCP in a supported browser and both HTTP/MCP clients. Record provider receipt URLs and payment receipts separately. Use the runbook to rehearse pause and restore. Publish calibrated limits and retention/deletion policy before opening public signup.

The current deployment preflight intentionally rejects enabling Advanced/MPP. Enabling billing requires a reviewed release that extends the secret manifest and deployment workflow, after the acceptance above. This repository does not automatically deploy on every merge.

## Inspect an existing staging setup

`Inspect staging setup` runs on main when its own workflow/script changes, or on manual dispatch. It uses the saved staging token for read-only Cloudflare API requests. Its summary contains presence checks for secrets and validated account/database/subdomain metadata, never secret values. It resolves a missing account ID only if the token returns exactly one account and finds only the exact `poststeward-identity-staging` database. The inspection neither deploys nor changes Cloudflare resources. This avoids asking an owner to copy configuration that the deployment token can already discover.

If the deployment token is unavailable, inspection still validates the syntax of saved nonsecret values, derives the proposed origin and OAuth callback, and reports missing application settings. It checks only presence booleans for a misplaced `CLOUDFLARE_API_TOKEN` variable or `CF_API_TOKEN` secret; it never reads their values or substitutes them into deployment. A proposed origin is not evidence that a Worker is live.

D1 diagnostics distinguish the saved database lookup from the account database list. If both return HTTP 401/403, inspect the token's D1 permission and account restriction rather than recreating the database. Error reports include numeric API codes only, never arbitrary upstream message bodies.
