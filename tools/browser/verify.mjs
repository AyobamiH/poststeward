/** Synthetic local browser acceptance. No provider credentials or live writes. */
import assert from 'node:assert/strict';
import { createServer } from 'node:http';
import { readFile, mkdir, writeFile } from 'node:fs/promises';
import { resolve, extname, join } from 'node:path';
import { createRequire } from 'node:module';
import { chromium } from 'playwright';
import { Miniflare, convertV4MiniflareOptions, Response as RuntimeResponse } from 'miniflare';
const require = createRequire(import.meta.url);
const axe = await readFile(require.resolve('axe-core/axe.min.js'), 'utf8');
await mkdir('ux-evidence', { recursive: true });
await writeFile('ux-evidence/axe.min.js', axe);
const root = resolve('public');
const mime = { '.html': 'text/html', '.js': 'text/javascript', '.css': 'text/css', '.svg': 'image/svg+xml', '.png': 'image/png', '.ico': 'image/x-icon', '.json': 'application/json', '.md': 'text/markdown', '.txt': 'text/plain' };
async function assets(request) {
  const path = new URL(request.url).pathname;
  const canonical = path === '/index.html' ? '/' : path === '/docs' || path === '/docs/index.html' ? '/docs/' : path.endsWith('.html') && path !== '/404.html' ? path.slice(0, -5) : path;
  if (canonical !== path) return new RuntimeResponse(null, { status: 307, headers: { Location: canonical } });
  const candidate = path.endsWith('/') ? path + 'index.html' : extname(path) ? path : path + '.html';
  let filename = resolve(root, '.' + candidate);
  if (!filename.startsWith(root + '/')) return new RuntimeResponse('Not found', { status: 404 });
  let status = 200, bytes;
  try { bytes = await readFile(filename); } catch { filename = join(root, '404.html'); bytes = await readFile(filename); status = 404; }
  return new RuntimeResponse(request.method === 'HEAD' ? null : bytes, { status, headers: { 'Content-Type': mime[extname(filename)] || 'application/octet-stream', 'Cache-Control': 'no-store' } });
}
let outbound = 0;
const config = JSON.parse(await readFile('wrangler.jsonc', 'utf8'));
const entry = config.main.split('/').pop().replace(/\.ts$/, '.js');
const mf = new Miniflare(convertV4MiniflareOptions({
  modules: true, scriptPath: 'dist/' + entry, compatibilityDate: config.compatibility_date, compatibilityFlags: ['nodejs_compat'],
  bindings: { ...config.vars, DEPLOY_ENV: 'staging', PUBLIC_ORIGIN: 'https://publish.example', RELEASE_SHA: 'a'.repeat(40), ENCRYPTION_KEY: 'a'.repeat(64), OIDC_ISSUER: 'https://identity.example', OIDC_CLIENT_ID: 'test', OIDC_CLIENT_SECRET: 'test-only', ALLOWED_OWNER_EMAILS: 'owner@example.com' },
  ratelimits: { EDGE_LIMITER: { namespace_id: '51001', simple: { limit: 10000, period: 60 } }, LOGIN_LIMITER: { namespace_id: '51002', simple: { limit: 10, period: 60 } } },
  d1Databases: { IDENTITY: 'ux-test' }, durableObjects: { WORKSPACES: { className: 'Workspace', useSQLite: true } },
  serviceBindings: { ASSETS: assets }, outboundService: async () => { outbound++; throw new Error('Outbound access forbidden in UX fixture'); },
}));
const server = createServer(async (req, res) => {
  try {
    const chunks = []; for await (const chunk of req) chunks.push(chunk);
    const headers = { ...req.headers }; delete headers.host;
    const response = await mf.dispatchFetch('https://publish.example' + req.url, { method: req.method, headers, ...(chunks.length ? { body: Buffer.concat(chunks) } : {}), redirect: 'manual' });
    res.writeHead(response.status, Object.fromEntries(response.headers));
    res.end(Buffer.from(await response.arrayBuffer()));
  } catch (error) { res.writeHead(500); res.end(String(error)); }
});
await new Promise((done) => server.listen(0, '127.0.0.1', done));
const origin = `http://127.0.0.1:${server.address().port}`;
const now = Date.now();
const accounts = ['x', 'threads', 'linkedin'].map((provider, i) => ({ alias: 'fixture_' + provider, provider, active: true, identity: { username: 'fixture_' + provider, id: 'stable-' + i }, verifiedAt: now - 60000, capabilities: { oauth: true, readback: provider !== 'linkedin', refresh: provider !== 'linkedin' } }));
const statuses = ['scheduled','executing','waiting_container','published_verified','published_unverified','ambiguous_effect','failed','drift_blocked','cancelled','future_unknown'];
const receipts = statuses.map((status, i) => ({ id: 'receipt-' + i, provider: accounts[i % 3].provider, account: accounts[i % 3].alias, status, text: i === 5 ? '<img src=x onerror="window.unsafe=true"> reviewed text ' + 'long'.repeat(70) : 'Synthetic reviewed copy ' + i, dueAt: now + i * 1000, updatedAt: now, createdAt: now - 60000, timezone: 'UTC', reason: i === 5 ? 'Provider outcome uncertain. Do not republish.' : undefined, ...(status.startsWith('published') ? { postId: 'post-' + i } : {}) }));
const profiles = [{ id: 'fixture-profile', family: 'development', enabled: false, repository: 'fixture/repository', branch: 'main', path: 'README.md', intervalMinutes: 15, minSpacingMinutes: 60, nextRun: now + 3600000, lastCheck: now - 60000 }];
const fixture = {
  '/api/session': { workspace: 'SYNTHETIC-UX-FIXTURE', actor: 'synthetic-owner', csrf: 'synthetic', scopes: ['admin'] },
  '/api/grants': [{ actor: 'Expired fixture', scopes: '["read"]', expires_at: now - 1000 }, { actor: 'Active fixture', scopes: '["read"]', expires_at: now + 3600000 }, { actor: 'Revoked fixture', scopes: '["publish"]', expires_at: now + 3600000, revoked_at: now - 1000 }],
  '/api/connections/oauth/status': { providers: Object.fromEntries(['x', 'threads', 'linkedin'].map((provider) => [provider, { available: provider === 'threads', capabilities: { publish: { state: 'connection_required' }, readback: { state: provider === 'linkedin' ? 'external_approval_required' : 'connection_required' } } }])), connections: [] },
  '/api/recovery/status': { control: { quarantined: true }, effects: { uncertain: 1, containerUncertain: 0 }, plan: { id: 'fixture-plan', state: 'prepared', targetTime: now - 50000, reason: 'Synthetic plan, not an executed restore' } },
  '/api/recovery/checkpoints': { checkpoints: [{ id: 'fixture-checkpoint', capturedAt: now - 60000, source: 'automatic', release: 'a'.repeat(40), rootWrite: 'next', stateDigest: 'b'.repeat(64) }] },
  '/api/lifecycle/status': { deletion: null },
  '/api/pilot/status': { owner: { id: 'fixture-proof', workspace: 'SYNTHETIC-UX-FIXTURE', authenticatedAt: now, expiresAt: now + 60000 }, record: null, completed: false },
  '/api/sources/github/status': { configuration: { available: false }, installations: [], repositories: [] },
  '/api/operations/workspace_status': { plan: 'free', publishingPaused: false },
  '/api/operations/accounts_list': accounts,
  '/api/operations/projects_list': Array.from({ length: 10 }, (_, i) => ({ id: 'project-' + i, name: 'Synthetic project ' + i, accounts: accounts.map((a) => a.alias) })),
  '/api/operations/receipts_list': receipts,
  '/api/operations/automation_inspect': { profiles, deliveries: receipts.slice(0, 3) },
  '/api/operations/billing_status': { sandbox: true, methods: { checkout: { available: false } }, portalAvailable: false },
};
const failures = [], checks = [], forbidden = [], errors = [];
const browser = await chromium.launch({ headless: true, ...(process.env.CHROMIUM_PATH ? { executablePath: process.env.CHROMIUM_PATH } : {}) });
async function check(name, run) {
  try { await run(); checks.push({ name, passed: true }); } catch (error) { failures.push({ name, error: String(error) }); checks.push({ name, passed: false }); }
}
try {
  await check('API failures stay JSON even when HTML is accepted', async () => {
    for (const path of ['/api/not-a-route', '/mcp', '/payments/not-a-route']) {
      const response = await fetch(origin + path, { headers: { Accept: 'text/html' } });
      assert.match(response.headers.get('content-type'), /json/); assert.ok(response.status >= 400);
    }
  });
  await check('Public 404 preserves status and security headers', async () => {
    const response = await fetch(origin + '/missing-page', { headers: { Accept: 'text/html' } });
    assert.equal(response.status, 404); assert.ok(response.headers.get('content-security-policy')); assert.ok(response.headers.get('x-request-id')); assert.match(await response.text(), /PostSteward/);
  });
  await check('Device and sharing assets are real image files', async () => {
    for (const [path, width, height] of [['apple-touch-icon.png',180,180], ['icon-192.png',192,192], ['icon-512.png',512,512], ['og-image.png',1200,630]]) {
      const response = await fetch(origin + '/' + path); assert.equal(response.status, 200);
      const bytes = Buffer.from(await response.arrayBuffer()); assert.equal(bytes.subarray(1,4).toString(), 'PNG'); assert.equal(bytes.readUInt32BE(16), width); assert.equal(bytes.readUInt32BE(20), height);
    }
  });
  const paths = ['/', '/app', '/pilot', '/advanced-inventory', '/lifecycle', '/recovery', '/docs/', '/docs/agent-guide', '/docs/operations', '/privacy', '/terms', '/security', '/support', '/status'];
  for (const [width, scheme] of [[1440,'light'], [390,'light'], [768,'dark'], [320,'light']]) {
    const context = await browser.newContext({ viewport: { width, height: 1000 }, colorScheme: scheme, reducedMotion: 'reduce', serviceWorkers: 'block' });
    await context.route('**/*', async (route) => {
      const req = route.request(), url = new URL(req.url());
      if (url.origin !== origin) { forbidden.push(req.url()); return route.abort(); }
      if (url.pathname.startsWith('/api/')) {
        if (Object.hasOwn(fixture, url.pathname) && (req.method() === 'GET' || url.pathname.startsWith('/api/operations/'))) return route.fulfill({ json: fixture[url.pathname] });
        forbidden.push(req.method() + ' ' + url.pathname); return route.fulfill({ status: 403, json: { error: { message: 'No mutations in browser fixture' } } });
      }
      return route.continue();
    });
    const page = await context.newPage(); page.on('pageerror', (error) => errors.push(String(error)));
    for (const path of paths) {
      const label = `${path} ${width} ${scheme}`;
      await check(label, async () => {
        const response = await page.goto(origin + path); assert.equal(response.status(), 200);
        await page.waitForLoadState('networkidle');
        await page.locator('main').waitFor();
        assert.ok(await page.locator('.skip-link').count() === 1, 'One skip link');
        const overflow = await page.evaluate(() => ({ content: document.documentElement.scrollWidth, viewport: innerWidth }));
        assert.ok(overflow.content <= overflow.viewport + 1, 'Document overflow ' + JSON.stringify(overflow));
        assert.match(response.headers()['x-robots-tag'] || '', /noindex/, 'Staging is not indexed');
        await page.evaluate(axe);
        const result = await page.evaluate(async () => window.axe.run(document, { runOnly: { type: 'tag', values: ['wcag2a','wcag2aa','wcag21aa'] } }));
        const violations = result.violations.map((v) => ({ id: v.id, nodes: v.nodes.map((n) => ({ target: n.target, summary: n.failureSummary })) }));
        assert.equal(violations.length, 0, JSON.stringify(violations));
        if (path === '/app') {
          assert.equal(await page.locator('#receipts > .record').count(), 10);
          await page.locator('#ux-receipt-filter').selectOption('ambiguous_effect');
          assert.equal(await page.locator('#receipts > .record:visible').count(), 1);
          assert.match(await page.locator('#grants').innerText(), /Expired/);
          assert.equal(await page.locator('#receipts img').count(), 0);
          await page.locator('#ux-receipt-search').fill('no-match-at-all');
          assert.equal(await page.locator('#receipts > .record:visible').count(), 0);
          await page.locator('#ux-receipt-search').fill(''); await page.locator('#ux-receipt-filter').selectOption('');
          assert.equal(await page.locator('#receipts > .record:visible').count(), 10);
          assert.equal(await page.evaluate(() => window.unsafe), undefined);
        }
        if (path === '/docs/operations') {
          await page.locator('#operation-search').fill('receipt_get');
          assert.ok(await page.locator('.operation-card:visible').count() > 0);
          await page.locator('#operation-search').fill('no-match-at-all');
          assert.equal(await page.locator('.operation-card:visible').count(), 0);
          await page.locator('#operation-search').fill('');
        }
        if (path === '/app' || path === '/docs/' || path === '/' || path === '/status') await page.screenshot({ path: `ux-evidence/${path.replaceAll('/','_') || 'home'}-${width}-${scheme}.png`, fullPage: true });
      });
    }
    await check(`Keyboard navigation ${width}`, async () => {
      await page.goto(origin + '/app'); await page.waitForLoadState('networkidle');
      await page.keyboard.press('Tab'); assert.equal(await page.locator('.skip-link').evaluate((el) => document.activeElement === el), true);
      await page.keyboard.press('Enter'); assert.equal(await page.locator('main').evaluate((el) => document.activeElement === el), true);
      if (width <= 768) { await page.locator('.product-menu > summary').focus(); await page.keyboard.press('Enter'); assert.equal(await page.locator('.product-menu').getAttribute('open'), ''); }
    });
    await context.close();
  }
  await check('No JavaScript errors or unexpected outbound requests', async () => { assert.deepEqual(errors, []); assert.deepEqual(forbidden, []); assert.equal(outbound, 0); });
} finally {
  await browser.close(); server.close(); await mf.dispose();
  await writeFile('ux-evidence/report.json', JSON.stringify({ synthetic: true, evidenceBoundary: 'Local runtime and synthetic owner records; not live provider, billing, recovery or assistive-technology acceptance.', checks, failures, errors, forbidden, outbound }, null, 2));
}
console.log(JSON.stringify({ checks: checks.length, failures: failures.length, errors, forbidden }));
if (failures.length) { console.error(JSON.stringify(failures, null, 2)); process.exitCode = 1; }
