import { billingPrice } from "./billing-mode.ts";
import type { Env } from "./types.ts";
import { socialPages, socialPreviewTags, socialImagePath } from "./social-previews.ts";

const ownerPaths = new Set(["/app", "/pilot", "/advanced-inventory", "/lifecycle", "/recovery"]);
export const publicPages = ["/", "/docs/", "/docs/install", "/docs/agent-guide", "/docs/operations", "/privacy", "/terms", "/security", "/support", "/status"];
export const publicDiscoveryPaths = ["/agents.txt", "/llms.txt", "/help.json", "/openapi.json"];
const navigation = [["/app", "Overview"], ["/app#destinations", "Social accounts"], ["/app#publishing", "Create & schedule"], ["/app#evidence-panel", "Schedules & results"], ["/advanced-inventory", "Automation inventory"], ["/app#profile", "Automation profiles"], ["/app#agent-access", "Agent permissions"], ["/app#sources", "Sources & models"], ["/app#advanced", "Advanced & billing"], ["/app#runtime-authority-panel", "Local runtime"], ["/lifecycle", "Data & deletion"], ["/recovery", "Recovery"], ["/docs/", "Help & guides"], ["/support", "Support"]];
export function canonicalPath(path: string) {
  if (path === "/index.html") return "/";
  if (["/docs", "/docs/index.html"].includes(path)) return "/docs/";
  return path.endsWith(".html") ? path.slice(0, -5) : path;
}
export function escapeHtml(value: unknown) {
  return String(value ?? "").replaceAll("&", "&amp;").replaceAll("<", "&lt;").replaceAll(">", "&gt;").replaceAll('"', "&quot;").replaceAll("'", "&#39;");
}
export function wantsHtml(request: Request) {
  return request.method === "GET" && (request.headers.get("accept") || "").split(",").some((part) => {
    const [type, ...parameters] = part.trim().toLowerCase().split(";");
    const q = parameters.map((p) => p.trim()).find((p) => p.startsWith("q="));
    return type === "text/html" && (!q || Number(q.slice(2)) > 0);
  });
}
export function machinePath(path: string) {
  return /^\/(?:api|mcp|payments|webhooks|internal)(?:\/|$)/.test(path) || /\.(?:json|xml|md|txt|js|css|svg|png|ico|webmanifest|sh|py)$/.test(path);
}
export function humanBrowserPath(path: string) {
  return ["/auth/login", "/auth/callback", "/sources/github/setup", "/sources/github/callback"].includes(path) || /^\/connections\/oauth\/(x|threads|linkedin)\/callback$/.test(path);
}
export function productNavigation(path: string, mobile = false) {
  return navigation.map(([href, label]) => `<a href="${href}"${canonicalPath(path) === href ? ' aria-current="page"' : ""}>${label}</a>`).join("");
}
function safeOrigin(env: Env) {
  try {
    const url = new URL(env.PUBLIC_ORIGIN);
    return url.protocol === "https:" && !url.username && !url.password && url.origin === env.PUBLIC_ORIGIN && !url.hostname.endsWith(".invalid") ? url.origin : undefined;
  } catch { return undefined; }
}
export function runtimeHeroNote(env: Env) {
  if (env.DEPLOY_ENV === "production") return env.SIGNUP_MODE === "public"
    ? "Start free with Google. Connect your accounts and approve what your agents publish."
    : "Sign in with your approved Google account to open your workspace.";
  const environment = env.DEPLOY_ENV === "production" ? "PRODUCTION" : env.DEPLOY_ENV === "staging" ? "STAGING" : "ENVIRONMENT UNCONFIRMED";
  const access = env.SIGNUP_MODE === "public" ? "PUBLIC SIGNUP" : "OWNER ACCESS RESTRICTED";
  const advanced = env.ADVANCED_ENABLED === "true" ? "ADVANCED SUBJECT TO WORKSPACE ELIGIBILITY" : "ADVANCED AUTOMATION DISABLED";
  return `${environment} · ${access} · ${advanced}`;
}
export function browserErrorDocument(path: string, status: number, value: any) {
  const code = typeof value?.error?.code === "string" ? value.error.code.slice(0, 120) : "REQUEST_FAILED";
  const message = typeof value?.error?.message === "string" ? value.error.message.slice(0, 800) : "This browser operation did not complete.";
  const candidate = value?.error?.details?.reference;
  const reference = typeof candidate === "string" && /^[a-zA-Z0-9_-]{1,80}$/.test(candidate) ? candidate : "";
  const login = path === "/auth/login" || path === "/auth/callback";
  return `<!doctype html><html lang="en-GB"><head><meta charset="utf-8" /><meta name="viewport" content="width=device-width,initial-scale=1" /><meta name="robots" content="noindex,nofollow" /><title>PostSteward · This step did not complete</title><link rel="icon" href="/favicon.svg" type="image/svg+xml" /><link rel="stylesheet" href="/style.css" /><link rel="stylesheet" href="/production-ux.css" /><link rel="stylesheet" href="/journey.css" /><script src="/journey.js" defer></script><link rel="stylesheet" href="/accessibility.css" /></head><body class="browser-error-page"><a class="skip-link" href="#main-content">Skip to main content</a><main class="browser-error-card" id="main-content" tabindex="-1"><a class="wordmark" href="/"><img class="brand-mark" src="/favicon.svg" alt="" /><span>PostSteward</span></a><p class="eyebrow">BROWSER OPERATION</p><h1>This step did not complete.</h1><p class="browser-error-message">${escapeHtml(message)}</p><dl class="browser-error-meta"><div><dt>Status</dt><dd>${status}</dd></div><div><dt>Code</dt><dd><code>${escapeHtml(code)}</code></dd></div>${reference ? `<div><dt>Reference</dt><dd><code>${escapeHtml(reference)}</code></dd></div>` : ""}</dl><p class="muted">Inspect the existing receipt or workspace state before retrying a consequential action. A callback URL must not be replayed.</p><div class="browser-error-actions"><a class="button primary" href="${login ? "/auth/login" : "/app"}">${login ? "Start sign-in again" : "Return to workspace"}</a><a class="button" href="/support">Support guidance</a></div></main></body></html>`;
}
function presentedHeaders(response: Response, type = "text/html; charset=utf-8") {
  const headers = new Headers(response.headers);
  headers.set("Content-Type", type);
  headers.set("Cache-Control", "no-store");
  for (const key of ["Content-Length", "ETag", "Last-Modified", "Content-Encoding"]) headers.delete(key);
  return headers;
}
export function sitemapDocument(env: Env) {
  const origin = safeOrigin(env);
  const paths = env.DEPLOY_ENV === "production" && env.SIGNUP_MODE === "public" && origin ? [...publicPages, ...publicDiscoveryPaths] : [];
  return `<?xml version="1.0" encoding="UTF-8"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">${paths.map((path) => `<url><loc>${escapeHtml(origin + path)}</loc></url>`).join("")}</urlset>`;
}
async function presentHumanError(request: Request, response: Response) {
  const path = new URL(request.url).pathname;
  if (response.status < 400 || !wantsHtml(request) || !humanBrowserPath(path) || !(response.headers.get("content-type") || "").includes("application/json")) return response;
  const value: any = await response.clone().json().catch(() => ({}));
  if (!value.error?.details?.reference && response.headers.get("X-Request-ID")) value.error = { ...value.error, details: { reference: response.headers.get("X-Request-ID") } };
  return new Response(browserErrorDocument(path, response.status, value), { status: response.status, headers: presentedHeaders(response) });
}
async function presentNotFound(request: Request, response: Response, env: Env) {
  const path = new URL(request.url).pathname;
  if (response.status !== 404 || !wantsHtml(request) || machinePath(path) || humanBrowserPath(path) || path.startsWith("/auth/")) return response;
  const asset = await env.ASSETS.fetch(new Request(new URL("/404.html", request.url), { headers: { Accept: "text/html" } }));
  if (!(asset.headers.get("content-type") || "").includes("text/html")) return response;
  return new Response(asset.body, { status: 404, headers: presentedHeaders(response) });
}
async function presentHtml(request: Request, response: Response, env: Env) {
  if (!(response.headers.get("content-type") || "").includes("text/html")) return response;
  const path = canonicalPath(new URL(request.url).pathname);
  const owner = ownerPaths.has(path);
  const home = path === "/";
  const environment = ["staging", "production"].includes(env.DEPLOY_ENV || "") ? env.DEPLOY_ENV! : "unknown";
  const headers = presentedHeaders(response);
  if (environment !== "production" || env.SIGNUP_MODE !== "public" || owner || response.status >= 400) headers.set("X-Robots-Tag", "noindex, nofollow");
  const origin = safeOrigin(env);
  const preview = origin && response.status === 200 ? socialPreviewTags(path, origin) : "";
  const document = new Response(response.body, { status: response.status, statusText: response.statusText, headers });
  const rewriter = new HTMLRewriter()
    .on('link[rel="canonical"]', { element(e) { e.remove(); } })
    .on('meta[property^="og:"]', { element(e) { e.remove(); } })
    .on('meta[name^="twitter:"]', { element(e) { e.remove(); } })
    .on('meta[name="description"]', { element(e) { if (preview) e.remove(); } })
    .on('title', { element(e) { if (preview) e.setInnerContent(socialPages[path].title); } })
    .on("a.skip-link", { element(e) { e.remove(); } })
    .on("head", { element(e) {
      e.append('<link rel="apple-touch-icon" href="/apple-touch-icon.png" sizes="180x180" /><link rel="icon" href="/favicon.ico" sizes="any" /><link rel="stylesheet" href="/accessibility.css" /><link rel="stylesheet" href="/production-ux.css" /><link rel="stylesheet" href="/journey.css" /><script src="/journey.js" defer></script>', { html: true });
      e.append('<script src="/wayfinding.js" defer></script>', { html: true });
      if (owner) e.append('<script type="module" src="/owner-ui.js"></script>', { html: true });
      if (path === "/app") e.append('<link rel="stylesheet" href="/workspace-layout.css" />', { html: true });
      if (preview) e.append(preview, { html: true });
    } })
    .on("body", { element(e) {
      e.setAttribute("data-environment", environment);
      e.setAttribute("data-signup-mode", env.SIGNUP_MODE === "public" ? "public" : "restricted");
      e.prepend('<a class="skip-link" href="#main-content">Skip to main content</a>', { html: true });
    } })
    .on("main", { element(e) { e.setAttribute("id", "main-content"); e.setAttribute("tabindex", "-1"); } })
    .on(".environment-badge", { element(e) { if (environment === "production") e.remove(); else { e.setInnerContent(environment); e.setAttribute("title", `Runtime environment: ${environment}`); } } })
    .on(".product-nav", { element(e) { e.setAttribute("aria-label", "Product navigation"); e.setInnerContent(productNavigation(path), { html: true }); } })
    .on(".product-menu-panel", { element(e) { e.setInnerContent(productNavigation(path, true), { html: true }); } })
    .on(".site-footer-links", { element(e) { e.setInnerContent('<a href="/docs/install">Install</a><a href="/docs/agent-guide">Agent guide</a><a href="/docs/operations">Operations</a><a href="/help.json">Machine help</a><a href="/privacy">Privacy</a><a href="/terms">Terms</a><a href="/security">Security</a><a href="/support">Support</a><a href="/status">Status</a>', { html: true }); } });
  if (home) {
    rewriter.on('[data-plan="advanced"] .plan-price', { element(e) {
      const price = billingPrice(env);
      e.setInnerContent(price.configured ? new Intl.NumberFormat("en-GB", {style:"currency",currency:price.currency.toUpperCase()}).format(price.amount! / 100) + " per workspace / month" : "GBP · monthly price to be confirmed");
    } });
    rewriter.on(".hero-note", { element(e) { e.setInnerContent(runtimeHeroNote(env)); } });
    rewriter.on("#advanced-state", { element(e) { e.setInnerContent(env.ADVANCED_ENABLED === "true" ? billingPrice(env).configured ? "Advanced requires confirmed payment and your approval of each automation profile." : "Advanced automation is enabled for entitled workspaces. Purchases open when the GBP monthly price is confirmed." : "Advanced automation is unavailable here. Free publishing and scheduling are available."); } });
  }
  if (!path.startsWith("/docs/")) {
    rewriter.on('a[href="/docs/agent-guide.md"]', { element(e) { e.setAttribute("href", "/docs/agent-guide"); } })
      .on('a[href="/docs/operations.md"]', { element(e) { e.setAttribute("href", "/docs/operations"); } });
  }
  return rewriter.transform(document);
}
export async function presentBrowserResponse(request: Request, response: Response, env: Env) {
  const path = new URL(request.url).pathname;
  if (path === "/og-image.svg" && ["GET", "HEAD"].includes(request.method) && response.status === 404) {
    const headers = new Headers(response.headers);
    for (const key of ["Content-Length", "Content-Type", "Content-Encoding"]) headers.delete(key);
    headers.set("Location", socialImagePath);
    headers.set("Cache-Control", "public, max-age=86400");
    return new Response(null, {status:301,headers});
  }
  if (path === "/sitemap.xml" && request.method === "GET" && response.ok) return new Response(sitemapDocument(env), { status: 200, headers: presentedHeaders(response, "application/xml; charset=utf-8") });
  if (machinePath(path)) return response;
  const error = await presentHumanError(request, response);
  if (error !== response) return error;
  return presentHtml(request, await presentNotFound(request, response, env), env);
}
