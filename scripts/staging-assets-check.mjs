import { pathToFileURL } from "node:url";
import { resolve } from "node:path";

function origin(value) {
  const parsed = new URL(value);
  if (
    parsed.protocol !== "https:" ||
    parsed.username ||
    parsed.password ||
    parsed.pathname !== "/" ||
    parsed.search ||
    parsed.hash
  )
    throw new Error("POSTSTEWARD_ORIGIN must be an exact HTTPS origin without credentials or path state.");
  return parsed.origin;
}

async function check(base, path, marker, contentType, send) {
  const response = await send(new URL(path, base), {
    redirect: "manual",
    cache: "no-store",
    headers: { "Cache-Control": "no-cache" },
    signal: AbortSignal.timeout(15_000),
  });
  if (response.status !== 200)
    throw new Error(`${path} returned HTTP ${response.status}; expected 200.`);
  const actualType = response.headers.get("content-type") || "";
  if (!actualType.includes(contentType))
    throw new Error(`${path} returned unexpected Content-Type ${actualType || "<missing>"}.`);
  const hsts = response.headers.get("strict-transport-security") || "";
  const nosniff = response.headers.get("x-content-type-options") || "";
  if (!hsts.includes("max-age=") || nosniff !== "nosniff")
    throw new Error(`${path} is missing the expected security headers.`);
  const body = await response.text();
  if (!body.includes(marker))
    throw new Error(`${path} does not contain the expected deployed marker.`);
  return { path, status: 200, contentType, markerPresent: true, secureHeaders: true };
}

export async function verifyAdvancedInventoryAssets(baseInput, send = fetch) {
  const base = origin(baseInput);
  const html = await check(
    base,
    "/advanced-inventory.html",
    "PostSteward Advanced inventory",
    "text/html",
    send,
  );
  const script = await check(
    base,
    "/advanced-inventory.js",
    'invoke("automation_inspect")',
    "javascript",
    send,
  );
  return { origin: base, advancedInventory: "deployed", checks: [html, script] };
}

async function main() {
  const result = await verifyAdvancedInventoryAssets(
    process.env.POSTSTEWARD_ORIGIN || "",
  );
  console.log(JSON.stringify(result, null, 2));
}

if (process.argv[1] && import.meta.url === pathToFileURL(resolve(process.argv[1])).href)
  main().catch((error) => {
    console.error(error instanceof Error ? error.message : String(error));
    process.exitCode = 1;
  });
