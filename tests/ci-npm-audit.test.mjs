import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';
import { isRetryableAuditTransport, runAudit, MAX_AUDIT_ATTEMPTS } from '../scripts/ci-npm-audit.mjs';

test('only transient audit transport failures are retryable', () => {
  assert.equal(isRetryableAuditTransport({ status: 1, stderr: 'npm warn audit request to registry.npmjs.org failed, reason: Client network socket disconnected before secure TLS connection was established\nnpm error audit endpoint returned an error' }), true);
  assert.equal(isRetryableAuditTransport({ status: 1, stderr: 'npm error code ETIMEDOUT' }), true);
  assert.equal(isRetryableAuditTransport({ status: 0, stderr: 'npm error audit endpoint returned an error' }), false);
  assert.equal(isRetryableAuditTransport({ status: 1, stdout: 'Severity: high\nnpm error audit endpoint returned an error' }), false);
  assert.equal(isRetryableAuditTransport({ status: 1, stdout: '1 critical severity vulnerability' }), false);
  assert.equal(isRetryableAuditTransport({ status: 1, stdout: '4 moderate severity vulnerabilities' }), false);
  assert.equal(isRetryableAuditTransport({ status: 1, stderr: 'npm error ERR_AUDIT_INVALID_LOCKFILE' }), false);
});
test('transient retry succeeds only after a genuine successful full audit', async () => {
  const argv = [];
  const sleeps = [];
  const result = await runAudit({
    run: (_cmd, args) => { argv.push(args); return argv.length === 1
      ? { status: 1, stderr: 'npm error audit endpoint returned an error' }
      : { status: 0, stdout: 'found 0 high severity vulnerabilities\n' }; },
    sleep: async ms => { sleeps.push(ms); },
  });
  assert.equal(result, 0);
  assert.equal(argv.length, 2);
  assert.deepEqual(sleeps, [1000]);
  assert.ok(argv[0].includes('--audit-level=high'));
  assert.ok(argv[0].includes('--fetch-retries=0'));
});
test('exhausted transport errors never pass and never start a fourth attempt', async () => {
  let tries = 0;
  const outcome = await runAudit({
    run: () => { tries++; return {status:1,stderr:'npm error audit endpoint returned an error'}; },
    sleep: async()=>{},
  });
  assert.equal(outcome, 1);
  assert.equal(tries, MAX_AUDIT_ATTEMPTS);
});
test('known vulnerabilities fail immediately; production audit keeps production scope', async () => {
  let count = 0;
  const outcome = await runAudit({
    productionOnly: true,
    run: (_cmd, args) => {
      count++;
      assert.ok(args.includes('--omit=dev'));
      return {status:1,stdout:'Severity: high\nGHSA-test'};
    },
    sleep: async()=>{throw new Error('Must not sleep on a security failure');},
  });
  assert.equal(outcome, 1);
  assert.equal(count, 1);
});

test('protected Verify workflow retains both mandatory high-severity audit scopes', () => {
  const workflow = readFileSync(new URL('../.github/workflows/ci.yml', import.meta.url), 'utf8');
  assert.match(workflow, /name: Production dependency audit\n\s+run: node scripts\/ci-npm-audit\.mjs --production-only/);
  assert.match(workflow, /name: Full dependency audit\n\s+run: node scripts\/ci-npm-audit\.mjs(?:\n|$)/);
  assert.doesNotMatch(workflow, /continue-on-error:\s*true/);
});
