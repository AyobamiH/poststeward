export async function registerWebMCP(
  help,
  invoke,
  actorScopes,
  context = document.modelContext,
) {
  if (!context?.registerTool) return { available: false, count: 0 };
  const abort = new AbortController();
  let count = 0;
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
  return { available: true, count, unregister: () => abort.abort() };
}
