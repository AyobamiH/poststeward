import assert from "node:assert/strict";
import test from "node:test";
import { Pilot } from "../src/pilot.ts";
import { Engine } from "../src/engine.ts";
import { harness, owner } from "./helpers.ts";
import type { Account } from "../src/types.ts";
import type { OwnerAuthority } from "../src/owner-proof.ts";

async function linkedInPilot(readback: boolean) {
  const h = harness();
  await h.setup("linkedin");
  const account = h.store.get<Account>("account:account")!;
  account.capabilities = { oauth: true, refresh: false, readback };
  h.store.put("account:account", account);
  const authority: OwnerAuthority = {
    sessionHash: "linkedin-owner-session",
    proof: {
      id: crypto.randomUUID(),
      issuer: "https://accounts.google.com",
      subject: owner.id,
      workspace: owner.workspace,
      authenticatedAt: h.now(),
      expiresAt: h.now() + 86400000,
      release: h.env.RELEASE_SHA,
    },
  };
  const engine = new Engine(h.store, h.env, h.provider, h.options);
  const pilot = new Pilot(
    h.store,
    h.env,
    engine,
    h.provider,
    h.options.authorized,
    h.now,
  );
  return { ...h, authority, engine, pilot };
}

test("controlled LinkedIn acceptance is enabled only by an explicit readback capability", async () => {
  const denied = await linkedInPilot(false);
  await assert.rejects(
    denied.pilot.run(
      "prepare",
      { alias: "account", text: "Exact LinkedIn acceptance copy." },
      owner,
      denied.authority,
    ),
    { code: "READBACK_UNSUPPORTED" },
  );

  const allowed = await linkedInPilot(true);
  const preview: any = await allowed.pilot.run(
    "prepare",
    { alias: "account", text: "Exact LinkedIn acceptance copy." },
    owner,
    allowed.authority,
  );
  assert.equal(preview.record.account.provider, "linkedin");
  assert.equal(preview.record.account.capabilities.readback, true);
  assert.equal(preview.delivery, null);
  assert.equal(allowed.calls.publish, 0);
});

test("losing LinkedIn readback authority after review blocks confirmation before publication", async () => {
  const h = await linkedInPilot(true);
  const preview: any = await h.pilot.run(
    "prepare",
    { alias: "account", text: "Review authority must remain stable." },
    owner,
    h.authority,
  );
  const account = h.store.get<Account>("account:account")!;
  account.capabilities = { ...account.capabilities!, readback: false };
  h.store.put("account:account", account);
  await assert.rejects(
    h.pilot.run(
      "confirm",
      {
        reviewId: preview.record.id,
        reviewDigest: preview.record.reviewDigest,
        approve: true,
      },
      owner,
      h.authority,
    ),
    { code: "READBACK_AUTHORITY_CHANGED" },
  );
  assert.equal(h.store.list("delivery:").length, 0);
  assert.equal(h.calls.publish, 0);
});
