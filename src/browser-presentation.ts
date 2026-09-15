import type { Env } from "./types.ts";

const ownerPaths = new Set([
  "/app",
  "/app.html",
  "/pilot",
  "/pilot.html",
  "/advanced-inventory",
  "/advanced-inventory.html",
  "/lifecycle",
  "/lifecycle.html",
  "/recovery",
  "/recovery.html",
]);

function escapeHtml(value: unknown) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#39;");
}

function wantsHtml(request: Request) {
  const accept = request.headers.get("accept") || "";
  return request.method === "GET" && /(?:^|,)\s*text\/html(?:\s*;|\s*,|$)/i.test(accept);
}

export function humanBrowserPath(path: string) {
  return (
    path === "/auth/login" ||
    path === "/auth/callback" ||
    /^\/connections\/oauth\/(x|threads|linkedin)\/callback$/.test(path) ||
    path === "/sources/github/setup" ||
    path === "/sources/github/callback"
  );
}

function errorReturn(path: string) {
  if (path === "/auth/login" || path === "/auth/callback")
    return { href: "/auth/login", label: "Start sign-in again" };
  return { href: "/app", label: "Return to workspace" };
}

export function browserErrorDocument(
  path: string,
  status: number,
  value: any,
) {
  const code = typeof value?.error?.code === "string" ? value.error.code : "REQUEST_FAILED";
  const message =
    typeof value?.error?.message === "string"
      ? value.error.message
      : "This browser operation did not complete.";
  const reference =
    typeof value?.error?.details?.reference === "string"
      ? value.error.details.reference
      : "";
  const back = errorReturn(path);
  return `<!doctype html>
<html lang="en">
  <head>
    <meta charset="utf-8" />
    <meta name="viewport" content="width=device-width,initial-scale=1" />
    <meta name="robots" content="noindex,nofollow" />
    <title>PostSteward · Operation did not complete</title>
    <link rel="icon" href="/favicon.svg" type="image/svg+xml" />
    <link rel="stylesheet" href="/style.css" />
    <link rel="stylesheet" href="/production-ux.css" />
  </head>
  <body class="browser-error-page">
    <main class="browser-error-card">
      <a class="wordmark" href="/" aria-label="PostSteward home"><img class="brand-mark" src="/favicon.svg" alt="" /><span>PostSteward</span></a>
      <p class="eyebrow">BROWSER OPERATION</p>
      <h1>This step did not complete.</h1>
      <p class="browser-error-message">${escapeHtml(message)}</p>
      <dl class="browser-error-meta">
        <div><dt>Status</dt><dd>${status}</dd></div>
        <div><dt>Code</dt><dd><code>${escapeHtml(code)}</code></dd></div>
        ${reference ? `<div><dt>Reference</dt><dd><code>${escapeHtml(reference)}</code></dd></div>` : ""}
      </dl>
      <p class="muted">No success is inferred from this page. Use the existing PostSteward receipt or workspace state before retrying any consequential action.</p>
      <div class="browser-error-actions"><a class="button primary" href="${back.href}">${back.label}</a><a class="button" href="/">PostSteward home</a></div>
    </main>
  </body>
</html>`;
}

async function presentHumanError(request: Request, response: Response) {
  const path = new URL(request.url).pathname;
  if (
    response.status < 400 ||
    !wantsHtml(request) ||
    !humanBrowserPath(path) ||
    !(response.headers.get("content-type") || "").includes("application/json")
  )
    return response;
  const value = await response.clone().json().catch(() => ({}));
  const headers = new Headers(response.headers);
  headers.set("Content-Type", "text/html; charset=utf-8");
  headers.set("Cache-Control", "no-store");
  headers.delete("Content-Length");
  return new Response(browserErrorDocument(path, response.status, value), {
    status: response.status,
    headers,
  });
}

function runtimeHeroNote(env: Env) {
  if (env.DEPLOY_ENV === "production" && env.SIGNUP_MODE === "public") return "";
  if (env.DEPLOY_ENV === "production")
    return "PRODUCTION · OWNER ACCESS RESTRICTED · PUBLIC SIGNUP REMAINS GATED";
  return "RESTRICTED STAGING · PUBLIC SIGNUP AND ADVANCED AUTOMATION REMAIN GATED";
}

function socialMetadata(env: Env) {
  const origin = env.PUBLIC_ORIGIN;
  const image = origin + "/og-image.png";
  return `<link rel="canonical" href="${origin}/" />
<meta property="og:type" content="website" />
<meta property="og:site_name" content="PostSteward" />
<meta property="og:title" content="PostSteward · Verifiable agentic publishing" />
<meta property="og:description" content="Controlled social publishing for AI agents with explicit authority, durable schedules and inspectable provider evidence." />
<meta property="og:url" content="${origin}/" />
<meta property="og:image" content="${image}" />
<meta property="og:image:width" content="1200" />
<meta property="og:image:height" content="630" />
<meta name="twitter:card" content="summary_large_image" />
<meta name="twitter:title" content="PostSteward · Verifiable agentic publishing" />
<meta name="twitter:description" content="Controlled social publishing for AI agents with explicit authority and inspectable provider evidence." />
<meta name="twitter:image" content="${image}" />`;
}

async function presentHtml(request: Request, response: Response, env: Env) {
  if (!(response.headers.get("content-type") || "").includes("text/html")) return response;
  const path = new URL(request.url).pathname;
  const owner = ownerPaths.has(path);
  const home = path === "/" || path === "/index.html";
  const environment = env.DEPLOY_ENV || "unknown";
  const heroNote = runtimeHeroNote(env);
  const rewriter = new HTMLRewriter()
    .on("head", {
      element(element) {
        element.append('<link rel="stylesheet" href="/production-ux.css" />', { html: true });
        if (owner)
          element.append('<script type="module" src="/owner-ui.js"></script>', { html: true });
        if (home) element.append(socialMetadata(env), { html: true });
      },
    })
    .on("body", {
      element(element) {
        element.setAttribute("data-environment", environment);
        element.setAttribute("data-signup-mode", env.SIGNUP_MODE);
        element.setAttribute(
          "data-advanced-enabled",
          env.ADVANCED_ENABLED === "true" ? "true" : "false",
        );
      },
    })
    .on(".environment-badge", {
      element(element) {
        element.setInnerContent(environment);
        element.setAttribute("title", `Runtime environment: ${environment}`);
      },
    });
  if (home)
    rewriter.on(".hero-note", {
      element(element) {
        if (!heroNote) element.remove();
        else element.setInnerContent(heroNote);
      },
    });
  return rewriter.transform(response);
}

export async function presentBrowserResponse(
  request: Request,
  response: Response,
  env: Env,
) {
  const error = await presentHumanError(request, response);
  if (error !== response) return error;
  return presentHtml(request, response, env);
}
