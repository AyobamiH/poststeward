import assert from "node:assert/strict";
import test from "node:test";
import { runtime } from "./runtime-fixture.ts";

test("owner-proof expansion preserves the old Worker SQL contract and cascades only dependent records", async () => {
  const { mf, db } = await runtime();
  try {
    // This is the exact four-value write shape used before the new deployment.
    await db.prepare("INSERT INTO login_states SELECT ?,?,?,? WHERE (SELECT count(*) FROM login_states) < 10000")
      .bind("legacy-state", "verifier", "nonce", Date.now() + 60000).run();
    await db.prepare("INSERT INTO login_return_paths VALUES (?,?)").bind("legacy-state", "/pilot").run();
    await db.prepare("INSERT INTO login_states VALUES (?,?,?,?)").bind("unrelated", "v", "n", Date.now() + 60000).run();
    const oldCallback = await db.prepare("DELETE FROM login_states WHERE state_hash=? AND expires_at>? RETURNING *")
      .bind("legacy-state", Date.now()).first<any>();
    assert.equal(oldCallback.verifier, "verifier");
    assert.equal(await db.prepare("SELECT * FROM login_return_paths WHERE state_hash=?").bind("legacy-state").first(), null);
    assert.ok(await db.prepare("SELECT * FROM login_states WHERE state_hash=?").bind("unrelated").first());
  } finally { await mf.dispose(); }
});
