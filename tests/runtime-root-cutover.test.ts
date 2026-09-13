import assert from "node:assert/strict";
import test from "node:test";
import { Response as RuntimeResponse } from "miniflare";
import { runtime } from "./runtime-fixture.ts";
import { environment } from "./helpers.ts";
import { credentialRoots, envelopeRoot, rootIdentifier, seal, unseal } from "../src/crypto.ts";

const release = "a".repeat(40), token = "b".repeat(64);
const next = Buffer.alloc(32, 23).toString("base64");
const workspace = "cutover-workspace";
const endpoint = "https://publish.example/internal/root-cutover";

test("Workers cutover preserves account/OAuth/GitHub authority across redeployment and records real readback without provider writes", async () => {
  let reads = 0;
  const f = await runtime(async request => {
    const url = new URL(request.url);
    assert.equal(request.method, "GET");
    assert.equal(url.href, "https://api.x.com/2/users/me");
    reads++;
    return RuntimeResponse.json({ data: { id: "user-1", username: "owner" } });
  }, { DEPLOY_ENV: "staging", RELEASE_SHA: release,
    X_OAUTH_CLIENT_ID: "x-client", X_OAUTH_CLIENT_SECRET: "client-secret" });
  let db = f.db;
  try {
    await db.prepare("INSERT INTO workspace_registry(workspace) VALUES (?)").bind(workspace).run();
    const connect = async (alias: string) => {
      const ns = await f.mf.getDurableObjectNamespace("WORKSPACES");
      return ns.get(ns.idFromName(workspace)).fetch("https://workspace/oauth/connect", {
      method: "POST", body: JSON.stringify({ workspace, actor: { workspace, id: "owner", scopes: ["admin"] },
        input: { alias, token: { provider: "x", accessToken: "private-access", refreshToken: "private-refresh",
          expiresAt: Date.now() + 86400000, scopes: ["tweet.read", "tweet.write", "users.read", "offline.access"] } } }),
    }); };
    const connected = await connect("account");
    assert.equal(connected.status, 200, await connected.text());
    const githubValue = { accessToken: "private-github-access", refreshToken: "private-github-refresh" };
    const original = await seal(githubValue, environment.ENCRYPTION_KEY, workspace + ":github:42", "2");
    await db.prepare("INSERT INTO github_installations(workspace,installation_id,account_id,account_login,account_type,user_id,user_login,repository_selection,status,credential,credential_revision,token_expires_at,refresh_expires_at,linked_at,last_verified_at,updated_at) VALUES (?,42,7,'acme','Organization',101,'owner','selected','linked',?,1,?,?,?,?,?)")
      .bind(workspace, original, Date.now() + 86400000, Date.now() + 86400000, Date.now(), Date.now(), Date.now()).run();
    const operator = (input: any, bearer = token, headers = {}) => f.mf.dispatchFetch(endpoint, {
      method: "POST", headers: { Authorization: `Bearer ${bearer}`, "Content-Type": "application/json", ...headers },
      body: JSON.stringify({ ...input, release: input.release || release }),
    });
    assert.equal((await operator({ action: "list" })).status, 403);
    await f.reconfigure({ ENCRYPTION_KEY_NEXT: next, ENCRYPTION_ROOT_WRITE: "next",
      ROOT_ROTATION_TOKEN: token, ROOT_ROTATION_EXPIRES_AT: String(Date.now() + 600000) });
    db = await f.mf.getD1Database("IDENTITY");
    assert.equal((await operator({ action: "list" }, "wrong")).status, 403);
    assert.equal((await operator({ action: "list" }, token, { Cookie: "__Host-session=owner" })).status, 403);
    assert.equal((await operator({ action: "list", release: "c".repeat(40) })).status, 409);
    assert.equal((await operator({ action: "migrate", workspace: "unknown" })).status, 409);
    const list: any = await (await operator({ action: "list" })).json();
    assert.deepEqual(list.workspaces, [workspace]);
    assert.equal(list.next, null);
    const inspection = await operator({ action: "inspect", workspace });
    assert.equal(inspection.status, 200, await inspection.clone().text());
    const before: any = await inspection.json();
    assert.equal(before.pending, 3);
    assert.equal(before.changed, 0);
    // A new account between inspection and commit must invalidate the exact manifest.
    const newer = await connect("new-account");
    assert.equal(newer.status, 200, await newer.text());
    assert.equal((await operator({ action: "migrate", workspace, expectedDigest: before.inventoryDigest })).status, 409);
    const fresh: any = await (await operator({ action: "inspect", workspace })).json();
    assert.equal(fresh.pending, 3);
    const migrated = await operator({ action: "migrate", workspace, expectedDigest: fresh.inventoryDigest });
    assert.equal(migrated.status, 200, await migrated.clone().text());
    const receipt: any = await migrated.json();
    assert.equal(receipt.changed, 3);
    assert.equal(receipt.pending, 0);
    assert.equal(receipt.verifiedComplete, false); // Requires a separate final inspection.
    const complete: any = await (await operator({ action: "inspect", workspace })).json();
    assert.equal(complete.verifiedComplete, true);
    assert.equal(complete.credentialCount, 5);
    const stored: any = await db.prepare("SELECT * FROM github_installations WHERE workspace=?").bind(workspace).first();
    assert.equal(stored.credential_revision, 2);
    assert.equal(stored.status, "linked");
    assert.equal(envelopeRoot(stored.credential), await rootIdentifier(next));
    assert.deepEqual(await unseal(stored.credential, credentialRoots({ ...environment, ENCRYPTION_KEY_NEXT: next }), workspace + ":github:42"), githubValue);
    await assert.rejects(unseal(stored.credential, environment.ENCRYPTION_KEY, workspace + ":github:42"));
    assert.equal(reads, 2);
    assert.doesNotMatch(JSON.stringify({ receipt, complete, list }), /private-access|private-refresh|private-github|replacement|ciphertext/);
    assert.equal((await db.prepare("SELECT count(*) AS n FROM external_effects").first<any>())!.n, 0);
    // A refresh lease blocks migration even when its wall-clock lease expired.
    await db.prepare("UPDATE github_installations SET refresh_lease=?,refresh_lease_until=? WHERE workspace=?")
      .bind("lease-still-uncertain", Date.now() - 1000, workspace).run();
    assert.equal((await operator({ action: "inspect", workspace })).status, 409);
    await f.reconfigure({ ROOT_ROTATION_EXPIRES_AT: "1" });
    assert.equal((await operator({ action: "list" })).status, 403);
  } finally { await f.mf.dispose(); }
});
