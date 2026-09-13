import assert from "node:assert/strict";
import test from "node:test";
import { inspectLifecycleBoundary } from "../scripts/lifecycle-smoke.mjs";

const origin = "https://staging.example";
const headers = {
  "X-Frame-Options": "DENY",
  "Strict-Transport-Security": "max-age=31536000",
  "Cache-Control": "no-store",
};

test("hosted lifecycle checks never authenticate or submit a valid deletion", async () => {
  let deleteBodies = [];
  const report = await inspectLifecycleBoundary(origin, async (url, init) => {
    const path = new URL(url).pathname;
    assert.equal(init.redirect, "manual");
    if (path === "/lifecycle")
      return new Response(
        '<html><h2>Delete this PostSteward workspace</h2><input id="confirmation"></html>',
        { headers: { ...headers, "Content-Type": "text/html" } },
      );
    if (path === "/lifecycle.js") return new Response("script", { headers });
    if (path === "/api/lifecycle/delete") deleteBodies.push(init.body);
    return Response.json(
      { error: { code: "DENIED" } },
      {
        status: init.headers?.Origin === "https://untrusted.example" ? 403 : 401,
        headers,
      },
    );
  });
  assert.equal(report.passed, true);
  assert.equal(report.checks.length, 5);
  assert.equal(report.deletionAttempted, false);
  assert.equal(deleteBodies.length, 2);
  assert.ok(deleteBodies.every((body) => !String(body).includes("00000000-")));
});

test("unexpectedly public lifecycle mutation fails acceptance", async () => {
  const report = await inspectLifecycleBoundary(origin, async (url, init) => {
    const path = new URL(url).pathname;
    if (path === "/lifecycle")
      return new Response(
        '<html><h2>Delete this PostSteward workspace</h2><input id="confirmation"></html>',
        { headers: { ...headers, "Content-Type": "text/html" } },
      );
    if (path === "/lifecycle.js") return new Response("script", { headers });
    if (path === "/api/lifecycle/delete")
      return Response.json({}, { status: 200, headers });
    return Response.json({}, { status: 401, headers });
  });
  assert.equal(report.passed, false);
});
