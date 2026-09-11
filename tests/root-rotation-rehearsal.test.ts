import assert from "node:assert/strict";
import test from "node:test";
import { seal, unseal } from "../src/crypto.ts";
import {
  rewrapCredentialEnvelope,
  verifyRewrappedEnvelope,
} from "../src/root-rotation.ts";

const oldRoot = Buffer.alloc(32, 17).toString("base64");
const newRoot = Buffer.alloc(32, 29).toString("base64");
const context = "workspace:rotation-canary";

test("root-secret replacement can rewrap a credential without exposing plaintext evidence", async () => {
  const secret = {
    accessToken: "test-only-secret",
    refreshToken: "test-only-refresh",
    expiresAt: 123456789,
  };
  const original = await seal(secret, oldRoot, context, "2");
  const rewrapped = await rewrapCredentialEnvelope(
    original,
    oldRoot,
    newRoot,
    context,
    "2",
  );
  assert.notEqual(original, rewrapped);
  assert.deepEqual(await unseal(rewrapped, newRoot, context), secret);
  await assert.rejects(unseal(rewrapped, oldRoot, context));

  assert.deepEqual(
    await verifyRewrappedEnvelope(
      original,
      rewrapped,
      oldRoot,
      newRoot,
      context,
    ),
    {
      originalVersion: "2",
      rewrappedVersion: "2",
      newRootReads: true,
      oldRootRejectedForRewrapped: true,
    },
  );
});

test("rewrap fails closed for the wrong old root or authenticated context", async () => {
  const original = await seal({ token: "secret" }, oldRoot, context, "2");
  await assert.rejects(
    rewrapCredentialEnvelope(original, newRoot, newRoot, context, "2"),
  );
  await assert.rejects(
    rewrapCredentialEnvelope(
      original,
      oldRoot,
      newRoot,
      "another-workspace:rotation-canary",
      "2",
    ),
  );
});
