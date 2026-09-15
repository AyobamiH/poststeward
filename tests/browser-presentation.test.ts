import assert from "node:assert/strict";
import test from "node:test";
import {
  browserErrorDocument,
  humanBrowserPath,
} from "../src/browser-presentation.ts";

test("human browser presentation is restricted to owner-facing browser journeys", () => {
  for (const path of [
    "/auth/login",
    "/auth/callback",
    "/connections/oauth/x/callback",
    "/connections/oauth/threads/callback",
    "/connections/oauth/linkedin/callback",
    "/sources/github/setup",
    "/sources/github/callback",
  ]) assert.equal(humanBrowserPath(path), true, path);

  for (const path of [
    "/api/session",
    "/api/operations/publish_now",
    "/mcp",
    "/payments/quote",
    "/webhooks/stripe",
  ]) assert.equal(humanBrowserPath(path), false, path);
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
  assert.doesNotMatch(html, /never-render-this/);
  assert.doesNotMatch(html, /privateToken/);
});

test("browser error page escapes provider-controlled text", () => {
  const html = browserErrorDocument("/connections/oauth/x/callback", 400, {
    error: {
      code: "<script>alert(1)</script>",
      message: '<img src=x onerror="alert(1)">',
    },
  });
  assert.doesNotMatch(html, /<script>alert/);
  assert.doesNotMatch(html, /<img src=x/);
  assert.match(html, /&lt;script&gt;/);
  assert.match(html, /&lt;img src=x onerror=&quot;alert\(1\)&quot;&gt;/);
  assert.match(html, /Return to workspace/);
});
