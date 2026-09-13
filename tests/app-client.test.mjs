import assert from "node:assert/strict";
import test from "node:test";
import { readFileSync } from "node:fs";
import {
  oauthHosts,
  providerPostHosts,
  trustedExternal,
} from "../public/app-client.js";

test("external navigation accepts only exact HTTPS provider hosts", () => {
  assert.equal(
    trustedExternal("https://x.com/i/oauth2/authorize?x=1", oauthHosts("x")),
    "https://x.com/i/oauth2/authorize?x=1",
  );
  assert.equal(
    trustedExternal("https://threads.net/oauth/authorize", oauthHosts("threads")),
    "https://threads.net/oauth/authorize",
  );
  assert.equal(
    trustedExternal(
      "https://www.linkedin.com/oauth/v2/authorization",
      oauthHosts("linkedin"),
    ),
    "https://www.linkedin.com/oauth/v2/authorization",
  );
  for (const value of [
    "https://x.com.evil.example/i/oauth2/authorize",
    "http://x.com/i/oauth2/authorize",
    "https://user:secret@x.com/i/oauth2/authorize",
    "https://x.com:8443/i/oauth2/authorize",
    "javascript:alert(1)",
  ]) assert.equal(trustedExternal(value, oauthHosts("x")), undefined);
});

test("provider receipt navigation is provider-specific", () => {
  assert.equal(
    trustedExternal("https://x.com/i/web/status/123", providerPostHosts("x")),
    "https://x.com/i/web/status/123",
  );
  assert.equal(
    trustedExternal(
      "https://www.threads.com/@owner/post/ABC",
      providerPostHosts("threads"),
    ),
    "https://www.threads.com/@owner/post/ABC",
  );
  assert.equal(
    trustedExternal(
      "https://www.linkedin.com/feed/update/urn:li:share:123/",
      providerPostHosts("linkedin"),
    ),
    "https://www.linkedin.com/feed/update/urn:li:share:123/",
  );
  assert.equal(
    trustedExternal("https://www.linkedin.com/post/123", providerPostHosts("x")),
    undefined,
  );
});

test("Stripe redirects are constrained before the owner browser navigates", () => {
  assert.equal(
    trustedExternal("https://checkout.stripe.com/c/pay/test", ["checkout.stripe.com"]),
    "https://checkout.stripe.com/c/pay/test",
  );
  assert.equal(
    trustedExternal("https://billing.stripe.com/p/session/test", ["billing.stripe.com"]),
    "https://billing.stripe.com/p/session/test",
  );
  assert.equal(
    trustedExternal("https://checkout.stripe.com.evil.example/c/pay/test", [
      "checkout.stripe.com",
    ]),
    undefined,
  );
});

test("owner workspace keeps credentials out of browser storage and bounds API waits", () => {
  const source = readFileSync("public/app.js", "utf8");
  assert.doesNotMatch(source, /localStorage|sessionStorage|document\.cookie|innerHTML/);
  assert.match(source, /form\.elements\.accessToken\.value = ""/);
  assert.match(source, /AbortSignal\.timeout\(20000\)/);
  assert.match(source, /trustedExternal/);
  assert.doesNotMatch(source, /location\.assign\(started\.authorizationUrl\)/);
  assert.doesNotMatch(source, /location\.assign\(checkout\.url\)/);
});
