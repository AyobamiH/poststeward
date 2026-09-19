import assert from "node:assert/strict";
import test from "node:test";
import { runtime } from "./runtime-fixture.ts";

test("capacity snapshot accepts an admitted UUID before local workspace identity exists", async () => {
  const { mf } = await runtime();
  try {
    const namespace = await mf.getDurableObjectNamespace("WORKSPACES");
    const workspace = crypto.randomUUID();
    const stub = namespace.get(namespace.idFromName(workspace));
    const response = await stub.fetch(
      "https://workspace.internal/capacity/snapshot",
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ workspace }),
      },
    );

    assert.equal(response.status, 200, await response.clone().text());
    const { observedAt, ...snapshot } = (await response.json()) as Record<
      string,
      unknown
    >;
    assert.equal(typeof observedAt, "number");
    assert.deepEqual(snapshot, {
      schemaVersion: 1,
      release: "test",
      records: 0,
      bytes: 0,
      maxValueBytes: 0,
      dailyDeliveries: 0,
      activeSchedules: 0,
      sourceProfiles: 0,
      workspaceRequests: 0,
      alarmCycles: 0,
    });
  } finally {
    await mf.dispose();
  }
});

test("capacity snapshot rejects whitespace and control characters in workspace identity", async () => {
  const { mf } = await runtime();
  try {
    const namespace = await mf.getDurableObjectNamespace("WORKSPACES");
    for (const workspace of ["workspace with spaces", "workspace\u0000control"]) {
      const stub = namespace.get(namespace.idFromName(workspace));
      const response = await stub.fetch(
        "https://workspace.internal/capacity/snapshot",
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ workspace }),
        },
      );
      assert.equal(response.status, 400);
      assert.deepEqual(await response.json(), { error: "invalid_workspace" });
    }
  } finally {
    await mf.dispose();
  }
});
