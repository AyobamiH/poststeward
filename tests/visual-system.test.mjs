import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

const read = (path) => readFileSync(path, "utf8");

test("PostSteward visual shell is local, semantic and shared across product surfaces", () => {
  const css = read("public/style.css");
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
  assert.doesNotMatch(css, /@import|fonts\.googleapis\.com|use\.typekit\.net/);

  for (const html of pages) {
    assert.match(html, /href="\/favicon\.svg"/);
    assert.match(html, /href="\/style\.css"/);
    assert.match(html, /PostSteward/);
  }
});

test("redesign preserves consequential owner and publishing controls", () => {
  const app = read("public/app.html");
  const pilot = read("public/pilot.html");
  const lifecycle = read("public/lifecycle.html");
  const recovery = read("public/recovery.html");

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
