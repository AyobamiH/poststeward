import assert from "node:assert/strict";
import test from "node:test";
import { digest } from "../src/common.ts";
import { beginWorkspaceDeletion } from "../src/lifecycle.ts";
import { runtime } from "./runtime-fixture.ts";

const origin = "https://publish.example";

async function seedSession(db: D1Database, suffix: string, withProof = false) {
  const workspace = `github-lifecycle-workspace-${suffix}`;
  const actor = `github-lifecycle-owner-${suffix}`;
  const session = `github-lifecycle-session-${suffix}`;
  const csrf = `github-lifecycle-csrf-${suffix}`;
  const sessionHash = await digest(session);
  const now = Date.now();
  const statements = [
    db.prepare("INSERT INTO principals(subject,workspace,created_at) VALUES (?,?,?)").bind(actor, workspace, now),
    db.prepare("INSERT INTO sessions(token_hash,workspace,actor,expires_at,csrf) VALUES (?,?,?,?,?)").bind(sessionHash, workspace, actor, now + 3600000, csrf),
  ];
  if (withProof)
    statements.push(
      db.prepare(
        "INSERT INTO owner_proofs(session_hash,id,issuer,client_id,email_hash,email_verified,authenticated_at,release) VALUES (?,?,?,?,?,?,?,?)",
      ).bind(
        sessionHash,
        `github-lifecycle-proof-${suffix}`,
        "https://accounts.google.com",
        "poststeward-test",
        await digest("owner@example.com"),
        1,
        now,
        "test",
      ),
    );
  await db.batch(statements);
  return { workspace, actor, session, sessionHash, csrf, now };
}

function browserHeaders(owner: Awaited<ReturnType<typeof seedSession>>) {
  return {
    Cookie: `__Host-session=${owner.session}`,
    Origin: origin,
    "Content-Type": "application/json",
    "X-CSRF-Token": owner.csrf,
  };
}

async function count(db: D1Database, table: string, workspace: string) {
  const row = await db
    .prepare(`SELECT count(*) AS n FROM ${table} WHERE workspace=?`)
    .bind(workspace)
    .first<{ n: number }>();
  return Number(row?.n || 0);
}

test("pending workspace deletion fences every GitHub source control route before GitHub handling", async () => {
  const { mf, db } = await runtime(undefined, {
    OIDC_ISSUER: "https://accounts.google.com",
  });
  try {
    const owner = await seedSession(db, "fence");
    await beginWorkspaceDeletion(db, owner.workspace, owner.now + 1);
    const headers = browserHeaders(owner);
    const requests: Array<[string, RequestInit]> = [
      ["/api/sources/github/status", { headers: { Cookie: `__Host-session=${owner.session}` } }],
      ["/api/sources/github/start", { method: "POST", headers, body: "{}" }],
      ["/api/sources/github/unlink", { method: "POST", headers, body: JSON.stringify({ installationId: 42 }) }],
      ["/sources/github/setup?state=fake&installation_id=42", { headers: { Cookie: `__Host-session=${owner.session}` } }],
      ["/sources/github/callback?state=fake&code=fake", { headers: { Cookie: `__Host-session=${owner.session}` } }],
    ];
    for (const [path, init] of requests) {
      const response = await mf.dispatchFetch(origin + path, init);
      assert.equal(response.status, 410, path);
      const value: any = await response.json();
      assert.equal(value.error.code, "WORKSPACE_DELETION_IN_PROGRESS", path);
    }
  } finally {
    await mf.dispose();
  }
});

test("completed workspace erasure removes GitHub pending state, encrypted authority and repository links", async () => {
  const { mf, db } = await runtime(undefined, {
    OIDC_ISSUER: "https://accounts.google.com",
  });
  try {
    const owner = await seedSession(db, "erase", true);
    await db.batch([
      db.prepare(
        "INSERT INTO github_install_states(state_hash,session_hash,workspace,actor,verifier,installation_id,expires_at,created_at) VALUES (?,?,?,?,?,?,?,?)",
      ).bind(
        "github-lifecycle-state",
        owner.sessionHash,
        owner.workspace,
        owner.actor,
        "v".repeat(43),
        42,
        owner.now + 600000,
        owner.now,
      ),
      db.prepare(
        "INSERT INTO github_installations(workspace,installation_id,account_id,account_login,account_type,user_id,user_login,repository_selection,status,last_error,credential,credential_revision,token_expires_at,refresh_expires_at,linked_at,last_verified_at,updated_at) VALUES (?,?,?,?,?,?,?,'selected','linked',NULL,?,1,?,?,?,?,?)",
      ).bind(
        owner.workspace,
        42,
        7,
        "acme",
        "Organization",
        101,
        "owner-login",
        "encrypted-github-credential-fixture",
        owner.now + 3600000,
        owner.now + 86400000,
        owner.now,
        owner.now,
        owner.now,
      ),
      db.prepare(
        "INSERT INTO github_repository_links(workspace,repository_id,installation_id,full_name,private,linked_at,verified_at) VALUES (?,?,?,?,?,?,?)",
      ).bind(owner.workspace, 99, 42, "acme/private-repo", 1, owner.now, owner.now),
    ]);

    for (const table of [
      "github_install_states",
      "github_installations",
      "github_repository_links",
    ])
      assert.equal(await count(db, table, owner.workspace), 1, `seed ${table}`);

    const response = await mf.dispatchFetch(`${origin}/api/lifecycle/delete`, {
      method: "POST",
      headers: browserHeaders(owner),
      body: JSON.stringify({
        delete: true,
        confirmation: `DELETE ${owner.workspace}`,
      }),
    });
    assert.equal(response.status, 200, await response.clone().text());
    const value: any = await response.json();
    assert.equal(value.deleted, true);
    assert.equal(value.tombstoneRetained, true);

    for (const table of [
      "github_install_states",
      "github_installations",
      "github_repository_links",
    ])
      assert.equal(await count(db, table, owner.workspace), 0, `purge ${table}`);
  } finally {
    await mf.dispose();
  }
});
