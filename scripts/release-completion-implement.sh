#!/usr/bin/env bash
set -euo pipefail

cat > migrations/0007_provider_oauth_return_path.sql <<'EOF'
ALTER TABLE provider_oauth_states ADD COLUMN return_path TEXT NOT NULL DEFAULT '/pilot' CHECK (return_path IN ('/pilot','/app'));
EOF

cat > src/readiness.ts <<'EOF'
import { oauthConfiguration } from "./provider-oauth.ts";
import type { Env } from "./types.ts";

export function releaseReadiness(env: Env) {
  const providers = oauthConfiguration(env);
  return {
    release: env.RELEASE_SHA,
    environment: env.DEPLOY_ENV || "unknown",
    access: {
      signupMode: env.SIGNUP_MODE,
      publicSignup: env.SIGNUP_MODE === "public",
    },
    providers: Object.fromEntries(
      Object.entries(providers).map(([provider, value]) => [
        provider,
        { oauth: value.available, readback: value.readback },
      ]),
    ),
    payments: {
      advancedEnabled: env.ADVANCED_ENABLED === "true",
      mppEnabled: env.MPP_ENABLED === "true",
      checkoutConfigured: Boolean(env.STRIPE_SECRET_KEY && env.STRIPE_PRICE_ID),
      mppConfigured: Boolean(env.STRIPE_PROFILE_ID && env.MPP_SECRET),
    },
    recovery: {
      externalEffectLedger: true,
      ownerPitr: true,
      destructiveActions: "owner_only",
    },
    evidenceStillExternal: [
      "real_owner_consent",
      "real_provider_grant",
      "controlled_live_publication",
      "native_browser_webmcp",
      "real_pitr_rehearsal",
      "payment_settlement",
      "public_release",
    ],
  };
}
EOF

cat > src/recovery-confirmation.ts <<'EOF'
import { requireValue } from "./common.ts";

export type RecoveryConfirmationAction = "RESTORE" | "UNDO" | "RESUME";

export function demandRecoveryConfirmation(
  action: RecoveryConfirmationAction,
  workspace: string,
  value: unknown,
) {
  requireValue(
    value === `${action} ${workspace}`,
    "RECOVERY_CONFIRMATION_REQUIRED",
    `Type ${action} followed by the exact workspace ID before this recovery action.`,
    409,
  );
}
EOF

cat > scripts/assert-merge-provenance.mjs <<'EOF'
export async function assertMergeProvenance(env = process.env, send = fetch) {
  if (env.REQUIRE_MERGED_PR !== "true") return { required: false };
  const repository = env.GITHUB_REPOSITORY || "";
  const sha = env.GITHUB_SHA || "";
  const token = env.GITHUB_TOKEN || "";
  if (
    !/^[A-Za-z0-9_.-]+\/[A-Za-z0-9_.-]+$/.test(repository) ||
    !/^[a-f0-9]{40}$/.test(sha) ||
    token.length < 10
  )
    throw new Error("Merged-PR provenance inputs are incomplete.");
  let response;
  try {
    response = await send(
      `https://api.github.com/repos/${repository}/commits/${sha}/pulls`,
      {
        redirect: "manual",
        signal: AbortSignal.timeout(10000),
        headers: {
          Accept: "application/vnd.github+json",
          Authorization: `Bearer ${token}`,
          "X-GitHub-Api-Version": "2022-11-28",
          "User-Agent": "poststeward-release-gate",
        },
      },
    );
  } catch {
    throw new Error(
      "Merged-PR provenance could not be verified; deployment is blocked.",
    );
  }
  if (!response.ok) {
    void response.body?.cancel().catch(() => {});
    throw new Error(
      `Merged-PR provenance API returned HTTP ${response.status}; deployment is blocked.`,
    );
  }
  const rows = await response.json();
  if (!Array.isArray(rows))
    throw new Error(
      "Merged-PR provenance response was invalid; deployment is blocked.",
    );
  const match = rows.find(
    (pr) =>
      pr?.merged_at &&
      pr?.base?.ref === "main" &&
      pr?.merge_commit_sha === sha,
  );
  if (!match)
    throw new Error(
      "Staging deployment requires this exact main revision to be the merge commit of a reviewed pull request.",
    );
  return { required: true, pullRequest: match.number, sha };
}

if (import.meta.url === `file://${process.argv[1]}`) {
  const result = await assertMergeProvenance();
  console.log(`POSTSTEWARD_MERGE_PROVENANCE ${JSON.stringify(result)}`);
}
EOF

python3 - <<'PY'
from pathlib import Path

p = Path('src/types.ts')
s = p.read_text()
old = 'export interface Env {\n  EDGE_LIMITER: RateLimit;'
new = 'export interface Env {\n  DEPLOY_ENV?: string;\n  EDGE_LIMITER: RateLimit;'
assert old in s
p.write_text(s.replace(old, new, 1))

p = Path('src/provider-oauth.ts')
s = p.read_text()
old = 'type OAuthProvider = (typeof providerNames)[number];\nconst aliasPattern'
new = 'type OAuthProvider = (typeof providerNames)[number];\ntype OAuthReturnPath = "/pilot" | "/app";\nconst aliasPattern'
assert old in s
s = s.replace(old, new, 1)
old = '  verifier?: string;\n  expires_at: number;'
new = '  verifier?: string;\n  return_path: OAuthReturnPath;\n  expires_at: number;'
assert old in s
s = s.replace(old, new, 1)
old = '''function stateCookie(value: string, maxAge: number) {
  return `${stateCookieName}=${value}; Path=/; HttpOnly; Secure; SameSite=Lax; Max-Age=${maxAge}`;
}
'''
new = old + '''function returnPath(value: unknown): OAuthReturnPath {
  requireValue(
    value === undefined || value === "/pilot" || value === "/app",
    "OAUTH_RETURN_NOT_ALLOWED",
    "Provider authorization can return only to the workspace or controlled-publication page.",
    400,
  );
  return value === "/app" ? "/app" : "/pilot";
}
function providerRedirect(path: OAuthReturnPath, query: string) {
  return new Response(null, {
    status: 302,
    headers: {
      Location: `${path}?${query}`,
      "Set-Cookie": stateCookie("", 0),
      "Cache-Control": "no-store",
    },
  });
}
'''
assert old in s
s = s.replace(old, new, 1)
old = '  const input = (await request.json()) as { alias?: unknown };\n  requireValue(\n    typeof input.alias === "string" && aliasPattern.test(input.alias),'
new = '  const input = (await request.json()) as { alias?: unknown; returnPath?: unknown };\n  const destination = returnPath(input.returnPath);\n  requireValue(\n    typeof input.alias === "string" && aliasPattern.test(input.alias),'
assert old in s
s = s.replace(old, new, 1)
old = '''    "INSERT INTO provider_oauth_states(state_hash,session_hash,workspace,actor,provider,alias,verifier,expires_at,created_at) SELECT ?,?,?,?,?,?,?,?,? WHERE (SELECT count(*) FROM provider_oauth_states) < 10000",
  )
    .bind(
      stateHash,
      await digest(session),
      auth.actor.workspace,
      auth.actor.id,
      provider,
      input.alias,
      verifier || null,
      Date.now() + 600000,
      Date.now(),
    )'''
new = '''    "INSERT INTO provider_oauth_states(state_hash,session_hash,workspace,actor,provider,alias,verifier,return_path,expires_at,created_at) SELECT ?,?,?,?,?,?,?,?,?,? WHERE (SELECT count(*) FROM provider_oauth_states) < 10000",
  )
    .bind(
      stateHash,
      await digest(session),
      auth.actor.workspace,
      auth.actor.id,
      provider,
      input.alias,
      verifier || null,
      destination,
      Date.now() + 600000,
      Date.now(),
    )'''
assert old in s
s = s.replace(old, new, 1)
old = '''      callback: c.callback,
      scopes: c.scopes,
    },'''
new = '''      callback: c.callback,
      scopes: c.scopes,
      returnPath: destination,
    },'''
assert old in s
s = s.replace(old, new, 1)
old = '): Promise<{ alias: string; token?: OAuthTokenSet; response?: Response }> {'
new = '): Promise<{ alias: string; returnPath: OAuthReturnPath; token?: OAuthTokenSet; response?: Response }> {'
assert old in s
s = s.replace(old, new, 1)
old = '''  if (url.searchParams.has("error"))
    return {
      alias: row.alias,
      response: new Response(null, {
        status: 302,
        headers: {
          Location: "/pilot?connection=denied",
          "Set-Cookie": stateCookie("", 0),
          "Cache-Control": "no-store",
        },
      }),
    };'''
new = '''  const destination = returnPath(row.return_path);
  if (url.searchParams.has("error"))
    return {
      alias: row.alias,
      returnPath: destination,
      response: providerRedirect(destination, "connection=denied"),
    };'''
assert old in s
s = s.replace(old, new, 1)
old = '''  return {
    alias: row.alias,
    token: await exchangeCode(env, provider, code, row.verifier),
  };
}

export function providerOAuthSuccess(provider: OAuthProvider) {
  return new Response(null, {
    status: 302,
    headers: {
      Location: `/pilot?connected=${provider}`,
      "Set-Cookie": stateCookie("", 0),
      "Cache-Control": "no-store",
    },
  });
}'''
new = '''  return {
    alias: row.alias,
    returnPath: destination,
    token: await exchangeCode(env, provider, code, row.verifier),
  };
}

export function providerOAuthSuccess(
  provider: OAuthProvider,
  destination: OAuthReturnPath = "/pilot",
) {
  return providerRedirect(returnPath(destination), `connected=${provider}`);
}'''
assert old in s
p.write_text(s.replace(old, new, 1))

p = Path('src/worker.ts')
s = p.read_text()
old = 'import { mcp } from "./mcp.ts";\nimport { SQLiteStore } from "./store.ts";'
new = 'import { mcp } from "./mcp.ts";\nimport { releaseReadiness } from "./readiness.ts";\nimport { demandRecoveryConfirmation } from "./recovery-confirmation.ts";\nimport { SQLiteStore } from "./store.ts";'
assert old in s
s = s.replace(old, new, 1)
s = s.replace('''const recoveryExecuteSchema = z.strictObject({
  ...recoveryActionBase,
  execute: z.literal(true),
});''', '''const recoveryExecuteSchema = z.strictObject({
  ...recoveryActionBase,
  execute: z.literal(true),
  confirmation: z.string().max(200),
});''')
s = s.replace('''const recoveryResumeSchema = z.strictObject({
  ...recoveryActionBase,
  resume: z.literal(true),
});''', '''const recoveryResumeSchema = z.strictObject({
  ...recoveryActionBase,
  resume: z.literal(true),
  confirmation: z.string().max(200),
});''')
s = s.replace('''const recoveryUndoSchema = z.strictObject({
  ...recoveryActionBase,
  undo: z.literal(true),
});''', '''const recoveryUndoSchema = z.strictObject({
  ...recoveryActionBase,
  undo: z.literal(true),
  confirmation: z.string().max(200),
});''')
old = '    return providerOAuthSuccess(provider);'
new = '    return providerOAuthSuccess(provider, completed.returnPath);'
assert old in s
s = s.replace(old, new, 1)
old = '''  if (path === "/help.json" && request.method === "GET")
    return json(help(env, url.searchParams.get("scope") || undefined), 200, {'''
new = '''  if (path === "/readiness.json" && request.method === "GET")
    return json(releaseReadiness(env), 200, { "Cache-Control": "no-store" });
  if (path === "/help.json" && request.method === "GET")
    return json(help(env, url.searchParams.get("scope") || undefined), 200, {'''
assert old in s
s = s.replace(old, new, 1)
for marker, action in [
  ('const input = parse(recoveryExecuteSchema, await request.json());', 'RESTORE'),
  ('const input = parse(recoveryResumeSchema, await request.json());', 'RESUME'),
  ('const input = parse(recoveryUndoSchema, await request.json());', 'UNDO'),
]:
  assert marker in s
  s = s.replace(marker, marker + f'\n        demandRecoveryConfirmation("{action}", auth.actor.workspace, input.confirmation);', 1)
p.write_text(s)

p = Path('public/pilot.js')
s = p.read_text()
old = 'const started = await api(`/api/connections/oauth/${provider}/start`, { alias });'
new = 'const started = await api(`/api/connections/oauth/${provider}/start`, { alias, returnPath: "/pilot" });'
assert old in s
p.write_text(s.replace(old, new, 1))

p = Path('scripts/hosted-checks.mjs')
s = p.read_text()
old = '''  await check("exact catalogue revision; 26 operations; payments disabled", "/help.json", 200, {}, async (r) => {
    const b = await r.json();
    return secure(r) && b.release === release && b.operations?.length === 26 && b.payment?.enabled === false;
  });'''
new = old + '''
  await check("release readiness is fail-closed and exact-revision", "/readiness.json", 200, {}, async (r) => {
    const b = await r.json();
    return secure(r) && r.headers.get("cache-control")?.includes("no-store") && b.release === release &&
      b.access?.signupMode === "restricted" && b.access?.publicSignup === false &&
      b.payments?.advancedEnabled === false && b.payments?.mppEnabled === false &&
      b.recovery?.externalEffectLedger === true && b.recovery?.ownerPitr === true;
  });'''
assert old in s
p.write_text(s.replace(old, new, 1))

p = Path('.github/workflows/deploy.yml')
s = p.read_text()
s = s.replace('permissions:\n  contents: read\n', 'permissions:\n  contents: read\n  pull-requests: read\n', 1)
needle = '''      - uses: actions/setup-node@249970729cb0ef3589644e2896645e5dc5ba9c38 # v6
        with:
          node-version: 24
      - run: npm ci
'''
replacement = '''      - uses: actions/setup-node@249970729cb0ef3589644e2896645e5dc5ba9c38 # v6
        with:
          node-version: 24
      - name: Require merged-PR provenance for automatic staging deployment
        if: inputs.environment == 'staging' && github.event_name == 'push'
        env:
          REQUIRE_MERGED_PR: "true"
          GITHUB_TOKEN: ${{ github.token }}
        run: node scripts/assert-merge-provenance.mjs
      - run: npm ci
'''
assert needle in s
p.write_text(s.replace(needle, replacement, 1))

p = Path('.github/workflows/deploy-staging-request.yml')
s = p.read_text()
s = s.replace('# Deployment request: 2026-09-09-recovery-ledger-pitr-boundary', '# Deployment request: 2026-09-10-release-completion-control-plane')
s = s.replace('permissions:\n  contents: read\n', 'permissions:\n  contents: read\n  pull-requests: read\n', 1)
p.write_text(s)
PY

cat > .github/CODEOWNERS <<'EOF'
* @AyobamiH
/.github/ @AyobamiH
/migrations/ @AyobamiH
/src/ @AyobamiH
/scripts/ @AyobamiH
EOF

mkdir -p .github/pull_request_template
cat > .github/pull_request_template/release-change.md <<'EOF'
## Change boundary
- [ ] Exact layer and customer consequence are stated.
- [ ] External effects, authority changes and financial effects are identified.
- [ ] Unknown outcomes fail closed and do not invite duplicate effects.

## Evidence
- [ ] `npm run verify` passes.
- [ ] Production and full high-severity dependency audits pass.
- [ ] Migrations are additive/forward-safe or include an explicit transition plan.
- [ ] Hosted verification is requested only when this merge should deploy staging.

## Release boundary
- [ ] No public signup, billing, provider grant, destructive recovery or live publication is inferred from CI.
- [ ] Any external consent/approval/settlement gate is named explicitly.
EOF

cat > public/app.html <<'EOF'
<!doctype html>
<html lang="en">
  <head>
    <meta charset="utf-8" />
    <meta name="viewport" content="width=device-width,initial-scale=1" />
    <meta name="robots" content="noindex,nofollow" />
    <title>PostSteward workspace</title>
    <link rel="stylesheet" href="/style.css" />
    <script type="module" src="/app.js"></script>
  </head>
  <body>
    <header><a class="wordmark" href="/">PostSteward</a><nav><a href="/pilot">Controlled acceptance</a><a href="/docs/agent-guide.md">Help</a><button id="refresh">Refresh</button><button id="signout">Sign out</button></nav></header>
    <main class="workspace">
      <div id="session-notice" role="status">Loading workspace…</div>
      <section class="summary"><div><p class="eyebrow">WORKSPACE</p><h1 id="plan">Free publishing</h1><p id="readiness" class="muted"></p><p id="webmcp-status" class="muted"></p></div><button id="pause">Pause publishing</button></section>
      <div class="workspace-grid">
        <section class="panel">
          <h2>1. Connect an account</h2><p class="muted">Provider OAuth is the primary connection path. Buttons remain unavailable until the matching provider application is configured for this deployment.</p>
          <form id="oauth" autocomplete="off"><label>Account alias<input name="alias" required pattern="[A-Za-z0-9_-]{1,100}" placeholder="product_x" /></label><div class="row" id="oauth-buttons"><button type="button" data-provider="x" disabled>Connect X</button><button type="button" data-provider="threads" disabled>Connect Threads</button><button type="button" data-provider="linkedin" disabled>Connect LinkedIn</button></div><p id="oauth-status" class="muted">Checking provider applications…</p></form>
          <details><summary>Manual token import fallback</summary><p class="muted">Use only an already-authorised user token. Manual import does not claim refresh support. Tokens are cleared from the browser form after each attempt.</p><form id="connection" autocomplete="off"><label>Account alias<input name="alias" required pattern="[A-Za-z0-9_-]+" /></label><label>Provider<select name="provider"><option value="x">X</option><option value="threads">Threads</option><option value="linkedin">LinkedIn</option></select></label><label>Provider access token<input name="accessToken" type="password" autocomplete="off" required /></label><label>Token expires at<input name="expiry" type="datetime-local" /></label><label class="check"><input name="funding" type="checkbox" /> For X manual import, these credentials use my own funded developer app.</label><button>Verify and connect</button></form></details>
          <div id="accounts" class="record-list"></div>
        </section>
        <section class="panel"><h2>2. Bind a project</h2><form id="project"><label>Project ID<input name="id" required pattern="[A-Za-z0-9_-]+" /></label><label>Name<input name="name" required /></label><label>Account aliases, comma separated<input name="accounts" required /></label><button>Create project</button></form><div id="projects" class="record-list"></div><h2>3. Submit exact copy</h2><form id="campaign"><label>Project<select name="project" id="project-select" required></select></label><label>Destination account<select name="alias" id="account-select" required></select></label><label>Approved text<textarea name="text" rows="5" required></textarea></label><button>Create and validate campaign</button></form><div id="campaign-preview" class="preview" hidden></div><form id="delivery" hidden><label>Schedule time with UTC offset (empty means publish now)<input name="at" type="text" placeholder="2026-10-01T12:00:00Z" /></label><button class="primary">Submit delivery</button></form></section>
        <section class="panel wide"><div class="row"><h2>Delivery receipts</h2><button id="export">Export workspace</button></div><p class="muted">A reservation is not proof of publication. Verified, unverified and uncertain outcomes remain distinct.</p><div id="receipts" class="record-list"></div></section>
        <section class="panel"><h2>Agent access</h2><form id="grant"><fieldset><legend>Allowed operations</legend><label class="check"><input type="checkbox" name="scope" value="read" checked /> Inspect</label><label class="check"><input type="checkbox" name="scope" value="campaign:write" /> Create campaigns and routes</label><label class="check"><input type="checkbox" name="scope" value="publish" /> Publish now</label><label class="check"><input type="checkbox" name="scope" value="schedule" /> Schedule and cancel</label><label class="check"><input type="checkbox" name="scope" value="automation" /> Manage automation</label><label class="check"><input type="checkbox" name="scope" value="billing" /> Make purchases</label></fieldset><label>Expires in hours<input type="number" name="hours" min="1" max="720" value="168" /></label><button>Create agent token</button></form><pre id="token" hidden></pre><div id="grants" class="record-list"></div></section>
        <section class="panel"><h2>Advanced · $5 per month</h2><p>Continuing source monitoring and schedule management. Configuration and preview are visible here; paid execution remains disabled until settlement validation.</p><div id="billing-status"></div><div class="row"><button id="subscribe">Get subscription quote</button><button id="portal">Manage billing</button></div><div id="quote" class="preview" hidden></div><h3>Automation profile</h3><form id="profile"><label>Profile ID<input name="id" required pattern="[A-Za-z0-9_-]+" /></label><label>Project<select name="project" id="profile-project" required></select></label><label>Repository<input name="repository" required placeholder="owner/repository" /></label><label>Branch<input name="branch" required value="main" /></label><label>Path<input name="path" required value="README.md" /></label><label>Family<input name="family" required value="development" pattern="[A-Za-z0-9_-]+" /></label><label>Destination<select name="alias" id="profile-account" required></select></label><label>Exact reviewed template<textarea name="template" rows="4" required></textarea></label><label>Interval minutes<input name="interval" type="number" min="15" max="10080" value="60" /></label><label>Minimum spacing minutes<input name="spacing" type="number" min="15" max="10080" value="60" /></label><button id="profile-configure">Store reviewed profile</button></form><div id="profiles" class="record-list"></div></section>
        <section class="panel wide"><h2>Recovery and external-effect safety</h2><p class="muted">Recovery is an owner-only authority transition. Preparing quarantines the workspace. Restore and undo require an exact typed workspace confirmation and never run automatically during deployment.</p><div id="recovery-status" class="preview"></div><form id="recovery-prepare"><label>Restore target, previous 30 days<input name="at" type="datetime-local" required /></label><label>Reason<input name="reason" required minlength="3" maxlength="240" /></label><button>Prepare recovery plan</button></form><div id="recovery-actions" hidden><p id="recovery-instruction" class="muted"></p><label>Typed confirmation<input id="recovery-confirmation" autocomplete="off" /></label><div class="row"><button id="recovery-execute" type="button">Execute restore</button><button id="recovery-reconcile" type="button">Reconcile restarted workspace</button><button id="recovery-resume" type="button">Resume after reconciliation</button><button id="recovery-undo" type="button">Undo reconciled restore</button><button id="recovery-cancel" type="button">Cancel prepared plan</button></div></div></section>
      </div><pre id="result" role="status" aria-live="polite" hidden></pre>
    </main>
  </body>
</html>
EOF

cat > public/app.js <<'EOF'
import { registerWebMCP } from "./webmcp.js";
const $ = (id) => document.getElementById(id);
let session, selectedCampaign, paused = false, oauthInfo, recovery;
const key = () => crypto.randomUUID();
async function api(path, input, method = input === undefined ? "GET" : "POST") {
  const response = await fetch(path, { method, credentials: "same-origin", cache: "no-store", redirect: "manual", headers: { "Content-Type": "application/json", ...(session?.csrf ? { "X-CSRF-Token": session.csrf } : {}) }, ...(input !== undefined ? { body: JSON.stringify(input) } : {}) });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) { const error = new Error(data.error?.message || "Request failed."); error.code = data.error?.code; error.status = response.status; throw error; }
  return data;
}
const invoke = (name, input = {}) => api("/api/operations/" + name, input);
function show(data, error = false) { $("result").hidden = false; $("result").textContent = typeof data === "string" ? data : JSON.stringify(data, null, 2); $("result").classList.toggle("error", error); }
async function action(fn) { try { await fn(); } catch (e) { show(e.message, true); } }
function records(id, items, render) { const root = $(id); root.replaceChildren(); if (!items.length) { const p = document.createElement("p"); p.className = "muted"; p.textContent = "Nothing here yet."; root.append(p); } for (const item of items) { const row = document.createElement("div"); row.className = "record"; render(row, item); root.append(row); } }
function line(row, value, strong = false) { const e = document.createElement(strong ? "strong" : "span"); e.textContent = value; row.append(e); }
function button(row, label, fn, disabled = false) { const b = document.createElement("button"); b.type = "button"; b.textContent = label; b.disabled = disabled; b.onclick = () => action(fn); row.append(b); }
function renderOAuth() { if (!oauthInfo) return; for (const b of $("oauth-buttons").querySelectorAll("button[data-provider]")) b.disabled = oauthInfo.providers?.[b.dataset.provider]?.available !== true; const providers = Object.entries(oauthInfo.providers || {}).map(([name, value]) => `${name}: ${value.available ? "OAuth ready" : "provider app not configured"}${name === "linkedin" ? value.readback ? ", readback enabled" : ", readback approval absent" : ""}`); const connections = (oauthInfo.connections || []).map((item) => `${item.alias}: ${item.status}${item.needsReauthorization ? ", reauthorise" : ""}`); $("oauth-status").textContent = [...providers, ...connections].join(" · ") || "No provider applications are configured."; }
function renderRecovery() { if (!recovery) { $("recovery-status").textContent = "Recovery status unavailable."; return; } const plan = recovery.plan; $("recovery-status").textContent = `Quarantine: ${recovery.control?.quarantined ? "ON" : "off"}. External effects: ${JSON.stringify(recovery.effects || {})}. ${plan ? `Plan ${plan.id} · ${plan.state} · target ${new Date(plan.targetTime).toISOString()} · ${plan.reason}` : "No recovery plan."}`; $("recovery-actions").hidden = !plan; for (const id of ["recovery-execute", "recovery-reconcile", "recovery-resume", "recovery-undo", "recovery-cancel"]) $(id).disabled = true; let instruction = ""; if (plan?.state === "prepared") { $("recovery-execute").disabled = false; $("recovery-cancel").disabled = false; instruction = `To restore, type RESTORE ${session.workspace}.`; } if (plan?.state === "armed") { $("recovery-reconcile").disabled = false; instruction = "The object restart must reconcile restored authority before any resume."; } if (plan?.state === "reconciled") { $("recovery-resume").disabled = false; if (plan.undoAvailable) $("recovery-undo").disabled = false; instruction = `To resume type RESUME ${session.workspace}. To undo type UNDO ${session.workspace}.`; } $("recovery-instruction").textContent = instruction; }
async function refresh() {
  const [status, accounts, projects, receipts, billing, profiles, grants, readiness] = await Promise.all([invoke("workspace_status"), invoke("accounts_list"), invoke("projects_list"), invoke("receipts_list", { limit: 50 }), invoke("billing_status"), invoke("automation_inspect"), api("/api/grants"), api("/readiness.json")]);
  paused = status.publishingPaused; $("plan").textContent = status.plan === "advanced" ? "Advanced workspace" : "Free publishing"; $("pause").textContent = paused ? "Resume publishing" : "Pause publishing"; $("readiness").textContent = `Release ${readiness.release.slice(0, 12)} · ${readiness.access.signupMode} signup · provider OAuth ${Object.values(readiness.providers).filter((x) => x.oauth).length}/3 · Advanced ${readiness.payments.advancedEnabled ? "enabled" : "disabled"}`;
  try { oauthInfo = await api("/api/connections/oauth/status"); } catch { oauthInfo = { providers: {}, connections: [] }; }
  try { recovery = await api("/api/recovery/status"); } catch { recovery = undefined; }
  renderOAuth(); renderRecovery();
  records("accounts", accounts, (r, a) => { line(r, `${a.alias} · ${a.provider}`, true); line(r, `${a.identity.username} · stable ${a.identity.id} · ${a.active ? "Connected" : "Disconnected"}${a.capabilities?.oauth ? " · OAuth" : " · manual"}${a.capabilities?.refresh ? " · refresh" : ""}${a.capabilities?.readback ? " · readback" : ""}`); if (a.active) button(r, "Disconnect", async () => { show(await invoke("account_disconnect", { alias: a.alias, idempotencyKey: key() })); await refresh(); }); });
  records("projects", projects, (r, p) => { line(r, p.name, true); line(r, p.accounts.join(", ")); });
  for (const id of ["project-select", "profile-project"]) $(id).replaceChildren(...projects.map((p) => new Option(p.name, p.id)));
  for (const id of ["account-select", "profile-account"]) $(id).replaceChildren(...accounts.filter((a) => a.active).map((a) => new Option(`${a.alias} · ${a.provider}`, a.alias)));
  records("receipts", receipts, (r, d) => { line(r, `${d.provider} · ${d.account} · ${d.status}`, true); line(r, `${new Date(d.dueAt).toLocaleString()} · ${d.reason || ""}`); const copy = document.createElement("p"); copy.textContent = d.text; r.append(copy); if (d.url) { const link = document.createElement("a"); link.href = d.url; link.textContent = "Provider post"; link.target = "_blank"; link.rel = "noopener noreferrer"; r.append(link); } if (["scheduled", "waiting_container"].includes(d.status)) button(r, "Cancel", async () => { show(await invoke("schedule_cancel", { delivery: d.id, idempotencyKey: key() })); await refresh(); }); if (d.postId) button(r, "Capture metrics", async () => show(await invoke("metrics_capture", { delivery: d.id, idempotencyKey: key() }))); });
  $("billing-status").textContent = billing.entitlement ? "Confirmed access until " + new Date(billing.entitlement.until).toLocaleString() : billing.methods.checkout.available ? "Subscription checkout is available." : "Purchases remain disabled pending settlement acceptance."; $("subscribe").disabled = !billing.methods.checkout.available; $("portal").disabled = !billing.methods.checkout.available; $("profile-configure").disabled = status.plan !== "advanced";
  records("profiles", profiles.profiles, (r, p) => { line(r, p.id + " · " + (p.enabled ? "Running" : "Paused"), true); line(r, p.repository + " · " + (p.error || p.family)); button(r, "Preview", async () => show(await invoke("automation_preview", { id: p.id })), status.plan !== "advanced"); button(r, p.enabled ? "Pause" : "Enable", async () => { show(await invoke(p.enabled ? "automation_pause" : "automation_enable", { id: p.id, idempotencyKey: key() })); await refresh(); }, !p.enabled && status.plan !== "advanced"); });
  records("grants", grants, (r, g) => { line(r, g.actor, true); line(r, (g.revoked_at ? "Revoked · " : "") + g.scopes); if (!g.revoked_at) button(r, "Revoke", async () => { await api("/api/grants", { id: g.id }, "DELETE"); await refresh(); }); });
}
for (const b of $("oauth-buttons").querySelectorAll("button[data-provider]")) b.onclick = () => action(async () => { const alias = new FormData($("oauth")).get("alias"); if (!/^[A-Za-z0-9_-]{1,100}$/.test(alias)) throw new Error("Choose an account alias first."); const started = await api(`/api/connections/oauth/${b.dataset.provider}/start`, { alias, returnPath: "/app" }); location.assign(started.authorizationUrl); });
for (const id of ["connection", "project", "campaign", "delivery", "grant", "profile", "recovery-prepare"]) $(id).onsubmit = (e) => { e.preventDefault(); const form = e.currentTarget, fd = new FormData(form); action(async () => {
  if (id === "connection") { try { const input = { alias: fd.get("alias"), provider: fd.get("provider"), accessToken: fd.get("accessToken"), ...(fd.get("expiry") ? { expiresAt: new Date(fd.get("expiry")).getTime() } : {}), ...(fd.has("funding") ? { funding: "customer_app" } : {}) }; show(await api("/api/connections/import", input)); await refresh(); } finally { form.elements.accessToken.value = ""; } }
  if (id === "project") { show(await invoke("project_put", { id: fd.get("id"), name: fd.get("name"), accounts: fd.get("accounts").split(",").map((x) => x.trim()).filter(Boolean), idempotencyKey: key() })); await refresh(); }
  if (id === "campaign") { selectedCampaign = await invoke("campaign_create", { project: fd.get("project"), text: { [fd.get("alias")]: fd.get("text") }, idempotencyKey: key() }); await invoke("campaign_validate", { campaign: selectedCampaign.id }); $("campaign-preview").hidden = false; $("campaign-preview").textContent = Object.entries(selectedCampaign.text).map(([a,t]) => a + "\n" + t).join("\n\n"); $("delivery").hidden = false; show("Campaign stored and validated. Review the exact copy before submitting."); }
  if (id === "delivery") { const at = fd.get("at"); show(await invoke(at ? "schedule_create" : "publish_now", { campaign: selectedCampaign.id, idempotencyKey: key(), ...(at ? { at, timezone: Intl.DateTimeFormat().resolvedOptions().timeZone } : {}) })); await refresh(); }
  if (id === "grant") { const grant = await api("/api/grants", { scopes: fd.getAll("scope"), hours: Number(fd.get("hours")) }); $("token").hidden = false; $("token").textContent = grant.token; await refresh(); }
  if (id === "profile") { show(await invoke("automation_configure", { id: fd.get("id"), project: fd.get("project"), repository: fd.get("repository"), branch: fd.get("branch"), path: fd.get("path"), templates: { [fd.get("alias")]: fd.get("template") }, family: fd.get("family"), intervalMinutes: Number(fd.get("interval")), minSpacingMinutes: Number(fd.get("spacing")), idempotencyKey: key() })); await refresh(); }
  if (id === "recovery-prepare") { const at = new Date(fd.get("at")); if (!Number.isFinite(at.getTime())) throw new Error("Choose a valid recovery target."); show(await api("/api/recovery/prepare", { at: at.toISOString(), reason: fd.get("reason") })); $("recovery-confirmation").value = ""; await refresh(); }
}); };
function currentPlan() { if (!recovery?.plan) throw new Error("No recovery plan is available."); return recovery.plan; }
$("recovery-execute").onclick = () => action(async () => { const p = currentPlan(); show(await api("/api/recovery/execute", { id: p.id, digest: p.digest, execute: true, confirmation: $("recovery-confirmation").value })); await refresh(); });
$("recovery-reconcile").onclick = () => action(async () => { const p = currentPlan(); show(await api("/api/recovery/reconcile", { id: p.id, digest: p.digest, reconcile: true })); await refresh(); });
$("recovery-resume").onclick = () => action(async () => { const p = currentPlan(); show(await api("/api/recovery/resume", { id: p.id, digest: p.digest, resume: true, confirmation: $("recovery-confirmation").value })); await refresh(); });
$("recovery-undo").onclick = () => action(async () => { const p = currentPlan(); show(await api("/api/recovery/undo", { id: p.id, digest: p.digest, undo: true, confirmation: $("recovery-confirmation").value })); await refresh(); });
$("recovery-cancel").onclick = () => action(async () => { const p = currentPlan(); show(await api("/api/recovery/cancel", { id: p.id, digest: p.digest, cancel: true })); await refresh(); });
$("refresh").onclick = () => action(refresh); $("pause").onclick = () => action(async () => { show(await invoke("publishing_pause", { paused: !paused, idempotencyKey: key() })); await refresh(); });
$("signout").onclick = () => action(async () => { await api("/auth/logout", {}); location.assign("/"); });
$("export").onclick = () => action(async () => { const data = await invoke("workspace_export"); const url = URL.createObjectURL(new Blob([JSON.stringify(data, null, 2)], { type: "application/json" })); const a = document.createElement("a"); a.href = url; a.download = "publishing-workspace.json"; a.click(); setTimeout(() => URL.revokeObjectURL(url), 1000); });
$("subscribe").onclick = () => action(async () => { const q = await invoke("billing_quote", { mode: "subscription", idempotencyKey: key() }); $("quote").hidden = false; $("quote").replaceChildren(document.createTextNode("$5.00 USD per month. Automatically renews. ")); button($("quote"), "Continue to Stripe", async () => { const checkout = await invoke("billing_checkout", { quote: q.id, idempotencyKey: key() }); location.assign(checkout.url); }); });
$("portal").onclick = () => action(async () => { const p = await invoke("billing_portal", { idempotencyKey: key() }); location.assign(p.url); });
try { session = await api("/api/session"); $("session-notice").textContent = "Workspace " + session.workspace; const help = await api("/help.json"); const registered = await registerWebMCP(help, invoke, session.scopes); $("webmcp-status").textContent = registered.available ? `${registered.count} browser agent tools available.` : "Remote MCP and HTTP are available. Native WebMCP is not available in this browser."; await refresh(); const params = new URL(location.href).searchParams; if (params.get("connected")) show(`${params.get("connected")} OAuth completed. Verify the stable account identity before publishing.`); else if (params.get("connection") === "denied") show("Provider authorization was declined. No connection was created.", true); if (params.has("connected") || params.has("connection")) history.replaceState(null, "", "/app"); } catch (e) { $("session-notice").replaceChildren(); const a = document.createElement("a"); a.href = "/auth/login"; a.textContent = "Sign in to open your workspace"; $("session-notice").append(a); show(e.message, true); }
EOF

cat > tests/release-completion.test.mjs <<'EOF'
import assert from "node:assert/strict";
import test from "node:test";
import { readFileSync } from "node:fs";
import { assertMergeProvenance } from "../scripts/assert-merge-provenance.mjs";
import { demandRecoveryConfirmation } from "../src/recovery-confirmation.ts";
import { releaseReadiness } from "../src/readiness.ts";
import { environment } from "./helpers.ts";

test("release readiness exposes capabilities without secrets and remains fail-closed", () => {
  const value = releaseReadiness({ ...environment, DEPLOY_ENV: "staging", SIGNUP_MODE: "restricted", ADVANCED_ENABLED: "false", MPP_ENABLED: "false", X_OAUTH_CLIENT_ID: "x", X_OAUTH_CLIENT_SECRET: "secret-x" });
  assert.equal(value.access.publicSignup, false); assert.equal(value.payments.advancedEnabled, false); assert.equal(value.providers.x.oauth, true); assert.equal(value.providers.threads.oauth, false); assert.equal(value.recovery.externalEffectLedger, true); assert.doesNotMatch(JSON.stringify(value), /secret-x|ENCRYPTION|CLIENT_SECRET/);
});

test("recovery high-consequence transitions require the exact workspace phrase", () => {
  assert.doesNotThrow(() => demandRecoveryConfirmation("RESTORE", "workspace-1", "RESTORE workspace-1"));
  for (const value of ["restore workspace-1", "RESTORE workspace-2", "RESTORE  workspace-1", ""]) assert.throws(() => demandRecoveryConfirmation("RESTORE", "workspace-1", value), { code: "RECOVERY_CONFIRMATION_REQUIRED" });
});

test("staging merge provenance accepts only the exact merged main PR and redacts upstream failure", async () => {
  const sha = "a".repeat(40), base = { REQUIRE_MERGED_PR: "true", GITHUB_REPOSITORY: "AyobamiH/poststeward", GITHUB_SHA: sha, GITHUB_TOKEN: "private-actions-token" };
  const ok = await assertMergeProvenance(base, async (_url, init) => { assert.equal(init.redirect, "manual"); assert.equal(init.headers.Authorization, "Bearer private-actions-token"); return Response.json([{ number: 14, merged_at: "2026-09-10T00:00:00Z", base: { ref: "main" }, merge_commit_sha: sha }]); });
  assert.equal(ok.pullRequest, 14);
  await assert.rejects(assertMergeProvenance(base, async () => Response.json([{ number: 14, merged_at: null, base: { ref: "main" }, merge_commit_sha: sha }])), /requires this exact main revision/);
  await assert.rejects(assertMergeProvenance(base, async () => { throw new Error("PRIVATE_NETWORK_DETAIL"); }), (e) => !e.message.includes("PRIVATE_NETWORK_DETAIL"));
});

test("workspace UI makes OAuth primary, exposes reviewed Advanced and recovery controls, and keeps tokens ephemeral", () => {
  const html = readFileSync("public/app.html", "utf8"), js = readFileSync("public/app.js", "utf8");
  assert.match(html, /id="oauth-buttons"/); assert.match(html, /Manual token import fallback/); assert.match(html, /id="recovery-prepare"/); assert.match(html, /id="profile"/); assert.match(js, /returnPath: "\/app"/); assert.match(js, /RESTORE \$\{session\.workspace\}/); assert.match(js, /form\.elements\.accessToken\.value = ""/); assert.doesNotMatch(js, /localStorage|sessionStorage|innerHTML|document\.cookie/);
});
EOF

python3 - <<'PY'
from pathlib import Path
p = Path('tests/runtime-provider-oauth.test.ts')
s = p.read_text()
s += r'''

test("provider OAuth return destination is state-bound, fixed and cannot become an open redirect", async () => {
  const { mf, db } = await runtime(undefined, {
    OIDC_ISSUER: "https://accounts.google.com",
    X_OAUTH_CLIENT_ID: "x-client",
    X_OAUTH_CLIENT_SECRET: "x-client-secret",
  }, 100);
  try {
    const owner = await seedOwner(db);
    const bad = await mf.dispatchFetch(`${origin}/api/connections/oauth/x/start`, {
      method: "POST", headers: ownerHeaders(owner),
      body: JSON.stringify({ alias: "primary", returnPath: "https://evil.example" }),
    });
    assert.equal(bad.status, 400);
    assert.equal((await db.prepare("SELECT count(*) AS n FROM provider_oauth_states").first<any>())?.n, 0);
    const start = await mf.dispatchFetch(`${origin}/api/connections/oauth/x/start`, {
      method: "POST", headers: ownerHeaders(owner),
      body: JSON.stringify({ alias: "primary", returnPath: "/app" }),
    });
    assert.equal(start.status, 200);
    const started: any = await start.json();
    assert.equal(started.returnPath, "/app");
    const state = new URL(started.authorizationUrl).searchParams.get("state")!;
    const stored = await db.prepare("SELECT return_path FROM provider_oauth_states").first<any>();
    assert.equal(stored.return_path, "/app");
    const denied = await mf.dispatchFetch(`${origin}/connections/oauth/x/callback?state=${encodeURIComponent(state)}&error=access_denied`, {
      redirect: "manual", headers: { Cookie: `__Host-session=${owner.session}; __Host-provider-oauth=${state}` },
    });
    assert.equal(denied.status, 302);
    assert.equal(denied.headers.get("location"), "/app?connection=denied");
  } finally { await mf.dispose(); }
});
'''
p.write_text(s)

p = Path('tests/hosted-checks.test.mjs')
s = p.read_text()
s = s.replace('if (path === "/help.json") return Response.json({ release, operations: Array(26).fill({}), payment: { enabled: false } }, { headers });', 'if (path === "/help.json") return Response.json({ release, operations: Array(26).fill({}), payment: { enabled: false } }, { headers });\n    if (path === "/readiness.json") return Response.json({ release, access: { signupMode: "restricted", publicSignup: false }, payments: { advancedEnabled: false, mppEnabled: false }, recovery: { externalEffectLedger: true, ownerPitr: true } }, { headers });')
s = s.replace('hosted report checks 17 surfaces', 'hosted report checks 18 surfaces').replace('assert.equal(report.checks.length, 17);', 'assert.equal(report.checks.length, 18);')
p.write_text(s)

p = Path('tests/deployment-request.test.mjs')
s = p.read_text()
old = 'assert.match(request, /permissions:\\n  contents: read\\n/);'
new = 'assert.match(request, /permissions:\\n  contents: read\\n  pull-requests: read\\n/);'
assert old in s
s = s.replace(old, new, 1)
old = 'assert.doesNotMatch(verification, /secrets\\.|environment:/);'
new = 'assert.doesNotMatch(verification, /secrets\\.|environment:/);\n  assert.ok(verification.includes("Require merged-PR provenance for automatic staging deployment"));\n  assert.ok(verification.includes("run: node scripts/assert-merge-provenance.mjs"));'
assert old in s
p.write_text(s.replace(old, new, 1))
PY

cat > docs/release-completion-plan.md <<'EOF'
# Release completion control plane

PostSteward separates code-complete capability from externally proven capability. A green build or staging deployment must never silently turn an external gate into a product claim.

## Implemented and deployable

- Restricted Google OIDC owner sign-in and session-bound controlled publication approval.
- X, Threads and LinkedIn publishing adapters with explicit uncertain-outcome handling and durable external-effect fences.
- Provider OAuth architecture for X, Threads and LinkedIn, encrypted access/refresh storage, background refresh and identity-drift blocking. Each provider remains unavailable until its own application credentials are configured.
- Capability-gated LinkedIn member-post readback. It remains disabled unless the LinkedIn app actually has `r_member_social`.
- Workspace PITR coordination, quarantine, restored-authority invalidation, exact undo and D1 effect fences outside restored Durable Object state.
- Free explicit publishing/scheduling/receipts and Advanced source-monitoring foundations. Advanced/MPP stay disabled until payment acceptance is proven.
- Blocking production and full dependency audits.
- Automatic staging deployment additionally requires the exact main revision to be associated with a merged pull request. GitHub branch rules remain a separate repository-admin control.

## External evidence gates

1. Real owner Google consent and authenticated-browser acceptance.
2. At least one real provider application/grant, exact controlled publication and independent readback.
3. A deliberate staging PITR rehearsal with owner approval and post-restore reconciliation.
4. Native WebMCP acceptance in a browser that actually implements the API.
5. Stripe sandbox settlement/refund/dispute acceptance and, separately, eligible MPP merchant/wallet validation.
6. GitHub main ruleset/required-check enforcement, Cloudflare custom-domain/WAF/alerts and production origin/resources.

The workspace UI exposes current readiness, provider connection health, reviewed Advanced profile configuration and owner recovery status/actions. Destructive recovery never runs from deployment automation.
EOF

python3 - <<'PY'
from pathlib import Path
p = Path('README.md')
s = p.read_text()
s = s.replace('**Status: deployed to restricted staging. The owner acceptance workflow is implemented; 99 automated tests, 29 hosted HTTP checks and two fresh unauthenticated browser checks passed. Real owner consent and the first live controlled publication remain pending. Public charging is not approved.**', '**Status: deployed to restricted staging. Owner acceptance, self-service provider OAuth architecture, exact provider readback, dependency-audit closure and restore-safe external-effect recovery are implemented. The PR #13 candidate passed 130 automated tests and its exact merge revision deployed successfully. Real owner/provider consent, a controlled live publication, a real PITR rehearsal, native WebMCP acceptance and payment settlement remain external evidence gates. Public charging is not approved.**')
s = s.replace('- Owner-controlled `/pilot` acceptance for one text-only X or Threads publication: fresh sign-in, expiring immutable review, one-shot reservation, thirty-second cancellation window, session-bound dispatch and bounded independent post-ID readback. LinkedIn\'s general support is unchanged; its unimplemented member readback excludes it from this milestone.', '- Owner-controlled `/pilot` acceptance for one text-only X, Threads or capability-approved LinkedIn publication: fresh sign-in, expiring immutable review, one-shot reservation, thirty-second cancellation window, session-bound dispatch and bounded independent post-ID readback.')
s = s.replace('2. Complete provider OAuth onboarding and token refresh/rotation. This restricted pilot imports authorised user tokens through its authenticated application and verifies identity. It is not yet a self-service social-provider OAuth product. Validate a fresh customer\'s account and provider permissions.', '2. Configure and approve at least one real X/Threads/LinkedIn provider application and validate the implemented OAuth/refresh path with a real owner grant. Provider OAuth code exists, but staging correctly reports all provider apps unavailable until their credentials are supplied.')
s = s.replace('6. Calibrate traffic/storage retention limits; implement account erasure, credential key rotation and operational alerts; rehearse state restore with external-effect reconciliation. Triage the full-install development/tooling advisories separately from the passing production-dependency audit. Choose and validate the production origin before public launch.', '6. Calibrate traffic/storage retention limits; implement account erasure, credential key rotation and operational alerts; perform a real staging restore rehearsal with external-effect reconciliation. Both production and full high-severity dependency audits are clean. Choose and validate the production origin before public launch.')
p.write_text(s)

p = Path('docs/implementation-status.md')
s = p.read_text()
s = s.replace('# PostSteward implementation status: 9 September 2026', '# PostSteward implementation status: 10 September 2026')
start = s.index('## Current engineering evidence')
end = s.index('## Implemented')
evidence = '''## Current engineering evidence

| Evidence | Recorded result |
| --- | --- |
| Owner acceptance page | `https://poststeward-staging.woeinvests.workers.dev/pilot` |
| Active runtime revision | `28579527895955651b2f4b7ea61227ce194113a6` (PR #13 merge) |
| Cloudflare version | `464a8dc4-f3ae-4965-94df-2259bc7c0339` |
| PR #13 merge deployment | Run `34448815060`: migrations `0005` and `0006`, exact revision upload and all hosted checks succeeded |
| PR #13 candidate verification | Run `34413334443`: 130 tests passed, zero failed/skipped/cancelled; type checking, generated docs and bundling passed |
| Hosted verification | 17 base HTTP checks, 12 controlled-publication checks and two fresh unauthenticated Chromium contexts passed |
| Dependency audits | Production and full high-severity audits both report zero known vulnerabilities |
| Provider app configuration | X, Threads and LinkedIn OAuth client IDs are currently absent in staging; implementation remains fail-closed |
| Public release | Restricted signup; Advanced and MPP disabled |

The active Worker is no longer the older owner-acceptance revision recorded in the 9 September receipt. PR #11 added provider OAuth/readback, PR #12 closed the dependency audit findings and PR #13 added restore-safe effect fencing/PITR. This document supersedes the stale 99-test/advisory status while preserving those historical receipts.

'''
s = s[:start] + evidence + s[end:]
s = s.replace('| Controlled readback | At most eight separate reads of the known post ID, thirty seconds apart; exact ID/author/text matching; no publication retry to repair missing evidence |', '| Controlled readback | At most eight separate reads of the known post ID, thirty seconds apart; exact ID/author/text matching; no publication retry to repair missing evidence; LinkedIn is capability-gated by approved member readback |')
s = s.replace('| Agent surfaces | 26 shared operations across HTTP, remote MCP and browser WebMCP; generated help/reference/OpenAPI and public discovery files |', '| Agent surfaces | 26 shared operations across HTTP, remote MCP and browser WebMCP; generated help/reference/OpenAPI and public discovery files |\n| Provider OAuth | X PKCE OAuth, Threads long-lived tokens and LinkedIn OAuth with encrypted refresh handling, owner/session binding and identity-drift blocking |\n| Recovery safety | D1 external-effect/container fences, global quarantine, owner-only PITR plans, exact undo and restored-authority invalidation |')
s = s.replace('Direct publishing and explicit scheduling remain Free; continuing campaign management is the proposed paid boundary. Advanced and MPP are disabled in the active deployment. Pilot acceptance supports X or Threads only because the current LinkedIn member adapter cannot independently read back a post; this does not remove general LinkedIn support.', 'Direct publishing and explicit scheduling remain Free; continuing campaign management is the proposed paid boundary. Advanced and MPP are disabled in the active deployment. LinkedIn controlled acceptance is implemented but remains available only when the deployment and connection prove approved member-post readback authority.')
s = s.replace('The 99-test suite', 'The current 130-test suite')
old = '''## Remaining public-release work

1. Complete and record real Google consent, authenticated browser interaction, invited/uninvited-owner isolation/revocation and the first controlled provider publication/readback. These are still pending, not claimed completed by deployment.
2. Complete self-service provider OAuth onboarding and token refresh/rotation. The restricted pilot currently imports an authorised user token over its authenticated application. Do not borrow another product's credentials or call token import a completed OAuth onboarding product.
3. Complete richer Advanced profile/category-management, private GitHub installation and customer review UI before purchases.
4. Configure Stripe sandbox and merchant MPP eligibility; finish recurring invoice, recovery, refund/dispute and eligible-wallet acceptance before any live charge.
5. Finish production traffic/retention limits, account erasure, rotation, operational alerts, restore rehearsal and development/tooling advisory triage. Choose and verify the production domain and its separate resources.'''
new = '''## Remaining public-release work

1. Complete real Google owner consent and a real provider grant, then record exactly one controlled publication plus independent provider readback.
2. Supply/approve provider application credentials. The OAuth, refresh and LinkedIn-readback code is implemented; staging currently exposes those providers as unavailable because no provider app IDs are configured.
3. Finish private GitHub source installation plus richer Advanced category management. The owner review/configuration UI is being promoted into the main workspace before purchases are enabled.
4. Run Stripe sandbox settlement, renewal, refund/dispute and eligible-wallet MPP acceptance before enabling Advanced or MPP.
5. Perform an explicitly approved staging PITR rehearsal, native WebMCP acceptance in a supporting browser, capacity/retention calibration, account erasure, encryption-key rotation, operational alerts, cross-tenant hosted attack testing and production-domain/WAF acceptance.
6. Enable an actual GitHub main ruleset with required Verify checks. The repository currently has no ruleset; the deployment path now adds its own merged-PR provenance gate but that is not a substitute for server-side branch protection.'''
assert old in s
p.write_text(s.replace(old, new, 1))
PY

npx prettier --write src/readiness.ts src/recovery-confirmation.ts src/provider-oauth.ts src/worker.ts src/types.ts public/app.html public/app.js public/pilot.js scripts/assert-merge-provenance.mjs scripts/hosted-checks.mjs tests/release-completion.test.mjs tests/runtime-provider-oauth.test.ts tests/hosted-checks.test.mjs tests/deployment-request.test.mjs .github/pull_request_template/release-change.md .github/workflows/deploy.yml .github/workflows/deploy-staging-request.yml docs/release-completion-plan.md README.md docs/implementation-status.md
npm ci
npm audit --omit=dev --audit-level=high
npm audit --audit-level=high
npm run verify
rm .github/workflows/release-completion-implement.yml scripts/release-completion-implement.sh
git diff --check
git status --short
git config user.name 'AyobamiH'
git config user.email '47716486+AyobamiH@users.noreply.github.com'
git add -A
git commit -m 'Complete owner control plane and gate staging releases by merged PR provenance'
git push origin HEAD:feat/release-completion-control-plane
