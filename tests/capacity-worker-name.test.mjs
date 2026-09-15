import assert from "node:assert/strict";
import test from "node:test";
import { capacityWorkerName } from "../scripts/capacity-observe.mjs";

test("capacity uses the already-established Worker resource names", () => {
  assert.equal(capacityWorkerName("staging"), "poststeward-staging");
  assert.equal(capacityWorkerName("production"), "poststeward");
  for (const value of [undefined, null, "", "development", "prod"])
    assert.throws(() => capacityWorkerName(value));
});
