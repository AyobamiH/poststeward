import assert from "node:assert/strict";
import test from "node:test";
import { readFileSync } from "node:fs";

const wrangler = JSON.parse(readFileSync("wrangler.jsonc", "utf8"));
const deploy = readFileSync(".github/workflows/deploy.yml", "utf8");
const request = readFileSync(
  ".github/workflows/deploy-staging-request.yml",
  "utf8",
);
const edge = readFileSync("src/canary-edge.ts", "utf8");

test("alert scheduler adds five-minute delivery without replacing hourly identity expiry", () => {
  assert.deepEqual(wrangler.triggers.crons.sort(), ["*/5 * * * *", "17 * * * *"]);
  assert.match(edge, /controller\.cron === "17 \* \* \* \*"/);
  assert.match(edge, /sweepOperationalConditions\(env\)/);
  assert.match(edge, /flushOperationalAlerts\(env\)/);
});

test("alert webhook authority is optional protected deployment input", () => {
  for (const name of [
    "OPERATIONAL_ALERT_WEBHOOK_URL",
    "OPERATIONAL_ALERT_WEBHOOK_TOKEN",
  ]) {
    assert.ok(deploy.includes(`${name}:`));
    assert.ok(request.includes(`${name}:`));
    assert.ok(deploy.includes(`secrets.${name}`));
    assert.ok(request.includes(`secrets.${name}`));
  }
});

test("staging deployment remains non-destructive and alert delivery can stay unconfigured", () => {
  assert.match(request, /run_pitr_diagnostic: false/);
  assert.match(request, /Alert delivery remains inert unless a protected webhook destination is configured/);
});
