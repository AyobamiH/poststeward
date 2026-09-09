# Restricted staging deployment receipt: 9 September 2026

**Outcome: deployed successfully; all 71 automated tests and all 17 hosted checks passed.** This receipt establishes the restricted staging deployment, not public launch, completed customer onboarding or payment readiness.

## Exact deployment identity

| Field | Value |
| --- | --- |
| Repository | `AyobamiH/poststeward` |
| Runtime revision | `d4fe795cea5e7216692f4d433ce807782dc31292` |
| Worker | `poststeward-staging` |
| Origin | `https://poststeward-staging.woeinvests.workers.dev` |
| Workspace page | `https://poststeward-staging.woeinvests.workers.dev/app` |
| D1 database | `poststeward-identity-staging` |
| D1 UUID | `d68d73f6-a7b3-4530-82e7-28c2028c050a` |
| Cloudflare version | `49a1c0bf-21fd-4a36-9146-53247df65c24` |
| Deployment run | [34391992401](https://github.com/AyobamiH/poststeward/actions/runs/34391992401) |
| Verification job | [102602294236](https://github.com/AyobamiH/poststeward/actions/runs/34391992401/job/102602294236) |
| Deployment and hosted-check job | [102602526908](https://github.com/AyobamiH/poststeward/actions/runs/34391992401/job/102602526908) |
| Live observation time | `2026-09-09T18:57:13.505Z` |
| Exact-revision readiness | Passed on attempt 2 |

Both jobs completed successfully. The runtime SHA is the exact value checked by `/health` and `/help.json`. Documentation-only commits following this receipt do not change the deployed runtime: only a subsequent explicit deployment request does so.

The Worker has the dedicated D1 identity binding, SQLite Durable Object binding, Assets binding, edge/login rate bindings and the `17 * * * *` expiry-cleanup trigger. The deployment validated database identity and required secrets before migrating or uploading. Both `0001_identity.sql` and `0002_security_indexes.sql` were applied in [run 34390648455](https://github.com/AyobamiH/poststeward/actions/runs/34390648455); that earlier run failed its immediate hosted check, not its migration/upload. The accepted final run found no pending migrations. Existing resources and the stored encryption key were retained.

## Automated verification

The verification job ran a clean dependency installation, the production-dependency audit, TypeScript checking, generated-document drift checks, Wrangler bundling and the complete test suite. It reported **71 tests passed, zero failed, zero skipped and zero cancelled**. The independent [main CI run 34391992198](https://github.com/AyobamiH/poststeward/actions/runs/34391992198) also passed.

The production-dependency audit reported zero known vulnerabilities. The full install still reported three high-severity advisories with development/tooling dependencies included. Those findings are not fixed or characterised by this receipt and must be triaged before production approval. Do not represent the whole dependency graph as clean.

Workers/D1/Durable Object/Assets runtime tests execute the actual runtime locally in CI. Their social-provider and Stripe responses are simulated. They do not establish a real social publication or settled payment.

## Hosted acceptance: 17 of 17 passed

These results came from real HTTPS requests in the deployment job, after the exact revision became ready. The checker did not follow redirects and did not retry a deployment, publication or payment.

| Check | HTTP status | Result |
| --- | --- | --- |
| `/health`: exact release, healthy status, Advanced disabled, security headers | 200 | Passed |
| `/help.json`: exact release, 26 operations, payments disabled, security headers | 200 | Passed |
| `/`: PostSteward HTML without redirect | 200 | Passed |
| `/app`: PostSteward HTML without redirect | 200 | Passed |
| `/style.css`: available with security headers | 200 | Passed |
| `/app.js`: available with security headers | 200 | Passed |
| `/webmcp.js`: available with security headers | 200 | Passed |
| `/docs/agent-guide.md`: available with security headers | 200 | Passed |
| `/llms.txt`: available with security headers | 200 | Passed |
| `/openapi.json`: available with security headers | 200 | Passed |
| `/plans.json`: available with security headers | 200 | Passed |
| Unauthenticated `/api/session`: rejected and no-store | 401 | Passed |
| Forged Bearer token through deployed D1: `UNAUTHENTICATED` | 401 | Passed |
| Cross-origin `/api/session`: rejected | 403 | Passed |
| Unauthenticated MCP request: rejected | 401 | Passed |
| Callback without login state: `LOGIN_STATE_INVALID` | 400 | Passed |
| Google login initiation: matching client/callback, code flow, PKCE S256, state, nonce and secure cookie | 302 | Passed |

Google initiation confirmed the authorization request's shape and the secure login-state cookie. The hosted request exercised issuer discovery and D1 login-state storage. It did not complete consent or the authorization-code exchange. The checker records fixed check names, status codes and booleans, never OAuth state, nonce, cookie values or secret values.

## Changes that made the deployment complete

[PR #3](https://github.com/AyobamiH/poststeward/pull/3) introduced an explicit, owner-authored staging request that reuses the same-commit deployment workflow. It avoided the manual dashboard handoff without granting Actions write permission or deploying ordinary application/documentation merges. Production remains manual.

[PR #4](https://github.com/AyobamiH/poststeward/pull/4) corrected the reusable-workflow secret boundary by forwarding only the four existing named fallbacks. The initial request had stopped before mutation because its deployment token was empty. No secret was re-entered, exposed or regenerated. It also added the production-dependency audit gate.

[PR #5](https://github.com/AyobamiH/poststeward/pull/5) introduced bounded, exact-revision readiness checks and the 17 hosted checks above. This distinguished initial edge propagation from an actual application defect. [Run 34391322840](https://github.com/AyobamiH/poststeward/actions/runs/34391322840) passed 16 checks but correctly failed `/app`, which returned HTTP 307.

[PR #6](https://github.com/AyobamiH/poststeward/pull/6) removed the redundant `/app` to `/app.html` rewrite. Cloudflare's canonical asset routing had redirected that internal fetch back to `/app`, causing a loop. The fix passes the original request to Assets. A regression uses the real Workers Assets implementation and repository HTML, including query strings and canonical redirect resolution. The accepted deployment now returns HTTP 200 for `/app`.

All four PRs were merged after successful CI using the owner's GitHub identity. No runtime authentication, invitation or billing boundary was relaxed to make the checks pass.

## Effect and acceptance boundaries

Signup remains restricted to verified invited owners. `ADVANCED_ENABLED=false` and `MPP_ENABLED=false` were deployed. Free publishing is not globally paused, but still requires authenticated workspace authority, an account connection and approved content. The acceptance run did not connect a social account, publish a social post, create a real customer grant, charge money or deploy production. The login-initiation check created an expiring login-state record, not an authenticated owner session.

The remaining acceptance work is completed Google owner sign-in, live invited/uninvited-owner and workspace/grant checks, a controlled approved provider publication, native supported-browser WebMCP, restore and operational alerts, development/tooling advisory triage, and billing sandbox/settlement validation before any paid launch. Provider OAuth onboarding/refresh and remaining Advanced functionality are tracked in [implementation status](implementation-status.md).
