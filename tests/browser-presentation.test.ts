import assert from "node:assert/strict";
import test from "node:test";
import {
  browserErrorDocument,
  canonicalPath,
  humanBrowserPath,
  machinePath,
  productNavigation,
  runtimeHeroNote,
  sitemapDocument,
  wantsHtml,
} from "../src/browser-presentation.ts";
import type { Env } from "../src/types.ts";

const env = (values: Partial<Env> = {}) =>
  ({
    PUBLIC_ORIGIN: "https://publish.example",
    SIGNUP_MODE: "restricted",
    DEPLOY_ENV: "staging",
    ADVANCED_ENABLED: "false",
    ...values,
  }) as Env;

test("clean public URLs retain one canonical identity", () => {
  assert.equal(canonicalPath("/docs/agent-guide.html"), "/docs/agent-guide");
  assert.equal(canonicalPath("/docs/index.html"), "/docs/");
  assert.equal(canonicalPath("/docs"), "/docs/");
  assert.equal(canonicalPath("/index.html"), "/");
  assert.equal(canonicalPath("/privacy"), "/privacy");
});

test("browser negotiation requires explicit acceptable HTML and machine paths stay machine-shaped", () => {
  for (const accept of [
    "",
    "*/*",
    "application/json",
    "text/html;q=0",
    "text/html; q=0, application/json",
  ])
    assert.equal(
      wantsHtml(
        new Request("https://publish.example/", {
          headers: { Accept: accept },
        }),
      ),
      false,
      accept,
    );
  assert.equal(
    wantsHtml(
      new Request("https://publish.example/", {
        headers: { Accept: "text/html,application/xhtml+xml" },
      }),
    ),
    true,
  );
  for (const path of [
    "/api/nope",
    "/mcp",
    "/mcp/unknown",
    "/payments/quote",
    "/internal/test",
    "/webhooks/stripe",
    "/docs/agent-guide.md",
    "/help.json",
    "/missing.js",
  ]) assert.equal(machinePath(path), true, path);
  assert.equal(machinePath("/missing-public-page"), false);
});

test("human browser presentation remains limited to owner browser journeys", () => {
  for (const path of [
    "/auth/login",
    "/auth/callback",
    "/connections/oauth/x/callback",
    "/connections/oauth/threads/callback",
    "/connections/oauth/linkedin/callback",
    "/sources/github/setup",
    "/sources/github/callback",
  ]) assert.equal(humanBrowserPath(path), true, path);
  for (const path of ["/api/session", "/mcp", "/payments/quote", "/webhooks/stripe"])
    assert.equal(humanBrowserPath(path), false, path);
});

test("environment is not inferred from hostnames and Advanced labels follow runtime policy", () => {
  assert.match(runtimeHeroNote(env()), /STAGING.*RESTRICTED.*DISABLED/);
  assert.match(runtimeHeroNote(env({ DEPLOY_ENV: "production" })), /^PRODUCTION.*RESTRICTED/);
  assert.doesNotMatch(
    runtimeHeroNote(env({ DEPLOY_ENV: "production", SIGNUP_MODE: "public" })),
    /STAGING/,
  );
  assert.match(runtimeHeroNote(env({ ADVANCED_ENABLED: "true" })), /WORKSPACE ELIGIBILITY/);
});

test("restricted environments do not advertise a crawlable sitemap", () => {
  assert.doesNotMatch(sitemapDocument(env()), /<loc>/);
  const publicXml = sitemapDocument(
    env({ DEPLOY_ENV: "production", SIGNUP_MODE: "public" }),
  );
  assert.match(publicXml, /https:\/\/publish.example\/docs\/agent-guide/);
  assert.doesNotMatch(publicXml, /\/app<|\/pilot</);
  assert.doesNotMatch(
    sitemapDocument(
      env({
        DEPLOY_ENV: "production",
        SIGNUP_MODE: "public",
        PUBLIC_ORIGIN: 'https://invalid.example/"',
      }),
    ),
    /<loc>/,
  );
});

test("shared product navigation has exactly one current destination", () => {
  const html = productNavigation("/advanced-inventory.html", true);
  assert.equal((html.match(/aria-current="page"/g) || []).length, 1);
  assert.match(html, /href="\/advanced-inventory" aria-current="page"/);
  assert.doesNotMatch(productNavigation('" onclick="evil'), /onclick/);
});

test("browser error page exposes only bounded safe failure evidence", () => {
  const html = browserErrorDocument("/auth/callback", 503, {
    error: {
      code: "LOGIN_CLIENT_REJECTED",
      message: "The identity provider rejected PostSteward's OAuth client configuration.",
      details: {
        reference: "123e4567-e89b-12d3-a456-426614174000",
        private: "never-render-this",
      },
    },
    privateToken: "never-render-this-either",
  });
  assert.match(html, /LOGIN_CLIENT_REJECTED/);
  assert.match(html, /123e4567-e89b-12d3-a456-426614174000/);
  assert.match(html, /Start sign-in again/);
  assert.doesNotMatch(html, /never-render-this|privateToken/);
});

test("browser error page escapes provider-controlled text and drops unbounded references", () => {
  const html = browserErrorDocument("/connections/oauth/x/callback", 400, {
    error: {
      code: "<script>alert(1)</script>",
      message: '<img src=x onerror="alert(1)">',
      details: { reference: "x".repeat(1000) },
    },
  });
  assert.doesNotMatch(html, /<script>alert|<img src=x|x{100}/);
  assert.match(html, /&lt;script&gt;/);
  assert.match(html, /&lt;img src=x onerror=&quot;alert\(1\)&quot;&gt;/);
  assert.match(html, /Return to workspace/);
});
