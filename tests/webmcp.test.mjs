import { test } from "node:test";
import assert from "node:assert/strict";
import { registerWebMCP } from "../public/webmcp.js";
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
