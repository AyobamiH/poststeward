# Provider connections and token lifecycle

PostSteward treats social-provider authorisation as a separate security boundary from Google owner sign-in. A browser owner can authorise X, Threads or LinkedIn only when the corresponding provider application is configured in the deployment. Missing provider application credentials disable that OAuth button; they never cause the service to fall back to a guessed token or another product's credentials.

## OAuth contracts

- **X** uses OAuth 2.0 Authorization Code with PKCE, requests `tweet.read`, `tweet.write`, `users.read` and `offline.access`, and requires a refresh token. PostSteward refreshes before access-token expiry and retains the account binding version when the stable provider identity and capability set are unchanged.
- **Threads** requests `threads_basic` and `threads_content_publish`, exchanges the short-lived code token for a long-lived token server-side, then refreshes the long-lived token before expiry. Refresh evidence must include a usable replacement token and lifetime; an empty or malformed response does not extend the stored expiry.
- **LinkedIn** uses three-legged OAuth for `openid`, `profile` and `w_member_social`. `r_member_social` is requested only when `LINKEDIN_MEMBER_READBACK=true` and the LinkedIn application has actually been approved for that restricted permission. Programmatic refresh is used only when LinkedIn returns a refresh token; otherwise PostSteward surfaces reauthorisation before expiry instead of pretending refresh support exists.

Provider application secrets remain Worker secrets. Client IDs are non-secret deployment variables. Authorisation codes, tokens, refresh tokens and OAuth state values are never placed in application logs, repository content or browser storage. OAuth state is one-use, bound to the current owner session, workspace, provider and alias, and expires after ten minutes.

## Credential rotation

OAuth access credentials are encrypted in the workspace Durable Object using the existing application encryption key. Refresh tokens are encrypted separately with a distinct associated-data context. Automatic refresh re-reads the provider identity. If the stable provider identity changes, the account is deactivated and its binding version changes so pending work is blocked rather than silently redirected.

A successful refresh does not change the binding version unless the effective capability set changes. This prevents routine token rotation from invalidating correctly captured schedules while still forcing a new review when readback or refresh authority changes.

Disconnect is locally authoritative: PostSteward immediately removes OAuth refresh metadata and overwrites the stored access credential with a tombstone. X provider-side revocation is attempted when the service app is configured, but a provider outage cannot cause PostSteward to retain a locally disconnected credential.

## LinkedIn verification boundary

The Posts API can retrieve a post by its durable URN, but member readback requires LinkedIn's restricted `r_member_social` permission. When that capability is present, PostSteward verifies the creation URN, stable author URN, exact commentary and `PUBLISHED` lifecycle through a separate GET. Without that permission, LinkedIn can still be used by the normal publisher, but the controlled first-publication acceptance does not offer LinkedIn as an independently verifiable destination.

This distinction is deliberate: code support is not represented as permission approval. External provider application review, billing or plan eligibility and real user consent remain evidence gates outside the repository.
