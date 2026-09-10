import assert from "node:assert/strict";
import test from "node:test";
import { readFileSync } from "node:fs";
import { assertMergeProvenance } from "../scripts/assert-merge-provenance.mjs";
import { demandRecoveryConfirmation } from "../src/recovery-confirmation.ts";
import { releaseReadiness } from "../src/readiness.ts";
import { environment } from "./helpers.ts";

test("release readiness exposes capabilities without secrets and remains fail-closed", () => {
  const value = releaseReadiness({
    ...environment,
    DEPLOY_ENV: "staging",
    SIGNUP_MODE: "restricted",
    ADVANCED_ENABLED: "false",
    MPP_ENABLED: "false",
    X_OAUTH_CLIENT_ID: "x",
    X_OAUTH_CLIENT_SECRET: "secret-x",
  });
  assert.equal(value.access.publicSignup, false);
  assert.equal(value.payments.advancedEnabled, false);
  assert.equal(value.providers.x.oauth, true);
  assert.equal(value.providers.threads.oauth, false);
  assert.equal(value.recovery.externalEffectLedger, true);
  assert.doesNotMatch(
    JSON.stringify(value),
    /secret-x|ENCRYPTION|CLIENT_SECRET/,
  );
});

test("recovery high-consequence transitions require the exact workspace phrase", () => {
  assert.doesNotThrow(() =>
    demandRecoveryConfirmation("RESTORE", "workspace-1", "RESTORE workspace-1"),
  );
  for (const value of [
    "restore workspace-1",
    "RESTORE workspace-2",
    "RESTORE  workspace-1",
    "",
  ])
    assert.throws(
      () => demandRecoveryConfirmation("RESTORE", "workspace-1", value),
      { code: "RECOVERY_CONFIRMATION_REQUIRED" },
    );
});

test("staging merge provenance accepts only the exact merged main PR and redacts upstream failure", async () => {
  const sha = "a".repeat(40),
    base = {
      REQUIRE_MERGED_PR: "true",
      GITHUB_REPOSITORY: "AyobamiH/poststeward",
      GITHUB_SHA: sha,
      GITHUB_TOKEN: "private-actions-token",
    };
  const ok = await assertMergeProvenance(base, async (_url, init) => {
    assert.equal(init.redirect, "manual");
    assert.equal(init.headers.Authorization, "Bearer private-actions-token");
    return Response.json([
      {
        number: 14,
        merged_at: "2026-09-10T00:00:00Z",
        base: { ref: "main" },
        merge_commit_sha: sha,
      },
    ]);
  });
  assert.equal(ok.pullRequest, 14);
  await assert.rejects(
    assertMergeProvenance(base, async () =>
      Response.json([
        {
          number: 14,
          merged_at: null,
          base: { ref: "main" },
          merge_commit_sha: sha,
        },
      ]),
    ),
    /requires this exact main revision/,
  );
  await assert.rejects(
    assertMergeProvenance(base, async () => {
      throw new Error("PRIVATE_NETWORK_DETAIL");
    }),
    (e) => !e.message.includes("PRIVATE_NETWORK_DETAIL"),
  );
});

test("workspace UI makes OAuth primary, exposes reviewed Advanced and recovery controls, and keeps tokens ephemeral", () => {
  const html = readFileSync("public/app.html", "utf8"),
    js = readFileSync("public/app.js", "utf8");
  assert.match(html, /id="oauth-buttons"/);
  assert.match(html, /Manual token import fallback/);
  assert.match(html, /id="recovery-prepare"/);
  assert.match(html, /id="profile"/);
  assert.match(js, /returnPath: "\/app"/);
  assert.match(js, /RESTORE \$\{session\.workspace\}/);
  assert.match(js, /form\.elements\.accessToken\.value = ""/);
  assert.doesNotMatch(
    js,
    /localStorage|sessionStorage|innerHTML|document\.cookie/,
  );
});
