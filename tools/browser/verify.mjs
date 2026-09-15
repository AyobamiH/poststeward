/** Real local Worker rendering plus synthetic owner records; no live external effects. */
import assert from "node:assert/strict";
import { createServer } from "node:http";
import { readFile, mkdir, writeFile } from "node:fs/promises";
import { resolve, extname, join } from "node:path";
import { createRequire } from "node:module";
import { chromium } from "playwright";
import {
  Miniflare,
  convertV4MiniflareOptions,
  Response as RuntimeResponse,
} from "miniflare";
import { fixture } from "./fixtures.mjs";

const require = createRequire(import.meta.url);
const root = resolve("public");
const axe = await readFile(require.resolve("axe-core/axe.min.js"), "utf8");
await mkdir("ux-evidence", { recursive: true });
await writeFile("ux-evidence/axe.min.js", axe);

const mime = {
  ".html": "text/html",
  ".js": "text/javascript",
  ".css": "text/css",
  ".svg": "image/svg+xml",
  ".png": "image/png",
  ".ico": "image/x-icon",
  ".json": "application/json",
  ".md": "text/markdown",
  ".txt": "text/plain",
};
async function assets(request) {
  const path = new URL(request.url).pathname;
  const canonical =
    path === "/index.html"
      ? "/"
      : path === "/docs" || path === "/docs/index.html"
        ? "/docs/"
        : path.endsWith(".html") && path !== "/404.html"
          ? path.slice(0, -5)
          : path;
  if (canonical !== path)
    return new RuntimeResponse(null, {
      status: 307,
      headers: { Location: canonical },
    });
  const candidate = path.endsWith("/")
    ? path + "index.html"
    : extname(path)
      ? path
      : path + ".html";
  let filename = resolve(root, "." + candidate);
  if (!filename.startsWith(root + "/"))
    return new RuntimeResponse("Not found", { status: 404 });
  let status = 200;
  let bytes;
  try {
    bytes = await readFile(filename);
  } catch {
    filename = join(root, "404.html");
    bytes = await readFile(filename);
    status = 404;
  }
  return new RuntimeResponse(request.method === "HEAD" ? null : bytes, {
    status,
    headers: {
      "Content-Type": mime[extname(filename)] || "application/octet-stream",
      "Cache-Control": "no-store",
    },
  });
}

let outbound = 0;
const config = JSON.parse(await readFile("wrangler.jsonc", "utf8"));
const entry = config.main.split("/").pop().replace(/\.ts$/, ".js");
const mf = new Miniflare(
  convertV4MiniflareOptions({
    modules: true,
    scriptPath: "dist/" + entry,
    compatibilityDate: config.compatibility_date,
    compatibilityFlags: ["nodejs_compat"],
    bindings: {
      ...config.vars,
      DEPLOY_ENV: "staging",
      PUBLIC_ORIGIN: "https://publish.example",
      RELEASE_SHA: "a".repeat(40),
      ENCRYPTION_KEY: "a".repeat(64),
      OIDC_ISSUER: "https://identity.example",
      OIDC_CLIENT_ID: "test",
      OIDC_CLIENT_SECRET: "test-only",
      ALLOWED_OWNER_EMAILS: "owner@example.com",
    },
    ratelimits: {
      EDGE_LIMITER: {
        namespace_id: "51001",
        simple: { limit: 10000, period: 60 },
      },
      LOGIN_LIMITER: {
        namespace_id: "51002",
        simple: { limit: 10, period: 60 },
      },
    },
    d1Databases: { IDENTITY: "ux-test" },
    durableObjects: { WORKSPACES: { className: "Workspace", useSQLite: true } },
    serviceBindings: { ASSETS: assets },
    outboundService: async () => {
      outbound++;
      throw new Error("Outbound access forbidden in UX fixture");
    },
  }),
);

let origin;
const server = createServer(async (req, res) => {
  try {
    const chunks = [];
    for await (const chunk of req) chunks.push(chunk);
    const headers = { ...req.headers };
    delete headers.host;
    if (headers.origin === origin) headers.origin = "https://publish.example";
    const response = await mf.dispatchFetch(
      "https://publish.example" + req.url,
      {
        method: req.method,
        headers,
        ...(chunks.length ? { body: Buffer.concat(chunks) } : {}),
        redirect: "manual",
      },
    );
    res.writeHead(response.status, Object.fromEntries(response.headers));
    res.end(Buffer.from(await response.arrayBuffer()));
  } catch (error) {
    res.writeHead(500);
    res.end(String(error));
  }
});
await new Promise((done) => server.listen(0, "127.0.0.1", done));
origin = `http://127.0.0.1:${server.address().port}`;

const failures = [];
const checks = [];
const forbidden = [];
const errors = [];
const browser = await chromium.launch({ headless: true });
async function check(name, run) {
  try {
    await run();
    checks.push({ name, passed: true });
  } catch (error) {
    failures.push({ name, error: String(error) });
    checks.push({ name, passed: false });
  }
}
async function audit(page) {
  await page.evaluate(axe);
  const result = await page.evaluate(async () =>
    window.axe.run(document, {
      runOnly: {
        type: "tag",
        values: ["wcag2a", "wcag2aa", "wcag21aa", "wcag22aa"],
      },
    }),
  );
  return result.violations.map((violation) => ({
    id: violation.id,
    nodes: violation.nodes.map((node) => ({
      target: node.target,
      summary: node.failureSummary,
    })),
  }));
}

try {
  await check("API failures stay JSON even when HTML is accepted", async () => {
    for (const path of ["/api/not-a-route", "/mcp", "/payments/not-a-route"]) {
      const response = await fetch(origin + path, {
        headers: { Accept: "text/html" },
      });
      assert.match(response.headers.get("content-type"), /json/);
      assert.ok(response.status >= 400);
    }
  });
  await check("Public 404 preserves status and security headers", async () => {
    const response = await fetch(origin + "/missing-page", {
      headers: { Accept: "text/html" },
    });
    assert.equal(response.status, 404);
    assert.ok(response.headers.get("content-security-policy"));
    assert.ok(response.headers.get("x-request-id"));
    assert.match(await response.text(), /PostSteward/);
  });
  await check(
    "Human callback errors preserve safe browser and JSON representations",
    async () => {
      const html = await fetch(origin + "/auth/callback", {
        headers: { Accept: "text/html" },
      });
      assert.equal(html.status, 400);
      assert.match(html.headers.get("content-type"), /html/);
      assert.match(await html.text(), /Start sign-in again/);
      const json = await fetch(origin + "/auth/callback", {
        headers: { Accept: "application/json" },
      });
      assert.equal(json.status, 400);
      assert.match(json.headers.get("content-type"), /json/);
    },
  );
  await check("Device and sharing assets are real image files", async () => {
    for (const [path, width, height] of [
      ["apple-touch-icon.png", 180, 180],
      ["icon-192.png", 192, 192],
      ["icon-512.png", 512, 512],
      ["og-image.png", 1200, 630],
    ]) {
      const response = await fetch(origin + "/" + path);
      assert.equal(response.status, 200);
      const bytes = Buffer.from(await response.arrayBuffer());
      assert.equal(bytes.subarray(1, 4).toString(), "PNG");
      assert.equal(bytes.readUInt32BE(16), width);
      assert.equal(bytes.readUInt32BE(20), height);
    }
  });

  const paths = [
    "/",
    "/app",
    "/pilot",
    "/advanced-inventory",
    "/lifecycle",
    "/recovery",
    "/docs/",
    "/docs/agent-guide",
    "/docs/operations",
    "/privacy",
    "/terms",
    "/security",
    "/support",
    "/status",
  ];
  for (const [width, scheme] of [
    [1440, "light"],
    [390, "light"],
    [768, "dark"],
    [320, "light"],
  ]) {
    const context = await browser.newContext({
      viewport: { width, height: 1000 },
      colorScheme: scheme,
      reducedMotion: "reduce",
      serviceWorkers: "block",
    });
    await context.route("**/*", async (route) => {
      const request = route.request();
      const url = new URL(request.url());
      if (url.origin !== origin) {
        forbidden.push(request.url());
        return route.abort();
      }
      if (url.pathname.startsWith("/api/")) {
        const method = url.pathname.startsWith("/api/operations/")
          ? "POST"
          : "GET";
        if (
          Object.hasOwn(fixture, url.pathname) &&
          request.method() === method
        )
          return route.fulfill({ json: fixture[url.pathname] });
        forbidden.push(request.method() + " " + url.pathname);
        return route.fulfill({
          status: 403,
          json: { error: { message: "No mutations in browser fixture" } },
        });
      }
      return route.continue();
    });
    const page = await context.newPage();
    page.on("pageerror", (error) => errors.push(String(error)));
    page.on("response", (response) => {
      if (
        /\.(js|css)$/.test(new URL(response.url()).pathname) &&
        response.status() >= 400
      )
        errors.push(`${response.status()} ${response.url()}`);
    });

    for (const path of paths) {
      await check(`${path} ${width} ${scheme}`, async () => {
        const response = await page.goto(origin + path);
        assert.equal(response.status(), 200);
        await page.waitForLoadState("networkidle");
        await page.locator("main").waitFor();
        if (["/app", "/docs/", "/", "/status"].includes(path))
          await page.screenshot({
            path: `ux-evidence/${path.replaceAll("/", "_") || "home"}-${width}-${scheme}.png`,
            fullPage: true,
          });
        assert.equal(await page.locator(".skip-link").count(), 1);
        const dimensions = await page.evaluate(() => ({
          content: document.documentElement.scrollWidth,
          viewport: innerWidth,
        }));
        assert.ok(
          dimensions.content <= dimensions.viewport + 1,
          "Document overflow " + JSON.stringify(dimensions),
        );
        assert.match(response.headers()["x-robots-tag"] || "", /noindex/);
        const violations = await audit(page);
        assert.equal(violations.length, 0, JSON.stringify(violations));

        if (path === "/app") {
          assert.equal(await page.locator("#receipts > .record").count(), 10);
          await page
            .locator("#ux-receipt-filter")
            .selectOption("ambiguous_effect");
          assert.equal(
            await page.locator("#receipts > .record:visible").count(),
            1,
          );
          assert.match(await page.locator("#grants").innerText(), /Expired/);
          assert.equal(await page.locator("#receipts img").count(), 0);
          await page.locator("#ux-receipt-search").fill("no-match-at-all");
          assert.equal(
            await page.locator("#receipts > .record:visible").count(),
            0,
          );
          await page.locator("#ux-receipt-search").fill("");
          await page.locator("#ux-receipt-filter").selectOption("");
          assert.equal(
            await page.locator("#receipts > .record:visible").count(),
            10,
          );
          assert.equal(await page.evaluate(() => window.unsafe), undefined);
        }
        if (path === "/docs/operations") {
          await page.locator("#operation-search").fill("receipt_get");
          assert.ok(await page.locator(".operation-card:visible").count() > 0);
          await page.locator("#operation-search").fill("no-match-at-all");
          assert.equal(
            await page.locator(".operation-card:visible").count(),
            0,
          );
          await page.locator("#operation-search").fill("");
        }
      });
    }

    await check(`Keyboard navigation ${width}`, async () => {
      await page.goto(origin + "/app");
      await page.waitForLoadState("networkidle");
      await page.keyboard.press("Tab");
      assert.equal(
        await page
          .locator(".skip-link")
          .evaluate((element) => document.activeElement === element),
        true,
      );
      await page.keyboard.press("Enter");
      assert.equal(
        await page
          .locator("main")
          .evaluate((element) => document.activeElement === element),
        true,
      );
      if (width <= 768) {
        await page.locator(".product-menu > summary").focus();
        await page.keyboard.press("Enter");
        assert.equal(
          await page.locator(".product-menu").getAttribute("open"),
          "",
        );
      }
    });
    await context.close();
  }

  await check("Signed-out workspace has no populated authority", async () => {
    const context = await browser.newContext();
    await context.route("**/api/**", (route) =>
      route.fulfill({
        status: 401,
        json: {
          error: {
            code: "UNAUTHENTICATED",
            message: "Sign in to inspect this workspace.",
          },
        },
      }),
    );
    const page = await context.newPage();
    await page.goto(origin + "/app");
    await page.waitForLoadState("networkidle");
    assert.equal(await page.locator("#receipts .record").count(), 0);
    assert.match(await page.locator("#session-notice").innerText(), /Sign in/);
    await page.screenshot({
      path: "ux-evidence/workspace-signed-out.png",
      fullPage: true,
    });
    await context.close();
  });

  await check("Status outage is not an empty or healthy ledger", async () => {
    const context = await browser.newContext();
    await context.route("**/readiness.json", (route) =>
      route.fulfill({
        status: 503,
        json: { error: { message: "Synthetic outage" } },
      }),
    );
    const page = await context.newPage();
    await page.goto(origin + "/status");
    await page.waitForLoadState("networkidle");
    assert.match(await page.locator("#status-note").innerText(), /unavailable/i);
    assert.equal(await page.locator("#release-gates .status-row").count(), 0);
    await context.close();
  });

  await check("No JavaScript errors or unexpected outbound requests", async () => {
    assert.deepEqual(errors, []);
    assert.deepEqual(forbidden, []);
    assert.equal(outbound, 0);
  });
} finally {
  await browser.close();
  server.close();
  await mf.dispose();
  await writeFile(
    "ux-evidence/report.json",
    JSON.stringify(
      {
        synthetic: true,
        evidenceBoundary:
          "Local compiled Worker and synthetic owner records. Not live provider, billing, recovery, pixel-baseline or assistive-technology acceptance.",
        checks,
        failures,
        errors,
        forbidden,
        outbound,
      },
      null,
      2,
    ),
  );
}
console.log(
  JSON.stringify({ checks: checks.length, failures: failures.length, errors, forbidden }),
);
if (failures.length) {
  console.error(JSON.stringify(failures, null, 2));
  process.exitCode = 1;
}
