import { catalog, describe, plans } from "./operations/catalog.ts";
import type { Env } from "./types.ts";
export function help(
  env: Pick<Env, "ADVANCED_ENABLED" | "MPP_ENABLED" | "RELEASE_SHA">,
  scope?: string,
) {
  return {
    name: "PostSteward",
    version: "0.1.0",
    release: env.RELEASE_SHA,
    authentication: {
      owner: "/auth/login",
      agent: "Owner-issued scoped Bearer token; create it in /app.",
      browser: "HttpOnly session with CSRF protection.",
      mcpOAuth: "Not yet implemented; use a client supporting Bearer headers.",
    },
    transports: {
      http: "/api/operations/{name}",
      mcp: "/mcp",
      webmcp: {
        page: "/app",
        api: "document.modelContext",
        availability: "Requires a browser with WebMCP enabled.",
      },
    },
    plans,
    operations: catalog
      .filter((o) => !scope || o.name.startsWith(scope))
      .map((o) => ({
        ...describe(o),
        availability:
          o.tier === "advanced" && env.ADVANCED_ENABLED !== "true"
            ? "disabled_pending_validation"
            : "implemented",
      })),
    payment: {
      machineEndpoint: "/payments/{quote}",
      authentication: "Authorization: Bearer <agent token>",
      paymentCredential: "Payment-Authorization: Payment <credential>",
      enabled: env.MPP_ENABLED === "true",
    },
    limitsEndpoint: "/api/operations/workspace_status",
    evidence:
      "A returned reservation is not publication proof. Read each receipt.",
  };
}
export function openapi() {
  return {
    openapi: "3.1.0",
    info: {
      title: "PostSteward",
      version: "0.1.0",
      description:
        "Free agent-directed publication and explicit scheduling. Advanced continuing operation is USD 5 per month.",
    },
    paths: Object.fromEntries(
      catalog.map((o) => [
        "/api/operations/" + o.name,
        {
          post: {
            operationId: o.name,
            summary: o.description,
            security: [{ agentToken: [] }],
            requestBody: {
              required: true,
              content: {
                "application/json": {
                  schema: describe(o).inputSchema,
                  example: o.example,
                },
              },
            },
            responses: {
              "200": {
                description:
                  "Operation result; inspect delivery status for publication evidence.",
              },
              "400": { description: "Invalid input." },
              "401": { description: "Authentication required." },
              "403": { description: "Scope required." },
              "402": { description: "Advanced entitlement required." },
              "409": {
                description: "Authority, quote or idempotency conflict.",
              },
            },
          },
        },
      ]),
    ),
    components: {
      securitySchemes: { agentToken: { type: "http", scheme: "bearer" } },
    },
  };
}
