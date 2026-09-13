import { test } from "node:test";
import assert from "node:assert/strict";
import { registerWebMCP, checkNativeWebMCP } from "../public/webmcp.js";
test("browser WebMCP delegates exact tool inputs and declares consequential actions", async () => {
  const registered = [],
    calls = [];
  const context = {
    registerTool: async (tool, options) => registered.push({ tool, options }),
  };
  const help = {
    operations: [
      {
        name: "publish_now",
        description: "Publish exact approved content",
        inputSchema: { type: "object" },
        scope: "publish",
        effects: ["EXTERNAL_PROVIDER_EFFECT"],
      },
      {
        name: "billing_checkout",
        description: "Purchase",
        inputSchema: { type: "object" },
        scope: "billing",
        effects: ["FINANCIAL_EFFECT"],
      },
    ],
  };
  const result = await registerWebMCP(
    help,
    async (name, input) => {
      calls.push({ name, input });
      return { status: "scheduled" };
    },
    ["publish"],
    context,
  );
  assert.equal(result.count, 1);
  assert.equal(registered[0].tool.annotations.consequentialHint, true);
  assert.equal(registered[0].tool.annotations.readOnlyHint, false);
  const input = { campaign: "approved", idempotencyKey: "same-key" };
  assert.deepEqual(JSON.parse(await registered[0].tool.execute(input)), {
    status: "scheduled",
  });
  assert.deepEqual(calls, [{ name: "publish_now", input }]);
  result.unregister();
  assert.equal(registered[0].options.signal.aborted, true);
});
test("unsupported browser is reported without fabricating native WebMCP", async () => {
  assert.deepEqual(await registerWebMCP({ operations: [] }, () => {}, [], {}), {
    available: false,
    count: 0,
  });
});

test("partial registration failure revokes every previously registered tool", async () => {
  const signals = [];
  const failure = new Error("Browser rejected tool registration");
  await assert.rejects(registerWebMCP({
    operations: ["first", "second"].map((name) => ({
      name, scope: "read", effects: ["READ_ONLY"], description: name,
      inputSchema: { type: "object" },
    })),
  }, () => {}, ["read"], {
    registerTool: async (_tool, options) => {
      signals.push(options.signal);
      if (signals.length === 2) throw failure;
    },
  }), failure);
  assert.equal(signals.length, 2);
  assert.ok(signals.every((signal) => signal.aborted));
});

test("native check uses the current document tool and verifies its workspace result (simulated API contract)", async (t) => {
  const beforeDocument = Object.getOwnPropertyDescriptor(globalThis, "document");
  const beforeWindow = Object.getOwnPropertyDescriptor(globalThis, "window");
  t.after(() => {
    if (beforeDocument) Object.defineProperty(globalThis, "document", beforeDocument);
    else delete globalThis.document;
    if (beforeWindow) Object.defineProperty(globalThis, "window", beforeWindow);
    else delete globalThis.window;
  });
  const page = {};
  globalThis.window = page;
  const tool = { name: "workspace_status", window: page };
  let workspace = "expected";
  globalThis.document = { modelContext: {
    getTools: async (options) => { assert.deepEqual(options, { fromOrigins: [] }); return [tool]; },
    executeTool: async (selected, input) => {
      assert.equal(selected, tool); assert.deepEqual(input, {});
      return JSON.stringify({ workspace, release: "test" });
    },
  }};
  assert.equal((await checkNativeWebMCP("expected")).workspace, "expected");
  workspace = "other";
  await assert.rejects(checkNativeWebMCP("expected"), /unexpected workspace/);
});
