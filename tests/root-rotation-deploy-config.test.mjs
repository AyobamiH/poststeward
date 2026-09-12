import assert from "node:assert/strict";
import test from "node:test";
import {
  appendRootRotationSecrets,
  configureRootRotation,
} from "../scripts/root-rotation-deploy-config.mjs";

const oldRoot = Buffer.alloc(32, 21).toString("base64");
const newRoot = Buffer.alloc(32, 22).toString("base64");
const base = {
  vars: {
    PUBLISHING_PAUSED: "false",
    ROOT_ROTATION_MODE: "off",
    ROOT_ROTATION_ID: "",
  },
};

test("off mode keeps rotation disabled and refuses a stale next root", () => {
  const configured = configureRootRotation(base, {
    ROOT_ROTATION_MODE: "off",
    PUBLISHING_PAUSED: "false",
  });
  assert.equal(configured.vars.ROOT_ROTATION_MODE, "off");
  assert.equal(configured.vars.PUBLISHING_PAUSED, "false");
  assert.throws(
    () =>
      appendRootRotationSecrets(
        { ENCRYPTION_KEY: oldRoot },
        {
          ROOT_ROTATION_MODE: "off",
          ENCRYPTION_KEY: oldRoot,
          ENCRYPTION_KEY_NEXT: newRoot,
        },
      ),
    /Remove ENCRYPTION_KEY_NEXT/,
  );
});

test("rewrap mode requires paused staging and deploys only the next root binding", () => {
  const env = {
    DEPLOY_ENV: "staging",
    ROOT_ROTATION_MODE: "rewrap",
    ROOT_ROTATION_ID: "acceptance-20260912-root",
    PUBLISHING_PAUSED: "true",
    ENCRYPTION_KEY: oldRoot,
    ENCRYPTION_KEY_NEXT: newRoot,
  };
  const configured = configureRootRotation(base, env);
  assert.equal(configured.vars.ROOT_ROTATION_MODE, "rewrap");
  assert.equal(configured.vars.ROOT_ROTATION_ID, "acceptance-20260912-root");
  assert.equal(configured.vars.PUBLISHING_PAUSED, "true");
  const secrets = appendRootRotationSecrets(
    { ENCRYPTION_KEY: oldRoot, OIDC_CLIENT_SECRET: "not-a-real-secret" },
    env,
  );
  assert.equal(secrets.ENCRYPTION_KEY_NEXT, newRoot);
  assert.equal(JSON.stringify(configured).includes(newRoot), false);
});

test("rewrap rejects production, unpaused maintenance and root reuse", () => {
  for (const env of [
    {
      DEPLOY_ENV: "production",
      ROOT_ROTATION_MODE: "rewrap",
      ROOT_ROTATION_ID: "acceptance-20260912-root",
      PUBLISHING_PAUSED: "true",
    },
    {
      DEPLOY_ENV: "staging",
      ROOT_ROTATION_MODE: "rewrap",
      ROOT_ROTATION_ID: "acceptance-20260912-root",
      PUBLISHING_PAUSED: "false",
    },
  ])
    assert.throws(() => configureRootRotation(base, env));

  assert.throws(() =>
    appendRootRotationSecrets(
      { ENCRYPTION_KEY: oldRoot },
      {
        DEPLOY_ENV: "staging",
        ROOT_ROTATION_MODE: "rewrap",
        ROOT_ROTATION_ID: "acceptance-20260912-root",
        PUBLISHING_PAUSED: "true",
        ENCRYPTION_KEY: oldRoot,
        ENCRYPTION_KEY_NEXT: oldRoot,
      },
    ),
  );
});

test("verify mode requires the protected next-root slot to be removed", () => {
  const env = {
    DEPLOY_ENV: "staging",
    ROOT_ROTATION_MODE: "verify",
    ROOT_ROTATION_ID: "acceptance-20260912-root",
    PUBLISHING_PAUSED: "true",
    ENCRYPTION_KEY: newRoot,
  };
  const configured = configureRootRotation(base, env);
  assert.equal(configured.vars.ROOT_ROTATION_MODE, "verify");
  assert.doesNotThrow(() =>
    appendRootRotationSecrets({ ENCRYPTION_KEY: newRoot }, env),
  );
  assert.throws(() =>
    appendRootRotationSecrets(
      { ENCRYPTION_KEY: newRoot },
      { ...env, ENCRYPTION_KEY_NEXT: newRoot },
    ),
  );
});
