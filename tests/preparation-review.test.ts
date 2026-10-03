import test from "node:test";
import assert from "node:assert/strict";
import { currentCopyReview } from "../src/preparation-review.ts";

test("shared editorial check preserves exact current assertions and excludes stale annotations", () => {
  const strategy = {
    audience: "Operations teams",
    objective: "Explain the synthetic fixture",
    positioning: "Old launch claim removed by owner",
    changes: [{ fact: "Old benefit removed by owner" }],
  };
  const drafts = [
    {
      alias: "threads",
      text: "Synthetic fixture — not a deployed feature.\nRead the notes.",
      rationale: "Old readability claim removed by owner",
      claims: [{ claim: "Old unsupported benefit" }],
    },
    {
      alias: "x",
      text: "100% secure",
      rationale: "This current assertion must still be checked",
      claims: [],
    },
  ];
  const before = structuredClone({ strategy, drafts });
  const review = currentCopyReview(strategy, drafts);
  assert.deepEqual(review, {
    reviewTarget: "current_saved_channel_text",
    reviewIntent: {
      audience: strategy.audience,
      objective: strategy.objective,
    },
    drafts: drafts.map(({ alias, text }) => ({ alias, text })),
  });
  assert.deepEqual({ strategy, drafts }, before);
  assert.ok(!JSON.stringify(review).includes("removed by owner"));
  assert.equal(review.drafts?.[1].text, "100% secure");
});
