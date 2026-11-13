// Re-attempt only transport-failed lockfile installs in the isolated CI runner.
// No dependency bypass, no synthetic success, and no arbitrary command execution.
// Terminal dependency/integrity errors fail on their first occurrence.
import { spawn } from 'node:child_process';
import { existsSync } from 'node:fs';
import { join, resolve } from 'node:path';
import { setTimeout as delay } from 'node:timers/promises';
import { fileURLToPath } from 'node:url';

const transient = /\b(?:ECONNRESET|ETIMEDOUT|EAI_AGAIN|ENETUNREACH|EHOSTUNREACH|ECONNABORTED)\b|ReadTimeoutError|Read timed out|timed out while reading|Temporary failure in name resolution|Connection (?:broken|aborted|reset)|Client network socket disconnected before secure TLS connection was established/i;
const terminal = /\b(?:ERESOLVE|EINTEGRITY|EBADENGINE|EBADPLATFORM|EINVALIDPACKAGENAME|CERT_HAS_EXPIRED|SELF_SIGNED_CERT_IN_CHAIN)\b|No matching distribution found|Could not find a version that satisfies the requirement|Invalid requirement|hashes do not match/i;

export function isRetryableInstallTransport(result) {
  const output = String(result.stdout || '') + '\n' + String(result.stderr || '');
  if (result.status === 0 || terminal.test(output)) return false;
  return transient.test(output) || ['ETIMEDOUT', 'ECONNRESET', 'ENETUNREACH', 'EAI_AGAIN'].includes(result.error?.code);
}

export function resolveInstall(mode, env = process.env) {
  if (mode === 'npm-root' || mode === 'npm-browser') {
    const args = ['ci', '--fetch-retries=1', '--fetch-timeout=35000', '--fetch-retry-mintimeout=1000', '--fetch-retry-maxtimeout=4000'];
    // A runner's inherited npm omit setting must not silently remove the
    // required native workerd/esbuild optional dependencies from the lockfile.
    if (mode === 'npm-root') args.push('--include=optional');
    if (mode === 'npm-browser') args.push('--prefix', 'tools/browser', '--ignore-scripts');
    return { command: 'npm', args, attempts: 2, timeout: 150000 };
  }
  if (mode === 'runtime-matrix' || mode === 'runtime-verify') {
    if (!env.RUNNER_TEMP || !env.RUNNER_TEMP.startsWith('/')) {
      throw new Error('Absolute RUNNER_TEMP is required for isolated Python installation');
    }
    const extra = env.POSTSTEWARD_CI_RUNTIME_EXTRA || 'dev';
    if (!['dev', 'test'].includes(extra)) throw new Error('Unsupported Python dependency extra: ' + extra);
    const folder = mode === 'runtime-matrix' ? 'poststeward-runtime-matrix' : 'poststeward-runtime-verify';
    return {
      command: join(env.RUNNER_TEMP, folder, 'bin', 'python'),
      args: ['-m', 'pip', 'install', '--retries', '1', '--timeout', '45', '-e', './runtime[' + extra + ']', 'setuptools>=68', 'wheel'],
      attempts: 3,
      timeout: 240000
    };
  }
  throw new Error('Unknown locked CI installation mode: ' + mode);
}

// npm can return zero despite silently missing an optional native platform
// archive (observed in protected #251 verify). This is mandatory runtime
// content on the trusted Linux/x64 verifier, not an optional test assertion.
// Re-run only the same bounded locked install; never claim a green receipt.
export function inspectNativeArtifacts(mode, {
  platform = process.platform, arch = process.arch, cwd = process.cwd(), exists = existsSync
} = {}) {
  const required = mode === 'npm-root' && platform === 'linux' && arch === 'x64'
    ? ['node_modules/@cloudflare/workerd-linux-64/bin/workerd',
       'node_modules/@esbuild/linux-x64/bin/esbuild']
    : [];
  const missing = required.filter(file => !exists(join(cwd, file)));
  return { ok: missing.length === 0, missing };
}

// Run in a separate POSIX process group so that a timed-out npm/pip install
// cannot leave lifecycle-script or pip subprocesses running into the retry.
// spawnSync's timeout only kills its direct child and is insufficient here.
export function spawnBoundedInstall(command, args, { env = process.env, timeout = 150000 } = {}) {
  if (process.platform === 'win32') throw new Error('Isolated CI installer requires a POSIX runner');
  return new Promise(resolve => {
    let child;
    try {
      child = spawn(command, args, { env, detached: true, stdio: ['ignore', 'pipe', 'pipe'] });
    } catch (error) {
      resolve({ status: null, error, stdout: '', stderr: '' });
      return;
    }
    let stdout = '';
    let stderr = '';
    let error;
    let timedOut = false;
    let escalation;
    // Retain enough diagnostics for classification without unbounded memory use.
    const append = (previous, bytes) => (previous + bytes.toString('utf8')).slice(-8 * 1024 * 1024);
    child.stdout?.on('data', bytes => { stdout = append(stdout, bytes); });
    child.stderr?.on('data', bytes => { stderr = append(stderr, bytes); });
    child.once('error', failure => { error = failure; });
    const stopGroup = signal => {
      if (!child.pid) return;
      try { process.kill(-child.pid, signal); }
      catch (failure) { if (failure.code !== 'ESRCH') error ||= failure; }
    };
    const watchdog = setTimeout(() => {
      timedOut = true;
      stopGroup('SIGTERM');
      escalation = setTimeout(() => stopGroup('SIGKILL'), 2000);
    }, timeout);
    child.once('close', (status, signal) => {
      clearTimeout(watchdog);
      if (escalation) clearTimeout(escalation);
      // Fence descendants even if the parent exits zero; no background
      // installer may outlive this attempt or overlap a later CI phase.
      stopGroup('SIGKILL');
      resolve({ status: timedOut ? null : status, signal, stdout, stderr,
        error: timedOut ? Object.assign(new Error('CI installer deadline exceeded'), { code: 'ETIMEDOUT' }) : error });
    });
  });
}

export async function runInstall(mode, {
  env = process.env, run = spawnBoundedInstall, sleep = delay, stdout = process.stdout,
  stderr = process.stderr, inspectNative = inspectNativeArtifacts
} = {}) {
  const spec = resolveInstall(mode, env);
  for (let attempt = 1; attempt <= spec.attempts; attempt++) {
    const result = await run(spec.command, spec.args, {
      encoding: 'utf8', timeout: spec.timeout, maxBuffer: 8 * 1024 * 1024, env
    });
    stdout.write(String(result.stdout || ''));
    stderr.write(String(result.stderr || ''));
    if (result.status === 0) {
      const native = inspectNative(mode);
      if (native.ok) {
        stdout.write('POSTSTEWARD_CI_INSTALL_OK mode=' + mode + ' attempts=' + attempt + '\n');
        return 0;
      }
      stderr.write('POSTSTEWARD_CI_NATIVE_MISSING mode=' + mode + ' paths=' + native.missing.join(',') + '\n');
      if (attempt === spec.attempts) {
        stderr.write('POSTSTEWARD_CI_INSTALL_FAILED mode=' + mode + ' attempts=' + attempt +
          ' classification=missing_native_artifact\n');
        return 1;
      }
      stderr.write('POSTSTEWARD_CI_INSTALL_TRANSIENT mode=' + mode + ' attempt=' + attempt +
        ' retrying same locked install after missing required native archive\n');
      await sleep(1000 * attempt);
      continue;
    }
    const network = isRetryableInstallTransport(result);
    if (!network || attempt === spec.attempts) {
      stderr.write('POSTSTEWARD_CI_INSTALL_FAILED mode=' + mode + ' attempts=' + attempt +
        ' classification=' + (network ? 'transport_exhausted' : 'permanent_or_unknown') + '\n');
      return 1;
    }
    stderr.write('POSTSTEWARD_CI_INSTALL_TRANSIENT mode=' + mode + ' attempt=' + attempt +
      ' retrying unchanged locked dependencies\n');
    await sleep(1000 * attempt);
  }
  return 1;
}

if (process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  if (process.argv.length !== 3) {
    console.error('Usage: node scripts/ci-install-retry.mjs npm-root|npm-browser|runtime-matrix|runtime-verify');
    process.exitCode = 2;
  } else {
    try { process.exitCode = await runInstall(process.argv[2]); }
    catch (error) { console.error('POSTSTEWARD_CI_INSTALL_INVALID ' + error.message); process.exitCode = 2; }
  }
}
