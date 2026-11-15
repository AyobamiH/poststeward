import assert from 'node:assert/strict';
import { mkdtempSync, writeFileSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import test from 'node:test';
import { verifyBrowserEvidence } from '../tools/browser/ci-evidence.mjs';

const HEAD = 'a'.repeat(40);
const MAIN = 'b'.repeat(40);
function fixture(change = () => {}) {
  const dir = mkdtempSync(join(tmpdir(), 'ps-browser-evidence-'));
  const report = {
    synthetic: true,
    checks: Array.from({ length: 100 }, (_, i) => ({ name: 'browser check ' + i, passed: true })),
    failures: [], errors: [], forbidden: [], outbound: 0,
  };
  const values = {
    'revision.txt': HEAD + '\n',
    'main-revision.txt': MAIN + '\n',
    'source.tar': 'exact head archive',
    'main-source.tar': 'exact main archive',
    'report.json': JSON.stringify(report),
  };
  change(values, report);
  for (const [name, value] of Object.entries(values)) writeFileSync(join(dir, name), value);
  return { dir, report };
}
function check(change, expected = HEAD) {
  const { dir } = fixture(change);
  try { return verifyBrowserEvidence(dir, expected); }
  finally { rmSync(dir, { recursive: true, force: true }); }
}
test('full exact-source result produces a bounded source-bound receipt', () => {
  const result = check(() => {});
  assert.equal(result.browser_checks_passed, 100);
  assert.equal(result.source_revision, HEAD);
  assert.match(result.browser_report_sha256, /^[0-9a-f]{64}$/);
  assert.equal(result.provider_effects, 0);
});
test('missing or changed exact source and missing integration proof fail closed', () => {
  assert.throws(() => check(() => {}, 'c'.repeat(40)), /source does not match/);
  assert.throws(() => check((v) => { delete v['main-source.tar']; }), /ENOENT/);
  assert.throws(() => check((v) => { v['main-revision.txt'] = 'not-a-sha'; }), /Integration main/);
});
test('partial, duplicated, failed or unsafe browser checks cannot produce a pass', () => {
  assert.throws(() => check((v, r) => { r.checks.pop(); v['report.json'] = JSON.stringify(r); }), /complete browser/);
  assert.throws(() => check((v, r) => { r.checks[1].name = r.checks[0].name; v['report.json'] = JSON.stringify(r); }), /unique/);
  assert.throws(() => check((v, r) => { r.checks[0].passed = false; v['report.json'] = JSON.stringify(r); }), /Every named/);
  assert.throws(() => check((v, r) => { r.errors.push('unexpected'); v['report.json'] = JSON.stringify(r); }), /Nonempty errors/);
  assert.throws(() => check((v, r) => { r.outbound = 1; v['report.json'] = JSON.stringify(r); }), /Unexpected outbound/);
});
