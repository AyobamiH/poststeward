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

async function request(base, path, send) {
  return send(new URL(path, base), {
    redirect: "manual",
    cache: "no-store",
    headers: { "Cache-Control": "no-cache" },
    signal: AbortSignal.timeout(15_000),
  });
}

async function check(
  base,
  requestedPath,
  allowedCanonicalPaths,
  marker,
  contentType,
  send,
) {
  let resolvedPath = requestedPath;
  let response = await request(base, requestedPath, send);

  if ([307, 308].includes(response.status)) {
    const location = response.headers.get("location");
    if (!location)
      throw new Error(`${requestedPath} redirected without a Location header.`);
    const target = new URL(location, base);
    if (
      target.origin !== base ||
      target.username ||
      target.password ||
      target.search ||
      target.hash ||
      !allowedCanonicalPaths.includes(target.pathname)
    )
      throw new Error(`${requestedPath} redirected outside its allowed canonical asset path.`);
    resolvedPath = target.pathname;
    response = await request(base, resolvedPath, send);
  }

  if (response.status !== 200)
    throw new Error(`${requestedPath} returned HTTP ${response.status}; expected 200 after canonicalisation.`);
  const actualType = response.headers.get("content-type") || "";
  if (!actualType.includes(contentType))
    throw new Error(`${requestedPath} returned unexpected Content-Type ${actualType || "<missing>"}.`);
  const hsts = response.headers.get("strict-transport-security") || "";
  const nosniff = response.headers.get("x-content-type-options") || "";
  if (!hsts.includes("max-age=") || nosniff !== "nosniff")
    throw new Error(`${requestedPath} is missing the expected security headers.`);
  const body = await response.text();
  if (!body.includes(marker))
    throw new Error(`${requestedPath} does not contain the expected deployed marker.`);
  return {
    requestedPath,
    resolvedPath,
    status: 200,
    contentType,
    markerPresent: true,
    secureHeaders: true,
  };
}

export async function verifyAdvancedInventoryAssets(baseInput, send = fetch) {
  const base = origin(baseInput);
  const html = await check(
    base,
    "/advanced-inventory.html",
    ["/advanced-inventory.html", "/advanced-inventory", "/advanced-inventory/"],
    "PostSteward Advanced inventory",
    "text/html",
    send,
  );
  const script = await check(
    base,
    "/advanced-inventory.js",
    ["/advanced-inventory.js"],
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
