import assert from "node:assert/strict";
import test from "node:test";
import { Miniflare, convertV4MiniflareOptions } from "miniflare";
import { environment } from "./helpers.ts";

test("real Workers Assets resolves the workspace and canonical redirects without looping", async () => {
  const mf = new Miniflare(convertV4MiniflareOptions({
    modules: true,
    scriptPath: "dist/edge.js",
    compatibilityDate: "2026-09-09",
    compatibilityFlags: ["nodejs_compat"],
    bindings: Object.fromEntries(Object.entries(environment).filter(([, value]) => typeof value === "string")) as Record<string, string>,
    ratelimits: {
      EDGE_LIMITER: { namespace_id: "51001", simple: { limit: 120, period: 60 } },
      LOGIN_LIMITER: { namespace_id: "51002", simple: { limit: 10, period: 60 } },
    },
    d1Databases: { IDENTITY: "asset-identity-test" },
    durableObjects: { WORKSPACES: { className: "Workspace", useSQLite: true } },
    assets: {
      directory: "public",
      binding: "ASSETS",
      routerConfig: { has_user_worker: true, invoke_user_worker_ahead_of_assets: true },
      assetConfig: { html_handling: "auto-trailing-slash" },
    },
  }));
  try {
    for (const path of ["/", "/app", "/app?tab=connections", "/lifecycle"]) {
      const response = await mf.dispatchFetch("https://publish.example" + path, { redirect: "manual" });
      assert.equal(response.status, 200, path);
      assert.equal(response.headers.get("location"), null);
      assert.match(response.headers.get("content-type") || "", /text\/html/);
      assert.match(await response.text(), /PostSteward/);
      assert.equal(response.headers.get("x-frame-options"), "DENY");
    }
    const canonical = await mf.dispatchFetch("https://publish.example/app.html", { redirect: "manual" });
    assert.equal(canonical.status, 307);
    const target = new URL(canonical.headers.get("location") || "", "https://publish.example");
    assert.equal(target.pathname, "/app");
    const resolved = await mf.dispatchFetch(target.toString(), { redirect: "manual" });
    assert.equal(resolved.status, 200);
    assert.match(await resolved.text(), /PostSteward/);
    const unauthenticated = await mf.dispatchFetch("https://publish.example/api/session");
    assert.equal(unauthenticated.status, 401);
  } finally {
    await mf.dispose();
  }
});
