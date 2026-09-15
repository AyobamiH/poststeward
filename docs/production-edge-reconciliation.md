# Production edge reconciliation

PostSteward keeps production-edge mutation separate from deployment and from acceptance evidence.

The reviewed helper `scripts/production-edge-apply.mjs` is intentionally narrow:

- it requires an exact custom HTTPS origin and the exact Cloudflare zone containing that hostname;
- it refuses `workers.dev` and hostnames outside the confirmed zone;
- it resolves exactly one active zone before mutation;
- it manages only two named, host-scoped Ruleset Engine rules;
- it refuses ambiguous same-description/expression candidates rather than creating duplicates;
- it is a dry run unless `POSTSTEWARD_APPLY_PRODUCTION_EDGE=APPLY_POSTSTEWARD_PRODUCTION_EDGE` is supplied;
- after a mutation, it re-reads the phase entry points and requires the exact canonical rule definitions.

The canonical rules are deliberately conservative:

1. `poststeward_block_unsupported_methods_v1` blocks `TRACE` and `TRACK` only for the PostSteward production hostname.
2. `poststeward_auth_rate_limit_v1` rate-limits only `/auth/` on the production hostname to 30 requests per minute per Cloudflare colo/IP pair, with a 60-second mitigation timeout. The application’s own login limiter remains stricter and independent.

The Worker custom domain is not created by this helper. The reviewed Worker deployment already treats `APP_ORIGIN` as a custom domain and Wrangler reconciles that binding during the production deployment. Keeping Worker routing and zone security in separate controllers makes each change independently reviewable and reversible.

The manual GitHub workflow `.github/workflows/production-edge-reconcile.yml` is owner-only, `main`-only and uses the protected production Cloudflare token. The exact origin and zone remain explicit workflow inputs; the workflow does not guess a domain from repository history or another product.

A successful reconciliation is still not production-edge acceptance. After the production Worker is deployed at the exact custom origin, run `npm run edge:check` with read-authorised Cloudflare credentials. The gate closes only when the hosted release, TLS/security headers, proxied DNS, WAF and rate-limit readback all pass on the intended production origin.
