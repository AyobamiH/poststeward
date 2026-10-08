import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

async function text(path) {
  return readFile(new URL(`../${path}`, import.meta.url), "utf8");
}

test("public marketing page carries truthful structured data and image semantics", async () => {
  const html = await text("public/index.html");
  assert.match(html, /alt="PostSteward"/);
  assert.match(html, /"@type": "Organization"/);
  assert.match(html, /"@type": "WebSite"/);
  assert.match(html, /"@type": "SoftwareApplication"/);
  assert.match(html, /"logo": "https:\/\/poststeward\.com\/icon-512\.png"/);
  assert.doesNotMatch(html, /"offers"/);
});

test("agent discovery keeps public and private boundaries explicit", async () => {
  const [robots, agents, llms] = await Promise.all([
    text("public/robots.txt"),
    text("public/agents.txt"),
    text("public/llms.txt"),
  ]);

  for (const crawler of [
    "OAI-SearchBot",
    "GPTBot",
    "Claude-SearchBot",
    "ClaudeBot",
    "PerplexityBot",
    "Googlebot",
    "bingbot",
    "Applebot",
  ]) {
    assert.match(robots, new RegExp(`User-agent: ${crawler.replace(/[.*+?^$()|[\\]{}]/g, "\\$&")}`));
  }
  assert.match(robots, /User-agent: \*/);
  for (const path of ["/api/", "/app", "/auth/", "/payments/", "/webhooks/"]) {
    assert.ok(robots.includes(`Disallow: ${path}`));
  }
  assert.match(robots, /Sitemap: https:\/\/poststeward\.com\/sitemap\.xml/);

  assert.match(agents, /Machine operation catalogue/);
  assert.match(agents, /Reservation and provider-request acceptance are not publication proof/);
  assert.match(agents, /Workspace data, credentials, receipts and authenticated routes are not public discovery data/);
  assert.match(llms, /Reservation IDs are not publication proof/);
});

test("production sitemap includes machine discovery paths", async () => {
  const source = await text("src/browser-presentation.ts");
  assert.match(source, /publicDiscoveryPaths/);
  for (const path of ["/agents.txt", "/llms.txt", "/help.json", "/openapi.json"]) {
    assert.ok(source.includes(`"${path}"`));
  }
  assert.match(source, /\.\.\.publicDiscoveryPaths/);
});
