# PostSteward security model

This document records implemented boundaries and remaining evidence. It is not a certification or a claim that a successful build proves a secure public service.

## Trust and authority

The Internet, agent inputs and monitored repository content are untrusted. An identity provider authenticates an owner; a browser session can issue bounded agent grants. Each request resolves its workspace from a stored session or hashed grant, never from a caller-supplied tenant header. Workspace Durable Objects have no public routing. Provider credentials are encrypted with AES-GCM using workspace/account-specific authenticated context. Exact approved text and verified account identity are captured before dispatch; uncertainty never permits a blind repeat.

OIDC uses state bound to an HttpOnly browser cookie, atomic single-use state consumption, PKCE, nonce, issuer/audience checks and signature verification against the issuer's JWKS. Initial deployments admit only allowlisted identities with a boolean verified-email claim. Unknown signup configuration fails closed. Restricted access is an initial rollout control; public signup requires an explicit reviewed release change.

Session mutations require the configured Origin and session CSRF token. A malformed Authorization header cannot fall back to a privileged browser cookie. Agent grants expire, can be revoked and are capped at 50 active grants per workspace. Revocation is checked again before later scheduled dispatch.

## Abuse and browser boundaries

- Origin and hostname must exactly match the configured HTTPS origin. There is no deployment-placeholder bypass.
- Cloudflare rate bindings admit at most 10 sign-in requests or 120 other protected requests per IP per minute per location. They fail closed if unavailable. Rate keys include the service/origin and a hash of Cloudflare's ingress IP; X-Forwarded-For is not trusted. Cloudflare's counters are eventually consistent and are not a global cost ceiling.
- A SQLite transaction enforces 120 requests per fixed minute per workspace across operation calls, regions, grants and HTTP/MCP/WebMCP. Boundary bursts can straddle adjacent minutes. Scheduled execution and verified billing reconciliation do not depend on a client's remaining request allowance.
- Request bodies are bounded to 32 KiB (Stripe webhooks: 256 KiB) and ten seconds; streamed transfer cannot bypass the byte limit. API bodies require JSON.
- OIDC state storage is capped at 10,000 outstanding rows per environment. An hourly task removes bounded batches of expired state and sessions; long-expired grants are removed after 30 additional days. Delivery receipts and uncertain effects are never purged by this task.
- HSTS, CSP including object/frame restrictions, no-referrer, nosniff and uncached sensitive responses protect the browser surface. Public docs remain crawlable and require no JavaScript challenge.

## Deployment boundary

Staging and production have different Worker names, D1 databases, Durable Object namespaces, rate namespaces, OIDC clients and encryption keys. The deployment checks the D1 UUID and name from Cloudflare before applying any migration. Custom domains disable workers.dev and version preview URLs are disabled in all environments.

The manual GitHub workflow deploys only this repository's main revision after verification. Actions are pinned to full commit SHAs. The verification job receives no deployment secrets. The deploy job installs pinned dependencies with lifecycle scripts disabled and receives credentials only in its deployment step. Three application secrets are uploaded alongside code from a temporary mode-0600 file that is removed in a finally block. The Cloudflare API token is never included in Worker secrets.

Protect GitHub main and environment branches, restrict repository administrators, and enable account MFA. Environment approval availability depends on the GitHub plan; it must be checked in the actual private repository. A separate token per environment improves revocation, but Cloudflare's account-scoped Workers/D1 permissions still cover other resources in that account. A dedicated Cloudflare account provides stronger administrative isolation than names alone. Application encryption does not protect against someone authorised to replace the Worker code.

Automatic invocation logging and traces are disabled to avoid recording OIDC callback query strings. Failures emit only a generated request ID, release and HTTP status; the same request ID is returned in the response header. Application/provider/credential payloads are not logged. Set up platform error/usage alerts and a separately secured redacted logging sink before public traffic. Do not enable raw request logging as a shortcut.

## Remaining public release evidence

A real deployment still needs owner sign-in and OAuth consent checks, live provider permissions and controlled publication, supported-browser WebMCP, storage/traffic capacity calibration, cross-tenant attack testing on the hosted origin, and restoration/key-rotation rehearsals. Comprehensive retained-workspace quotas, erasure, audit retention and operational alert delivery remain release work. Rate limits and a CPU cap do not impose a total account-spend limit.

Use a custom domain for zone-level WAF controls before unrestricted traffic; configure API-compatible blocking/rate rules without interactive challenges on MCP, API or Stripe webhook routes. Rules must be reviewed in the actual Cloudflare zone and tested with real agent clients. No WAF rule or alert has been created by this code change. Keep signup restricted and paid purchases disabled until the outstanding work is accepted.

References: [Cloudflare rate limiting](https://developers.cloudflare.com/workers/runtime-apis/bindings/rate-limit/), [Worker secrets](https://developers.cloudflare.com/workers/configuration/secrets/), [GitHub workflow security](https://docs.github.com/en/actions/reference/security/secure-use).
