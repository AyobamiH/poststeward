# Production bootstrap with temporarily shared staging credentials

The production runtime is intentionally separate from the public showcase:

- public showcase: `https://poststeward.com`
- production application/runtime: `https://app.poststeward.com`
- production Worker: `poststeward`
- production D1: `poststeward-identity-production`

## Temporary bootstrap decision

Production may temporarily reuse the **same uncompromised credential values** already used by staging for integrations where minting and re-scoping a second token would delay the controlled launch.

This is explicit technical debt, not evidence of environment-level credential isolation.

Set the production GitHub environment variable:

```
PRODUCTION_CREDENTIAL_MODE=shared_staging_bootstrap
```

After the shared credentials are rotated to production-specific values, change it to:

```
PRODUCTION_CREDENTIAL_MODE=isolated
```

## What stays separate immediately

These values must be production-specific from the first deployment:

- `D1_ID` for the dedicated `poststeward-identity-production` database;
- `APP_ORIGIN=https://app.poststeward.com`;
- `OIDC_CLIENT_ID` and `OIDC_CLIENT_SECRET` for the production Google/OIDC client.

The production Google redirect URI is:

```
https://app.poststeward.com/auth/callback
```

## What may be copied from staging during bootstrap

The existing values may be copied into the **production GitHub environment** without creating new credentials:

- `CLOUDFLARE_ACCOUNT_ID`
- `CLOUDFLARE_API_TOKEN`
- `ENCRYPTION_KEY` and, if retained only for reads, `ENCRYPTION_KEY_NEXT`
- `ALLOWED_OWNER_EMAILS`
- private GitHub App client ID / slug / client secret
- provider OAuth client IDs / client secrets that are already configured
- operational alert webhook credentials if/when staging has a real configured destination

Do not enable Stripe sandbox, Advanced, MPP or public signup in production merely because their staging credentials exist.

## Callback consequence of shared provider applications

Reusing a provider application's client credentials does **not** make its staging callback valid for production. The provider application must also allow the exact production callback before that provider can be accepted on production.

For the private GitHub App, add the production user-authorisation callback alongside staging rather than replacing staging:

```
https://app.poststeward.com/sources/github/callback
```

The setup URL is environment-sensitive. Do not switch it away from the staging setup URL until the production private-source journey is deliberately being accepted; a separate GitHub App remains the cleaner long-term state.

Provider callbacks follow the same rule:

```
https://app.poststeward.com/connections/oauth/<provider>/callback
```

## Safety boundary during bootstrap

The production preflight still requires:

- restricted signup;
- Advanced disabled;
- zero Advanced canary allocation;
- MPP disabled;
- Stripe sandbox disabled;
- legacy encryption-root writer mode;
- the dedicated production D1 identity;
- the exact production application origin;
- a full reviewed release SHA.

Credential reuse never overrides those controls.

## Rotation debt

Before claiming isolated production credentials, rotate the shared values deliberately. Do not rotate encryption roots casually after customer ciphertext exists; use the existing root-rotation/recovery process. Provider/deployment credentials can be replaced independently and then the bootstrap mode can be retired.
