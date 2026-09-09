# Explicit staging deployment requests

The owner authorised completion of the first PostSteward staging deployment on 9 September 2026. Setup inspection job 102593136947 confirmed all required settings were present and D1 identity access succeeded. This is readiness evidence, not a deployment or Google sign-in receipt.

An intentional change to `.github/workflows/deploy-staging-request.yml`, merged to `main` by `AyobamiH`, requests a staging deployment of that exact merge revision. Change its request identifier only when deliberately requesting another staging deployment. Ordinary application or documentation merges do not deploy.

The request calls the existing `deploy.yml` at the same commit. It does not dispatch another workflow, add a personal access token, use `secrets: inherit`, or widen the Actions token beyond `contents: read`. Only the four already-used secret names are forwarded as optional repository-level fallbacks. Secrets in the selected staging environment take precedence. Secrets are exposed as process environment variables only to the existing configuration/deployment steps, not to dependency installation, audit, tests or smoke checks. Existing environment protections remain in force.

The initial run 34390087653 stopped before any Cloudflare mutation because the saved deployment token was repository-scoped and had not been forwarded across the reusable-workflow boundary. Explicitly forwarding the four named fallbacks restores the same credential resolution available to the original manual workflow; no credential value is read, logged, copied into source, or regenerated. CI and deployment verification also gate on the production-dependency audit at high severity.

The reusable workflow checks the repository, main ref and environment. Production remains limited to a manual workflow-dispatch event. The request fixes its environment to staging. Cloudflare account/database identity checks, additive migrations, secret validation, exact release-SHA verification, invite-only owner access and disabled billing are unchanged.

A successful deployment requires both the deployment step and the live smoke check to pass. Record the resulting run, revision, Cloudflare version and live checks in the implementation status. Owner Google sign-in and real social publication are separate acceptance evidence and must not be inferred from deployment success.
