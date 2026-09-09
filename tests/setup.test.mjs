import { test } from "node:test";
import assert from "node:assert/strict";
import { inspectStaging } from "../scripts/inspect-staging.mjs";
const account = "a".repeat(32),
  database = "11111111-1111-4111-8111-111111111111";
test("staging inspection discovers existing resources with GET only and never reports token values", async () => {
  const calls = [];
  const result = await inspectStaging(
    {
      CLOUDFLARE_API_TOKEN: "test-only-secret",
      HAS_OIDC_CLIENT_SECRET: "true",
    },
    async (url, options) => {
      calls.push(url);
      assert.equal(options.method, "GET");
      assert.equal(options.redirect, "error");
      assert.ok(url.startsWith("https://api.cloudflare.com/client/v4/"));
      const p = new URL(url).pathname;
      let value;
      if (p === "/client/v4/accounts") value = [{ id: account }];
      else if (p.endsWith("/workers/subdomain"))
        value = { subdomain: "test-account" };
      else if (p.endsWith("/d1/database"))
        value = [{ uuid: database, name: "poststeward-identity-staging" }];
      else return new Response("not found", { status: 404 });
      return Response.json({ success: true, result: value });
    },
  );
  assert.equal(result.discovered.D1_ID, database);
  assert.equal(result.discovered.WORKERS_SUBDOMAIN, "test-account");
  assert.equal(result.discovered.workerExists, false);
  assert.equal(result.configured.OIDC_CLIENT_SECRET, true);
  assert.ok(!JSON.stringify(result).includes("test-only-secret"));
  assert.equal(calls.length, 4);
});
test("inspection rejects ambiguous accounts and redacts unexpected upstream errors", async () => {
  const env = { CLOUDFLARE_API_TOKEN: "sensitive-test-token" };
  const multiple = await inspectStaging(env, async () =>
    Response.json({
      success: true,
      result: [{ id: account }, { id: "b".repeat(32) }],
    }),
  );
  assert.match(multiple.issues[0], /exactly one account/);
  const failed = await inspectStaging(env, async () => {
    throw new Error("sensitive-test-token");
  });
  assert.ok(!JSON.stringify(failed).includes("sensitive-test-token"));
  const missing = await inspectStaging({}, async () => {
    throw new Error("Must not call without credentials");
  });
  assert.equal(missing.configured.CLOUDFLARE_API_TOKEN, false);
});
