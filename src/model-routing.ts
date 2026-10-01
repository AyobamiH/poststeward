import { z } from "zod";
import { Fault, requireValue } from "./common.ts";
import {
  MAX_INPUT_BYTES,
  MAX_OUTPUT_TOKENS,
  preparationEditorialPolicy,
  type ModelPort,
} from "./preparation-model.ts";

// Deliberately bounded, reviewed catalogue. New model IDs require contract and
// editorial acceptance, not merely appearing in an upstream catalogue.
export const MODEL_CATALOG_DATE = "2026-10-01";
export const modelCatalog = [
  {
    provider: "cloudflare_workers",
    model: "@cf/meta/llama-3.3-70b-instruct-fp8-fast",
    input: 0.293,
    output: 2.253,
    contextBytes: 32000,
  },
  {
    provider: "cloudflare_workers",
    model: "@cf/meta/llama-3.1-8b-instruct",
    input: 0.282,
    output: 0.827,
    contextBytes: 24000,
  },
  {
    provider: "cloudflare_gateway",
    model: "gpt-4.1-mini",
    input: 0.4,
    output: 1.6,
    contextBytes: 48000,
  },
  {
    provider: "cloudflare_gateway",
    model: "gpt-4.1",
    input: 2,
    output: 8,
    contextBytes: 48000,
  },
] as const;
export const routeSchema = z
  .strictObject({
    provider: z.enum(["cloudflare_workers", "cloudflare_gateway"]),
    model: z.string().min(1).max(120),
    funding: z.enum(["workers_ai", "gateway_credits"]),
    gatewayId: z
      .string()
      .regex(/^[a-zA-Z0-9_-]{1,64}$/)
      .optional(),
  })
  .superRefine((r, ctx) => {
    if (
      !modelCatalog.some(
        (m) => m.provider === r.provider && m.model === r.model,
      )
    )
      ctx.addIssue({
        code: "custom",
        message: "Model is outside the reviewed preparation catalogue.",
      });
    if (
      r.provider === "cloudflare_gateway" &&
      (r.funding !== "gateway_credits" || !r.gatewayId)
    )
      ctx.addIssue({
        code: "custom",
        message:
          "OpenAI Gateway requires a gateway and prepaid Unified Billing.",
      });
    if (r.funding === "gateway_credits" && !r.gatewayId)
      ctx.addIssue({
        code: "custom",
        message: "Credit funding requires a gateway.",
      });
    if (r.funding === "workers_ai" && r.gatewayId)
      ctx.addIssue({
        code: "custom",
        message: "Direct Workers AI billing must not select a gateway.",
      });
  });
export const routingSchema = z
  .strictObject({
    version: z.literal(1),
    accountId: z.string().regex(/^[a-f0-9]{32}$/),
    primary: routeSchema,
    fallbacks: z.array(routeSchema).max(1),
    maxOutputTokens: z.number().int().min(500).max(MAX_OUTPUT_TOKENS),
    maxInputBytes: z.number().int().min(2000).max(MAX_INPUT_BYTES),
    temperature: z.number().min(0).max(1),
    maxJobUsd: z.number().min(0.001).max(10),
    maxDailyUsd: z.number().min(0.001).max(100),
    logging: z.enum(["none", "metadata"]),
    retention: z.literal("reviewed_removal").default("reviewed_removal"),
  })
  .superRefine((r, ctx) => {
    const ids = [r.primary, ...r.fallbacks].map(
      (x) =>
        x.provider +
        ":" +
        x.model +
        ":" +
        x.funding +
        ":" +
        (x.gatewayId || ""),
    );
    if (new Set(ids).size !== ids.length)
      ctx.addIssue({
        code: "custom",
        message:
          "Fallback routes must be distinct; automatic retries are not supported.",
      });
  });
export type ModelRoute = z.infer<typeof routeSchema>;
export type Routing = z.infer<typeof routingSchema>;
export type FundingProof = {
  account: string;
  gateway?: string;
  funding: ModelRoute["funding"];
  checkedAt: number;
  catalogueDate: string;
  accountBalance: number | null;
  balanceUnit: "provider_reported_shared_account_balance";
  liveInvoiceVerified: false;
  gatewayModifiedAt?: string;
  gatewaySpendLimitsConfigured?: boolean;
};

export function modelPrice(route: ModelRoute) {
  const entry = modelCatalog.find(
    (m) => m.model === route.model && m.provider === route.provider,
  )!;
  requireValue(
    entry,
    "MODEL_UNAVAILABLE",
    "Select a model from the reviewed catalogue.",
    409,
  );
  return entry;
}
export function attemptCeiling(routing: Routing, route: ModelRoute) {
  const p = modelPrice(route);
  // UTF-8 bytes are a conservative tokenizer bound for these reviewed models.
  // Include the published credit purchase fee as a conservative cost allocation.
  return Math.ceil(
    (Math.min(routing.maxInputBytes, p.contextBytes) * p.input +
      routing.maxOutputTokens * p.output) *
      (route.funding === "gateway_credits" ? 1.05 : 1),
  );
}
export function pipelineCeiling(routing: Routing, phases: number) {
  return (
    phases *
    [routing.primary, ...routing.fallbacks].reduce(
      (sum, r) => sum + attemptCeiling(routing, r),
      0,
    )
  );
}
export async function boundedJSON(response: Response) {
  requireValue(
    response.body,
    "MODEL_RESPONSE_INVALID",
    "Provider returned no body.",
    502,
  );
  const reader = response.body.getReader();
  const parts: Uint8Array[] = [];
  let size = 0;
  try {
    while (true) {
      const item = await reader.read();
      if (item.done) break;
      size += item.value.byteLength;
      requireValue(
        size <= 256000,
        "MODEL_RESPONSE_TOO_LARGE",
        "Provider response exceeded its limit.",
        502,
      );
      parts.push(item.value);
    }
  } finally {
    await reader.cancel().catch(() => {});
  }
  const bytes = new Uint8Array(size);
  let at = 0;
  for (const p of parts) {
    bytes.set(p, at);
    at += p.byteLength;
  }
  try {
    return JSON.parse(new TextDecoder().decode(bytes));
  } catch {
    throw new Fault(
      "MODEL_RESPONSE_INVALID",
      "Provider returned invalid JSON.",
      502,
    );
  }
}

// Metadata-only requests. No gateway mutation, account creation, top-up or inference.
export async function verifyCloudflare(
  routing: Routing,
  route: ModelRoute,
  token: string,
  now: number,
  send: typeof fetch = fetch,
): Promise<FundingProof> {
  requireValue(
    now >= Date.parse(MODEL_CATALOG_DATE) &&
      now - Date.parse(MODEL_CATALOG_DATE) < 30 * 86400000,
    "MODEL_PRICING_STALE",
    "The reviewed pricing catalogue expired; refresh it before generation.",
    409,
  );
  routeSchema.parse(route);
  routingSchema.parse(routing);
  const base = `https://api.cloudflare.com/client/v4/accounts/${routing.accountId}`;
  const read = async (path: string) => {
    let response: Response;
    try {
      response = await send(base + path, {
        headers: { Authorization: `Bearer ${token}` },
        redirect: "manual",
        signal: AbortSignal.timeout(15000),
      });
    } catch {
      throw new Fault(
        "MODEL_VALIDATION_UNAVAILABLE",
        "Cloudflare metadata is unavailable. No inference was attempted.",
        503,
      );
    }
    if (!response.ok) {
      await response.body?.cancel();
      throw new Fault(
        response.status === 401
          ? "MODEL_AUTH_REJECTED"
          : "MODEL_INSPECTION_PERMISSION_REQUIRED",
        "Cloudflare account metadata could not be read. Check the selected account, Workers AI Read and optional AI Gateway Read/billing inspection permissions. No resource changes or inference occurred.",
        409,
      );
    }
    const body = await boundedJSON(response);
    requireValue(
      body.success === true && body.result !== undefined,
      "MODEL_VALIDATION_INVALID",
      "Cloudflare metadata could not be established.",
      409,
    );
    return body;
  };
  if (route.provider === "cloudflare_workers") {
    const catalog = await read(
      "/ai/models/search?search=" +
        encodeURIComponent(route.model.replace(/^@cf\//, "")) +
        "&per_page=100",
    );
    requireValue(
      Array.isArray(catalog.result) &&
        catalog.result.some((m: any) => m.name === route.model),
      "MODEL_UNAVAILABLE",
      "The selected Workers AI model is absent from the account catalogue.",
      409,
    );
  } else {
    // Workers AI search does not enumerate third-party models. Check the official
    // public Gateway catalogue without sending any customer credential to it.
    let response: Response;
    try {
      response = await send(
        `https://developers.cloudflare.com/ai/models/openai/${route.model}/index.md`,
        { redirect: "manual", signal: AbortSignal.timeout(15000) },
      );
    } catch {
      throw new Fault(
        "MODEL_VALIDATION_UNAVAILABLE",
        "The official Gateway model catalogue is unavailable. No inference occurred.",
        503,
      );
    }
    requireValue(
      response.ok &&
        Number(response.headers.get("content-length") || 0) <= 256000,
      "MODEL_UNAVAILABLE",
      "The official Gateway model catalogue could not be verified.",
      409,
    );
    const text = await boundedText(response);
    const price = modelPrice(route);
    requireValue(
      text.includes(`\`openai/${route.model}\``) && text.includes("Responses"),
      "MODEL_UNAVAILABLE",
      "This model's Responses capability could not be established.",
      409,
    );
    requireValue(
      text.includes(`Input (per 1M tokens)$${price.input.toFixed(2)}`) &&
        text.includes(`Output (per 1M tokens)$${price.output.toFixed(2)}`),
      "MODEL_PRICING_CHANGED",
      "Gateway pricing differs from the reviewed allowance rates. Generation is blocked until catalogue review.",
      409,
    );
  }
  const proof: FundingProof = {
    account: routing.accountId,
    funding: route.funding,
    checkedAt: now,
    catalogueDate: MODEL_CATALOG_DATE,
    accountBalance: null,
    balanceUnit: "provider_reported_shared_account_balance",
    liveInvoiceVerified: false,
  };
  if (route.funding === "workers_ai") return proof;
  const gateway = (await read(`/ai-gateway/gateways/${route.gatewayId}`))
    .result;
  requireValue(
    gateway.id === route.gatewayId && gateway.byok_only !== true,
    "MODEL_FUNDING_UNVERIFIED",
    "Select a gateway that permits Unified Billing; PostSteward will not edit it.",
    409,
  );
  requireValue(
    !gateway.retry_max_attempts,
    "MODEL_GATEWAY_RETRIES_ENABLED",
    "Disable automatic retries on a dedicated gateway before using this bounded preparation route.",
    409,
  );
  requireValue(
    gateway.spend_limits?.enabled === true &&
      gateway.spend_limits.rules?.some(
        (r: any) =>
          r.enabled !== false &&
          r.limitType === "cost" &&
          Number.isFinite(r.limit) &&
          r.limit > 0 &&
          Number.isFinite(r.window) &&
          r.window > 0 &&
          !r.model &&
          !r.provider &&
          (!r.metadata || Object.keys(r.metadata).length === 0),
      ),
    "MODEL_GATEWAY_SPEND_LIMIT_REQUIRED",
    "Configure a positive gateway-wide spend limit on a dedicated gateway before connecting. PostSteward will not change it; concurrent gateway traffic can still overshoot this secondary limit.",
    409,
  );
  if (route.provider === "cloudflare_workers")
    requireValue(
      gateway.workers_ai_billing_mode === "unified",
      "MODEL_FUNDING_UNVERIFIED",
      "The gateway must explicitly select Unified billing for Workers AI.",
      409,
    );
  // Bound and exhaust pagination; never treat a partial list as absence of keys.
  for (let page = 1; page <= 5; page++) {
    const configs = await read(
      `/ai-gateway/gateways/${route.gatewayId}/provider_configs?page=${page}&per_page=100`,
    );
    requireValue(
      Array.isArray(configs.result),
      "MODEL_FUNDING_UNVERIFIED",
      "Provider-key configuration could not be inspected.",
      409,
    );
    requireValue(
      !configs.result.some((c: any) => c.provider_slug === "openai"),
      "MODEL_STORED_KEY_CONFLICT",
      "This gateway stores OpenAI provider credentials. Use a dedicated key-free gateway for the credit-funded route; no shared keys were changed.",
      409,
    );
    const pages = configs.result_info?.total_pages;
    if (
      (typeof pages === "number" && pages <= page) ||
      (pages === undefined && configs.result.length < 100)
    )
      break;
    requireValue(
      page < 5,
      "MODEL_FUNDING_UNVERIFIED",
      "The provider-key inventory exceeds safe inspection limits.",
      409,
    );
  }
  const balance = (await read("/ai-gateway/billing/credit-balance")).result;
  requireValue(
    typeof balance.balance === "number" &&
      Number.isFinite(balance.balance) &&
      balance.balance > 0,
    "MODEL_INSUFFICIENT_CREDITS",
    "This Cloudflare account reports no positive AI Gateway balance. No top-up or invoice was changed.",
    409,
  );
  return {
    ...proof,
    gateway: route.gatewayId,
    gatewayModifiedAt: gateway.modified_at,
    gatewaySpendLimitsConfigured: true,
    accountBalance: balance.balance,
  };
}

async function boundedText(response: Response) {
  requireValue(
    response.body,
    "MODEL_VALIDATION_INVALID",
    "Catalogue returned no body.",
    502,
  );
  const reader = response.body.getReader();
  let size = 0,
    text = "";
  const decoder = new TextDecoder();
  try {
    while (true) {
      const item = await reader.read();
      if (item.done) break;
      size += item.value.byteLength;
      requireValue(
        size <= 256000,
        "MODEL_RESPONSE_TOO_LARGE",
        "Catalogue response exceeded its limit.",
        502,
      );
      text += decoder.decode(item.value, { stream: true });
    }
  } finally {
    await reader.cancel().catch(() => {});
  }
  return text + decoder.decode();
}

export function cloudflareModel(
  routing: Routing,
  route: ModelRoute,
  send: typeof fetch = fetch,
): ModelPort {
  return async (token, stage, data, schema) => {
    const price = modelPrice(route);
    const messages = [
      {
        role: "system",
        content:
          preparationEditorialPolicy(stage) +
          "\nSchema: " +
          JSON.stringify(z.toJSONSchema(schema, { target: "draft-7" })),
      },
      { role: "user", content: JSON.stringify({ stage, material: data }) },
    ];
    requireValue(
      new TextEncoder().encode(JSON.stringify(messages)).byteLength <=
        Math.min(routing.maxInputBytes, price.contextBytes),
      "PREPARATION_INPUT_TOO_LARGE",
      "Input exceeds this model's reviewed context boundary. Select a smaller release or context.",
      422,
    );
    const headers: Record<string, string> = {
      "Content-Type": "application/json",
      "cf-aig-skip-cache": "true",
      "cf-aig-collect-log-payload": "false",
      "cf-aig-collect-log": routing.logging === "metadata" ? "true" : "false",
      "cf-aig-max-attempts": "1",
    };
    let url: string, body: unknown;
    if (route.provider === "cloudflare_gateway") {
      url = `https://gateway.ai.cloudflare.com/v1/${routing.accountId}/${route.gatewayId}/openai/responses`;
      headers["cf-aig-authorization"] = `Bearer ${token}`;
      body = {
        model: route.model,
        store: false,
        stream: false,
        max_output_tokens: routing.maxOutputTokens,
        temperature: routing.temperature,
        input: messages,
        text: {
          format: {
            type: "json_schema",
            name: "preparation_" + stage,
            strict: true,
            schema: z.toJSONSchema(schema, { target: "draft-7" }),
          },
        },
      };
    } else {
      url = `https://api.cloudflare.com/client/v4/accounts/${routing.accountId}/ai/run/${route.model}`;
      headers.Authorization = `Bearer ${token}`;
      if (route.gatewayId) headers["cf-aig-gateway-id"] = route.gatewayId;
      body = {
        messages,
        stream: false,
        max_tokens: routing.maxOutputTokens,
        temperature: routing.temperature,
        response_format: {
          type: "json_schema",
          json_schema: z.toJSONSchema(schema, { target: "draft-7" }),
        },
      };
    }
    requireValue(
      new TextEncoder().encode(JSON.stringify(body)).byteLength <=
        Math.min(routing.maxInputBytes, price.contextBytes),
      "PREPARATION_INPUT_TOO_LARGE",
      "The complete request including editorial schema exceeds this model's reserved input boundary.",
      422,
    );
    const started = Date.now();
    let response: Response;
    try {
      response = await send(url, {
        method: "POST",
        body: JSON.stringify(body),
        headers,
        redirect: "manual",
        signal: AbortSignal.timeout(45000),
      });
    } catch {
      throw new Fault(
        "MODEL_CALL_UNCERTAIN",
        "Inference did not return; billing may have occurred. No retry or fallback will follow.",
        502,
      );
    }
    if (!response.ok) {
      await response.body?.cancel();
      const code =
        response.status === 429
          ? "MODEL_RATE_LIMITED"
          : [401, 403].includes(response.status)
            ? "MODEL_AUTH_REJECTED"
            : [400, 404].includes(response.status)
              ? "MODEL_UNAVAILABLE"
              : "MODEL_CALL_UNCERTAIN";
      throw new Fault(
        code,
        code === "MODEL_CALL_UNCERTAIN"
          ? "Provider outcome is uncertain; inspect account usage before retrying."
          : "The selected model request was rejected. Check account/model availability and funding; raw provider errors are withheld.",
        502,
      );
    }
    const json = await boundedJSON(response);
    let text: unknown, usage: any;
    if (route.provider === "cloudflare_gateway") {
      requireValue(
        json.gatewayMetadata?.keySource === "Unified",
        "MODEL_FUNDING_UNVERIFIED",
        "Gateway response did not attest Unified Billing. Its cost remains uncertain; inspect the customer gateway before any further generation.",
        409,
      );

      requireValue(
        json.status === "completed" && !json.error,
        "MODEL_OUTPUT_INVALID",
        "Generation did not complete.",
        502,
      );
      const output = (json.output || []).flatMap((i: any) =>
        i.type === "message" ? i.content || [] : [],
      );
      requireValue(
        output.length === 1 && output[0].type === "output_text",
        "MODEL_OUTPUT_INVALID",
        "Provider did not return one structured editorial result.",
        502,
      );
      text = output[0].text;
      usage = json.usage;
    } else {
      requireValue(
        json.success === true,
        "MODEL_OUTPUT_INVALID",
        "Workers AI did not report success.",
        502,
      );
      text = json.result?.response;
      usage = json.result?.usage;
    }
    let value: unknown;
    try {
      value = typeof text === "string" ? JSON.parse(text) : text;
    } catch {
      throw new Fault(
        "MODEL_OUTPUT_INVALID",
        "Provider returned invalid editorial JSON.",
        502,
      );
    }
    const inputTokens = usage?.input_tokens ?? usage?.prompt_tokens;
    const outputTokens = usage?.output_tokens ?? usage?.completion_tokens;
    requireValue(
      Number.isSafeInteger(inputTokens) &&
        inputTokens >= 0 &&
        inputTokens <= Math.min(routing.maxInputBytes, price.contextBytes) &&
        Number.isSafeInteger(outputTokens) &&
        outputTokens >= 0 &&
        outputTokens <= routing.maxOutputTokens,
      "MODEL_USAGE_INVALID",
      "Provider usage is unverifiable; the worst-case reservation remains charged to the allowance.",
      502,
    );
    requireValue(
      schema.safeParse(value).success,
      "MODEL_OUTPUT_INVALID",
      "Generated content does not match the shared editorial contract.",
      502,
    );
    return {
      value,
      inputTokens,
      outputTokens,
      latencyMs: Date.now() - started,
      ...(route.provider === "cloudflare_gateway"
        ? { fundingSource: "Unified" as const }
        : {}),
    };
  };
}
