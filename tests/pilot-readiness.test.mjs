import assert from "node:assert/strict";
import test from "node:test";
import { readFileSync } from "node:fs";
import { inspectPilot } from "../scripts/pilot-smoke.mjs";
const origin = "https://pilot.example";
const headers = { "X-Frame-Options": "DENY", "Strict-Transport-Security": "max-age=31536000", "Cache-Control": "no-store" };

test("new static assets tolerate only bounded 404 propagation without replaying POSTs", async () => {
  let css = 0, posts = 0, waits = 0;
  const report = await inspectPilot(origin, async (url, init) => {
    assert.equal(init.redirect, "manual");
    if (new URL(url).pathname === "/pilot.css") {
      css++; return new Response("", { status: css < 3 ? 404 : 200, headers });
    }
    if (init.method === "POST") posts++;
    return new Response("", { status: 403, headers });
  }, async (ms) => { waits++; assert.equal(ms, 2000); });
  const check = report.checks.find((c) => c.name === "acceptance asset /pilot.css");
  assert.equal(check.passed, true); assert.equal(check.attempts, 3);
  assert.equal(css, 3); assert.equal(waits, 2); assert.equal(posts, 4);
});

test("permanent asset absence still fails and redirects/access rejection are never retried", async () => {
  for (const status of [404, 307, 401]) {
    let css = 0;
    const report = await inspectPilot(origin, async (url) => {
      const code = new URL(url).pathname === "/pilot.css" ? (css++, status) : 403;
      return new Response("", { status: code, headers });
    }, async () => {});
    assert.equal(css, status === 404 ? 5 : 1);
    assert.equal(report.checks.find((c) => c.name === "acceptance asset /pilot.css").passed, false);
    assert.equal(report.passed, false);
  }
});

test("hosted acceptance reruns require an explicit runtime revision and cannot redeploy or access deployment credentials", () => {
  const workflow = readFileSync(".github/workflows/verify-hosted-pilot.yml", "utf8");
  assert.match(workflow, /RUNTIME_REVISION:/);
  assert.match(workflow, /GITHUB_SHA: process.env.RUNTIME_REVISION/);
  assert.match(workflow, /github.ref == 'refs\/heads\/main'/);
  assert.match(workflow, /github.actor == 'AyobamiH'/);
  assert.doesNotMatch(workflow, /secrets\.|secrets:|CLOUDFLARE_API_TOKEN|npm run deploy|wrangler deploy|migrations apply|contents: write|actions: write/);
  assert.match(workflow, /run: node scripts\/smoke.mjs/);
  assert.match(workflow, /run: node scripts\/pilot-smoke.mjs/);
  assert.match(workflow, /run: node scripts\/pilot-browser-smoke.mjs/);
});
