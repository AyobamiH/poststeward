# Provider connections and token lifecycle

PostSteward treats social-provider authorisation as a separate security boundary from Google owner sign-in. A browser owner can authorise X, Threads or LinkedIn only when the corresponding provider application is configured in the deployment. Missing provider application credentials disable that OAuth button; they never cause the service to fall back to a guessed token or another product's credentials.

## OAuth contracts

- **X** uses OAuth 2.0 Authorization Code with PKCE, requests `tweet.read`, `tweet.write`, `users.read` and `offline.access`, and requires a refresh token. PostSteward refreshes before access-token expiry and retains the account binding version when the stable provider identity and capability set are unchanged.
- **Threads** requests `threads_basic` and `threads_content_publish`, exchanges the short-lived code token for a long-lived token server-side, then refreshes the long-lived token before expiry. Refresh evidence must include a usable replacement token and lifetime; an empty or malformed response does not extend the stored expiry.
- **LinkedIn** uses three-legged OAuth through two deliberately separate applications. A member-profile application requests `openid`, `profile` and `w_member_social`; restricted `r_member_social` is requested only when `LINKEDIN_MEMBER_READBACK=true` and the application actually has that approval. LinkedIn requires Community Management to be the only product on its application, so a Page connection uses the dedicated `LINKEDIN_ORGANIZATION_OAUTH_CLIENT_ID` and secret, binds an exact organisation actor in one-use OAuth state, and requests only `w_organization_social` and `r_organization_social`. Programmatic refresh is used only when LinkedIn returns a refresh token; otherwise PostSteward surfaces reauthorisation before expiry instead of pretending refresh support exists.

Provider application secrets remain Worker secrets. Client IDs are non-secret deployment variables. Authorisation codes, tokens, refresh tokens and OAuth state values are never placed in application logs, repository content or browser storage. OAuth state is one-use, bound to the current owner session, workspace, provider and alias, and expires after ten minutes.

## Credential rotation

OAuth access credentials are encrypted in the workspace Durable Object using the existing application encryption key. Refresh tokens are encrypted separately with a distinct associated-data context. Automatic refresh re-reads the provider identity. If the stable provider identity changes, the account is deactivated and its binding version changes so pending work is blocked rather than silently redirected.

A successful refresh does not change the binding version unless the effective capability set changes. This prevents routine token rotation from invalidating correctly captured schedules while still forcing a new review when readback or refresh authority changes.

Disconnect is locally authoritative: PostSteward immediately removes OAuth refresh metadata and overwrites the stored access credential with a tombstone. X provider-side revocation is attempted when the service app is configured, but a provider outage cannot cause PostSteward to retain a locally disconnected credential.

## LinkedIn verification boundary

The member-profile and organisation/Page paths have different application credentials and readback authority. Member-profile readback requires restricted `r_member_social`. The reviewed Page path uses `r_organization_social`, which is role-gated to Pages the authenticated member administers or manages. Before storing a Page connection, PostSteward performs the permission-gated author finder for the exact state-bound Page URN; it does not request or depend on OpenID scopes that the Community Management application cannot hold. Every later identity recheck carries that same Page actor rather than falling back to the member profile.

The Page path is a PostSteward-managed multi-tenant application model. LinkedIn approves the central PostSteward application; a customer does not supply or obtain approval for a separate developer application. Each customer still completes three-legged consent with a LinkedIn member who holds an eligible role on the selected Page. The owner workspace accepts either the numeric Page ID or the complete organisation URN and normalises both to the stable Page URN before OAuth state is created.

PostSteward deliberately does not request `rw_organization_admin` merely to list Pages during onboarding. LinkedIn's organisation lookup and ACL discovery APIs require that broader permission, whereas the current publishing and readback contract needs only `w_organization_social` and `r_organization_social`. Page discovery can be introduced later as a separately reviewed authority expansion; it is not silently bundled into publishing consent.

For either path with granted readback authority, PostSteward verifies the creation URN, stable author URN, exact commentary and `PUBLISHED` lifecycle through a separate GET. The organisation path does not depend on or enable member-profile `r_member_social`.

This distinction is deliberate: code support is not represented as permission approval. External provider application review, billing or plan eligibility and real user consent remain evidence gates outside the repository.
