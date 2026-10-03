import type { Drafts, Strategy } from "./preparation-contracts.ts";

/** Shared check input for production and evaluation: only current copy is judged. */
export function currentCopyReview(
  strategy?: Pick<Strategy, "audience" | "objective">,
  drafts?: Pick<Drafts["drafts"][number], "alias" | "text">[],
) {
  return {
    reviewTarget: "current_saved_channel_text",
    reviewIntent: {
      audience: strategy?.audience,
      objective: strategy?.objective,
    },
    // Original rationale and claim annotations remain provenance, not current copy.
    drafts: drafts?.map(({ alias, text }) => ({ alias, text })),
  };
}
