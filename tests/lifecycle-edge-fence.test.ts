import assert from "node:assert/strict";
import test from "node:test";
import { digest } from "../src/common.ts";
import { beginWorkspaceDeletion } from "../src/lifecycle.ts";
import { runtime } from "./runtime-fixture.ts";

test("pending deletion fences D1-level owner and agent routes while preserving owner recovery access", async () => {
  const { mf, db } = await runtime();
  const workspace = "pending-delete-edge-workspace";
  const actor = "pending-delete-owner";
  const session = "pending-delete-session";
  const csrf = "pending-delete-csrf";
  const agent = "pending-delete-agent";
  const now = Date.now();
  try {
    await db.batch([
      db
        .prepare("INSERT INTO principals(subject,workspace,created_at) VALUES (?,?,?)")
        .bind(actor, workspace, now),
      db
        .prepare("INSERT INTO sessions(token_hash,workspace,actor,expires_at,csrf) VALUES (?,?,?,?,?)")
        .bind(await digest(session), workspace, actor, now + 3600000, csrf),
      db
        .prepare("INSERT INTO grants(token_hash,workspace,actor,scopes,expires_at,revoked_at) VALUES (?,?,?,?,?,NULL)")
        .bind(await digest(agent), workspace, "agent", '["read"]', now + 3600000),
    ]);
    await beginWorkspaceDeletion(db, workspace, now + 1);

    const browser = {
      Cookie: `__Host-session=${session}`,
      Origin: "https://publish.example",
      "Content-Type": "application/json",
      "X-CSRF-Token": csrf,
    };

    const sessionResponse = await mf.dispatchFetch(
      "https://publish.example/api/session",
      { headers: { Cookie: `__Host-session=${session}` } },
    );
    assert.equal(sessionResponse.status, 200);

    const lifecycle = await mf.dispatchFetch(
      "https://publish.example/api/lifecycle/status",
      { headers: { Cookie: `__Host-session=${session}` } },
    );
    assert.equal(lifecycle.status, 200);
    assert.equal((await lifecycle.json() as any).deletion.state, "pending");

    const grantMutation = await mf.dispatchFetch(
      "https://publish.example/api/grants",
      {
        method: "POST",
        headers: browser,
        body: JSON.stringify({ scopes: ["read"], hours: 1 }),
      },
    );
    assert.equal(grantMutation.status, 410);

    const oauthStatus = await mf.dispatchFetch(
      "https://publish.example/api/connections/oauth/status",
      { headers: { Cookie: `__Host-session=${session}` } },
    );
    assert.equal(oauthStatus.status, 410);

    const ordinaryOperation = await mf.dispatchFetch(
      "https://publish.example/api/operations/workspace_status",
      { method: "POST", headers: browser, body: "{}" },
    );
    assert.equal(ordinaryOperation.status, 410);

    const agentSession = await mf.dispatchFetch(
      "https://publish.example/api/session",
      { headers: { Authorization: `Bearer ${agent}` } },
    );
    assert.equal(agentSession.status, 410);

    const logout = await mf.dispatchFetch(
      "https://publish.example/auth/logout",
      { method: "POST", headers: browser, body: "{}" },
    );
    assert.equal(logout.status, 200);
  } finally {
    await mf.dispose();
  }
});
