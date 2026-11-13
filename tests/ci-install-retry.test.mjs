import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { resolveInstall, isRetryableInstallTransport, runInstall, spawnBoundedInstall } from '../scripts/ci-install-retry.mjs';
import { execFileSync } from 'node:child_process';

const pipes = () => {
  let output = '';
  return { stdout: { write: s => { output += s; } }, stderr: { write: s => { output += s; } }, get: () => output };
};

test('transient npm registry failure retries locked install and preserves success only on real zero', async () => {
  const io = pipes();
  const calls = [];
  const waits = [];
  const sequence = [{ status: 155, stderr: 'npm error code ENETUNREACH' }, { status: 0, stdout: 'added 20 packages' }];
  const result = await runInstall('npm-root', {
    run: (command, args, options) => { calls.push({ command, args, options }); return sequence.shift(); },
    sleep: ms => { waits.push(ms); }, ...io
  });
  assert.equal(result, 0);
  assert.equal(calls.length, 2);
  assert.deepEqual(waits, [1000]);
  assert.deepEqual(calls[0].args, calls[1].args);
  assert.match(io.get(), /POSTSTEWARD_CI_INSTALL_OK/);
});

test('dependency resolution or integrity failure never retries even with transport noise', async () => {
  let calls = 0;
  const io = pipes();
  const result = await runInstall('npm-root', {
    run: () => { calls++; return { status: 1, stderr: 'EINTEGRITY and ETIMEDOUT' }; },
    sleep: () => { throw new Error('unexpected wait'); }, ...io
  });
  assert.equal(result, 1);
  assert.equal(calls, 1);
  assert.match(io.get(), /permanent_or_unknown/);
});

test('network retry budget exhausts and never returns artificial green', async () => {
  let calls = 0;
  const io = pipes();
  const result = await runInstall('runtime-matrix', {
    env: { RUNNER_TEMP: '/tmp/ci-private' },
    run: () => { calls++; return { status: 2, stderr: 'urllib3.exceptions.ReadTimeoutError: Read timed out' }; },
    sleep: () => {}, ...io
  });
  assert.equal(calls, 3);
  assert.equal(result, 1);
  assert.match(io.get(), /transport_exhausted/);
});

test('unrecognised failures never loop', async () => {
  let calls = 0;
  const result = await runInstall('npm-browser', {
    run: () => { calls++; return { status: 1, stderr: 'unexpected failure' }; },
    sleep: () => { throw new Error('unexpected wait'); }, ...pipes()
  });
  assert.equal(result, 1);
  assert.equal(calls, 1);
});

test('Python installs use isolated requested venv, exact source extras and bounded timeout', () => {
  const spec = resolveInstall('runtime-verify', { RUNNER_TEMP: '/tmp/poststeward-private' });
  assert.equal(spec.command, '/tmp/poststeward-private/poststeward-runtime-verify/bin/python');
  assert.ok(spec.args.includes('./runtime[dev]'));
  assert.ok(spec.timeout <= 240000);
  const lean = resolveInstall('runtime-matrix', { RUNNER_TEMP: '/tmp/poststeward-private', POSTSTEWARD_CI_RUNTIME_EXTRA: 'test' });
  assert.ok(lean.args.includes('./runtime[test]'));
  assert.throws(() => resolveInstall('runtime-matrix', { RUNNER_TEMP: '/tmp/poststeward-private', POSTSTEWARD_CI_RUNTIME_EXTRA: 'arbitrary' }), /Unsupported Python/);
  assert.throws(() => resolveInstall('runtime-verify', { RUNNER_TEMP: '' }), /RUNNER_TEMP/);
  assert.throws(() => resolveInstall('unknown'), /Unknown locked/);
});

test('npm browser install preserves ignore-scripts and source package lock', () => {
  const spec = resolveInstall('npm-browser');
  assert.deepEqual(spec.args.slice(0,1), ['ci']);
  assert.ok(spec.args.includes('--ignore-scripts'));
  assert.ok(spec.args.includes('tools/browser'));
  assert.equal(spec.attempts, 2);
  assert.equal(isRetryableInstallTransport({ status: 0, stderr: 'ETIMEDOUT' }), false);
});

test('protected workflows use bounded retry for the four source install gates', () => {
  const ci = readFileSync(new URL('../.github/workflows/ci.yml', import.meta.url), 'utf8');
  const browser = readFileSync(new URL('../.github/workflows/ux-browser.yml', import.meta.url), 'utf8');
  assert.match(ci, /node scripts\/ci-install-retry\.mjs npm-root/);
  assert.match(ci, /node scripts\/ci-install-retry\.mjs runtime-matrix/);
  assert.match(ci, /node scripts\/ci-install-retry\.mjs runtime-verify/);
  assert.match(browser, /node scripts\/ci-install-retry\.mjs npm-browser/);
  assert.match(browser, /node scripts\/ci-install-retry\.mjs npm-root/);
  assert.doesNotMatch(browser, /^\s+npm ci\s*$/m);
});


test('real timeout terminates installer process group before any retry can start', { skip: process.platform === 'win32' }, async () => {
  const result = await spawnBoundedInstall('bash', ['-c', 'sleep 20 & echo $!; wait'], { timeout: 150 });
  const grandchild = Number(result.stdout.trim());
  assert.ok(Number.isInteger(grandchild) && grandchild > 0);
  assert.equal(result.error?.code, 'ETIMEDOUT');
  let state = '';
  try { state = execFileSync('ps', ['-o', 'stat=', '-p', String(grandchild)], { encoding: 'utf8' }).trim(); }
  catch { state = ''; }
  // A momentary zombie is harmless: it cannot execute another install.
  assert.ok(!state || state.startsWith('Z'), `timed-out descendant still running: pid=${grandchild} state=${state}`);
});

test('real nonzero installer exit fences abandoned grandchildren', { skip: process.platform === 'win32' }, async () => {
  const result = await spawnBoundedInstall('bash', ['-c', 'sleep 20 >/dev/null 2>&1 & echo $!; exit 17'], { timeout: 1800 });
  const grandchild = Number(result.stdout.trim());
  assert.equal(result.status, 17);
  assert.ok(grandchild > 0);
  let state = '';
  try { state = execFileSync('ps', ['-o', 'stat=', '-p', String(grandchild)], { encoding: 'utf8' }).trim(); }
  catch { state = ''; }
  assert.ok(!state || state.startsWith('Z'), `failed installer descendant still running: pid=${grandchild} state=${state}`);
});


test('successful installer cannot leave untracked background dependencies running', { skip: process.platform === 'win32' }, async () => {
  const result = await spawnBoundedInstall('bash', ['-c', 'sleep 20 >/dev/null 2>&1 & echo $!; exit 0'], { timeout: 1800 });
  const grandchild = Number(result.stdout.trim());
  assert.equal(result.status, 0);
  assert.ok(grandchild > 0);
  let state = '';
  try { state = execFileSync('ps', ['-o', 'stat=', '-p', String(grandchild)], { encoding: 'utf8' }).trim(); }
  catch { state = ''; }
  assert.ok(!state || state.startsWith('Z'), `successful installer leaked process: pid=${grandchild} state=${state}`);
});
