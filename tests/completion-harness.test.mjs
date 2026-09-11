import assert from "node:assert/strict";
import test from "node:test";
import {
  checkAgentGrant,
  checkCrossTenantIsolation,
  checkReadiness,
  checkRevokedGrant,
} from "../scripts/hosted-acceptance.mjs";
import { evaluateCapacity } from "../scripts/capacity-calibration.mjs";
import { evaluateRulesets } from "../scripts/github-main-protection-check.mjs";
import { evaluateEdgeEvidence } from "../scripts/production-edge-check.mjs";

const json = (body, status = 200, headers = {}) =>
  new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json", ...headers },
  });

test("readiness acceptance requires Threads-first restricted staging", async () => {
  const result = await checkReadiness("https://staging.example", async () =>
    json({
      release: "a".repeat(40),
      access: { signupMode: "restricted" },
      providers: {
        threads: { oauth: true },
        x: { oauth: false },
        linkedin: { oauth: false },
      },
      payments: { advancedEnabled: false, mppEnabled: false },
    }),
  );
  assert.equal(result.threadsOAuth, true);
  await assert.rejects(
    checkReadiness("https://staging.example", async () =>
      json({
        access: { signupMode: "restricted" },
        providers: {
          threads: { oauth: false },
          x: { oauth: false },
          linkedin: { oauth: false },
        },
        payments: { advancedEnabled: false, mppEnabled: false },
      }),
    ),
    /Threads OAuth is not configured/,
  );
});

test("agent acceptance proves HTTP and remote MCP resolve to one release without logging the workspace", async () => {
  const workspace = "00000000-0000-4000-8000-000000000001";
  const release = "b".repeat(40);
  const send = async (url, init) => {
    if (String(url).endsWith("/api/operations/workspace_status"))
      return json({ workspace, release });
    const body = JSON.parse(init.body);
    if (body.method === "initialize") return json({ jsonrpc: "2.0", id: 1, result: {} });
    return json({
      jsonrpc: "2.0",
      id: 2,
      result: { structuredContent: { result: { workspace, release } } },
    });
  };
  const result = await checkAgentGrant(
    "https://staging.example",
    "agent-token-1234567890",
    send,
  );
  assert.equal(result.http, "accepted");
  assert.equal(result.remoteMcp, "accepted");
  assert.notEqual(result.workspaceFingerprint, workspace);
  assert.equal(result.workspaceFingerprint.length, 16);
});

test("revoked acceptance requires denial on both HTTP and MCP", async () => {
  let calls = 0;
  const result = await checkRevokedGrant(
    "https://staging.example",
    "agent-token-1234567890",
    async () => {
      calls++;
      return json({ error: { code: "UNAUTHENTICATED" } }, 401);
    },
  );
  assert.equal(calls, 2);
  assert.deepEqual(result, { http: "denied", remoteMcp: "denied" });
});

test("cross-tenant harness rejects object-ID swapping and hostile Origin replay", async () => {
  const a = "workspace-a";
  const b = "workspace-b";
  const tokenA = "agent-token-a-123456789";
  const tokenB = "agent-token-b-123456789";
  const send = async (url, init) => {
    const auth = new Headers(init.headers).get("authorization");
    if (new Headers(init.headers).get("origin") === "https://attacker.invalid")
      return json({ error: { code: "ORIGIN_REJECTED" } }, 403);
    const body = JSON.parse(init.body);
    if (String(url).endsWith("/workspace_status"))
      return json({ workspace: auth?.endsWith(tokenA) ? a : b, release: "c".repeat(40) });
    if (String(url).endsWith("/receipt_get"))
      return auth?.endsWith(tokenA)
        ? json({ id: body.delivery, status: "published_verified" })
        : json({ error: { code: "NOT_FOUND" } }, 404);
    throw new Error("unexpected request");
  };
  const result = await checkCrossTenantIsolation(
    "https://staging.example",
    tokenA,
    tokenB,
    "delivery-1",
    send,
  );
  assert.equal(result.objectIdSwap, "denied");
  assert.equal(result.hostileOriginReplay, "denied");
  assert.notEqual(result.workspaceA, result.workspaceB);
});

test("capacity calibration demands at least thirty percent headroom", () => {
  const safe = evaluateCapacity({
    workspaces: 100,
    peakRecordsPerWorkspace: 5000,
    peakBytesPerWorkspace: 4 * 1024 * 1024,
    maxValueBytes: 32 * 1024,
    peakDailyDeliveries: 5,
    peakActiveSchedules: 20,
    peakProfiles: 3,
    requestsPerWorkspaceDay: 500,
    providerPollsPerWorkspaceDay: 24,
  });
  assert.equal(safe.verdict, "calibrated_with_30pct_headroom");
  const tight = evaluateCapacity({
    workspaces: 1,
    peakRecordsPerWorkspace: 19000,
    peakBytesPerWorkspace: 4 * 1024 * 1024,
    maxValueBytes: 32 * 1024,
    peakDailyDeliveries: 5,
    peakActiveSchedules: 20,
    peakProfiles: 3,
    requestsPerWorkspaceDay: 500,
    providerPollsPerWorkspaceDay: 24,
  });
  assert.equal(tight.verdict, "insufficient_headroom");
});

test("GitHub main protection evaluator requires review, Verify, deletion and force-push protection", () => {
  const result = evaluateRulesets([
    {
      enforcement: "active",
      target: "branch",
      conditions: { ref_name: { include: ["~DEFAULT_BRANCH"] } },
      rules: [
        { type: "deletion" },
        { type: "non_fast_forward" },
        {
          type: "pull_request",
          parameters: { required_approving_review_count: 1 },
        },
        {
          type: "required_status_checks",
          parameters: { required_status_checks: [{ context: "Verify" }] },
        },
      ],
    },
  ]);
  assert.equal(result.ready, true);
  assert.equal(evaluateRulesets([]).ready, false);
});

test("production edge evaluator requires custom security and rate evidence", () => {
  const headers = new Headers({
    "strict-transport-security": "max-age=31536000; includeSubDomains",
    "content-security-policy": "default-src 'self'",
  });
  const result = evaluateEdgeEvidence({
    readiness: {
      release: "d".repeat(40),
      access: { signupMode: "restricted" },
      payments: { advancedEnabled: false, mppEnabled: false },
    },
    headers,
    dnsRecords: [{ proxied: true }],
    customFirewallRules: [{ id: "waf" }],
    managedFirewallRules: [],
    rateLimitRules: [{ id: "rate" }],
  });
  assert.equal(result.ready, true);
  assert.equal(
    evaluateEdgeEvidence({
      readiness: { release: "development", access: {}, payments: {} },
      headers: new Headers(),
      dnsRecords: [],
      customFirewallRules: [],
      managedFirewallRules: [],
      rateLimitRules: [],
    }).ready,
    false,
  );
});
