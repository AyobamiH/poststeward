// A bounded, read-only retry for npm registry audit transport errors.
// A security finding, unknown failure, or exhausted transport budget always
// fails CI; no cached or synthetic "clean" report is ever accepted.
import { spawnSync } from 'node:child_process';
import { setTimeout as delay } from 'node:timers/promises';
import { fileURLToPath } from 'node:url';
import { resolve } from 'node:path';

export const MAX_AUDIT_ATTEMPTS = 3;
const transport = /audit (?:endpoint returned an error|request .* failed)|Client network socket disconnected before secure TLS connection was established|\b(?:ECONNRESET|ETIMEDOUT|EAI_AGAIN|ENETUNREACH)\b/i;
const securityFailure = /Severity:\s*(?:high|critical)\b|\b\d+\s+(?:high|critical)\s+severity vulnerabilities\b/i;

export function isRetryableAuditTransport(result) {
  const output = String(result.stdout || '') + '\n' + String(result.stderr || '');
  if (result.status === 0 || securityFailure.test(output)) return false;
  return transport.test(output) ||
    ['ECONNRESET', 'ETIMEDOUT', 'EAI_AGAIN', 'ENETUNREACH'].includes(result.error?.code);
}

export async function runAudit({ productionOnly = false, run = spawnSync, sleep = delay } = {}) {
  const args = ['audit', '--audit-level=high', '--fetch-timeout=25000', '--fetch-retries=0'];
  if (productionOnly) args.push('--omit=dev');
  for (let attempt = 1; attempt <= MAX_AUDIT_ATTEMPTS; attempt++) {
    const result = run('npm', args, { encoding: 'utf8', timeout: 35_000, maxBuffer: 5 * 1024 * 1024 });
    if (result.status === 0) {
      process.stdout.write(String(result.stdout || ''));
      process.stderr.write(String(result.stderr || ''));
      console.log('POSTSTEWARD_NPM_AUDIT_ACCEPTED attempts=' + attempt + ' scope=' + (productionOnly ? 'production' : 'full'));
      return 0;
    }
    if (attempt < MAX_AUDIT_ATTEMPTS && isRetryableAuditTransport(result)) {
      console.error('POSTSTEWARD_NPM_AUDIT_TRANSIENT attempt=' + attempt + ' scope=' + (productionOnly ? 'production' : 'full') + ' (bounded GET-only retry)');
      await sleep(1000 * attempt);
      continue;
    }
    process.stdout.write(String(result.stdout || ''));
    process.stderr.write(String(result.stderr || ''));
    console.error('POSTSTEWARD_NPM_AUDIT_FAILED scope=' + (productionOnly ? 'production' : 'full') +
      ' attempts=' + attempt + ' reason=' + (isRetryableAuditTransport(result) ? 'transport_exhausted' : 'audit_or_unknown_failure'));
    return 1;
  }
  return 1;
}

if (process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  if (process.argv.length > 3 || (process.argv[2] && process.argv[2] !== '--production-only')) {
    console.error('Usage: node scripts/ci-npm-audit.mjs [--production-only]');
    process.exitCode = 2;
  } else {
    process.exitCode = await runAudit({ productionOnly: process.argv[2] === '--production-only' });
  }
}
