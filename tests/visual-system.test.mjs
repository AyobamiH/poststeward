import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

const read = (path) => readFileSync(path, "utf8");

test("PostSteward visual shell is local, semantic and shared across product surfaces", () => {
  const css = read("public/style.css");
  const composition = read("public/composition.css");
  const favicon = read("public/favicon.svg");
  const pages = [
    "public/index.html",
    "public/app.html",
    "public/pilot.html",
    "public/lifecycle.html",
    "public/advanced-inventory.html",
    "public/recovery.html",
  ].map(read);

  assert.match(favicon, /publishing route forming a P/i);
  assert.match(css, /--brand:/);
  assert.match(css, /--success:/);
  assert.match(css, /--warning:/);
  assert.match(css, /--danger:/);
  assert.match(css, /prefers-color-scheme: dark/);
  assert.match(composition, /--page-max:/);
  assert.match(composition, /\.workspace-index/);
  assert.match(composition, /\.feature-story/);
  assert.doesNotMatch(css + composition, /@import|fonts\.googleapis\.com|use\.typekit\.net/);

  for (const html of pages) {
    assert.match(html, /href="\/favicon\.svg"/);
    assert.match(html, /href="\/style\.css"/);
    assert.match(html, /PostSteward/);
  }
});

test("public composition follows the product execution narrative", () => {
  const home = read("public/index.html");

  assert.match(home, /href="\/composition\.css"/);
  assert.match(home, /class="control-map"/);
  assert.match(home, /id="how-it-works"/);
  assert.match(home, /id="evidence"/);
  assert.match(home, /id="agents"/);
  assert.match(home, /id="pricing"/);

  const intent = home.indexOf("01 · CAPTURE INTENT");
  const effect = home.indexOf("02 · ROUTE THE EFFECT");
  const evidence = home.indexOf("03 · INSPECT EVIDENCE");
  const agents = home.indexOf('id="agents"');
  const pricing = home.indexOf('id="pricing"');

  assert.ok(intent > -1 && intent < effect);
  assert.ok(effect < evidence);
  assert.ok(evidence < agents);
  assert.ok(agents < pricing);
});

test("workspace composition preserves orientation and consequential controls", () => {
  const app = read("public/app.html");
  const pilot = read("public/pilot.html");
  const lifecycle = read("public/lifecycle.html");
  const recovery = read("public/recovery.html");

  assert.match(app, /href="\/composition\.css"/);
  assert.match(app, /class="workspace-index"/);
  for (const section of [
    "destinations",
    "publishing",
    "evidence-panel",
    "agent-access",
    "sources",
    "advanced",
    "recovery-safety",
  ]) {
    assert.match(app, new RegExp(`id="${section}"`));
  }

  for (const id of [
    "oauth",
    "campaign",
    "delivery",
    "receipts",
    "grant",
    "recovery-prepare",
    "recovery-execute",
    "recovery-confirmation",
  ]) {
    assert.match(app, new RegExp(`id="${id}"`));
  }

  for (const id of ["prepare-form", "confirm-form", "approve", "receipt"]) {
    assert.match(pilot, new RegExp(`id="${id}"`));
  }

  assert.match(lifecycle, /id="delete-form"/);
  assert.match(lifecycle, /id="confirmation"/);
  assert.match(recovery, /id="checkpoint-prepare"/);
  assert.match(recovery, /id="approximate-prepare"/);
});
