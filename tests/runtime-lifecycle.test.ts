import assert from "node:assert/strict";
import test from "node:test";
import { digest } from "../src/common.ts";
import { runtime } from "./runtime-fixture.ts";

async function fixture() {
  const { mf, db } = await runtime(undefined, {
    OIDC_ISSUER: "https://accounts.google.com",
    OIDC_CLIENT_ID: "poststeward-test",
    ALLOWED_OWNER_EMAILS: "owner@example.com",
  });
  const workspace = "00000000-0000-4000-8000-0000000000de";
  const actor = "owner-subject-hash";
  const token = "owner-session-for-delete";
  const tokenHash = await digest(token);
  const csrf = "delete-csrf";
  const now = Date.now();
  await db.batch([
    db.prepare("INSERT INTO principals VALUES (?,?,?)").bind(actor, workspace, now),
    db.prepare("INSERT INTO sessions VALUES (?,?,?,?,?)").bind(tokenHash, workspace, actor, now + 86400000, csrf),
    db.prepare(
      "INSERT INTO owner_proofs(session_hash,id,issuer,client_id,email_hash,email_verified,authenticated_at,release) VALUES (?,?,?,?,?,?,?,?)",
    ).bind(tokenHash, crypto.randomUUID(), "https://accounts.google.com", "poststeward-test", await digest("owner@example.com"), 1, now, "test"),
    db.prepare("INSERT INTO grants VALUES (?,?,?,?,?,NULL)").bind("grant-hash", workspace, "agent", '["read"]', now + 3600000),
    db.prepare("INSERT INTO stripe_customers VALUES (?,?)").bind("cus_fixture", workspace),
    db.prepare("INSERT INTO stripe_events VALUES (?,?,?,NULL)").bind("evt_fixture", workspace, now),
    db.prepare(
      "INSERT INTO external_effects(workspace,fingerprint,delivery_id,provider,text_digest,status,created_at,updated_at) VALUES (?,?,?,?,?,'uncertain',?,?)",
    ).bind(workspace, "fingerprint", "delivery", "x", "digest", now, now),
    db.prepare(
      "INSERT INTO external_containers(workspace,fingerprint,delivery_id,status,created_at,updated_at) VALUES (?,?,?,'uncertain',?,?)",
    ).bind(workspace, "container", "delivery", now, now),
    db.prepare(
      "INSERT INTO workspace_controls(workspace,publishing_quarantined,reason,quarantined_at,updated_at) VALUES (?,0,NULL,NULL,?)",
    ).bind(workspace, now),
    db.prepare(
      "INSERT INTO workspace_recovery_plans(id,workspace,actor,target_time,target_bookmark,pre_restore_bookmark,undo_bookmark,reason,digest,state,created_at,expires_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,'cancelled',?,?,?)",
    ).bind(crypto.randomUUID(), workspace, actor, now - 60000, "target", "before", null, "old cancelled recovery", "f".repeat(64), now, now + 600000, now),
    db.prepare(
      "INSERT INTO provider_oauth_states(state_hash,session_hash,workspace,actor,provider,alias,verifier,expires_at,created_at,return_path) VALUES (?,?,?,?,?,?,?,?,?,?)",
    ).bind("oauth-state", tokenHash, workspace, actor, "x", "account", "verifier", now + 600000, now, "/app"),
  ]);
  const headers = {
    Cookie: `__Host-session=${token}`,
    Origin: "https://publish.example",
    "Content-Type": "application/json",
    "X-CSRF-Token": csrf,
  };
  return { mf, db, workspace, actor, token, headers };
}

async function count(db: D1Database, table: string, workspace: string) {
  const row = await db
    .prepare(`SELECT count(*) AS n FROM ${table} WHERE workspace=?`)
    .bind(workspace)
    .first<{ n: number }>();
  return Number(row?.n || 0);
}

test("workspace erasure requires fresh owner confirmation, clears recoverable state and leaves only the tombstone", async () => {
  const f = await fixture();
  try {
    const initial = await f.mf.dispatchFetch(
      "https://publish.example/api/operations/workspace_status",
      { method: "POST", headers: f.headers, body: "{}" },
    );
    assert.equal(initial.status, 200, await initial.clone().text());

    const wrong = await f.mf.dispatchFetch(
      "https://publish.example/api/lifecycle/delete",
      {
        method: "POST",
        headers: f.headers,
        body: JSON.stringify({ delete: true, confirmation: "DELETE wrong" }),
      },
    );
    assert.equal(wrong.status, 409);
    assert.equal(await count(f.db, "workspace_deletions", f.workspace), 0);

    const crossOrigin = await f.mf.dispatchFetch(
      "https://publish.example/api/lifecycle/delete",
      {
        method: "POST",
        headers: { ...f.headers, Origin: "https://hostile.example" },
        body: JSON.stringify({
          delete: true,
          confirmation: `DELETE ${f.workspace}`,
        }),
      },
    );
    assert.equal(crossOrigin.status, 403);

    const response = await f.mf.dispatchFetch(
      "https://publish.example/api/lifecycle/delete",
      {
        method: "POST",
        headers: f.headers,
        body: JSON.stringify({
          delete: true,
          confirmation: `DELETE ${f.workspace}`,
        }),
      },
    );
    assert.equal(response.status, 200, await response.clone().text());
    const result: any = await response.json();
    assert.equal(result.deleted, true);
    assert.equal(result.tombstoneRetained, true);
    assert.match(response.headers.get("set-cookie") || "", /Max-Age=0/);

    for (const table of [
      "principals",
      "sessions",
      "grants",
      "stripe_customers",
      "stripe_events",
      "external_effects",
      "external_containers",
      "workspace_controls",
      "workspace_recovery_plans",
      "provider_oauth_states",
    ]) assert.equal(await count(f.db, table, f.workspace), 0, table);
    assert.equal(
      Number(
        (
          await f.db
            .prepare(
              "SELECT count(*) AS n FROM owner_proofs WHERE session_hash=?",
            )
            .bind(await digest(f.token))
            .first<any>()
        )?.n || 0,
      ),
      0,
    );
    const tombstone = await f.db
      .prepare("SELECT state,completed_at FROM workspace_deletions WHERE workspace=?")
      .bind(f.workspace)
      .first<any>();
    assert.equal(tombstone?.state, "completed");
    assert.ok(tombstone?.completed_at);

    assert.equal(
      (
        await f.mf.dispatchFetch("https://publish.example/api/session", {
          headers: { Cookie: `__Host-session=${f.token}` },
        })
      ).status,
      401,
    );

    const namespace = await f.mf.getDurableObjectNamespace("WORKSPACES");
    const stub = namespace.get(namespace.idFromName(f.workspace));
    const stale = await stub.fetch("https://workspace.internal/operation", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        workspace: f.workspace,
        actor: { workspace: f.workspace, id: f.actor, scopes: ["admin"] },
        name: "workspace_status",
        input: {},
      }),
    });
    assert.equal(stale.status, 410);
  } finally {
    await f.mf.dispose();
  }
});

test("agent authority and active recovery cannot erase a workspace", async () => {
  const f = await fixture();
  try {
    const agentToken = "agent-delete-attempt";
    await f.db
      .prepare("INSERT INTO grants VALUES (?,?,?,?,?,NULL)")
      .bind(
        await digest(agentToken),
        f.workspace,
        "agent-delete",
        '["admin"]',
        Date.now() + 60000,
      )
      .run();
    const agent = await f.mf.dispatchFetch(
      "https://publish.example/api/lifecycle/delete",
      {
        method: "POST",
        headers: {
          Authorization: `Bearer ${agentToken}`,
          "Content-Type": "application/json",
        },
        body: JSON.stringify({
          delete: true,
          confirmation: `DELETE ${f.workspace}`,
        }),
      },
    );
    assert.equal(agent.status, 403);

    await f.db
      .prepare(
        "UPDATE workspace_controls SET publishing_quarantined=1,reason='active recovery',quarantined_at=?,updated_at=? WHERE workspace=?",
      )
      .bind(Date.now(), Date.now(), f.workspace)
      .run();
    const recovery = await f.mf.dispatchFetch(
      "https://publish.example/api/lifecycle/delete",
      {
        method: "POST",
        headers: f.headers,
        body: JSON.stringify({
          delete: true,
          confirmation: `DELETE ${f.workspace}`,
        }),
      },
    );
    assert.equal(recovery.status, 409);
    assert.equal(await count(f.db, "workspace_deletions", f.workspace), 0);
  } finally {
    await f.mf.dispose();
  }
});
