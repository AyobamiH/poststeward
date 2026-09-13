import { mkdtempSync, readFileSync, rmSync, appendFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { spawnSync } from "node:child_process";
import { demand, validateConfiguration } from "./deployment-config.mjs";
const config = JSON.parse(readFileSync("wrangler.jsonc", "utf8"));
validateConfiguration(config);
// The runner's packaged browser is used; no unpinned npm browser driver is installed.
const browser = ["google-chrome", "google-chrome-stable", "chromium", "chromium-browser"].find((name) =>
  spawnSync(name, ["--version"], { encoding: "utf8", timeout: 10000 }).status === 0);
demand(browser, "No packaged Chromium browser was found. Browser acceptance has not passed.");
const checks = [];
for (const [width, height] of [[1280, 900], [390, 844]]) {
  const profile = mkdtempSync(join(tmpdir(), "poststeward-browser-"));
  try {
    const result = spawnSync(browser, ["--headless=new", "--disable-gpu", "--no-first-run", "--no-default-browser-check",
      "--user-data-dir=" + profile, "--window-size=" + width + "," + height,
      "--virtual-time-budget=5000", "--timeout=20000", "--dump-dom", config.vars.PUBLIC_ORIGIN + "/pilot"], {
      encoding: "utf8", timeout: 30000, maxBuffer: 1024 * 1024,
      // Do not pass deployment, Actions or provider credentials to the browser.
      env: { PATH: process.env.PATH, HOME: profile, TMPDIR: tmpdir(), LANG: "C.UTF-8" },
    });
    const dom = result.stdout || "";
    const notice = /id="notice"[^>]*>([\s\S]*?)<\/p>/.exec(dom)?.[1] || "";
    const passed = !result.error && result.status === 0 && notice.length > 0 &&
      !notice.includes("Checking the owner session") &&
      /id="workbench"[^>]*hidden/.test(dom) && /id="confirm"[^>]*disabled/.test(dom) &&
      /id="signin"[^>]*href="\/auth\/login\?return=%2Fpilot"/.test(dom);
    checks.push({ viewport: { width, height }, javascriptSettled: notice.length > 0 && !notice.includes("Checking the owner session"), ownerControlsBlocked: /id="workbench"[^>]*hidden/.test(dom), passed });
  } finally { rmSync(profile, { recursive: true, force: true }); }
}
const report = { release: config.vars.RELEASE_SHA, origin: config.vars.PUBLIC_ORIGIN,
  observedAt: new Date().toISOString(), checks, passed: checks.every((c) => c.passed),
  boundary: "Fresh unauthenticated Chromium contexts only. No consent, credentials, owner session, approval or publication is simulated by this browser check." };
console.log("POSTSTEWARD_PILOT_BROWSER_REPORT " + JSON.stringify(report));
if (process.env.GITHUB_STEP_SUMMARY) appendFileSync(process.env.GITHUB_STEP_SUMMARY, "\nBrowser acceptance:\n\n```json\n" + JSON.stringify(report, null, 2) + "\n```\n");
demand(report.passed, "Owner acceptance browser smoke failed. No completed owner sign-in or publication is claimed.");
