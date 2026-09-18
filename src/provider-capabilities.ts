import type { Provider } from "./types.ts";

export type ProviderCapabilityState =
  | "available"
  | "unavailable"
  | "unknown"
  | "connection_required"
  | "external_approval_required";

export interface ProviderCapability {
  state: ProviderCapabilityState;
  reason?: string;
  requiredScopes?: string[];
}

export interface ProviderCapabilitySet {
  identity: ProviderCapability;
  publish: ProviderCapability;
  readback: ProviderCapability;
  refresh: ProviderCapability;
  metrics: ProviderCapability;
}

function available(requiredScopes: string[] = []): ProviderCapability {
  return {
    state: "available",
    ...(requiredScopes.length ? { requiredScopes } : {}),
  };
}
function unavailable(
  reason: string,
  requiredScopes: string[] = [],
): ProviderCapability {
  return {
    state: "unavailable",
    reason,
    ...(requiredScopes.length ? { requiredScopes } : {}),
  };
}
function unknown(
  reason: string,
  requiredScopes: string[] = [],
): ProviderCapability {
  return {
    state: "unknown",
    reason,
    ...(requiredScopes.length ? { requiredScopes } : {}),
  };
}
function required(
  reason: string,
  requiredScopes: string[] = [],
): ProviderCapability {
  return {
    state: "external_approval_required",
    reason,
    ...(requiredScopes.length ? { requiredScopes } : {}),
  };
}
function connected(requiredScopes: string[] = []): ProviderCapability {
  return {
    state: "connection_required",
    ...(requiredScopes.length ? { requiredScopes } : {}),
  };
}

export function providerApplicationCapabilities(
  provider: Provider,
  configured: boolean,
  options: {
    linkedinMemberReadbackApproved: boolean;
    linkedinOrganizationActor?: boolean;
  },
): ProviderCapabilitySet {
  if (!configured) {
    const missing = unavailable("provider_app_not_configured");
    return {
      identity: missing,
      publish: missing,
      readback: missing,
      refresh: missing,
      metrics: missing,
    };
  }
  if (provider === "x")
    return {
      identity: connected(["users.read"]),
      publish: connected(["tweet.write"]),
      readback: connected(["tweet.read", "users.read"]),
      refresh: connected(["offline.access"]),
      metrics: connected(["tweet.read"]),
    };
  if (provider === "threads")
    return {
      identity: connected(["threads_basic"]),
      publish: connected(["threads_content_publish"]),
      readback: connected(["threads_basic"]),
      refresh: connected(),
      metrics: connected(["threads_manage_insights"]),
    };
  if (options.linkedinOrganizationActor)
    return {
      identity: connected(["openid", "profile"]),
      publish: connected(["w_organization_social"]),
      readback: connected(["r_organization_social"]),
      refresh: connected(),
      metrics: unavailable("organization_analytics_not_enabled"),
    };
  return {
    identity: connected(["openid", "profile"]),
    publish: connected(["w_member_social"]),
    readback: options.linkedinMemberReadbackApproved
      ? connected(["r_member_social"])
      : required("r_member_social_closed_to_new_requests", [
          "r_member_social",
        ]),
    refresh: connected(),
    metrics: unavailable("member_analytics_not_enabled"),
  };
}

export function negotiatedProviderCapabilities(
  provider: Provider,
  grantedScopes: string[],
  options: {
    refreshable: boolean;
    scopeEvidence: "provider" | "request_assumed";
    identityVerified: boolean;
    linkedinOrganizationActor?: boolean;
  },
): ProviderCapabilitySet {
  const scopes = new Set(grantedScopes);
  const proven = options.scopeEvidence === "provider";
  const scoped = (requiredScopes: string[], reason: string) => {
    if (!requiredScopes.every((scope) => scopes.has(scope)))
      return unavailable(reason, requiredScopes);
    return proven
      ? available(requiredScopes)
      : unknown("scope_response_absent", requiredScopes);
  };
  const identity = options.identityVerified
    ? available()
    : unknown("identity_not_verified");

  if (provider === "x")
    return {
      identity,
      publish: scoped(["tweet.write"], "tweet_write_not_granted"),
      readback: scoped(
        ["tweet.read", "users.read"],
        "tweet_read_not_granted",
      ),
      refresh:
        scopes.has("offline.access") && options.refreshable
          ? proven
            ? available(["offline.access"])
            : unknown("scope_response_absent", ["offline.access"])
          : unavailable("offline_access_or_refresh_token_missing", [
              "offline.access",
            ]),
      metrics: scoped(["tweet.read"], "tweet_read_not_granted"),
    };

  if (provider === "threads")
    return {
      identity,
      publish: scoped(
        ["threads_content_publish"],
        "threads_publish_not_granted",
      ),
      readback: scoped(["threads_basic"], "threads_basic_not_granted"),
      refresh: options.refreshable
        ? available()
        : unavailable("threads_refresh_unavailable"),
      metrics: scopes.has("threads_manage_insights")
        ? proven
          ? available(["threads_manage_insights"])
          : unknown("scope_response_absent", ["threads_manage_insights"])
        : unavailable("threads_manage_insights_not_granted", [
            "threads_manage_insights",
          ]),
    };

  if (options.linkedinOrganizationActor)
    return {
      identity,
      publish: scoped(
        ["w_organization_social"],
        "w_organization_social_not_granted",
      ),
      readback: scoped(
        ["r_organization_social"],
        "r_organization_social_not_granted",
      ),
      refresh: options.refreshable
        ? available()
        : unavailable("provider_refresh_token_not_issued"),
      metrics: unavailable("organization_analytics_not_enabled"),
    };
  return {
    identity,
    publish: scoped(["w_member_social"], "w_member_social_not_granted"),
    readback: scopes.has("r_member_social")
      ? proven
        ? available(["r_member_social"])
        : unknown("r_member_social_not_proven", ["r_member_social"])
      : required("r_member_social_closed_to_new_requests", [
          "r_member_social",
        ]),
    refresh: options.refreshable
      ? available()
      : unavailable("provider_refresh_token_not_issued"),
    metrics: unavailable("member_analytics_not_enabled"),
  };
}

/**
 * Compatibility projection for the existing account record. Detailed evidence
 * stays on OAuth metadata so callers can distinguish provider-confirmed scope
 * from a provider that simply omitted scope echoing.
 */
export function booleanCapabilities(
  provider: Provider,
  capabilities: ProviderCapabilitySet,
) {
  const assumedBaseline = (capability: ProviderCapability) =>
    capability.state === "unknown" &&
    capability.reason === "scope_response_absent";
  return {
    oauth: true,
    refresh: capabilities.refresh.state === "available",
    readback:
      capabilities.readback.state === "available" ||
      (provider === "threads" && assumedBaseline(capabilities.readback)),
    ...(provider === "threads" && capabilities.metrics.state === "available"
      ? { metrics: true }
      : {}),
  };
}
