import assert from "node:assert/strict";
import test from "node:test";
import { verifyAdvancedInventoryAssets } from "../scripts/staging-assets-check.mjs";

const secureHeaders = {
  "strict-transport-security": "max-age=31536000; includeSubDomains",
  "x-content-type-options": "nosniff",
};

function response(body, contentType, status = 200, extra = {}) {
  return new Response(body, {
    status,
    headers: { "content-type": contentType, ...secureHeaders, ...extra },
  });
}

test("Advanced inventory hosted verifier accepts exact deployed HTML and JS markers", async () => {
  const result = await verifyAdvancedInventoryAssets(
    "https://staging.example/",
    async (url) => {
      if (url.pathname === "/advanced-inventory.html")
        return response(
          "<!doctype html><title>PostSteward Advanced inventory</title>",
          "text/html; charset=utf-8",
        );
      if (url.pathname === "/advanced-inventory.js")
        return response(
          'const x = invoke("automation_inspect");',
          "text/javascript; charset=utf-8",
        );
      throw new Error("unexpected request");
    },
  );
  assert.equal(result.advancedInventory, "deployed");
  assert.equal(result.checks.length, 2);
  assert.ok(result.checks.every((item) => item.secureHeaders));
});

test("Advanced inventory hosted verifier fails closed on redirects, wrong content or missing hardening", async () => {
  await assert.rejects(
    verifyAdvancedInventoryAssets("http://staging.example/", async () => response("", "text/html")),
    /exact HTTPS origin/,
  );
  await assert.rejects(
    verifyAdvancedInventoryAssets("https://staging.example/path", async () => response("", "text/html")),
    /exact HTTPS origin/,
  );
  await assert.rejects(
    verifyAdvancedInventoryAssets("https://staging.example/", async (url) => {
      if (url.pathname.endsWith(".html"))
        return new Response("moved", { status: 302, headers: { location: "/app" } });
      return response('invoke("automation_inspect")', "text/javascript");
    }),
    /returned HTTP 302/,
  );
  await assert.rejects(
    verifyAdvancedInventoryAssets("https://staging.example/", async (url) => {
      if (url.pathname.endsWith(".html"))
        return new Response("PostSteward Advanced inventory", {
          status: 200,
          headers: { "content-type": "text/html" },
        });
      return response('invoke("automation_inspect")', "text/javascript");
    }),
    /security headers/,
  );
});
