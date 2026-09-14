import assert from "node:assert/strict";
import test from "node:test";
import { Fault } from "../src/common.ts";
import {
  captureCurrentRecoveryBookmark,
  captureRecoveryBookmarks,
} from "../src/recovery-pitr.ts";

function assertFault(error: unknown, code: string) {
  assert.ok(error instanceof Fault);
  assert.equal(error.code, code);
  assert.equal(error.status, 502);
  return true;
}

test("exact checkpoint capture synchronises and reads only the current bookmark", async () => {
  const calls: string[] = [];
  const bookmark = await captureCurrentRecoveryBookmark({
    async sync() {
      calls.push("sync");
    },
    async getCurrentBookmark() {
      calls.push("current");
      return "current-bookmark-exact-0001";
    },
    async getBookmarkForTime() {
      calls.push("target");
      throw new Error("time lookup must not run");
    },
  });
  assert.equal(bookmark, "current-bookmark-exact-0001");
  assert.deepEqual(calls, ["sync", "current"]);
});

test("PITR bookmark capture synchronises then reads current and target sequentially", async () => {
  const calls: string[] = [];
  const result = await captureRecoveryBookmarks(
    {
      async sync() {
        calls.push("sync");
      },
      async getCurrentBookmark() {
        calls.push("current");
        return "current-bookmark-0001";
      },
      async getBookmarkForTime(value) {
        calls.push(`target:${value}`);
        return "target-bookmark-0001";
      },
    },
    123456,
  );
  assert.deepEqual(calls, ["sync", "current", "target:123456"]);
  assert.deepEqual(result, {
    preRestoreBookmark: "current-bookmark-0001",
    targetBookmark: "target-bookmark-0001",
  });
});

test("PITR sync failure is named and stops before bookmark reads", async () => {
  let currentCalled = false;
  await assert.rejects(
    () =>
      captureRecoveryBookmarks(
        {
          async sync() {
            throw new Error("platform detail must not escape");
          },
          async getCurrentBookmark() {
            currentCalled = true;
            return "never";
          },
          async getBookmarkForTime() {
            return "never";
          },
        },
        1,
      ),
    (error) => assertFault(error, "RECOVERY_PITR_SYNC_FAILED"),
  );
  assert.equal(currentCalled, false);
});

test("PITR current-bookmark failure is named and target lookup never starts", async () => {
  let targetCalled = false;
  await assert.rejects(
    () =>
      captureRecoveryBookmarks(
        {
          async getCurrentBookmark() {
            throw new Error("opaque platform failure");
          },
          async getBookmarkForTime() {
            targetCalled = true;
            return "never";
          },
        },
        1,
      ),
    (error) => assertFault(error, "RECOVERY_PITR_CURRENT_BOOKMARK_FAILED"),
  );
  assert.equal(targetCalled, false);
});

test("PITR target-bookmark failure is named only after current bookmark succeeds", async () => {
  let currentCalled = false;
  await assert.rejects(
    () =>
      captureRecoveryBookmarks(
        {
          async getCurrentBookmark() {
            currentCalled = true;
            return "current-bookmark-0001";
          },
          async getBookmarkForTime() {
            throw new Error("opaque platform failure");
          },
        },
        1,
      ),
    (error) => assertFault(error, "RECOVERY_PITR_TARGET_BOOKMARK_FAILED"),
  );
  assert.equal(currentCalled, true);
});
