import assert from "node:assert/strict";
import test from "node:test";
import { operationalAlertSecrets } from "../scripts/operational-alert-config.mjs";

test("alert delivery is optional and inert without protected configuration", () => {
  assert.deepEqual(operationalAlertSecrets({}), {});
});

test("bounded HTTPS webhook with optional token is accepted", () => {
  assert.deepEqual(
    operationalAlertSecrets({
      OPERATIONAL_ALERT_WEBHOOK_URL: "https://alerts.example/hooks/poststeward",
      OPERATIONAL_ALERT_WEBHOOK_TOKEN: "protected-test-token",
    }),
    {
      OPERATIONAL_ALERT_WEBHOOK_URL: "https://alerts.example/hooks/poststeward",
      OPERATIONAL_ALERT_WEBHOOK_TOKEN: "protected-test-token",
    },
  );
});

test("alert token cannot exist without a webhook URL", () => {
  assert.throws(
    () => operationalAlertSecrets({ OPERATIONAL_ALERT_WEBHOOK_TOKEN: "protected-test-token" }),
    /cannot be configured without/,
  );
});

test("alert webhook rejects insecure or credential-bearing URLs", () => {
  for (const url of [
    "http://alerts.example/hook",
    "https://user:pass@alerts.example/hook",
  ])
    assert.throws(
      () => operationalAlertSecrets({ OPERATIONAL_ALERT_WEBHOOK_URL: url }),
      /bounded HTTPS URL without embedded credentials/,
    );
});
