import { setTimeout as delay } from "node:timers/promises";
import { demand, httpsUrl } from "./deployment-config.mjs";

export async function waitForRevision(origin, release, { send = fetch, sleep = delay, attempts = 12 } = {}) {
  httpsUrl(origin, true);
  demand(/^[a-f0-9]{40}$/.test(release), "An exact release SHA is required.");
  demand(Number.isInteger(attempts) && attempts > 0 && attempts <= 12, "Readiness attempts must be bounded.");
  let lastStatus = 0;
  for (let attempt = 1; attempt <= attempts; attempt++) {
    let response;
    try {
      response = await send(new URL("/health", origin), { redirect: "manual", signal: AbortSignal.timeout(5000) });
      lastStatus = response.status;
    } catch {
      // A new workers.dev hostname may not yet resolve. Never print network exception bodies.
      lastStatus = 0;
    }
    if (response) {
      demand(![301, 302, 303, 307, 308, 401, 403].includes(response.status), "Readiness redirected or rejected access; not retrying.");
      const body = await response.json().catch(() => null);
      if (response.status === 200 && body?.status === "ok" && body.release === release)
        return { attempts: attempt, status: 200 };
    }
    if (attempt < attempts) await sleep(2000);
  }
  throw new Error(`Expected revision did not become ready within the bounded checks (last HTTP status ${lastStatus}).`);
}

export async function verifyHosted(c, { send = fetch, sleep = delay } = {}) {
  const origin = c.vars.PUBLIC_ORIGIN;
  const release = c.vars.RELEASE_SHA;
  const readiness = await waitForRevision(origin, release, { send, sleep });
  const checks = [];
  async function check(name, path, expected, options = {}, predicate = () => true) {
    let status = 0, passed = false;
    try {
      const response = await send(new URL(path, origin), { ...options, redirect: "manual", signal: AbortSignal.timeout(10000) });
      status = response.status;
      passed = status === expected && Boolean(await predicate(response));
      if (response.body && !response.bodyUsed) await response.body.cancel();
    } catch {
      // Store only fixed check names and status. No response bodies, cookies, state or exception data.
    }
    checks.push({ name, status, expected, passed });
  }
  const secure = (r) => r.headers.get("strict-transport-security")?.includes("max-age=") && r.headers.get("x-content-type-options") === "nosniff" && r.headers.get("x-frame-options") === "DENY";
  await check("exact healthy revision; Advanced disabled", "/health", 200, {}, async (r) => {
    const b = await r.json();
    return secure(r) && b.status === "ok" && b.release === release && b.advancedEnabled === false;
  });
  await check("exact catalogue revision; 26 operations; payments disabled", "/help.json", 200, {}, async (r) => {
    const b = await r.json();
    return secure(r) && b.release === release && b.operations?.length === 26 && b.payment?.enabled === false;
  });
  for (const path of ["/", "/app"])
    await check(`HTML serves without a redirect: ${path}`, path, 200, {}, async (r) => secure(r) && r.headers.get("content-type")?.includes("text/html") && (await r.text()).includes("PostSteward"));
  for (const path of ["/style.css", "/app.js", "/webmcp.js", "/docs/agent-guide.md", "/llms.txt", "/openapi.json", "/plans.json"])
    await check(`public resource: ${path}`, path, 200, {}, secure);
  await check("unauthenticated session rejected without caching", "/api/session", 401, {}, (r) => r.headers.get("cache-control")?.includes("no-store"));
  await check("forged bearer rejected through D1", "/api/session", 401, { headers: { Authorization: "Bearer invalid-deployment-probe" } }, async (r) => (await r.json()).error?.code === "UNAUTHENTICATED");
  await check("cross-origin session rejected", "/api/session", 403, { headers: { Origin: "https://untrusted.example" } });
  await check("unauthenticated MCP rejected", "/mcp", 401, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ jsonrpc: "2.0", id: 1, method: "tools/list" }) });
  await check("callback without login state rejected", "/auth/callback", 400, {}, async (r) => (await r.json()).error?.code === "LOGIN_STATE_INVALID");
  await check("OIDC login initiation, PKCE, nonce and secure state cookie", "/auth/login", 302, {}, (r) => {
    const url = new URL(r.headers.get("location"));
    const q = url.searchParams, cookie = r.headers.get("set-cookie") || "";
    return url.protocol === "https:" && (c.vars.OIDC_ISSUER !== "https://accounts.google.com" || url.hostname === "accounts.google.com") && q.get("client_id") === c.vars.OIDC_CLIENT_ID && q.get("redirect_uri") === origin + "/auth/callback" && q.get("response_type") === "code" && q.get("scope") === "openid profile email" && q.get("code_challenge_method") === "S256" && Boolean(q.get("state") && q.get("nonce") && q.get("code_challenge")) && cookie.includes("__Host-login=") && cookie.includes("HttpOnly") && cookie.includes("Secure") && cookie.includes("SameSite=Lax") && cookie.includes("Path=/") && r.headers.get("cache-control")?.includes("no-store");
  });
  return { origin, release, observedAt: new Date().toISOString(), readiness, checks, passed: checks.every((x) => x.passed), notVerified: ["completed owner sign-in", "live social publication", "native browser WebMCP", "restore", "payment settlement"] };
}
