import assert from "node:assert/strict";
import test from "node:test";
import { Response as RuntimeResponse } from "miniflare";
import { digest } from "../src/common.ts";
import { setWorkspaceQuarantine } from "../src/effects.ts";
import { runtime } from "./runtime-fixture.ts";

const origin = "https://publish.example";
async function seedAgent(db: D1Database, token = "recovery-agent") {
  const subject = "agent-owner";
  const workspace = "recovery-workspace";
  await db
    .prepare("INSERT INTO principals(subject,workspace,created_at) VALUES (?,?,?)")
    .bind(subject, workspace, Date.now())
    .run();
  await db
    .prepare("INSERT INTO grants VALUES (?,?,?,?,?,NULL)")
    .bind(
      await digest(token),
      workspace,
      subject,
      JSON.stringify(["admin", "read", "connections", "campaign:write", "publish"]),
      Date.now() + 3600000,
    )
    .run();
  return { subject, workspace, token };
}
function call(
  mf: Awaited<ReturnType<typeof runtime>>["mf"],
  token: string,
  path: string,
  input?: unknown,
) {
  return mf.dispatchFetch(origin + path, {
    method: input === undefined ? "GET" : "POST",
    headers: {
      Authorization: "Bearer " + token,
      ...(input === undefined ? {} : { "Content-Type": "application/json" }),
    },
    ...(input === undefined ? {} : { body: JSON.stringify(input) }),
  });
}

test("agent bearer authority cannot inspect owner recovery state even with an admin scope string", async () => {
  const { mf, db } = await runtime();
  try {
    const actor = await seedAgent(db);
    const response = await call(mf, actor.token, "/api/recovery/status");
    assert.equal(response.status, 403);
    const value: any = await response.json();
    assert.equal(value.error.code, "OWNER_SESSION_REQUIRED");
  } finally {
    await mf.dispose();
  }
});

test("global recovery quarantine blocks ordinary mutation but preserves inspection and risk-reducing pause", async () => {
  const { mf, db } = await runtime(async (request) => {
    const url = new URL(request.url);
    if (url.hostname === "api.x.com" && url.pathname === "/2/users/me")
      return RuntimeResponse.json({ data: { id: "stable-user", username: "owner" } });
    throw new Error("Unexpected network access");
  });
  try {
    const actor = await seedAgent(db, "quarantine-agent");
    const connected = await call(mf, actor.token, "/api/connections/import", {
      alias: "social",
      provider: "x",
      accessToken: "test-token-only",
      funding: "customer_app",
    });
    assert.equal(connected.status, 200, await connected.clone().text());

    await setWorkspaceQuarantine(db, actor.workspace, true, "recovery test quarantine");

    const status = await call(mf, actor.token, "/api/operations/workspace_status", {});
    assert.equal(status.status, 200);

    const blocked = await call(mf, actor.token, "/api/operations/project_put", {
      id: "project",
      name: "Should not mutate",
      accounts: ["social"],
      idempotencyKey: "blocked-project-001",
    });
    assert.equal(blocked.status, 409);
    assert.equal(((await blocked.json()) as any).error.code, "RECOVERY_QUARANTINED");

    const paused = await call(mf, actor.token, "/api/operations/publishing_pause", {
      paused: true,
      idempotencyKey: "recovery-pause-001",
    });
    assert.equal(paused.status, 200);
    assert.equal(((await paused.json()) as any).paused, true);
  } finally {
    await mf.dispose();
  }
});
