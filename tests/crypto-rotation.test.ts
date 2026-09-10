import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import { envelopeVersion, seal, unseal } from "../src/crypto.ts";

const root = Buffer.alloc(32, 7).toString("base64");
const context = "workspace:account";

test("legacy v1 ciphertext remains readable while v2 uses different derived key material", async () => {
  const value = { accessToken: "test-secret-value", expiresAt: 123 };
  const legacy = await seal(value, root, context, "1");
  const rotated = await seal(value, root, context, "2");
  assert.equal(envelopeVersion(legacy), "1");
  assert.equal(envelopeVersion(rotated), "2");
  assert.notEqual(JSON.parse(legacy).data, JSON.parse(rotated).data);
  assert.deepEqual(await unseal(legacy, root, context), value);
  assert.deepEqual(await unseal(rotated, root, context), value);
});

test("pre-version envelopes remain compatible with the original raw-key format", async () => {
  const value = { refreshToken: "legacy-refresh-secret" };
  const parsed = JSON.parse(await seal(value, root, context, "1"));
  delete parsed.version;
  assert.deepEqual(await unseal(JSON.stringify(parsed), root, context), value);
});

test("key version and authenticated context cannot be tampered without detection", async () => {
  const encrypted = await seal({ accessToken: "secret" }, root, context, "2");
  await assert.rejects(unseal(encrypted, root, "another-workspace:account"));
  const tampered = JSON.parse(encrypted);
  tampered.version = "3";
  await assert.rejects(unseal(JSON.stringify(tampered), root, context));
  await assert.rejects(seal({}, root, context, "0"), {
    code: "ENCRYPTION_VERSION_INVALID",
  });
});

test("new deployments write credential envelopes with key schedule version 2", () => {
  const config = JSON.parse(readFileSync("wrangler.jsonc", "utf8"));
  assert.equal(config.vars.ENCRYPTION_KEY_VERSION, "2");
});
