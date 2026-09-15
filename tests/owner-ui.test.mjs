import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

const ui = readFileSync("public/owner-ui.js", "utf8");
const css = readFileSync("public/production-ux.css", "utf8");
const entry = readFileSync("src/canary-edge.ts", "utf8");

test("owner UX enhances evidence without taking operational authority", () => {
  assert.match(ui, /ux-provider-grid/);
  assert.match(ui, /ux-receipt-filter/);
  assert.match(ui, /published_verified/);
  assert.match(ui, /ambiguous_effect/);
  assert.match(ui, /\/api\/connections\/oauth\/status/);
  assert.match(ui, /\/api\/recovery\/status/);
  assert.doesNotMatch(ui, /\/api\/operations\//);
  assert.doesNotMatch(ui, /publish_now|schedule_create|automation_enable/);
  assert.doesNotMatch(ui, /localStorage|sessionStorage|document\.cookie|innerHTML/);
});

test("production UX keeps semantic status roles separate", () => {
  assert.match(css, /data-receipt-state="published_verified"/);
  assert.match(css, /var\(--success\)/);
  assert.match(css, /var\(--warning\)/);
  assert.match(css, /var\(--danger\)/);
  assert.match(css, /browser-error-card/);
});

test("browser presentation wraps the established edge rather than replacing it", () => {
  assert.match(entry, /import edge, \{ Workspace as BaseWorkspace \} from "\.\/edge\.ts"/);
  assert.match(entry, /presentBrowserResponse/);
  assert.match(entry, /return edge\.scheduled\(controller, env\)/);
  assert.match(entry, /await edge\.fetch\(request, env, ctx\)/);
});
