# Launch-critical hardening — 19 September 2026

PostSteward's launch path uses existing controls and adds reviewed production evidence rather than a second operational stack.

## External design patterns used

### Cloudflare: custom production origin, layered edge controls and bounded admission

PostSteward follows Cloudflare's production routing pattern: the effectful application uses the custom HTTPS origin `https://app.poststeward.com`, while the zone owns WAF/rate controls independently of the Worker deployment.

References:

- https://developers.cloudflare.com/workers/configuration/routing/custom-domains/
- https://developers.cloudflare.com/waf/custom-rules/
- https://developers.cloudflare.com/waf/rate-limiting-rules/
- https://developers.cloudflare.com/workers/runtime-apis/bindings/rate-limit/

The production-edge controller manages only reviewed, named rules and independently reads them back after mutation.

Public onboarding deliberately does **not** add a generic interactive challenge in front of the Google OIDC redirect. Provider login is already state/nonce/PKCE-bound and protected by Cloudflare/Worker login rate limiting. New workspace admission is instead bounded atomically in D1:

- verified Google email required;
- returning owners never consume new-admission capacity;
- first 100 workspaces maximum;
- maximum 10 new workspaces per hour;
- staging remains restricted;
- public mode is honoured only by the reviewed production-request workflow;
- Advanced and MPP remain disabled.

### Google SRE: actionable alerts, not noisy error forwarding

The alert design separates durable application-semantic incidents from the out-of-band operator plane. The D1 alert outbox retains retry/deduplication semantics, while GitHub Issues provides an independently available operator receipt and acknowledgement path.

References:

- https://sre.google/workbook/alerting-on-slos/
- https://sre.google/workbook/on-call/

The production fire drill exercises all reviewed alert classes, deliberately fails one synthetic primary path, observes escalation to the GitHub issue fallback, reads the issue back, and closes it. No customer incident is manufactured.

### AWS Well-Architected: quota-aware capacity with explicit headroom

Capacity is treated as a quota-management problem rather than a one-off benchmark. Hosted per-workspace high-water observations are projected to the first 100 workspaces, then checked against product limits, provider quotas and the Cloudflare paid-plan envelope.

Reference:

- https://docs.aws.amazon.com/wellarchitected/latest/reliability-pillar/rel_manage_service_quota.html

The product requires at least 30% retained-state/product headroom. Threads quota evidence is explicit and dated rather than inferred. X remains optional for launch capacity because its commercial product cap is account-plan-specific.

### Cloudflare pricing: reviewed cost envelope, not a fixed assumption

Reference:

- https://developers.cloudflare.com/workers/platform/pricing/

The first-100 cost report checks projected monthly Worker requests, conservative p99 CPU, and observed D1 rows-per-request against the reviewed included-usage envelope. The $5 monthly estimate is valid only while those checks pass.

## Four launch gates

### 1. Production edge

Workflow: `.github/workflows/production-edge-reconcile.yml`

The workflow applies only the canonical WAF/auth-rate controls and now immediately runs the independent production edge verifier. Acceptance requires the exact hosted release, custom HTTPS origin, proxied DNS, security headers, WAF and rate-limit readback.

### 2. Operational alert delivery

Workflow: `.github/workflows/operational-alert-control-plane.yml`

The control plane now exercises both staging and production protected environments. Production evidence is bound to the exact hosted release and uses a GitHub issue create/readback/close flow plus a deliberately failed synthetic notification path.

### 3. Capacity/cost calibration

Workflow: `.github/workflows/staging-capacity-observation.yml`

The observer now runs against both staging and production. It projects hosted per-workspace high-water values to 100 workspaces and consumes:

- `docs/evidence/provider-quota-20260919.json`
- `docs/evidence/cloudflare-pricing-20260919.json`

Readiness requires 30% product headroom, 30% provider quota headroom and a projected Cloudflare usage envelope that fits the reviewed estimate.

### 4. Public signup

Public admission is implemented but remains **restricted during evidence collection**.

The public path requires a verified Google email and atomically caps new workspaces. Completed workspace deletion retains its old tombstone; a later legitimate sign-in receives a new workspace identity instead of resurrecting the deleted one.

Workflow: `.github/workflows/public-launch-operations.yml`

The operator fire drill exercises:

- support intake;
- abuse escalation;
- privacy/data request handling;
- incident command;
- provider-outage communication;
- emergency publishing pause.

Only after the first three production gates and this operator drill are accepted may the reviewed production-request workflow change its source-controlled signup request to `public`.

## Non-goals

This launch hardening does not:

- enable Advanced or MPP;
- reopen accepted Threads, recovery, Stripe sandbox, GitHub authority or cross-tenant evidence;
- create a social publication;
- manufacture customer incidents;
- infer X commercial quotas;
- expose provider secrets or raw customer/workspace identifiers in evidence.
