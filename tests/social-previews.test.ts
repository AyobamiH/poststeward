import assert from "node:assert/strict";
import test from "node:test";
import {socialPages, socialPreviewTags, socialImagePath} from "../src/social-previews.ts";
import {publicPages, canonicalPath} from "../src/browser-presentation.ts";
test("all public and owner entry points have fixed previews; secret queries and machine routes are excluded", () => {
  for (const path of [...publicPages, "/app", "/pilot", "/lifecycle", "/recovery", "/advanced-inventory"]) {
    const tags = socialPreviewTags(path, "https://app.poststeward.com");
    assert.match(tags, /og:image:type" content="image\/png/);
    assert.ok(tags.includes("https://app.poststeward.com" + socialImagePath));
    assert.ok(tags.includes('content="https://app.poststeward.com' + path + '"'));
    assert.match(tags, /twitter:description/);
    assert.doesNotMatch(tags, /product_threads|PRIVATE_PREVIEW_SENTINEL|access_token=/);
  }
  for (const path of ["/api/session", "/auth/callback", "/payments/private", "/sources/private", "/missing", "/app?access_token=PRIVATE_PREVIEW_SENTINEL", "__proto__", "toString"]) assert.equal(socialPreviewTags(path, "https://app.poststeward.com"), "");
  const url = new URL("https://app.poststeward.com/app?workspace=PRIVATE_PREVIEW_SENTINEL&access_token=PRIVATE_PREVIEW_SENTINEL");
  assert.equal(socialPreviewTags(canonicalPath(url.pathname), url.origin), socialPreviewTags("/app", url.origin));
});
test("preview origins cannot contain credentials, attacker paths or query strings", () => {
  for (const origin of ["http://app.poststeward.com", "https://user:secret@app.poststeward.com", "https://app.poststeward.com/private", "https://app.poststeward.com?token=private", 'https://app.poststeward.com/"', "invalid", "https://missing.invalid"]) assert.equal(socialPreviewTags("/", origin), "");
  assert.equal(socialPages["/app"].title, "Workspace — PostSteward");
  assert.match(socialPages["/app"].description, /Sign in/);
});
