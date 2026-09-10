export async function registerWebMCP(
  help,
  invoke,
  actorScopes,
  context = document.modelContext,
) {
  if (!context?.registerTool) return { available: false, count: 0 };
  const abort = new AbortController();
  let count = 0;
  try {
    for (const operation of help.operations.filter(
      (o) => actorScopes.includes("admin") || actorScopes.includes(o.scope),
    )) {
      await context.registerTool(
        {
          name: operation.name,
          description: operation.description,
          inputSchema: operation.inputSchema,
          annotations: {
            readOnlyHint: operation.effects.every((e) => e === "READ_ONLY"),
            consequentialHint: operation.effects.some((e) =>
              [
                "EXTERNAL_PROVIDER_EFFECT",
                "FINANCIAL_EFFECT",
                "FUTURE_CONSEQUENCE",
                "AUTHORITY_CHANGE",
              ].includes(e),
            ),
            untrustedContentHint: true,
          },
          execute: async (input) =>
            JSON.stringify(await invoke(operation.name, input)),
        },
        { signal: abort.signal },
      );
      count++;
    }
  } catch (error) {
    abort.abort();
    throw error;
  }
  return { available: true, count, unregister: () => abort.abort() };
}

export async function checkNativeWebMCP(expectedWorkspace) {
  const context = document.modelContext;
  if (!context?.getTools || !context?.executeTool)
    throw new Error("This browser does not expose the native WebMCP inspection and execution APIs.");
  const tools = await context.getTools({ fromOrigins: [] });
  const tool = tools.find((t) => t.name === "workspace_status" && t.window === window);
  if (!tool) throw new Error("The workspace_status tool is not registered on this page.");
  const result = JSON.parse(await context.executeTool(tool, {}, { signal: AbortSignal.timeout(20000) }));
  if (result.workspace !== expectedWorkspace)
    throw new Error("Native WebMCP returned an unexpected workspace.");
  return {
    path: "document.modelContext", operation: tool.name, input: {},
    workspace: result.workspace, release: result.release, observedAt: Date.now(),
    browser: navigator.userAgent,
    evidence: "In-page native API round trip; browser-agent acceptance is a separate observation.",
  };
}
