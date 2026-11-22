import assert from "node:assert/strict";
import test from "node:test";
import { classifyAutomaticPublication } from "../src/slo-producer-evidence.ts";

const due = { automatic: true, dueAt: 1000 };
test("due automatic delivery without post ID is eligible, not published", () => {
  assert.deepEqual(
    classifyAutomaticPublication(
      due,
      {
        status: "uncertain",
        post_id: null,
        published_at: null,
        updated_at: 5000,
      },
      6000,
    ),
    {
      eligible: true,
      published: false,
      verified: false,
      dueAt: 1000,
      publishedAt: null,
      verifiedAt: null,
    },
  );
});
test("provider intent timestamp is never a completed-publication timestamp", () => {
  const result = classifyAutomaticPublication(
    due,
    {
      status: "created",
      post_id: "provider-1",
      published_at: null,
      updated_at: 2000,
    },
    6000,
  );
  assert.equal(result.eligible, true);
  assert.equal(result.published, false);
});
test("verified provider completion gives true due-to-publish and verify times", () => {
  const result = classifyAutomaticPublication(
    due,
    {
      status: "verified",
      post_id: "provider-1",
      published_at: 4000,
      updated_at: 4500,
    },
    6000,
  );
  assert.equal(result.publishedAt! - result.dueAt!, 3000);
  assert.equal(result.verifiedAt! - result.publishedAt!, 500);
  assert.equal(result.verified, true);
});
test("unverified result never becomes provider-verified reconciliation", () => {
  const result = classifyAutomaticPublication(
    due,
    {
      status: "unverified",
      post_id: "provider-1",
      published_at: 4000,
      updated_at: 4500,
    },
    6000,
  );
  assert.equal(result.published, true);
  assert.equal(result.verified, false);
});
test("future or manual deliveries are not eligible", () => {
  assert.equal(
    classifyAutomaticPublication(
      { automatic: true, dueAt: 7000 },
      undefined,
      6000,
    ).eligible,
    false,
  );
  assert.equal(
    classifyAutomaticPublication(
      { automatic: false, dueAt: 1000 },
      undefined,
      6000,
    ).eligible,
    false,
  );
});
