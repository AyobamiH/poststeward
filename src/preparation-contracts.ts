import { z } from "zod";

export const preparationContext = z.strictObject({
  audience: z.string().min(10).max(1000),
  objective: z.string().min(10).max(1000),
  brandVoice: z.string().min(5).max(1000),
  productContext: z.string().min(10).max(6000),
  exclusions: z.string().max(2000),
  callToAction: z.string().min(1).max(500),
});
export const preparationSelection = z.strictObject({
  repository: z
    .string()
    .regex(/^[A-Za-z0-9_.-]+\/[A-Za-z0-9_.-]+$/)
    .max(200),
  releaseTag: z
    .string()
    .min(1)
    .max(120)
    .regex(/^[A-Za-z0-9_.-]+$/),
  previousTag: z
    .string()
    .min(1)
    .max(120)
    .regex(/^[A-Za-z0-9_.-]+$/)
    .optional(),
  documentationPaths: z
    .array(
      z
        .string()
        .max(200)
        .regex(/^(?!.*(?:\.\.|\/\.))[A-Za-z0-9_./-]+\.(?:md|mdx|txt)$/),
    )
    .max(3),
  allowPrivate: z.boolean(),
  allowUnreleased: z.boolean(),
});
export const evidenceItem = z.strictObject({
  id: z.string().min(1).max(100),
  url: z.string().url().max(700),
  kind: z.enum([
    "release",
    "commit",
    "diff",
    "documentation",
    "approved_context",
  ]),
  text: z.string().min(1).max(16000),
});
const reference = z.strictObject({
  evidence: z.string().min(1).max(100),
  quote: z.string().min(10).max(600),
});
export const strategySchema = z.strictObject({
  changes: z
    .array(
      z.strictObject({
        fact: z.string().min(5).max(1000),
        sources: z.array(reference).min(1).max(4),
        audienceProblem: z.string().max(1000),
        implication: z.string().max(1000),
      }),
    )
    .max(12),
  positioning: z.string().min(10).max(2000),
  objective: z.string().min(5).max(1000),
  audience: z.string().min(5).max(1000),
  channelApproach: z.string().min(5).max(2000),
  missingContext: z.array(z.string().min(1).max(500)).max(8),
  risks: z.array(z.string().min(1).max(500)).max(8),
});
export const draftsSchema = z.strictObject({
  drafts: z
    .array(
      z.strictObject({
        alias: z.string().min(1).max(100),
        text: z.string().min(1).max(12500),
        claims: z
          .array(
            z.strictObject({
              claim: z.string().min(5).max(1000),
              sources: z.array(reference).min(1).max(4),
            }),
          )
          .max(12),
        rationale: z.string().min(5).max(1500),
      }),
    )
    .min(1)
    .max(3),
});
export const critiqueSchema = z.strictObject({
  acceptableForOwnerReview: z.boolean(),
  issues: z
    .array(
      z.strictObject({
        alias: z.string().max(100),
        category: z.enum([
          "unsupported_claim",
          "injection",
          "confidential",
          "voice",
          "audience",
          "channel",
          "repetition",
          "missing_context",
        ]),
        detail: z.string().min(5).max(1000),
      }),
    )
    .max(20),
  summary: z.string().min(5).max(2000),
});
export type Evidence = z.infer<typeof evidenceItem>;
export type Context = z.infer<typeof preparationContext>;
export type Selection = z.infer<typeof preparationSelection>;
export type Strategy = z.infer<typeof strategySchema>;
export type Drafts = z.infer<typeof draftsSchema>;
export type Critique = z.infer<typeof critiqueSchema>;
