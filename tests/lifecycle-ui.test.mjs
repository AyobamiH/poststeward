import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

test("owner workspace exposes lifecycle controls and pending erasure is never described as inactive", () => {
  const app = readFileSync("public/app.html", "utf8");
  const html = readFileSync("public/lifecycle.html", "utf8");
  const js = readFileSync("public/lifecycle.js", "utf8");

  assert.match(app, /href="\/lifecycle">Data controls<\/a/);
  assert.match(html, /DELETE this PostSteward workspace|Delete this PostSteward workspace/);
  assert.match(js, /lifecycle\.deletion\?\.state === "pending"/);
  assert.match(js, /Deletion has started and this workspace is durably fenced/);
  assert.match(js, /Deletion is pending and this workspace is durably fenced/);
  assert.doesNotMatch(js, /localStorage|sessionStorage|document\.cookie|innerHTML/);
});
