/** Explicit evaluation; authored references never masquerade as model outputs. */
import { readFile, mkdir, writeFile } from "node:fs/promises";
import { openAIModel } from "../src/preparation-model.ts";
import {
  strategySchema,
  draftsSchema,
  critiqueSchema,
} from "../src/preparation-contracts.ts";
import { publicationParts } from "../src/publications.ts";

const live = process.argv.includes("--live");
if (!live && !process.argv.includes("--references"))
  throw new Error(
    "Choose --references (no model calls) or --live (your own approved API account).",
  );
const key = process.env.POSTSTEWARD_EVALUATION_API_KEY;
if (live && !key)
  throw new Error(
    "Live evaluation requires POSTSTEWARD_EVALUATION_API_KEY from secure environment settings. A ChatGPT subscription is not API access. No calls made.",
  );
const cases = JSON.parse(
  await readFile("tests/fixtures/preparation/releases.json", "utf8"),
);
const result: any[] = [];
const model = openAIModel();
let calls = 0;
for (const item of cases) {
  const baseline = `Development update for example/product: commit ${"a".repeat(40)}. Review https://github.com/example/product.`;
  let text = item.referenceDraft,
    strategy: any = null,
    critique: any = null,
    usage: any[] = [];
  if (live) {
    const material: any = {
      context: {
        audience: item.audience,
        objective: item.objective,
        brandVoice: "Specific, restrained British English",
        productContext:
          "Synthetic release evaluation only; the source does not describe a deployed PostSteward capability.",
        exclusions:
          "No unsupported pricing, availability, performance, security or legal claims.",
        callToAction: "Read the release notes",
      },
      evidence: [
        {
          id: "release",
          kind: "release",
          url: "https://github.com/example/product/releases/tag/fixture",
          text: item.source,
        },
      ],
      coverage:
        "Synthetic bounded release fixture; no real repository or publication.",
      gaps: [],
      channels: [{ alias: "fixture_x", provider: "x" }],
    };
    const interpreted = await model(
      key!,
      "interpret",
      material,
      strategySchema,
    );
    calls++;
    usage.push(interpreted);
    strategy = strategySchema.parse(interpreted.value);
    if (strategy.missingContext.length) text = "";
    else {
      const drafted = await model(
        key!,
        "draft",
        { ...material, strategy },
        draftsSchema,
      );
      calls++;
      usage.push(drafted);
      const drafts = draftsSchema.parse(drafted.value);
      text = drafts.drafts.map((d) => d.text).join("\n\n");
      const checked = await model(
        key!,
        "check",
        { ...material, strategy, drafts: drafts.drafts },
        critiqueSchema,
      );
      calls++;
      usage.push(checked);
      critique = critiqueSchema.parse(checked.value);
      for (const claim of [
        ...strategy.changes,
        ...drafts.drafts.flatMap((d) => d.claims),
      ])
        for (const ref of claim.sources)
          if (ref.evidence !== "release" || !item.source.includes(ref.quote))
            throw new Error(
              "Unsupported quote in " +
                item.id +
                "; evaluation stops without publication.",
            );
    }
  }
  const words = (value: string) =>
    new Set(value.toLowerCase().match(/[a-z]+/g) || []);
  const a = words(text),
    b = words(baseline),
    similarity =
      [...a].filter((word) => b.has(word)).length / new Set([...a, ...b]).size;
  if (text) publicationParts("x", text);
  result.push({
    id: item.id,
    label: live
      ? "Live model output; editorial check is model-assisted"
      : "Authored reference fixture; model generation unverified",
    source: item.source,
    text,
    deterministicBaseline: baseline,
    baselineWordJaccard: similarity,
    strategy,
    critique,
    inputTokens: usage.reduce((sum, entry) => sum + entry.inputTokens, 0),
    outputTokens: usage.reduce((sum, entry) => sum + entry.outputTokens, 0),
    modelLatencyMs: usage.reduce((sum, entry) => sum + entry.latencyMs, 0),
    humanReview: "unavailable",
    marketingEffectiveness: "unverified",
  });
}
await mkdir("ux-evidence/preparation", { recursive: true });
await writeFile(
  "ux-evidence/preparation/evaluation.json",
  JSON.stringify(
    {
      mode: live ? "live" : "authored_references",
      modelCalls: calls,
      limits:
        "At most 24 calls, 4,000 output tokens per call; no retries, no provider publication. API billing belongs to supplied account.",
      results: result,
    },
    null,
    2,
  ) + "\n",
);
console.log(
  JSON.stringify({
    cases: result.length,
    modelCalls: calls,
    mode: live ? "live" : "authored_references",
    artifact: "ux-evidence/preparation/evaluation.json",
    distinctTexts: new Set(result.map((item) => item.text)).size,
    realModelOutputsObtained: live,
    realModelQualityVerified: false,
    humanReview: false,
    marketingEffectivenessVerified: false,
  }),
);
