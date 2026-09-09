import { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js";
import { WebStandardStreamableHTTPServerTransport } from "@modelcontextprotocol/sdk/server/webStandardStreamableHttp.js";
import { catalog, describe } from "./operations/catalog.ts";
import { help } from "./discovery.ts";
import type { Actor, Env } from "./types.ts";
export async function mcp(
  request: Request,
  env: Env,
  actor: Actor,
  invoke: (name: string, input: unknown) => Promise<unknown>,
) {
  const server = new McpServer(
    { name: "poststeward", version: "0.1.0" },
    {
      instructions:
        "Read help before taking consequential actions. Publishing and explicit scheduling are free. Returned reservations require receipt inspection. Do not retry ambiguous effects with new keys.",
    },
  );
  server.registerResource(
    "operation-help",
    "publishing://help",
    {
      description:
        "Operation catalogue, consequence classifications, pricing and availability.",
      mimeType: "application/json",
    },
    async () => ({
      contents: [
        {
          uri: "publishing://help",
          mimeType: "application/json",
          text: JSON.stringify(help(env)),
        },
      ],
    }),
  );
  for (const operation of catalog.filter(
    (o) => actor.scopes.includes("admin") || actor.scopes.includes(o.scope),
  )) {
    server.registerTool(
      operation.name,
      {
        description: operation.description,
        inputSchema: operation.schema,
        annotations: describe(operation).annotations,
      },
      async (input) => {
        try {
          const result = await invoke(operation.name, input);
          return {
            content: [{ type: "text" as const, text: JSON.stringify(result) }],
            structuredContent: { result },
          };
        } catch (e) {
          return {
            isError: true,
            content: [
              {
                type: "text" as const,
                text: JSON.stringify({
                  error: e instanceof Error ? e.message : "Operation failed",
                }),
              },
            ],
          };
        }
      },
    );
  }
  const transport = new WebStandardStreamableHTTPServerTransport({
    sessionIdGenerator: undefined,
    enableJsonResponse: true,
  });
  await server.connect(transport);
  try {
    return await transport.handleRequest(request);
  } finally {
    await server.close();
  }
}
