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
      scriptPath: "dist/edge.js",
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
            data: {
              id: "runtime-post",
              author_id: "test-user",
              text: "Exact approved text",
            },
          });
        throw new Error(
          "Unexpected outbound network request: " + url.origin + url.pathname,
        );
      },
    }),
  );
  try {
    const db = await mf.getD1Database("IDENTITY");
    for (const file of readdirSync("migrations")
      .filter((name) => /^\d+.*\.sql$/.test(name))
      .sort())
      for (const statement of readFileSync("migrations/" + file, "utf8")
        .split(";")
        .map((x) => x.trim())
        .filter(Boolean))
        await db.prepare(statement).run();
    await db
      .prepare("INSERT INTO principals VALUES (?,?,?)")
      .bind(owner.id, owner.workspace, Date.now())
      .run();
    await db
      .prepare("INSERT INTO grants VALUES (?,?,?,?,?,NULL)")
      .bind(
        await digest("test-agent"),
        owner.workspace,
        owner.id,
        JSON.stringify([
          "read",
          "campaign:write",
          "connections",
          "publish",
          "schedule",
          "billing",
        ]),
        Date.now() + 3600000,
      )
      .run();
    await db
      .prepare("INSERT INTO grants VALUES (?,?,?,?,?,NULL)")
      .bind(
        await digest("other-agent"),
        "00000000-0000-4000-8000-000000000002",
        "other",
        JSON.stringify(["read"]),
        Date.now() + 3600000,
      )
      .run();
    const call = (path: string, input?: unknown, token = "test-agent") =>
      mf.dispatchFetch("https://publish.example" + path, {
        method: input === undefined ? "GET" : "POST",
        headers: {
          Authorization: "Bearer " + token,
          "Content-Type": "application/json",
          Accept: "application/json, text/event-stream",
        },
        ...(input !== undefined ? { body: JSON.stringify(input) } : {}),
      });
    const discovery = await mf.dispatchFetch(
      "https://publish.example/help.json",
    );
    assert.equal(discovery.status, 200);
    assert.equal(((await discovery.json()) as any).operations.length, 26);
    const unauth = await mf.dispatchFetch(
      "https://publish.example/api/operations/workspace_status",
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: "{}",
      },
    );
    assert.equal(unauth.status, 401);
    const connected = await call("/api/connections/import", {
      alias: "account",
      provider: "x",
      accessToken: "test-token-only",
      funding: "customer_app",
    });
    assert.equal(
      connected.status,
      200,
      JSON.stringify({ body: await connected.text(), outbound }),
    );
    assert.equal(
      (
        await call("/api/operations/project_put", {
          id: "project",
          name: "Project",
          accounts: ["account"],
          idempotencyKey: "project-001",
        })
      ).status,
      200,
    );
    const created: any = await (
      await call("/api/operations/campaign_create", {
        project: "project",
        text: { account: "Exact approved text" },
        idempotencyKey: "campaign-001",
      })
    ).json();
    assert.ok(created.id);
    const leaked = await call(
      "/api/operations/campaign_get",
      { campaign: created.id },
      "other-agent",
    );
    assert.equal(leaked.status, 404);
    const initialized = await call("/mcp", {
      jsonrpc: "2.0",
      id: 1,
      method: "initialize",
      params: {
        protocolVersion: "2025-03-26",
        capabilities: {},
        clientInfo: { name: "integration-test", version: "1" },
      },
    });
    assert.equal(initialized.status, 200);
    const mcpResult: any = await (
      await call("/mcp", {
        jsonrpc: "2.0",
        id: 2,
        method: "tools/call",
        params: { name: "campaign_get", arguments: { campaign: created.id } },
      })
    ).json();
    assert.equal(mcpResult.result.structuredContent.result.id, created.id);
    const reserved: any = await (
      await call("/api/operations/publish_now", {
        campaign: created.id,
        idempotencyKey: "publish-runtime-001",
      })
    ).json();
    let receipt: any;
    for (let attempt = 0; attempt < 20; attempt++) {
      receipt = await (
        await call("/api/operations/receipt_get", {
          delivery: reserved.deliveries[0].id,
        })
      ).json();
      if (receipt.status === "published_verified") break;
      await new Promise((resolve) => setTimeout(resolve, 100));
    }
    assert.equal(receipt.status, "published_verified", JSON.stringify(receipt));
    assert.equal(writes, 1);
    await call("/mcp", {
      jsonrpc: "2.0",
      id: 3,
      method: "tools/call",
      params: {
        name: "publish_now",
        arguments: {
          campaign: created.id,
          idempotencyKey: "another-runtime-key",
        },
      },
    });
    assert.equal(writes, 1);
    const quote: any = await (
      await call("/api/operations/billing_quote", {
        mode: "pass",
        idempotencyKey: "runtime-quote-001",
      })
    ).json();
    const challenge = await call("/payments/" + quote.id, {});
    assert.equal(challenge.status, 402);
    assert.ok(challenge.headers.get("www-authenticate")?.includes("Payment"));
    const scopeDenied = await call(
      "/api/operations/publish_now",
      { campaign: created.id, idempotencyKey: "publish-001" },
      "other-agent",
    );
    assert.equal(scopeDenied.status, 403);
    const forgedOrigin = await mf.dispatchFetch(
      "https://publish.example/api/operations/workspace_status",
      {
        method: "POST",
        headers: {
          Authorization: "Bearer test-agent",
          Origin: "https://evil.example",
          "Content-Type": "application/json",
        },
        body: "{}",
      },
    );
    assert.equal(forgedOrigin.status, 403);
  } finally {
    await mf.dispose();
  }
});
