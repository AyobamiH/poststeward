import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

const read = (path) => readFileSync(path, "utf8");

test("PostSteward visual system is local, semantic and shared across surfaces", () => {
  const css = read("public/style.css");
  const composition = read("public/composition.css");
  const productShell = read("public/product-shell.css");
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
  assert.match(composition, /\.feature-story/);
  assert.match(productShell, /--app-nav-width:/);
  assert.match(productShell, /\.product-sidebar/);
  assert.match(productShell, /\.product-page-header/);
  assert.match(productShell, /\.product-menu/);
  assert.doesNotMatch(css + composition + productShell, /@import|fonts\.googleapis\.com|use\.typekit\.net/);

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

test("owner surfaces share one application shell with distinct page roles", () => {
  const app = read("public/app.html");
  const pilot = read("public/pilot.html");
  const lifecycle = read("public/lifecycle.html");
  const inventory = read("public/advanced-inventory.html");
  const recovery = read("public/recovery.html");
  const ownerPages = [app, pilot, lifecycle, inventory, recovery];

  for (const html of ownerPages) {
    assert.match(html, /class="product-app"/);
    assert.match(html, /href="\/product-shell\.css"/);
    assert.match(html, /class="product-frame-shell"/);
    assert.match(html, /class="product-sidebar"/);
    assert.match(html, /class="product-page-header"/);
    assert.match(html, /class="product-menu"/);
  }

  assert.match(app, /class="work-zone-grid"/);
  assert.match(app, /class="evidence-zone"/);
  assert.match(pilot, /class="journey-layout"/);
  assert.match(pilot, /class="journey-progress"/);
  assert.match(lifecycle, /class="settings-sequence"/);
  assert.match(inventory, /class="resource-grid"/);
  assert.match(recovery, /class="recovery-contract"/);
});

test("redesign preserves consequential owner and publishing controls", () => {
  const app = read("public/app.html");
  const pilot = read("public/pilot.html");
  const lifecycle = read("public/lifecycle.html");
  const inventory = read("public/advanced-inventory.html");
  const recovery = read("public/recovery.html");

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
  assert.match(inventory, /id="categories"/);
  assert.match(inventory, /id="inventory"/);
  assert.match(inventory, /id="deliveries"/);
  assert.match(recovery, /id="checkpoint-prepare"/);
  assert.match(recovery, /id="approximate-prepare"/);
});
