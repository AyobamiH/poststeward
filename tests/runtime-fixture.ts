import { readFileSync, readdirSync } from "node:fs";
import { basename, extname, join } from "node:path";
import { Miniflare, convertV4MiniflareOptions, Response as RuntimeResponse } from "miniflare";
import { environment } from "./helpers.ts";

export function builtWorkerScriptPath(
  configPath = "wrangler.jsonc",
  outdir = "dist",
) {
  const config = JSON.parse(readFileSync(configPath, "utf8")) as { main?: unknown };
  if (typeof config.main !== "string" || !config.main.trim())
    throw new Error("Wrangler configuration must declare one Worker entrypoint.");
  const entry = basename(config.main);
  const extension = extname(entry);
  const output = extension ? entry.slice(0, -extension.length) + ".js" : entry + ".js";
  return join(outdir, output);
}

/** Actual Workers/D1/SQLite runtime. Outbound identity/provider responses are test fixtures, never live evidence. */
export async function runtime(outboundService?: (request: Request) => Promise<any>, bindings: Record<string, string> = {}, loginLimit = 10) {
  const options = {
    modules: true, scriptPath: builtWorkerScriptPath(), compatibilityDate: "2026-09-09", compatibilityFlags: ["nodejs_compat"],
    bindings: {
      ...Object.fromEntries(Object.entries(environment).filter(([, value]) => typeof value === "string")),
      OIDC_ISSUER: "https://identity.example", OIDC_CLIENT_ID: "poststeward-test", OIDC_CLIENT_SECRET: "not-a-real-secret",
      ALLOWED_OWNER_EMAILS: "owner@example.com", SIGNUP_MODE: "restricted", ...bindings,
    },
    ratelimits: {
      EDGE_LIMITER: { namespace_id: "51001", simple: { limit: 120, period: 60 } },
      LOGIN_LIMITER: { namespace_id: "51002", simple: { limit: loginLimit, period: 60 } },
    },
    d1Databases: { IDENTITY: "security-test" },
    durableObjects: { WORKSPACES: { className: "Workspace", useSQLite: true } },
    serviceBindings: { ASSETS: async () => new RuntimeResponse("asset") },
    outboundService: outboundService || (async () => { throw new Error("Unexpected network access"); }),
  } satisfies Parameters<typeof convertV4MiniflareOptions>[0];
  const mf = new Miniflare(convertV4MiniflareOptions(options));
  const db = await mf.getD1Database("IDENTITY");
  for (const file of readdirSync("migrations").filter((name) => /^\d+.*\.sql$/.test(name)).sort())
    for (const statement of readFileSync("migrations/" + file, "utf8").split(";").map((part) => part.trim()).filter(Boolean))
      await db.prepare(statement).run();
  return { mf, db, reconfigure: async (updated: Record<string, string>) => {
    Object.assign(options.bindings!, updated);
    await mf.setOptions(convertV4MiniflareOptions(options));
  } };
}
