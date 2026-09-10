# External capability gates

PostSteward must fail closed when a capability depends on an external account decision that repository code cannot truthfully manufacture.

## Provider application configuration

Each provider is independently available only when its application client ID and secret are both present in the selected deployment environment. The provider redirect URIs are:

- X: `https://poststeward-staging.woeinvests.workers.dev/connections/oauth/x/callback`
- Threads: `https://poststeward-staging.woeinvests.workers.dev/connections/oauth/threads/callback`
- LinkedIn: `https://poststeward-staging.woeinvests.workers.dev/connections/oauth/linkedin/callback`

The repository does not reuse credentials from another product. Provider review, app ownership and user consent are external authority decisions. Until one provider app is configured, manual token import remains a clearly labelled restricted fallback rather than being called self-service OAuth acceptance.

## Live controlled publication

The owner must complete Google sign-in in the hosted application, explicitly select the provider/account and exact text, and approve the one-shot publication. Completion requires a durable provider creation ID plus a separate exact readback for the same stable author and text. No CI workflow may insert an owner session, provider token or acceptance receipt to bypass this requirement.

## Browser WebMCP

The hosted browser code may report whether the browser exposes native WebMCP. Release acceptance requires an authenticated, supported browser to register the catalog and execute a read-only operation. The service must continue to work over HTTP/remote MCP when native WebMCP is absent.

## Payments

Stripe and machine-payment configuration stays disabled until sandbox provider-of-record evidence covers checkout, signed webhook reconciliation, recurring coverage, cancellation/expiry, refund/dispute revocation and the eligible machine-payment settlement path. A configured secret or successful mock is not payment acceptance.

## Recovery

Automated deployment may check migrations and non-destructive recovery access controls. It must not initiate point-in-time restore. A real recovery rehearsal intentionally changes a workspace and is therefore an owner operation with its own prepare/execute/reconcile/resume evidence.
