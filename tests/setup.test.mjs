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
test("missing token still reports callback, all missing credentials and misplaced-token presence", async () => {
  const result = await inspectStaging(
    {
      CLOUDFLARE_ACCOUNT_ID: account,
      D1_ID: database,
      WORKERS_SUBDOMAIN: "test-account",
      HAS_TOKEN_VARIABLE: "true",
      HAS_TOKEN_ALIAS_SECRET: "true",
    },
    async () => {
      assert.fail("No requests without token");
    },
  );
  assert.equal(
    result.oidcRedirectUri,
    "https://poststeward-staging.test-account.workers.dev/auth/callback",
  );
  assert.equal(result.validSyntax.D1_ID, true);
  assert.equal(result.deployed, false);
  assert.deepEqual(result.discovered, {});
  assert.ok(result.issues.some((x) => x.includes("saved as a variable")));
  for (const name of [
    "ENCRYPTION_KEY",
    "OIDC_CLIENT_SECRET",
    "ALLOWED_OWNER_EMAILS",
  ])
    assert.ok(result.issues.some((x) => x.includes(name)));
});
test("invalid configuration never becomes a reported callback URL", async () => {
  const result = await inspectStaging({
    CLOUDFLARE_ACCOUNT_ID: "placeholder",
    D1_ID: "00000000-0000-0000-0000-000000000000",
    WORKERS_SUBDOMAIN: "example.workers.dev",
    APP_ORIGIN: "https://user:do-not-report@example.com",
  });
  assert.equal(result.validSyntax.D1_ID, false);
  assert.equal(result.validSyntax.WORKERS_SUBDOMAIN, false);
  assert.equal(result.oidcRedirectUri, undefined);
  assert.ok(!JSON.stringify(result).includes("do-not-report"));
});
