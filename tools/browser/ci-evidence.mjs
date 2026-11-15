// Turn the real browser results into a source-bound, quota-independent CI receipt.
// This prints a bounded evidence record into GitHub job logs and the native
// job summary. It cannot convert a failed or partial browser run into success.
import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import { appendFileSync, readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const SHA = /^[0-9a-f]{40}$/;
const hex = (bytes) => createHash('sha256').update(bytes).digest('hex');
function bytes(root, name) {
  const value = readFileSync(resolve(root, name));
  assert.ok(value.length, `Missing or empty browser evidence: ${name}`);
  return value;
}
export function verifyBrowserEvidence(root, expectedSource) {
  assert.match(expectedSource || '', SHA, 'Expected exact CI source SHA is required');
  const source = bytes(root, 'revision.txt').toString('utf8').trim();
  const main = bytes(root, 'main-revision.txt').toString('utf8').trim();
  assert.equal(source, expectedSource, 'Browser source does not match this CI source');
  assert.match(main, SHA, 'Integration main revision must be exact');
  const reportBytes = bytes(root, 'report.json');
  const report = JSON.parse(reportBytes.toString('utf8'));
  assert.equal(report.synthetic, true, 'Synthetic browser evidence boundary is required');
  assert.ok(Array.isArray(report.checks) && report.checks.length >= 100,
    'The complete browser check set must execute');
  assert.ok(report.checks.every((c) => typeof c.name === 'string' && c.name.length > 0 && c.passed === true),
    'Every named browser check must have passed');
  assert.equal(new Set(report.checks.map((c) => c.name)).size, report.checks.length,
    'Browser check names must be unique');
  for (const key of ['failures', 'errors', 'forbidden']) {
    assert.ok(Array.isArray(report[key]) && report[key].length === 0, `Nonempty ${key} rejects acceptance`);
  }
  assert.equal(report.outbound, 0, 'Unexpected outbound effects reject acceptance');
  return {
    schema_version: 1,
    evidence: 'synthetic-browser-ci',
    status: 'passed',
    source_revision: source,
    integration_main_revision: main,
    source_archive_sha256: hex(bytes(root, 'source.tar')),
    integration_archive_sha256: hex(bytes(root, 'main-source.tar')),
    browser_report_sha256: hex(reportBytes),
    browser_checks_passed: report.checks.length,
    provider_effects: 0,
  };
}

if (process.argv[1] && fileURLToPath(import.meta.url) === resolve(process.argv[1])) {
  const summary = process.env.GITHUB_STEP_SUMMARY;
  assert.ok(summary, 'GitHub native job summary path is required for durable CI evidence');
  const receipt = verifyBrowserEvidence(resolve('ux-evidence'), process.env.GITHUB_SHA);
  console.log('POSTSTEWARD_BROWSER_EVIDENCE ' + JSON.stringify(receipt));
  appendFileSync(summary,
    ['### PostSteward synthetic browser CI evidence',
     `**${receipt.browser_checks_passed} real browser checks passed**; no errors, forbidden calls or provider effects.`,
     `Source: \`${receipt.source_revision}\`; integration main: \`${receipt.integration_main_revision}\`.`,
     `Source archive SHA-256: \`${receipt.source_archive_sha256}\`.`,
     `Integration archive SHA-256: \`${receipt.integration_archive_sha256}\`.`,
     `Report SHA-256: \`${receipt.browser_report_sha256}\`.`,
     'These are compiled-Worker/synthetic-fixture results, **not** live customer/provider acceptance.',
     'This job log contains the receipt and the 100 named browser checks; no storage-quota-dependent artifact is required.',
     ''].join('\n') + '\n');
}
