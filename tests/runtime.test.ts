import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync, readdirSync } from "node:fs";
import {
  Miniflare,
  convertV4MiniflareOptions,
  Response as RuntimeResponse,
} from "miniflare";
import { digest } from "../src/common.ts";
import { environment, owner } from "./helpers.ts";
test("real Workers runtime serves discovery, isolates tenants and runs HTTP/MCP against SQLite objects", async () => {
  const outbound: string[] = [];
  let writes = 0;
  const mf = new Miniflare(
    convertV4MiniflareOptions({
      modules: true,
      scriptPath: "dist/rotation-worker.js",
      compatibilityDate: "2026-09-09",
      compatibilityFlags: ["nodejs_compat"],
      bindings: {
        ...(Object.fromEntries(
          Object.entries(environment).filter(([, v]) => typeof v === "string"),
        ) as Record<string, string>),
        ADVANCED_ENABLED: "true",
        MPP_ENABLED: "true",
        STRIPE_SECRET_KEY: "sk_test_not_real",
        STRIPE_PRICE_ID: "price_test",
        STRIPE_PROFILE_ID: "profile_test",
        MPP_SECRET: "test-mpp-key-".repeat(5),
      },
      ratelimits: {
        EDGE_LIMITER: {
          namespace_id: "51001",
          simple: { limit: 120, period: 60 },
        },
        LOGIN_LIMITER: {
          namespace_id: "51002",
          simple: { limit: 10, period: 60 },
        },
      },
      d1Databases: { IDENTITY: "identity-test" },
      durableObjects: {
        WORKSPACES: { className: "Workspace", useSQLite: true },
      },
      serviceBindings: { ASSETS: async () => new RuntimeResponse("asset") },
      outboundService: async (request) => {
        const url = new URL(request.url);
        outbound.push(request.url);
        if (url.hostname === "api.x.com" && url.pathname === "/2/users/me")
          return RuntimeResponse.json({
            data: { id: "test-user", username: "test" },
          });
        if (
          url.hostname === "api.x.com" &&
          url.pathname === "/2/tweets" &&
          request.method === "POST"
        ) {
          writes++;
          return RuntimeResponse.json({ data: { id: "runtime-post" } });
        }
        if (
          url.hostname === "api.x.com" &&
          url.pathname === "/2/tweets/runtime-post"
        )
          return RuntimeResponse.json({
            data: { id: "runtime-post", text: "runtime text", author_id: "test-user" },
          });
        throw new Error("Unexpected network access: " + request.url);
      },
    }),
  );
  const db = await mf.getD1Database("IDENTITY");
  for (const file of readdirSync("migrations").filter((name) => /^\d+.*\.sql$/.test(name)).sort())
    for (const statement of readFileSync("migrations/" + file, "utf8").split(";").map((part) => part.trim()).filter(Boolean))
      await db.prepare(statement).run();
  try {
    const discovery = await mf.dispatchFetch("https://publish.example/.well-known/poststeward.json");
    assert.equal(discovery.status, 200);
    const discoveryBody: any = await discovery.json();
    assert.equal(discoveryBody.product, "PostSteward");

    const workspace = owner.workspace;
    const otherWorkspace = "00000000-0000-4000-8000-0000000000bb";
    const actor = { ...owner, workspace };
    const otherActor = { ...owner, workspace: otherWorkspace, id: "other-owner" };
    const invoke = async (who: any, name: string, input: any) => {
      const stub = await mf.getDurableObjectStub("WORKSPACES", who.workspace);
      return stub.fetch("https://workspace.internal/operation", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ workspace: who.workspace, actor: who, name, input }),
      });
    };

    const connect = await invoke(actor, "connection_import", {
      alias: "runtime-x",
      provider: "x",
      token: "runtime-token",
    });
    assert.equal(connect.status, 200);
    const otherStatus = await invoke(otherActor, "workspace_status", {});
    assert.equal(otherStatus.status, 200);
    const other: any = await otherStatus.json();
    assert.deepEqual(other.accounts, []);

    const project = await invoke(actor, "project_create", {
      idempotencyKey: "runtime-project",
      name: "runtime-project",
      accounts: ["runtime-x"],
    });
    assert.equal(project.status, 200);
    const campaign = await invoke(actor, "campaign_create", {
      idempotencyKey: "runtime-campaign",
      project: "runtime-project",
      text: { x: "runtime text" },
    });
    assert.equal(campaign.status, 200);
    const campaignBody: any = await campaign.json();
    const scheduled = await invoke(actor, "schedule_create", {
      idempotencyKey: "runtime-schedule",
      campaign: campaignBody.id,
      account: "runtime-x",
      when: new Date(Date.now() + 60_000).toISOString(),
      timezone: "UTC",
    });
    assert.equal(scheduled.status, 200);
    const scheduleBody: any = await scheduled.json();
    assert.ok(scheduleBody.id);

    const hash = await digest("runtime-agent-token");
    await db.prepare(
      "INSERT INTO grants(id,workspace,actor,token_hash,scopes,expires_at,created_at) VALUES (?,?,?,?,?,?,?)",
    )
      .bind(
        "runtime-grant",
        workspace,
        "runtime-agent",
        hash,
        JSON.stringify(["read"]),
        Date.now() + 60_000,
        Date.now(),
      )
      .run();
    const http = await mf.dispatchFetch("https://publish.example/api/operations/workspace_status", {
      method: "POST",
      headers: {
        Authorization: "Bearer runtime-agent-token",
        "Content-Type": "application/json",
      },
      body: "{}",
    });
    assert.equal(http.status, 200);
    const status: any = await http.json();
    assert.equal(status.workspace, workspace);

    const mcp = await mf.dispatchFetch("https://publish.example/mcp", {
      method: "POST",
      headers: {
        Authorization: "Bearer runtime-agent-token",
        "Content-Type": "application/json",
        Accept: "application/json, text/event-stream",
      },
      body: JSON.stringify({
        jsonrpc: "2.0",
        id: 1,
        method: "tools/call",
        params: { name: "workspace_status", arguments: {} },
      }),
    });
    assert.equal(mcp.status, 200);
    const mcpText = await mcp.text();
    assert.match(mcpText, new RegExp(workspace));

    assert.equal(writes, 0);
    assert.ok(outbound.some((url) => url.includes("api.x.com/2/users/me")));
    assert.equal(outbound.some((url) => url.includes("/2/tweets")), false);
  } finally {
    await mf.dispose();
  }
});
