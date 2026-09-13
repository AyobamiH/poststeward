import assert from "node:assert/strict";
import test from "node:test";
import { seal, unseal } from "../src/crypto.ts";
import { credentialInventory, rehearseRootInventory, type RotationSnapshot } from "../src/root-rotation-inventory.ts";

const oldRoot = Buffer.alloc(32, 17).toString("base64");
const nextRoot = Buffer.alloc(32, 29).toString("base64");
async function fixture(): Promise<RotationSnapshot> {
  return { release: "a".repeat(40), workspaceIds: ["one", "two"],
    complete: { workspaces: true, records: true, githubInstallations: true },
    workspaces: [
      { workspace: "one", records: [
        { key: "account:social", value: { alias: "social", secret: await seal({ accessToken: "PRIVATE_ACCESS" }, oldRoot, "one:social") } },
        { key: "oauth:social", value: { alias: "social", secret: await seal({ refreshToken: "PRIVATE_REFRESH" }, oldRoot, "one:oauth:social") } },
      ] }, { workspace: "two", records: [] },
    ], githubInstallations: [{ workspace: "two", installation_id: 123, credential_revision: 1,
      refresh_lease: null, credential: await seal({ accessToken: "PRIVATE_GITHUB" }, oldRoot, "two:github:123") }],
  };
}
test("full inventory rewrap covers accounts, OAuth refresh and GitHub across workspaces without leaking proof", async () => {
  const snapshot = await fixture();
  const before = JSON.stringify(snapshot);
  const result = await rehearseRootInventory(snapshot, oldRoot, nextRoot);
  assert.equal(result.evidence.credentialCount, 3);
  assert.equal(result.evidence.verifiedCount, 3);
  assert.equal(result.evidence.liveCutoverPerformed, false);
  assert.doesNotMatch(JSON.stringify(result.evidence), /PRIVATE_|accessToken|refreshToken|"replacement"|"iv"|"data"/);
  assert.equal(JSON.stringify(snapshot), before);
  for (const entry of result.replacements) {
    assert.ok(await unseal(entry.replacement, nextRoot, entry.context));
    await assert.rejects(unseal(entry.replacement, oldRoot, entry.context));
  }
});
test("incomplete or missing workspace inventory cannot pass rehearsal", async () => {
  const snapshot = await fixture();
  snapshot.complete.githubInstallations = false;
  assert.throws(() => credentialInventory(snapshot), /complete/);
  snapshot.complete.githubInstallations = true;
  snapshot.workspaces.pop();
  assert.throws(() => credentialInventory(snapshot), /Every inventoried/);
});
test("in-flight GitHub refresh and unknown credential families block rotation", async () => {
  const snapshot = await fixture();
  snapshot.githubInstallations[0].refresh_lease = "in-flight";
  assert.throws(() => credentialInventory(snapshot), /in flight/);
  snapshot.githubInstallations[0].refresh_lease = null;
  snapshot.workspaces[0].records.push({ key: "new-service:secret", value: { secret: "unmapped" } });
  assert.throws(() => credentialInventory(snapshot), /Unknown credential/);
});
test("wrong context and unchanged root cannot produce a successful inventory", async () => {
  const snapshot = await fixture();
  await assert.rejects(rehearseRootInventory(snapshot, oldRoot, oldRoot), /must differ/);
  snapshot.workspaces[0].records[0].value.secret = await seal({ accessToken: "secret" }, oldRoot, "wrong-context");
  await assert.rejects(rehearseRootInventory(snapshot, oldRoot, nextRoot));
});
