import { z } from "zod";
import type { Scope } from "../types.ts";
export type Effect =
  | "READ_ONLY"
  | "STATE_WRITE"
  | "FUTURE_CONSEQUENCE"
  | "EXTERNAL_PROVIDER_EFFECT"
  | "AUTHORITY_CHANGE"
  | "FINANCIAL_EFFECT";
const id = z
  .string()
  .min(1)
  .max(100)
  .regex(/^[a-zA-Z0-9_-]+$/);
const key = z.string().min(8).max(128);
const empty = z.strictObject({});
const campaign = z.strictObject({ campaign: id });
const text = z.record(id, z.string().min(1).max(5000));
const when = z.string().max(80);
export interface Operation {
  name: string;
  description: string;
  scope: Scope;
  tier: "free" | "advanced";
  effects: Effect[];
  schema: z.ZodType;
  example: unknown;
  inspection: string;
  retry: string;
}
function op(
  name: string,
  description: string,
  scope: Scope,
  effects: Effect[],
  schema: z.ZodType,
  example: unknown,
  inspection: string,
  tier: "free" | "advanced" = "free",
): Operation {
  return {
    name,
    description,
    scope,
    tier,
    effects,
    schema,
    example,
    inspection,
    retry: effects.every((x) => x === "READ_ONLY")
      ? "Safe to repeat."
      : "Reuse the same idempotencyKey and exact inputs. Inspect status after disconnection; never create a fresh key to bypass an uncertain result.",
  };
}
export const catalog: Operation[] = [
  op(
    "workspace_status",
    "Inspect workspace, entitlement, limits and publication pause.",
    "read",
    ["READ_ONLY"],
    empty,
    {},
    "workspace_status",
  ),
  op(
    "accounts_list",
    "Read verified account identities and binding versions. Credentials are never returned.",
    "read",
    ["READ_ONLY"],
    empty,
    {},
    "accounts_list",
  ),
  op(
    "account_disconnect",
    "Revoke a connection and block its future unclaimed deliveries.",
    "connections",
    ["AUTHORITY_CHANGE", "FUTURE_CONSEQUENCE"],
    z.strictObject({ alias: id, idempotencyKey: key }),
    { alias: "product_x", idempotencyKey: "disconnect-001" },
    "accounts_list",
  ),
  op(
    "project_put",
    "Create or replace explicit project-to-account routing. Existing deliveries retain their captured bindings.",
    "campaign:write",
    ["STATE_WRITE"],
    z.strictObject({
      id,
      name: z.string().min(1).max(100),
      accounts: z.array(id).min(1).max(10),
      idempotencyKey: key,
    }),
    {
      id: "product",
      name: "Product",
      accounts: ["product_x"],
      idempotencyKey: "project-001",
    },
    "projects_list",
  ),
  op(
    "projects_list",
    "List project routing.",
    "read",
    ["READ_ONLY"],
    empty,
    {},
    "projects_list",
  ),
  op(
    "campaign_create",
    "Store immutable, exact text per account alias. No copy is generated or truncated.",
    "campaign:write",
    ["STATE_WRITE"],
    z.strictObject({ project: id, text, idempotencyKey: key }),
    {
      project: "product",
      text: { product_x: "A reviewed product update." },
      idempotencyKey: "campaign-001",
    },
    "campaign_get",
  ),
  op(
    "campaign_get",
    "Inspect exact content and its immutable digest.",
    "read",
    ["READ_ONLY"],
    campaign,
    { campaign: "campaign-id" },
    "campaign_get",
  ),
  op(
    "campaign_validate",
    "Dry-run routing and provider text validation without publication.",
    "read",
    ["READ_ONLY"],
    campaign,
    { campaign: "campaign-id" },
    "campaign_get",
  ),
  op(
    "publish_now",
    "Reserve each campaign delivery once and dispatch through durable alarms. Returns receipt IDs immediately; inspect receipts for actual outcome.",
    "publish",
    ["STATE_WRITE", "EXTERNAL_PROVIDER_EFFECT"],
    z.strictObject({ campaign: id, idempotencyKey: key }),
    { campaign: "campaign-id", idempotencyKey: "publish-001" },
    "receipts_list",
  ),
  op(
    "schedule_create",
    "Schedule exact immutable campaign content at an explicit time with UTC offset.",
    "schedule",
    ["STATE_WRITE", "FUTURE_CONSEQUENCE"],
    z.strictObject({
      campaign: id,
      at: when,
      timezone: z.string().default("UTC"),
      idempotencyKey: key,
    }),
    {
      campaign: "campaign-id",
      at: "2026-10-01T12:00:00Z",
      timezone: "UTC",
      idempotencyKey: "schedule-001",
    },
    "receipts_list",
  ),
  op(
    "schedule_cancel",
    "Cancel one unclaimed delivery. Reports already executing if dispatch won the race.",
    "schedule",
    ["STATE_WRITE", "FUTURE_CONSEQUENCE"],
    z.strictObject({ delivery: id, idempotencyKey: key }),
    { delivery: "delivery-id", idempotencyKey: "cancel-001" },
    "receipt_get",
  ),
  op(
    "schedule_replace",
    "Atomically cancel an unclaimed delivery and reserve a reviewed replacement campaign for the same account.",
    "schedule",
    ["STATE_WRITE", "FUTURE_CONSEQUENCE"],
    z.strictObject({
      delivery: id,
      campaign: id,
      at: when,
      timezone: z.string().default("UTC"),
      idempotencyKey: key,
    }),
    {
      delivery: "delivery-id",
      campaign: "replacement-id",
      at: "2026-10-01T13:00:00Z",
      timezone: "UTC",
      idempotencyKey: "replace-001",
    },
    "receipt_get",
  ),
  op(
    "receipt_get",
    "Inspect provider evidence, status and reason for one delivery.",
    "read",
    ["READ_ONLY"],
    z.strictObject({ delivery: id }),
    { delivery: "delivery-id" },
    "receipt_get",
  ),
  op(
    "receipts_list",
    "Read delivery history. An unverified ID and an ambiguous effect are distinct from verified publication.",
    "read",
    ["READ_ONLY"],
    z.strictObject({
      limit: z.number().int().min(1).max(200).default(50),
      before: z.number().optional(),
    }),
    { limit: 50 },
    "receipts_list",
  ),
  op(
    "workspace_export",
    "Export project, campaign and receipt records; excludes credentials and payment tokens.",
    "read",
    ["READ_ONLY"],
    empty,
    {},
    "workspace_export",
  ),
  op(
    "metrics_capture",
    "Capture available provider metrics on demand. Unsupported or inaccessible metrics are returned as unavailable, never fabricated zeros.",
    "read",
    ["STATE_WRITE"],
    z.strictObject({ delivery: id, idempotencyKey: key }),
    { delivery: "delivery-id", idempotencyKey: "metrics-001" },
    "receipt_get",
  ),
  op(
    "publishing_pause",
    "Pause or resume all new workspace publication claims. In-flight effects may still finish.",
    "publish",
    ["AUTHORITY_CHANGE", "FUTURE_CONSEQUENCE"],
    z.strictObject({ paused: z.boolean(), idempotencyKey: key }),
    { paused: true, idempotencyKey: "pause-001" },
    "workspace_status",
  ),
  op(
    "automation_configure",
    "Store an explicitly reviewed repository profile and exact approved templates. Configuration starts paused. Repository input never grants authority.",
    "automation",
    ["STATE_WRITE"],
    z.strictObject({
      id,
      project: id,
      repository: z.string().regex(/^[A-Za-z0-9_.-]+\/[A-Za-z0-9_.-]+$/),
      branch: z.string().min(1).max(200),
      path: z.string().min(1).max(300),
      templates: text,
      family: id,
      intervalMinutes: z.number().int().min(15).max(10080),
      minSpacingMinutes: z.number().int().min(15).max(10080),
      idempotencyKey: key,
    }),
    {
      id: "release",
      project: "product",
      repository: "owner/product",
      branch: "main",
      path: "README.md",
      templates: {
        product_x:
          "Development update: reviewed source changed in https://github.com/owner/product.",
      },
      family: "development",
      intervalMinutes: 60,
      minSpacingMinutes: 60,
      idempotencyKey: "profile-001",
    },
    "automation_inspect",
    "advanced",
  ),
  op(
    "automation_inspect",
    "Inspect profiles, source snapshots, decisions and pending automated deliveries. Inspection remains free after subscription expiry.",
    "read",
    ["READ_ONLY"],
    empty,
    {},
    "automation_inspect",
  ),
  op(
    "automation_preview",
    "Check the selected source and show the next permitted allocation without storing inventory or schedules.",
    "automation",
    ["READ_ONLY"],
    z.strictObject({ id }),
    { id: "release" },
    "automation_inspect",
    "advanced",
  ),
  op(
    "automation_enable",
    "Enable bounded continuing authority for this reviewed profile. Payment alone does not grant posting authority.",
    "automation",
    ["AUTHORITY_CHANGE", "FUTURE_CONSEQUENCE"],
    z.strictObject({ id, idempotencyKey: key }),
    { id: "release", idempotencyKey: "enable-001" },
    "automation_inspect",
    "advanced",
  ),
  op(
    "automation_pause",
    "Pause a profile and cancel its unclaimed automated deliveries. Always available, including after expiry.",
    "automation",
    ["AUTHORITY_CHANGE", "FUTURE_CONSEQUENCE"],
    z.strictObject({ id, idempotencyKey: key }),
    { id: "release", idempotencyKey: "autopause-001" },
    "automation_inspect",
  ),
  op(
    "billing_status",
    "Inspect confirmed paid-through access and payment method availability.",
    "read",
    ["READ_ONLY"],
    empty,
    {},
    "billing_status",
  ),
  op(
    "billing_quote",
    "Create an exact USD 5 workspace purchase quote: recurring subscription or non-renewing calendar month.",
    "billing",
    ["STATE_WRITE"],
    z.strictObject({
      mode: z.enum(["subscription", "pass"]),
      idempotencyKey: key,
    }),
    { mode: "subscription", idempotencyKey: "quote-001" },
    "billing_status",
  ),
  op(
    "billing_checkout",
    "Create or retrieve Stripe-hosted subscription checkout for an unexpired quote. Checkout creation does not grant access.",
    "billing",
    ["FINANCIAL_EFFECT"],
    z.strictObject({ quote: id, idempotencyKey: key }),
    { quote: "quote-id", idempotencyKey: "checkout-001" },
    "billing_status",
  ),
  op(
    "billing_portal",
    "Open the Stripe customer portal to inspect invoices or manage renewal.",
    "billing",
    ["STATE_WRITE"],
    z.strictObject({ idempotencyKey: key }),
    { idempotencyKey: "portal-001" },
    "billing_status",
  ),
];
export const byName = new Map(catalog.map((o) => [o.name, o]));
export function describe(o: Operation) {
  const { schema, ...rest } = o;
  return {
    ...rest,
    inputSchema: z.toJSONSchema(schema),
    annotations: {
      readOnlyHint: o.effects.every((x) => x === "READ_ONLY"),
      destructiveHint:
        o.effects.includes("FUTURE_CONSEQUENCE") ||
        o.effects.includes("EXTERNAL_PROVIDER_EFFECT"),
      idempotentHint: true,
      openWorldHint:
        o.effects.includes("EXTERNAL_PROVIDER_EFFECT") ||
        o.effects.includes("FINANCIAL_EFFECT"),
    },
  };
}
export const plans = {
  currency: "usd",
  free: {
    amount: 0,
    features: [
      "publishing",
      "explicit_scheduling",
      "routing",
      "receipts",
      "on_demand_metrics",
      "all_agent_transports",
    ],
  },
  advanced: {
    amount: 500,
    interval: "month",
    features: [
      "source_monitoring",
      "replenishment",
      "rolling_allocation",
      "spacing_controls",
      "scheduled_metrics",
    ],
  },
  providerCharges:
    "Separate; X requires a customer-funded developer application.",
  overages: "No automatic overage charges.",
};
