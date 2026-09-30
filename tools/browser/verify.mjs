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
      ["social/poststeward-v2.png", 1200, 630],
    ]) {
      const response = await fetch(origin + "/" + path);
      assert.equal(response.status, 200);
      const bytes = Buffer.from(await response.arrayBuffer());
      assert.match(response.headers.get("content-type"), /image\/png/);
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
    "/docs/install",
    "/docs/agent-guide",
    "/docs/operations",
    "/privacy",
    "/terms",
    "/security",
    "/support",
    "/status",
  ];
  await check("Initial-HTML social previews are fixed, unique and private-data safe on every entry point", async () => {
    const legacy = await fetch(origin + "/og-image.svg?private=PRIVATE_PREVIEW_SENTINEL", {redirect:"manual"});
    assert.equal(legacy.status, 301);
    assert.equal(legacy.headers.get("location"), "/social/poststeward-v2.png");
    for (const path of paths) {
      const response = await fetch(origin + path + "?workspace=PRIVATE_PREVIEW_SENTINEL&access_token=PRIVATE_PREVIEW_SENTINEL");
      assert.equal(response.status, 200);
      const html = await response.text();
      const head = html.match(/<head[^>]*>([\s\S]*?)<\/head>/i)?.[1];
      assert.ok(head);
      assert.doesNotMatch(head, /PRIVATE_PREVIEW_SENTINEL|product_threads|owner@example\.com|og-image\.svg/);
      for (const key of ["og:title", "og:description", "og:url", "og:image", "twitter:card", "twitter:title", "twitter:description", "twitter:image"]) {
        assert.equal((head.match(new RegExp('(?:name|property)="' + key + '"', 'g')) || []).length, 1, path + " " + key);
      }
      assert.ok(head.includes('property="og:url" content="https://publish.example' + path + '"'));
      assert.ok(head.includes('property="og:image" content="https://publish.example/social/poststeward-v2.png"'));
      assert.match(response.headers.get("x-robots-tag"), /noindex/);
    }
  });
  for (const [width, scheme] of [
    [1440, "light"],
    [375, "light"],
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
        if (["/app", "/docs/", "/", "/status", "/advanced-inventory", "/lifecycle", "/recovery", "/docs/install", "/docs/agent-guide", "/docs/operations", "/support", "/privacy", "/terms", "/security", "/pilot"].includes(path))
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
          const providerGeometry = await page.locator(".ux-provider-card").evaluateAll(cards => cards.map(card => ({width:card.getBoundingClientRect().width, scroll:card.scrollWidth, children:[...card.querySelectorAll("*")].map(child=>({right:child.getBoundingClientRect().right,parentRight:card.getBoundingClientRect().right}))})));
          assert.ok(providerGeometry.every(card=>card.scroll <= card.width + 1 && card.children.every(child=>child.right<=child.parentRight+1)), "Provider content must stay inside its card");
          assert.match(await page.locator("#advanced-heading").innerText(), /GBP price to be confirmed/);
          assert.match(await page.locator("#runtime-executor-status").textContent(), /Executor hosted · generation 1/);
          assert.equal(await page.locator("#runtime-use-local").isDisabled(), true);
          assert.equal(await page.locator("#runtime-use-hosted").isDisabled(), true);
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
      await page.waitForFunction(() => document.activeElement.tagName === 'MAIN');
      assert.equal(
        await page
          .locator("main")
          .evaluate((element) => document.activeElement === element),
        true,
      );
      if (width <= 768) {
        await page.locator(".product-menu > summary").focus();
        await page.keyboard.press("Enter");
        await page.waitForFunction(() => document.querySelector('.product-menu').open);
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
    assert.equal(await page.locator('#workspace-content').isVisible(), false);
    assert.equal(await page.locator('#campaign').isVisible(), false);
    await page.screenshot({
      path: "ux-evidence/workspace-signed-out.png",
      fullPage: true,
    });
    await context.close();
  });

  async function ownerContext(overrides = {}) {
    const context = await browser.newContext({viewport:{width:375,height:900}});
    await context.route('**/api/**', async route => {
      const path = new URL(route.request().url()).pathname;
      if (Object.hasOwn(overrides,path)) return typeof overrides[path] === 'function' ? overrides[path](route) : route.fulfill({json:overrides[path]});
      if (Object.hasOwn(fixture,path)) return route.fulfill({json:fixture[path]});
      forbidden.push(route.request().method() + ' ' + path);
      return route.fulfill({status:403,json:{error:{message:'No external effects in synthetic checks'}}});
    });
    return context;
  }
  await check('Empty workspace gives an actionable account-first path', async () => {
    const context = await ownerContext({'/api/operations/accounts_list':[], '/api/operations/projects_list':[], '/api/operations/receipts_list':[]});
    const page = await context.newPage(); await page.goto(origin+'/app'); await page.waitForLoadState('networkidle');
    assert.match(await page.locator('#workspace-next-step').innerText(), /Connect your first social account/);
    await page.locator('#workspace-next-step a').click();
    await page.waitForFunction(()=>document.activeElement.id === 'accounts-heading');
    assert.equal(new URL(page.url()).hash, '#destinations');
    assert.equal(await page.locator('#accounts-heading').evaluate(el=>el===document.activeElement),true);
    assert.equal(await page.locator('#runtime-settings').getAttribute('open'), null);
    await page.screenshot({path:'ux-evidence/workspace-empty.png',fullPage:true}); await context.close();
  });
  await check('Direct local-settings links open disclosure, focus target and survive refresh', async () => {
    const context = await ownerContext(); const page = await context.newPage();
    await page.goto(origin+'/app#runtime-authority-panel'); await page.waitForLoadState('networkidle');
    await page.waitForFunction(()=>document.activeElement.id === 'runtime-authority-heading');
    assert.equal(await page.locator('#runtime-settings').getAttribute('open'),'');
    assert.equal(await page.locator('#runtime-authority-heading').evaluate(el=>el===document.activeElement),true);
    await page.reload(); await page.waitForLoadState('networkidle');
    assert.equal(await page.locator('#runtime-settings').getAttribute('open'),'');
    await page.locator('.product-menu > summary').click();
    await page.locator('.product-menu-panel a[href="/app#evidence-panel"]').click();
    await page.waitForFunction(()=>!document.querySelector('.product-menu').open);
    assert.equal(await page.locator('.product-menu').getAttribute('open'),null);
    assert.equal(new URL(page.url()).hash,'#evidence-panel');
    await page.goBack(); await page.waitForLoadState('networkidle');
    assert.equal(new URL(page.url()).hash,'#runtime-authority-panel');
    await context.close();
  });
  await check('Loaded workspace expires safely and offers sign-in without stale forms', async () => {
    const context = await ownerContext(); const page = await context.newPage();
    await page.goto(origin+'/app'); await page.waitForLoadState('networkidle');
    await page.route('**/api/operations/workspace_status',route=>route.fulfill({status:401,json:{error:{message:'Synthetic expired session',code:'UNAUTHENTICATED'}}}));
    await Promise.all([page.waitForResponse(response=>new URL(response.url()).pathname==='/api/operations/workspace_status' && response.status()===401), page.locator('#refresh').click()]);
    await page.waitForFunction(()=>document.getElementById('workspace-content').hidden);
    assert.equal(await page.locator('#workspace-content').isVisible(),false);
    assert.match(await page.locator('#session-notice').innerText(), /session expired.*Sign in/s);
    assert.equal(await page.locator('#pause').isVisible(),false);
    await page.screenshot({path:'ux-evidence/workspace-expired.png',fullPage:true}); await context.close();
  });
  await check('A failed initial read remains recoverable and never becomes an empty success', async () => {
    let failing=true;
    const context = await ownerContext({'/api/operations/projects_list':route=>failing?route.fulfill({status:503,json:{error:{message:'Synthetic interrupted read'}}}):route.fulfill({json:fixture['/api/operations/projects_list']})});
    const page=await context.newPage();await page.goto(origin+'/app');await page.waitForLoadState('networkidle');
    assert.equal(await page.locator('#workspace-content').isVisible(),false);
    assert.match(await page.locator('#session-notice').innerText(), /could not be loaded/);
    failing=false;await page.locator('#refresh').click();await page.waitForLoadState('networkidle');
    await page.waitForFunction(()=>!document.getElementById('workspace-content').hidden,{},{timeout:5000}).catch(async error => {throw new Error(String(error)+' '+await page.locator('#session-notice').innerText()+' '+await page.locator('#result').textContent());});
    assert.equal(await page.locator('#workspace-content').isVisible(),true);
    await context.close();
  });
  await check('Expiry during a secondary status read cannot reveal stale owner controls', async () => {
    const context=await ownerContext({'/api/connections/oauth/status':route=>route.fulfill({status:401,json:{error:{message:'Synthetic late expiry',code:'UNAUTHENTICATED'}}})});
    const page=await context.newPage();await page.goto(origin+'/app');await page.waitForLoadState('networkidle');
    assert.equal(await page.locator('#workspace-content').isVisible(),false);
    assert.match(await page.locator('#session-notice').innerText(), /session expired.*Sign in/s);
    await context.close();
  });
  await check('Repeated refresh while a read is pending causes one request batch', async () => {
    const context=await ownerContext();const page=await context.newPage();await page.goto(origin+'/app');await page.waitForLoadState('networkidle');
    let calls=0, release;
    const pending=new Promise(resolve=>{release=resolve;});
    await page.route('**/api/operations/workspace_status',async route=>{calls++;await pending;await route.fulfill({json:fixture['/api/operations/workspace_status']});});
    await page.locator('#refresh').focus();
    await page.evaluate(()=>{document.getElementById('refresh').click();document.getElementById('refresh').click();});
    await page.waitForFunction(()=>document.getElementById('refresh').getAttribute('aria-disabled') === 'true');
    assert.equal(await page.locator('#refresh').getAttribute('aria-disabled'),'true');
    release();await page.waitForLoadState('networkidle');assert.equal(calls,1);
    await context.close();
  });
  await check('Human task links target existing sections and mobile guide/trust navigation is available', async () => {
    const context=await ownerContext(); const page=await context.newPage();
    const targets=new Map();
    for(const path of ['/app','/docs/','/docs/install','/docs/agent-guide','/docs/operations','/privacy','/terms','/security','/support','/status']) {
      await page.goto(origin+path);await page.waitForLoadState('networkidle');
      targets.set(path,await page.locator('[id]').evaluateAll(nodes=>nodes.map(el=>el.getAttribute('id'))));
      if(path.startsWith('/docs/')) {
        const menu=page.locator('.docs-mobile-menu');
        await menu.locator('summary').focus();await page.keyboard.press('Enter');
        await page.waitForFunction(() => document.querySelector('.docs-mobile-menu').open);
        const links=menu.locator('nav a');assert.ok(await links.count()>0);
        for(const link of await links.all()) assert.equal(await link.isVisible(),true,`${path}: expanded docs link hidden`);
        const summaryBox=await menu.locator('summary').boundingBox();const panelBox=await menu.locator('nav').boundingBox();
        assert.ok(summaryBox.height>=44,`${path}: docs toggle touch target too small`);
        assert.ok(panelBox.y>=summaryBox.y+summaryBox.height,`${path}: menu covers its toggle`);
        assert.ok(panelBox.x>=0&&panelBox.x+panelBox.width<=375,`${path}: menu overflows viewport`);
        await page.keyboard.press('Escape');assert.equal(await menu.getAttribute('open'),null);
        assert.equal(await menu.locator('summary').evaluate(el=>el===document.activeElement),true);
      }
      if(['/privacy','/terms','/security','/support','/status'].includes(path)) assert.equal(await page.locator('.site-menu summary').isVisible(),true);
    }
    for(const path of ['/app','/docs/']) {
      await page.goto(origin+path);await page.waitForLoadState('networkidle');
      const links=await page.locator('a[href]').evaluateAll(nodes=>nodes.map(el=>el.href));
      for(const href of links) {const url=new URL(href);if(url.origin===origin && url.hash && targets.has(url.pathname)) assert.ok(targets.get(url.pathname).includes(decodeURIComponent(url.hash.slice(1))),href);}
    }
    await context.close();
  });

  await check('Short mobile menus clear their toggle and every task link is keyboard reachable', async () => {
    const context = await ownerContext();const page = await context.newPage();
    for(const height of [600,225]) {
      await page.setViewportSize({width:360,height});
      for(const path of ['/','/app','/docs/','/privacy']) {
        await page.goto(origin+path);await page.waitForLoadState('networkidle');
        const menu=page.locator('.site-menu,.product-menu,.docs-mobile-menu').first();
        await menu.locator('summary').focus();await page.keyboard.press('Enter');await page.waitForFunction(()=>document.querySelector('.site-menu[open],.product-menu[open],.docs-mobile-menu[open]'));
        const bounds=await menu.evaluate(e=>{const a=e.querySelector('summary').getBoundingClientRect(),p=e.querySelector('nav,.site-menu-panel').getBoundingClientRect();return {below:p.top>=a.bottom,inside:p.left>=0&&p.right<=innerWidth+1&&p.bottom<=innerHeight+1};});
        assert.equal(bounds.below,true,`${path}: menu obscures its summary`);assert.equal(bounds.inside,true,`${path}: menu escapes viewport`);
        const links=menu.locator('a');for(let i=0;i<await links.count();i++) {await page.keyboard.press('Tab');const focused=await links.nth(i).evaluate(e=>{const r=e.getBoundingClientRect(),p=e.closest('nav,.site-menu-panel').getBoundingClientRect();return e===document.activeElement&&r.top>=p.top-1&&r.bottom<=p.bottom+1;});assert.equal(focused,true,`${path}: task ${i} unreachable`);}
        await page.keyboard.press('Escape');assert.equal(await menu.locator('summary').evaluate(e=>document.activeElement===e),true);
      }
    }await context.close();
  });
  await check('Installation command, action and prerequisites stay connected and copy exact bytes', async () => {
    const context=await browser.newContext({viewport:{width:1440,height:600}});await context.grantPermissions(['clipboard-read','clipboard-write']);const page=await context.newPage();await page.goto(origin+'/');await page.waitForLoadState('networkidle');
    const expected="curl -fsSL --proto '=https' --tlsv1.2 https://poststeward.com/install.sh | bash";
    assert.equal(await page.locator('#install-command').innerText(),expected);
    assert.equal(await page.locator('#install-runtime [data-copy]').count(),1);assert.match(await page.locator('#install-runtime').innerText(),/Python 3.10/);assert.match(await page.locator('.control-map').innerText(),/EXAMPLE PUBLISHING FLOW/);
    const alignment=await page.evaluate(()=>Math.abs(document.querySelector('.hero-copy').getBoundingClientRect().top-document.querySelector('.control-map').getBoundingClientRect().top));assert.ok(alignment<=1);
    await page.locator('[data-copy="install-command"]').click();assert.equal(await page.evaluate(()=>navigator.clipboard.readText()),expected);assert.match(await page.locator('#install-feedback').innerText(),/copied/i);
    await context.close();
  });
  await check('Reflow and doubled text preserve all route widths with accessible local scrolling', async () => {
    const context=await ownerContext();const page=await context.newPage();
    for(const [width,height,double] of [[320,225,false],[640,450,false],[360,600,true]]) {
      await page.setViewportSize({width,height});
      for(const path of ['/','/docs/','/docs/install','/docs/agent-guide','/docs/operations','/privacy','/terms','/security','/support','/status','/app','/advanced-inventory','/lifecycle','/recovery','/pilot','/auth/callback','/missing-layout-audit-page']) {
        await page.goto(origin+path);await page.waitForLoadState('networkidle');
        if(double)await page.evaluate(()=>{const values=[...document.querySelectorAll('body *')].map(e=>{const s=getComputedStyle(e);return[e,parseFloat(s.fontSize),parseFloat(s.lineHeight)];});for(const [e,font,line]of values){e.style.fontSize=font*2+'px';if(Number.isFinite(line))e.style.lineHeight=line*2+'px';}});
        assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1),true,`${path} ${width} doubled=${double}`);
        assert.deepEqual(await audit(page),[],`${path} ${width} doubled=${double}`);
      }
    }await context.close();
  });
  await check('Long owner records wrap and disclosed information remains available beside usable forms', async () => {
    const accounts=fixture['/api/operations/accounts_list'].map(a=>({...a,alias:'synthetic_'+('long_account_').repeat(14),identity:{...a.identity,username:'SYNTHETIC_東京_'+('username').repeat(25)}}));
    const context=await ownerContext({'/api/operations/accounts_list':accounts});const page=await context.newPage();
    for(const width of [360,1024,1440]) {
      await page.setViewportSize({width,height:600});await page.goto(origin+'/app');await page.waitForLoadState('networkidle');
      assert.equal(await page.locator('#project-select').isVisible(),true);assert.equal(await page.locator('.project-library').getAttribute('open'),null);
      await page.locator('#accounts details').evaluateAll(nodes=>nodes.forEach(e=>e.open=true));await page.locator('.project-library summary').click();
      assert.equal(await page.locator('#projects .record').count(),fixture['/api/operations/projects_list'].length);assert.match(await page.locator('#accounts').innerText(),/Stable author ID/);
      assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1),true);
      await page.locator('#publishing').evaluate(e=>e.scrollIntoView({block:'start'}));const firstInput=page.locator('#project input').first();await firstInput.focus();assert.equal(await firstInput.evaluate(e=>{const r=e.getBoundingClientRect(),h=document.querySelector('header').getBoundingClientRect();return r.top>=h.bottom&&r.right<=innerWidth&&r.height>=44;}),true);
    }await context.close();
  });

  await check('Short desktop sidebar and hash targets preserve visible keyboard focus', async () => {
    const context=await ownerContext();const page=await context.newPage();await page.setViewportSize({width:1024,height:600});await page.goto(origin+'/app');await page.waitForLoadState('networkidle');
    const last=page.locator('.product-nav a').last();await last.focus();assert.equal(await last.evaluate(e=>{const r=e.getBoundingClientRect();return document.activeElement===e&&r.top>=0&&r.bottom<=innerHeight;}),true);
    await page.locator('.product-nav a[href="/app#publishing"]').click();await page.waitForFunction(()=>document.activeElement.id==='publishing-heading');assert.equal(await page.locator('#publishing-heading').evaluate(e=>e.getBoundingClientRect().top>=document.querySelector('header').getBoundingClientRect().bottom),true);await context.close();
  });
  await check('Campaign review success and lengthy validation failure stay adjacent and preserve exact copy', async () => {
    const text='Synthetic approved copy 東京 café. '+('https://example.invalid/path/').repeat(35),calls=[];
    for(const failure of [false,true]) {
      const context=await ownerContext({
        '/api/operations/campaign_create':async r=>{calls.push('synthetic campaign create');return r.fulfill({json:{id:'SYNTHETIC-CAMPAIGN',text:{fixture_x:text}}});},
        '/api/operations/campaign_validate':async r=>{calls.push('synthetic validation');return r.fulfill({status:failure?422:200,json:failure?{error:{message:'Synthetic validation failure. '+('Review the exact destination and approved text before trying again. ').repeat(25)}}:{valid:true}});},
      });const page=await context.newPage();
      for(const width of [360,1024,1440]) {
        await page.setViewportSize({width,height:600});await page.goto(origin+'/app');await page.waitForLoadState('networkidle');
        await page.locator('#campaign textarea').fill(text);await page.locator('#campaign button').click();await page.waitForFunction(()=>!document.getElementById('campaign').querySelector('button').disabled);
        await page.waitForFunction(expected=>document.getElementById('result').textContent.includes(expected),failure?'Synthetic validation failure':'Campaign stored and validated');
        assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1),true);assert.equal(await page.locator('#delivery').isVisible(),!failure);
        if(!failure)assert.equal(await page.locator('#campaign-preview').textContent(),'fixture_x\n'+text);
        assert.equal(await page.locator('#result').evaluate(e=>getComputedStyle(e).position),'static');assert.deepEqual(await audit(page),[]);
        await page.locator('#result').scrollIntoViewIfNeeded();await page.screenshot({path:`ux-evidence/layout-campaign-${failure?'error':'review'}-${width}.png`,fullPage:true});
      }await context.close();
    }assert.equal(calls.length,12); // Synthetic responses only; delivery is never submitted.
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
    await page.screenshot({path:"ux-evidence/status-outage.png",fullPage:true});
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
