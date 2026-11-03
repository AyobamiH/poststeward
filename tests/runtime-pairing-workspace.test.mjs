import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import vm from "node:vm";

const app = readFileSync("public/app.js", "utf8");
function sourceBetween(start, end) {
  const first = app.indexOf(start);
  const last = app.indexOf(end, first);
  assert.ok(first >= 0 && last > first, "Owner workspace functions must remain available");
  return app.slice(first, last);
}

// Execute the production request, pairing and initialisation functions together.
// The refresh boundary is observed rather than reimplementing workspace rendering.
const source = [
  sourceBetween("async function api(", "const invoke ="),
  sourceBetween("function runtimePairingParams(", "function renderRuntimeAuthority("),
  sourceBetween("function signInNotice(", "await openWorkspace();"),
].join("\n");

async function openPairingWorkspace(error) {
  const calls = [];
  const feedback = [];
  const replacements = [];
  const elements = new Map();
  const element = () => ({
    hidden: true,
    textContent: "",
    children: [],
    replaceChildren(...children) { this.children = children; },
    append(...children) { this.children.push(...children); },
  });
  const $ = (id) => {
    if (!elements.has(id)) elements.set(id, element());
    return elements.get(id);
  };
  const location = {
    href: "https://publish.example/app?runtime_pairing=fixture-pairing&code=ABCD1234&keep=1",
    pathname: "/app",
    search: "?runtime_pairing=fixture-pairing&code=ABCD1234&keep=1",
  };
  let refreshes = 0;
  const context = vm.createContext({
    $, URL, AbortSignal, location,
    session: undefined,
    sessionExpired: false,
    history: {
      replaceState(_state, _title, target) {
        replacements.push(target);
        const url = new URL(target, location.href);
        Object.assign(location, { href: url.href, pathname: url.pathname, search: url.search });
      },
    },
    document: {
      createTextNode: (textContent) => ({ textContent }),
      createElement: () => element(),
    },
    show: (message, failed = false) => {
      feedback.push({ message, failed });
      $("result").hidden = false;
      $("result").textContent = message;
    },
    action: () => assert.fail("Pairing must never approve itself"),
    invoke: () => assert.fail("Initialisation must not invoke a provider operation"),
    registerWebMCP: async () => ({ available: false }),
    refresh: async () => {
      refreshes++;
      $("workspace-content").hidden = false;
      $("session-notice").textContent = "Workspace data updated.";
    },
    fetch: async (path, options) => {
      calls.push({ path, method: options.method });
      let status = 200;
      let body;
      if (path === "/api/session")
        body = { workspace: "fixture-workspace", csrf: "fixture-csrf", scopes: [] };
      else if (path === "/api/runtime/pairing/inspect") {
        status = error?.status || 200;
        body = error
          ? { error: { code: error.code, message: "Synthetic inspect failure" } }
          : {
              installationId: "fixture-installation", label: "Fixture machine",
              platform: "Fixture OS", runtimeVersion: "fixture-version",
              sourceRevision: "fixture-revision", expiresAt: 2000000000000,
            };
      } else if (path === "/help.json") body = {};
      else assert.fail("Unexpected owner request: " + path);
      return { ok: status >= 200 && status < 300, status, json: async () => body };
    },
  });
  vm.runInContext(source, context, { filename: "public/app.js" });
  await context.openWorkspace();
  return { context, calls, feedback, replacements, $, refreshes, location };
}

for (const code of ["RUNTIME_PAIRING_EXPIRED", "RUNTIME_PAIRING_NOT_PENDING"]) {
  test(code + " preserves authenticated workspace initialisation without approving", async () => {
    const state = await openPairingWorkspace({ code, status: 409 });
    assert.equal(state.refreshes, 1);
    assert.deepEqual(state.calls, [
      { path: "/api/session", method: "GET" },
      { path: "/api/runtime/pairing/inspect", method: "POST" },
      { path: "/help.json", method: "GET" },
    ]);
    assert.equal(state.$("runtime-pairing-panel").hidden, true);
    assert.deepEqual(state.replacements, ["/app?keep=1"]);
    assert.equal(state.context.session.workspace, "fixture-workspace");
    assert.equal(state.context.sessionExpired, false);
    assert.match(state.feedback[0].message, /pairing request has ended/);
    assert.match(state.feedback[0].message, /poststeward onboard/);
    assert.equal(state.feedback[0].failed, true);
    assert.equal(state.feedback.length, 1);
    assert.equal(state.$("workspace-content").hidden, false);
    assert.equal(state.$("session-notice").textContent, "Workspace data updated.");
    assert.equal(state.$("result").hidden, false);
    assert.match(state.$("result").textContent, /pairing request has ended/);
    assert.match(state.$("result").textContent, /poststeward onboard/);
  });
}

test("a valid pending request retains its review controls and never auto-approves", async () => {
  const state = await openPairingWorkspace();
  assert.equal(state.refreshes, 1);
  assert.equal(state.$("runtime-pairing-panel").hidden, false);
  assert.equal(typeof state.$("runtime-pairing-approve").onclick, "function");
  assert.equal(typeof state.$("runtime-pairing-dismiss").onclick, "function");
  assert.equal(JSON.parse(state.$("runtime-pairing-details").textContent).installationId,
    "fixture-installation");
  assert.deepEqual(state.replacements, []);
  assert.equal(state.feedback.length, 0);
  assert.ok(state.calls.every(({ path }) => !path.endsWith("/approve")));
});

test("an unauthenticated pairing inspection still blocks workspace loading", async () => {
  const state = await openPairingWorkspace({ code: "UNAUTHENTICATED", status: 401 });
  assert.equal(state.refreshes, 0);
  assert.deepEqual(state.calls.map(({ path }) => path), [
    "/api/session", "/api/runtime/pairing/inspect",
  ]);
  assert.equal(state.context.session, undefined);
  assert.equal(state.context.sessionExpired, true);
  assert.equal(state.$("workspace-content").hidden, true);
  assert.match(state.$("session-notice").children[1].href, /^\/auth\/login\?return=/);
  assert.deepEqual(state.replacements, []);
  assert.equal(state.feedback[0].message, "Synthetic inspect failure");
});

test("an unexpected pairing failure is surfaced and does not claim a loaded workspace", async () => {
  const state = await openPairingWorkspace({ code: "SERVICE_UNAVAILABLE", status: 503 });
  assert.equal(state.refreshes, 0);
  assert.deepEqual(state.calls.map(({ path }) => path), [
    "/api/session", "/api/runtime/pairing/inspect",
  ]);
  assert.match(state.$("session-notice").textContent, /workspace data could not be loaded/);
  assert.deepEqual(state.replacements, []);
  assert.deepEqual(state.feedback, [{ message: "Synthetic inspect failure", failed: true }]);
});
