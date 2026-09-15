import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

test("reviewed SVGs generate correctly sized browser and social assets", () => {
  for (const [file, width, height] of [
    ["apple-touch-icon.png", 180, 180],
    ["icon-192.png", 192, 192],
    ["icon-512.png", 512, 512],
    ["og-image.png", 1200, 630],
  ]) {
    const bytes = readFileSync("public/" + file);
    assert.equal(bytes.subarray(1, 4).toString(), "PNG", file);
    assert.equal(bytes.readUInt32BE(16), width, file);
    assert.equal(bytes.readUInt32BE(20), height, file);
  }
  const ico = readFileSync("public/favicon.ico");
  assert.equal(ico.readUInt16LE(2), 1);
  assert.equal(ico.readUInt16LE(4), 3);
});

test("build and deployment regenerate assets instead of relying on orphan binaries", () => {
  const pkg = JSON.parse(readFileSync("package.json", "utf8"));
  assert.match(pkg.scripts.build, /^npm run assets && /);
  assert.match(pkg.scripts.deploy, /^npm run assets && /);
  const script = readFileSync("scripts/export-brand-assets.mjs", "utf8");
  assert.match(script, /public\/favicon\.svg/);
  assert.match(script, /public\/og-image\.svg/);
});
