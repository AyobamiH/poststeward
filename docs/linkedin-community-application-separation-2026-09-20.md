# LinkedIn Community Management application separation

Date: 2026-09-20

## Live portal finding

The LinkedIn Developer Portal provisioned **Sign In with LinkedIn using OpenID Connect** on application `266493906`, then refused the Community Management request with an explicit product-exclusivity message: Community Management must be the only product on its application. The existing application is therefore not Page-publishing authority and its client secret must not be installed as though it were.

## Engineering correction

PostSteward now models the two LinkedIn authorities as separate applications:

- member/OpenID: `LINKEDIN_OAUTH_CLIENT_ID` and `LINKEDIN_OAUTH_CLIENT_SECRET`;
- organisation/Page: `LINKEDIN_ORGANIZATION_OAUTH_CLIENT_ID` and `LINKEDIN_ORGANIZATION_OAUTH_CLIENT_SECRET`.

The Page path requests only `w_organization_social` and `r_organization_social`. It binds the exact reviewed actor `urn:li:organization:146607525` into one-use, owner-session OAuth state. Before storing the connection, it performs the permission-gated Posts author finder for that Page. The stored stable publishing identity is the Page URN, not the authorising member profile.

The member path remains independently available when its own application is configured. It continues to use `openid`, `profile` and `w_member_social`; its restricted readback authority is never inferred.

## Evidence boundary

Repository tests prove scope selection, credential routing, one-use state propagation, exact Page verification, encrypted token storage, later identity rechecks and deployment secret separation. They do not prove LinkedIn approval or a real owner grant.

LinkedIn's current Community Management review requires organisation information and a verified business-domain email. No such information is collected or submitted until the owner is ready. The remaining live sequence is:

1. create and verify a dedicated Community Management-only application for the PostSteward Page;
2. submit the provider access request with truthful owner-supplied organisation information;
3. after approval, install the dedicated client ID and protected secret in staging and production;
4. complete owner consent for `urn:li:organization:146607525`;
5. perform one separately approved Page publication and independent provider readback.

Primary contracts:

- https://learn.microsoft.com/en-us/linkedin/marketing/community-management-app-review
- https://learn.microsoft.com/en-us/linkedin/marketing/community-management/shares/posts-api
- https://learn.microsoft.com/en-us/linkedin/marketing/community-management/organizations/organization-access-control-by-role
