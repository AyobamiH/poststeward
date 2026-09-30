import assert from 'node:assert/strict';
import { createCipheriv, createHash, randomBytes } from 'node:crypto';
import { mkdirSync, mkdtempSync, readFileSync, statSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import test from 'node:test';

import {
  buildImportPlan,
  decryptCredential,
  discoverEnvFile,
  parseEnvText,
  persistPlan,
} from '../scripts/import-ocpf-db-credentials.mjs';

function encryptCredential(value, encryptionKey) {
  const iv = randomBytes(12);
  const key = createHash('sha256').update(encryptionKey).digest();
  const cipher = createCipheriv('aes-256-gcm', key, iv);
  const ciphertext = Buffer.concat([cipher.update(value, 'utf8'), cipher.final()]);
  const tag = cipher.getAuthTag();
  return `enc:v1:${Buffer.concat([iv, ciphertext, tag]).toString('base64')}`;
}

test('parseEnvText reads quoted and unquoted bootstrap values', () => {
  const parsed = parseEnvText(`\n# comment\nSUPABASE_URL=https://example.supabase.co\nSUPABASE_SERVICE_ROLE_KEY='secret'\nCREDENTIAL_ENCRYPTION_KEY="enc-key"\n`);
  assert.equal(parsed.SUPABASE_URL, 'https://example.supabase.co');
  assert.equal(parsed.SUPABASE_SERVICE_ROLE_KEY, 'secret');
  assert.equal(parsed.CREDENTIAL_ENCRYPTION_KEY, 'enc-key');
});

test('discoverEnvFile prefers the actual post-once repo env', () => {
  const root = mkdtempSync(join(tmpdir(), 'ocpf-env-'));
  const home = join(root, 'home');
  const repoRoot = join(home, 'post-once');
  mkdirSync(repoRoot, { recursive: true });
  writeFileSync(join(repoRoot, '.env'), 'SUPABASE_URL=x\n');
  const found = discoverEnvFile(null, { cwd: root, home, repoRoot });
  assert.equal(found, join(repoRoot, '.env'));
});

test('decryptCredential matches social-agents AES-256-GCM format', () => {
  const key = 'credential-encryption-key';
  const encoded = encryptCredential('threads-secret-token', key);
  assert.equal(decryptCredential(encoded, key), 'threads-secret-token');
});

test('buildImportPlan decrypts provider credentials without changing expiry truth', () => {
  const key = 'credential-encryption-key';
  const plan = buildImportPlan({
    threads_token_enc: encryptCredential('threads-token', key),
    threads_expires_at: '2026-10-01T00:00:00Z',
    threads_account_id: 'threads-id',
    linkedin_token_enc: encryptCredential('linkedin-token', key),
    linkedin_refresh_token_enc: encryptCredential('linkedin-refresh', key),
    linkedin_client_id_enc: encryptCredential('linkedin-client', key),
    linkedin_client_secret_enc: encryptCredential('linkedin-secret', key),
    linkedin_person_urn_enc: encryptCredential('urn:li:person:abc', key),
    linkedin_expires_at: '2026-10-02T00:00:00Z',
    linkedin_verification_status: 'verified',
  }, key, { LINKEDIN_VERSION: '202608' });

  assert.equal(plan.threads.access_token, 'threads-token');
  assert.equal(plan.threads.account_id, 'threads-id');
  assert.equal(plan.linkedin.token.access_token, 'linkedin-token');
  assert.equal(plan.linkedin.token.refresh_token, 'linkedin-refresh');
  assert.equal(plan.linkedin.settings.client_id, 'linkedin-client');
  assert.equal(plan.linkedin.settings.person_urn, 'urn:li:person:abc');
  assert.equal(plan.linkedin.client_secret, 'linkedin-secret');
  assert.equal(plan.linkedin.settings.verification_status, 'verified');
});

test('persistPlan writes private provider files outside the repository', () => {
  const config = mkdtempSync(join(tmpdir(), 'ocpf-config-'));
  const plan = {
    threads: { access_token: 'tok', token_type: 'bearer', expires_at: 0 },
    linkedin: {
      token: { access_token: 'li', refresh_token: null, expires_at: 0 },
      settings: { version: '202608', person_urn: 'urn:li:person:abc' },
      client_secret: 'secret',
    },
  };
  const result = persistPlan(plan, { OCPF_POST_CONFIG_DIR: config }, 'all');
  assert.equal(result.threads, true);
  assert.equal(result.linkedin, true);
  assert.equal(JSON.parse(readFileSync(join(config, 'threads-token.json'), 'utf8')).access_token, 'tok');
  assert.equal(JSON.parse(readFileSync(join(config, 'linkedin-token.json'), 'utf8')).access_token, 'li');
  if (process.platform !== 'win32') {
    assert.equal(statSync(join(config, 'threads-token.json')).mode & 0o777, 0o600);
    assert.equal(statSync(join(config, 'linkedin-client-secret')).mode & 0o777, 0o600);
  }
});
