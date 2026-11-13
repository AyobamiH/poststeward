// Re-attempt only transport-failed lockfile installs in the isolated CI runner.
// No dependency bypass, no synthetic success, and no arbitrary command execution.
// Terminal dependency/integrity errors fail on their first occurrence.
import { spawnSync } from 'node:child_process';
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

export async function runInstall(mode, {
  env = process.env, run = spawnSync, sleep = delay, stdout = process.stdout, stderr = process.stderr
} = {}) {
  const spec = resolveInstall(mode, env);
  for (let attempt = 1; attempt <= spec.attempts; attempt++) {
    const result = run(spec.command, spec.args, {
      encoding: 'utf8', timeout: spec.timeout, maxBuffer: 8 * 1024 * 1024, env
    });
    stdout.write(String(result.stdout || ''));
    stderr.write(String(result.stderr || ''));
    if (result.status === 0) {
      stdout.write('POSTSTEWARD_CI_INSTALL_OK mode=' + mode + ' attempts=' + attempt + '\n');
      return 0;
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
