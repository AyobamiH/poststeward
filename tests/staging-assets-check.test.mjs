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

test("Advanced inventory hosted verifier accepts Cloudflare canonical HTML redirect and exact JS markers", async () => {
  const result = await verifyAdvancedInventoryAssets(
    "https://staging.example/",
    async (url) => {
      if (url.pathname === "/advanced-inventory.html")
        return new Response(null, {
          status: 307,
          headers: { location: "/advanced-inventory" },
        });
      if (url.pathname === "/advanced-inventory")
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
  assert.equal(result.checks[0].resolvedPath, "/advanced-inventory");
  assert.ok(result.checks.every((item) => item.secureHeaders));
});

test("Advanced inventory hosted verifier also accepts direct hardened HTML", async () => {
  const result = await verifyAdvancedInventoryAssets(
    "https://staging.example/",
    async (url) => {
      if (url.pathname === "/advanced-inventory.html")
        return response(
          "<!doctype html><title>PostSteward Advanced inventory</title>",
          "text/html; charset=utf-8",
        );
      return response(
        'const x = invoke("automation_inspect");',
        "text/javascript; charset=utf-8",
      );
    },
  );
  assert.equal(result.checks[0].resolvedPath, "/advanced-inventory.html");
});

test("Advanced inventory hosted verifier rejects unsafe redirects and missing hardening", async () => {
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
        return new Response(null, {
          status: 307,
          headers: { location: "https://attacker.invalid/advanced-inventory" },
        });
      return response('invoke("automation_inspect")', "text/javascript");
    }),
    /outside its allowed canonical asset path/,
  );
  await assert.rejects(
    verifyAdvancedInventoryAssets("https://staging.example/", async (url) => {
      if (url.pathname.endsWith(".html"))
        return new Response(null, { status: 307, headers: { location: "/app" } });
      return response('invoke("automation_inspect")', "text/javascript");
    }),
    /outside its allowed canonical asset path/,
  );
  await assert.rejects(
    verifyAdvancedInventoryAssets("https://staging.example/", async (url) => {
      if (url.pathname.endsWith(".html"))
        return new Response("moved", { status: 302, headers: { location: "/advanced-inventory" } });
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
