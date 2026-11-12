import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';

const script = readFileSync(new URL('../tools/browser/verify.mjs', import.meta.url), 'utf8');
const start = script.indexOf("await check('Reflow and doubled text preserve all route widths with accessible local scrolling'");
const end = script.indexOf("await check('Long owner records wrap", start);
const reflow = script.slice(start, end);

test('reflow waits for actual document, visible main and settled fonts, not network idleness', () => {
  assert.ok(start > 0 && end > start, 'Reviewed reflow acceptance check must exist');
  assert.match(reflow, /page\.goto\(origin\s*\+\s*path,\s*\{waitUntil:\s*'load'\}\)/);
  assert.match(reflow, /page\.locator\('main'\)\.waitFor\(\{state:\s*'visible'\}\)/);
  assert.match(reflow, /document\.fonts\.ready/);
  assert.doesNotMatch(reflow, /networkidle/, 'Network idleness is not a user-visible readiness condition');
  assert.match(reflow, /path\s*===\s*'\/app'/);
  assert.match(reflow, /#workspace-content/);
  assert.match(reflow, /document\.documentElement\.scrollWidth\s*<=\s*innerWidth\+1/);
  assert.match(reflow, /audit\(page\)/);
});

test('campaign feedback evidence captures the visible reviewed result, not an unbounded page', () => {
  const start = script.indexOf("await check('Campaign review success and lengthy validation failure stay adjacent");
  const end = script.indexOf("await check('Focused workspace routes", start);
  const campaign = script.slice(start, end);
  assert.ok(start > 0 && end > start);
  assert.match(campaign, /page\.locator\('#result'\)\.scrollIntoViewIfNeeded\(\)/);
  assert.match(campaign, /page\.screenshot\(\{path:`ux-evidence\/layout-campaign-/);
  assert.match(campaign, /fullPage:false/);
  assert.match(campaign, /animations:'disabled'/);
  assert.match(campaign, /caret:'hide'/);
  assert.doesNotMatch(campaign, /fullPage:true/);
});
