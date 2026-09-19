import { digest, Fault, json, requireValue } from "./common.ts";
import type { Env, Provider } from "./types.ts";

type ThreadsSignedRequest = {
  algorithm: "HMAC-SHA256";
  user_id: string;
  issued_at?: number;
  expires?: number;
};

type ProviderBinding = {
  provider: Provider;
  identity_id: string;
  workspace: string;
  alias: string;
};

function decodeBase64Url(value: string) {
  requireValue(
    /^[A-Za-z0-9_-]+$/.test(value) && value.length <= 8192,
    "THREADS_SIGNED_REQUEST_INVALID",
    "Threads signed request is invalid.",
    400,
  );
  const padded = value.replace(/-/g, "+").replace(/_/g, "/").padEnd(
    Math.ceil(value.length / 4) * 4,
    "=",
  );
  try {
    const binary = atob(padded);
    return Uint8Array.from(binary, (char) => char.charCodeAt(0));
  } catch {
    throw new Fault(
      "THREADS_SIGNED_REQUEST_INVALID",
      "Threads signed request is invalid.",
      400,
    );
  }
}

async function verifyHmac(
  encodedPayload: string,
  encodedSignature: string,
  secret: string,
) {
  requireValue(
    typeof secret === "string" && secret.trim().length >= 8,
    "THREADS_CALLBACK_UNCONFIGURED",
    "Threads callback verification is not configured.",
    503,
  );
  const key = await crypto.subtle.importKey(
    "raw",
    new TextEncoder().encode(secret),
    { name: "HMAC", hash: "SHA-256" },
    false,
    ["verify"],
  );
  return crypto.subtle.verify(
    "HMAC",
    key,
    decodeBase64Url(encodedSignature),
    new TextEncoder().encode(encodedPayload),
  );
}

export async function parseThreadsSignedRequest(
  signedRequest: string,
  secret: string,
  now = Date.now(),
): Promise<ThreadsSignedRequest> {
  requireValue(
    typeof signedRequest === "string" &&
      signedRequest.length >= 16 &&
      signedRequest.length <= 16384,
    "THREADS_SIGNED_REQUEST_INVALID",
    "Threads signed request is invalid.",
    400,
  );
  const parts = signedRequest.split(".");
  requireValue(
    parts.length === 2 && parts[0] && parts[1],
    "THREADS_SIGNED_REQUEST_INVALID",
    "Threads signed request is invalid.",
    400,
  );
  requireValue(
    await verifyHmac(parts[1], parts[0], secret),
    "THREADS_SIGNED_REQUEST_INVALID",
    "Threads signed request signature is invalid.",
    400,
  );
  let value: any;
  try {
    value = JSON.parse(new TextDecoder().decode(decodeBase64Url(parts[1])));
  } catch {
    throw new Fault(
      "THREADS_SIGNED_REQUEST_INVALID",
      "Threads signed request payload is invalid.",
      400,
    );
  }
  requireValue(
    value?.algorithm === "HMAC-SHA256" &&
      typeof value.user_id === "string" &&
      value.user_id.length >= 1 &&
      value.user_id.length <= 256 &&
      !/[\s\x00-\x1f]/.test(value.user_id),
    "THREADS_SIGNED_REQUEST_INVALID",
    "Threads signed request payload is invalid.",
    400,
  );
  if (value.expires !== undefined)
    requireValue(
      Number.isFinite(value.expires) && value.expires * 1000 >= now,
      "THREADS_SIGNED_REQUEST_EXPIRED",
      "Threads signed request has expired.",
      400,
    );
  return value as ThreadsSignedRequest;
}

export async function signedRequestFromCallback(request: Request) {
  const contentType = request.headers.get("content-type") || "";
  requireValue(
    /^application\/x-www-form-urlencoded(?:\s*;|$)/i.test(contentType) ||
      /^multipart\/form-data(?:\s*;|$)/i.test(contentType),
    "THREADS_CALLBACK_CONTENT_TYPE",
    "Threads callbacks must use form data.",
    415,
  );
  const form = await request.formData();
  const signed = form.get("signed_request");
  requireValue(
    typeof signed === "string",
    "THREADS_SIGNED_REQUEST_REQUIRED",
    "Threads callback did not include a signed request.",
    400,
  );
  return signed;
}

export async function registerProviderIdentity(
  db: D1Database,
  binding: {
    provider: Provider;
    identityId: string;
    workspace: string;
    alias: string;
  },
  now = Date.now(),
) {
  requireValue(
    binding.identityId.length <= 256 &&
      binding.workspace.length <= 256 &&
      /^[A-Za-z0-9_-]{1,100}$/.test(binding.alias),
    "PROVIDER_BINDING_INVALID",
    "Provider identity binding is invalid.",
  );
  await db.batch([
    db
      .prepare(
        "DELETE FROM provider_identity_bindings WHERE provider=? AND workspace=? AND alias=?",
      )
      .bind(binding.provider, binding.workspace, binding.alias),
    db
      .prepare(
        "INSERT INTO provider_identity_bindings(provider,identity_id,workspace,alias,created_at,updated_at) VALUES (?,?,?,?,?,?)",
      )
      .bind(
        binding.provider,
        binding.identityId,
        binding.workspace,
        binding.alias,
        now,
        now,
      ),
  ]);
}

export async function removeProviderIdentity(
  db: D1Database,
  provider: Provider,
  workspace: string,
  alias: string,
) {
  await db
    .prepare(
      "DELETE FROM provider_identity_bindings WHERE provider=? AND workspace=? AND alias=?",
    )
    .bind(provider, workspace, alias)
    .run();
}

async function indexedBindings(
  db: D1Database,
  provider: Provider,
  identityId: string,
) {
  const result = await db
    .prepare(
      "SELECT provider,identity_id,workspace,alias FROM provider_identity_bindings WHERE provider=? AND identity_id=? ORDER BY workspace,alias LIMIT 100",
    )
    .bind(provider, identityId)
    .all<ProviderBinding>();
  return result.results || [];
}

async function candidateWorkspaces(db: D1Database) {
  const result = await db
    .prepare("SELECT workspace FROM workspace_registry ORDER BY workspace LIMIT 500")
    .all<{ workspace: string }>();
  return result.results || [];
}

async function revokeInWorkspace(
  env: Env,
  workspace: string,
  identityId: string,
  action: "uninstall" | "delete",
  alias?: string,
) {
  const response = await env.WORKSPACES.get(
    env.WORKSPACES.idFromName(workspace),
  ).fetch("https://workspace.internal/provider/threads-callback", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      workspace,
      input: { provider: "threads", identityId, action, alias },
    }),
  });
  if (!response.ok) {
    const value: any = await response.json().catch(() => ({}));
    throw new Fault(
      value.error?.code || "THREADS_CALLBACK_REVOKE_FAILED",
      value.error?.message || "Threads callback revocation failed.",
      response.status,
    );
  }
  return (await response.json()) as {
    matched: number;
    aliases: string[];
  };
}

export async function revokeThreadsIdentity(
  env: Env,
  identityId: string,
  action: "uninstall" | "delete",
) {
  const indexed = await indexedBindings(env.IDENTITY, "threads", identityId);
  const matches = new Map<string, Set<string>>();
  for (const binding of indexed) {
    const aliases = matches.get(binding.workspace) || new Set<string>();
    aliases.add(binding.alias);
    matches.set(binding.workspace, aliases);
  }

  let matched = 0;
  if (matches.size) {
    for (const [workspace, aliases] of matches)
      for (const alias of aliases) {
        const result = await revokeInWorkspace(
          env,
          workspace,
          identityId,
          action,
          alias,
        );
        matched += result.matched;
        if (result.matched)
          await removeProviderIdentity(env.IDENTITY, "threads", workspace, alias);
      }
  } else {
    // Backwards-compatible bounded fallback for connections created before
    // the provider identity index existed. The callback is authenticated by
    // Meta's app-secret HMAC before this scan is allowed.
    for (const { workspace } of await candidateWorkspaces(env.IDENTITY)) {
      const result = await revokeInWorkspace(
        env,
        workspace,
        identityId,
        action,
      );
      matched += result.matched;
      for (const alias of result.aliases)
        await removeProviderIdentity(env.IDENTITY, "threads", workspace, alias);
    }
  }

  return { matched };
}

export async function threadsUninstallCallback(request: Request, env: Env) {
  const signed = await signedRequestFromCallback(request);
  const payload = await parseThreadsSignedRequest(
    signed,
    env.THREADS_OAUTH_CLIENT_SECRET || "",
  );
  const result = await revokeThreadsIdentity(env, payload.user_id, "uninstall");
  return json({ received: true, revoked: result.matched });
}

export async function threadsDeleteCallback(request: Request, env: Env) {
  const signed = await signedRequestFromCallback(request);
  const payload = await parseThreadsSignedRequest(
    signed,
    env.THREADS_OAUTH_CLIENT_SECRET || "",
  );
  const requestedAt = Date.now();
  const result = await revokeThreadsIdentity(env, payload.user_id, "delete");
  const confirmationCode = crypto.randomUUID().replace(/-/g, "");
  const identityHash = await digest({
    provider: "threads",
    identity: payload.user_id,
  });
  await env.IDENTITY.prepare(
    "INSERT INTO provider_deletion_requests(confirmation_code,provider,identity_hash,state,matched_accounts,requested_at,completed_at) VALUES (?,'threads',?,'completed',?,?,?)",
  )
    .bind(
      confirmationCode,
      identityHash,
      result.matched,
      requestedAt,
      Date.now(),
    )
    .run();
  return json({
    url:
      env.PUBLIC_ORIGIN +
      "/connections/oauth/threads/delete/status?code=" +
      encodeURIComponent(confirmationCode),
    confirmation_code: confirmationCode,
  });
}

export async function threadsDeleteStatus(request: Request, env: Env) {
  const code = new URL(request.url).searchParams.get("code") || "";
  requireValue(
    /^[a-f0-9]{32}$/.test(code),
    "THREADS_DELETION_STATUS_INVALID",
    "Deletion confirmation code is invalid.",
    400,
  );
  const row = await env.IDENTITY.prepare(
    "SELECT state,requested_at,completed_at FROM provider_deletion_requests WHERE confirmation_code=? AND provider='threads'",
  )
    .bind(code)
    .first<{
      state: string;
      requested_at: number;
      completed_at: number;
    }>();
  requireValue(
    row,
    "THREADS_DELETION_STATUS_NOT_FOUND",
    "Deletion confirmation code was not found.",
    404,
  );
  return json({
    provider: "threads",
    state: row.state,
    requestedAt: row.requested_at,
    completedAt: row.completed_at,
  });
}
