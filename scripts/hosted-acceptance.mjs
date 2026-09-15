import { createHash, randomUUID } from "node:crypto";
import { pathToFileURL } from "node:url";
import { resolve } from "node:path";

function origin(value) {
  const parsed = new URL(value);
  if (parsed.protocol !== "https:" || parsed.username || parsed.password)
    throw new Error("POSTSTEWARD_ORIGIN must be an HTTPS origin without credentials.");
  return parsed.origin;
}

function token(value, name) {
  if (typeof value !== "string" || value.length < 16 || /\s/.test(value))
    throw new Error(`${name} must be supplied through the environment.`);
  return value;
}

function publicWorkspaceId(value) {
  return createHash("sha256").update(String(value)).digest("hex").slice(0, 16);
}

async function readJson(response) {
  const text = await response.text();
  let body = {};
  try {
    body = text ? JSON.parse(text) : {};
  } catch {
    throw new Error(`Expected JSON but received HTTP ${response.status}.`);
  }
  return body;
}

async function operation(base, bearer, name, input = {}, send = fetch) {
  return send(`${base}/api/operations/${name}`, {
    method: "POST",
    redirect: "manual",
    cache: "no-store",
    headers: {
      Authorization: `Bearer ${bearer}`,
      "Content-Type": "application/json",
      Accept: "application/json",
    },
    body: JSON.stringify(input),
    signal: AbortSignal.timeout(20_000),
  });
}

async function mcp(base, bearer, body, send = fetch) {
  return send(`${base}/mcp`, {
    method: "POST",
    redirect: "manual",
    cache: "no-store",
    headers: {
      Authorization: `Bearer ${bearer}`,
      "Content-Type": "application/json",
      Accept: "application/json, text/event-stream",
    },
    body: JSON.stringify(body),
    signal: AbortSignal.timeout(20_000),
  });
}

function requireStatus(response, expected, label) {
  if (response.status !== expected)
    throw new Error(`${label} returned HTTP ${response.status}; expected ${expected}.`);
}

async function hostedRelease(base, send = fetch) {
  const response = await send(`${base}/readiness.json`, {
    redirect: "manual",
    cache: "no-store",
    signal: AbortSignal.timeout(20_000),
  });
  requireStatus(response, 200, "hosted readiness");
  const body = await readJson(response);
  if (!/^[a-f0-9]{40}$/.test(String(body?.release || "")))
    throw new Error("Hosted readiness did not expose a pinned release SHA.");
  return String(body.release);
}

export async function checkReadiness(baseInput, send = fetch) {
  const base = origin(baseInput);
  const response = await send(`${base}/readiness.json`, {
    redirect: "manual",
    cache: "no-store",
    signal: AbortSignal.timeout(20_000),
  });
  requireStatus(response, 200, "readiness");
  const body = await readJson(response);
  if (body?.providers?.threads?.oauth !== true)
    throw new Error("Threads OAuth is not configured in this deployment.");
  if (body?.providers?.x?.oauth === true || body?.providers?.linkedin?.oauth === true)
    throw new Error("Threads-first acceptance expected X and LinkedIn to remain outside P0.");
  if (body?.payments?.advancedEnabled === true || body?.payments?.mppEnabled === true)
    throw new Error("Advanced and MPP must remain disabled during Threads-first P0 acceptance.");
  if (body?.access?.signupMode !== "restricted")
    throw new Error("Restricted staging signup is required for this acceptance run.");
  return {
    release: String(body.release || ""),
    threadsOAuth: true,
    xOAuth: false,
    linkedinOAuth: false,
    advancedEnabled: false,
    mppEnabled: false,
    signupMode: "restricted",
  };
}

export async function checkAgentGrant(baseInput, bearerInput, send = fetch) {
  const base = origin(baseInput);
  const bearer = token(bearerInput, "POSTSTEWARD_AGENT_TOKEN");
  const httpResponse = await operation(base, bearer, "workspace_status", {}, send);
  requireStatus(httpResponse, 200, "HTTP workspace_status");
  const httpBody = await readJson(httpResponse);
  if (!httpBody.workspace || !httpBody.release)
    throw new Error("HTTP workspace_status returned incomplete identity evidence.");

  const initialize = await mcp(
    base,
    bearer,
    {
      jsonrpc: "2.0",
      id: 1,
      method: "initialize",
      params: {
        protocolVersion: "2025-03-26",
        capabilities: {},
        clientInfo: { name: "poststeward-hosted-acceptance", version: "1" },
      },
    },
    send,
  );
  requireStatus(initialize, 200, "MCP initialize");
  await readJson(initialize);

  const called = await mcp(
    base,
    bearer,
    {
      jsonrpc: "2.0",
      id: 2,
      method: "tools/call",
      params: { name: "workspace_status", arguments: {} },
    },
    send,
  );
  requireStatus(called, 200, "MCP workspace_status");
  const mcpBody = await readJson(called);
  const result = mcpBody?.result?.structuredContent?.result;
  if (!result || result.workspace !== httpBody.workspace || result.release !== httpBody.release)
    throw new Error("HTTP and MCP did not resolve to the same workspace/release.");

  return {
    workspaceFingerprint: publicWorkspaceId(httpBody.workspace),
    release: httpBody.release,
    http: "accepted",
    remoteMcp: "accepted",
  };
}

export async function checkRevokedGrant(baseInput, bearerInput, send = fetch) {
  const base = origin(baseInput);
  const bearer = token(bearerInput, "POSTSTEWARD_AGENT_TOKEN");
  const httpResponse = await operation(base, bearer, "workspace_status", {}, send);
  requireStatus(httpResponse, 401, "revoked HTTP grant");
  const mcpResponse = await mcp(
    base,
    bearer,
    {
      jsonrpc: "2.0",
      id: 3,
      method: "tools/call",
      params: { name: "workspace_status", arguments: {} },
    },
    send,
  );
  requireStatus(mcpResponse, 401, "revoked MCP grant");
  return { http: "denied", remoteMcp: "denied" };
}

export async function checkCrossTenantIsolation(
  baseInput,
  tokenAInput,
  tokenBInput,
  foreignDeliveryId,
  send = fetch,
) {
  const base = origin(baseInput);
  const tokenA = token(tokenAInput, "POSTSTEWARD_AGENT_TOKEN_A");
  const tokenB = token(tokenBInput, "POSTSTEWARD_AGENT_TOKEN_B");
  if (!/^[A-Za-z0-9_-]{1,100}$/.test(foreignDeliveryId || ""))
    throw new Error("POSTSTEWARD_FOREIGN_DELIVERY_ID must be an existing delivery from workspace A.");

  const [aResponse, bResponse] = await Promise.all([
    operation(base, tokenA, "workspace_status", {}, send),
    operation(base, tokenB, "workspace_status", {}, send),
  ]);
  requireStatus(aResponse, 200, "workspace A status");
  requireStatus(bResponse, 200, "workspace B status");
  const a = await readJson(aResponse);
  const b = await readJson(bResponse);
  if (!a.workspace || !b.workspace || a.workspace === b.workspace)
    throw new Error("Cross-tenant acceptance requires two distinct workspaces.");

  const ownerRead = await operation(
    base,
    tokenA,
    "receipt_get",
    { delivery: foreignDeliveryId },
    send,
  );
  requireStatus(ownerRead, 200, "workspace A receipt read");
  const swapped = await operation(
    base,
    tokenB,
    "receipt_get",
    { delivery: foreignDeliveryId },
    send,
  );
  requireStatus(swapped, 404, "workspace B object-ID swap");

  const forgedOrigin = await send(`${base}/api/operations/workspace_status`, {
    method: "POST",
    redirect: "manual",
    headers: {
      Authorization: `Bearer ${tokenA}`,
      Origin: "https://attacker.invalid",
      "Content-Type": "application/json",
    },
    body: "{}",
    signal: AbortSignal.timeout(20_000),
  });
  requireStatus(forgedOrigin, 403, "hostile Origin replay");

  return {
    workspaceA: publicWorkspaceId(a.workspace),
    workspaceB: publicWorkspaceId(b.workspace),
    objectIdSwap: "denied",
    hostileOriginReplay: "denied",
  };
}

/**
 * Hosted isolation without provider or customer data. Two short-lived grants
 * must belong to explicitly disposable, distinct staging workspaces and include
 * read + publish. The only mutation is a reversible workspace pause canary.
 */
export async function checkCrossTenantStateIsolation(
  baseInput,
  tokenAInput,
  tokenBInput,
  expectedRelease,
  send = fetch,
) {
  const base = origin(baseInput);
  const tokenA = token(tokenAInput, "POSTSTEWARD_AGENT_TOKEN_A");
  const tokenB = token(tokenBInput, "POSTSTEWARD_AGENT_TOKEN_B");
  if (!/^[a-f0-9]{40}$/.test(expectedRelease || ""))
    throw new Error("Cross-tenant acceptance requires the exact reviewed release SHA.");
  const release = await hostedRelease(base, send);
  if (release !== expectedRelease)
    throw new Error("Hosted staging release does not equal the reviewed workflow revision.");

  const [aResponse, bResponse] = await Promise.all([
    operation(base, tokenA, "workspace_status", {}, send),
    operation(base, tokenB, "workspace_status", {}, send),
  ]);
  requireStatus(aResponse, 200, "workspace A status");
  requireStatus(bResponse, 200, "workspace B status");
  const a = await readJson(aResponse);
  const b = await readJson(bResponse);
  if (
    !a.workspace ||
    !b.workspace ||
    a.workspace === b.workspace ||
    a.release !== release ||
    b.release !== release
  )
    throw new Error("Cross-tenant acceptance requires two distinct workspaces on the exact release.");
  if (a.publishingPaused !== false || b.publishingPaused !== false)
    throw new Error("Disposable cross-tenant workspaces must begin unpaused.");

  let pauseApplied = false;
  let cleanupError;
  try {
    const pause = await operation(
      base,
      tokenA,
      "publishing_pause",
      {
        paused: true,
        idempotencyKey: `cross-tenant-pause-${randomUUID()}`,
      },
      send,
    );
    requireStatus(pause, 200, "workspace A pause canary");
    const pauseBody = await readJson(pause);
    if (pauseBody?.paused !== true)
      throw new Error("Workspace A pause canary did not commit.");
    pauseApplied = true;

    const [aPausedResponse, bUnchangedResponse] = await Promise.all([
      operation(base, tokenA, "workspace_status", {}, send),
      operation(base, tokenB, "workspace_status", {}, send),
    ]);
    requireStatus(aPausedResponse, 200, "workspace A paused status");
    requireStatus(bUnchangedResponse, 200, "workspace B unchanged status");
    const aPaused = await readJson(aPausedResponse);
    const bUnchanged = await readJson(bUnchangedResponse);
    if (aPaused.publishingPaused !== true || bUnchanged.publishingPaused !== false)
      throw new Error("Workspace-local pause state crossed a tenant boundary.");

    const forgedOrigin = await send(`${base}/api/operations/workspace_status`, {
      method: "POST",
      redirect: "manual",
      cache: "no-store",
      headers: {
        Authorization: `Bearer ${tokenA}`,
        Origin: "https://attacker.invalid",
        "Content-Type": "application/json",
      },
      body: "{}",
      signal: AbortSignal.timeout(20_000),
    });
    requireStatus(forgedOrigin, 403, "hostile Origin replay");

    return {
      release,
      observedAt: new Date().toISOString(),
      workspaceA: publicWorkspaceId(a.workspace),
      workspaceB: publicWorkspaceId(b.workspace),
      distinctWorkspaces: true,
      workspaceLocalStateIsolation: true,
      hostileOriginReplay: "denied",
      providerEffectAttempted: false,
      paymentAttempted: false,
      recoveryAttempted: false,
      tokenValuesEmitted: false,
      rawWorkspaceIdsEmitted: false,
      ready: true,
    };
  } finally {
    if (pauseApplied) {
      try {
        const resume = await operation(
          base,
          tokenA,
          "publishing_pause",
          {
            paused: false,
            idempotencyKey: `cross-tenant-resume-${randomUUID()}`,
          },
          send,
        );
        requireStatus(resume, 200, "workspace A pause cleanup");
        const resumed = await readJson(resume);
        if (resumed?.paused !== false)
          throw new Error("Workspace A pause cleanup did not commit.");
      } catch (error) {
        cleanupError = error;
      }
    }
    if (cleanupError) throw cleanupError;
  }
}

async function main() {
  const mode = process.argv[2];
  const base = process.env.POSTSTEWARD_ORIGIN || "";
  let result;
  if (mode === "readiness") result = await checkReadiness(base);
  else if (mode === "agent")
    result = await checkAgentGrant(base, process.env.POSTSTEWARD_AGENT_TOKEN || "");
  else if (mode === "revoked")
    result = await checkRevokedGrant(base, process.env.POSTSTEWARD_AGENT_TOKEN || "");
  else if (mode === "cross-tenant")
    result = await checkCrossTenantIsolation(
      base,
      process.env.POSTSTEWARD_AGENT_TOKEN_A || "",
      process.env.POSTSTEWARD_AGENT_TOKEN_B || "",
      process.env.POSTSTEWARD_FOREIGN_DELIVERY_ID || "",
    );
  else if (mode === "cross-tenant-state")
    result = await checkCrossTenantStateIsolation(
      base,
      process.env.POSTSTEWARD_AGENT_TOKEN_A || "",
      process.env.POSTSTEWARD_AGENT_TOKEN_B || "",
      process.env.GITHUB_SHA || "",
    );
  else
    throw new Error(
      "Usage: node scripts/hosted-acceptance.mjs readiness|agent|revoked|cross-tenant|cross-tenant-state",
    );
  const marker =
    mode === "cross-tenant-state"
      ? "POSTSTEWARD_CROSS_TENANT_STATE_ACCEPTANCE "
      : "";
  console.log(marker + JSON.stringify(result, null, marker ? 0 : 2));
}

if (process.argv[1] && import.meta.url === pathToFileURL(resolve(process.argv[1])).href)
  main().catch((error) => {
    console.error(error instanceof Error ? error.message : String(error));
    process.exitCode = 1;
  });
