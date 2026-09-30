#!/usr/bin/env node
import { createDecipheriv, createHash } from 'node:crypto';
import { existsSync, readFileSync } from 'node:fs';
import { homedir } from 'node:os';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const ENC_PREFIX = 'enc:v1:';
const GCM_IV_BYTES = 12;
const GCM_TAG_BYTES = 16;
const SCRIPT_DIR = dirname(fileURLToPath(import.meta.url));
const REPO_ROOT = resolve(SCRIPT_DIR, '..');

function parseEnvText(text) {
  const parsed = {};
  for (const raw of String(text).split(/\n/)) {
    const line = raw.replace(/\r$/, '');
    const trimmed = line.trim();
    if (!trimmed || trimmed.startsWith('#')) continue;
    const match = line.match(/^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)\s*$/);
    if (!match) continue;
    let value = match[2];
    if (
      value.length >= 2 &&
      ((value.startsWith('"') && value.endsWith('"')) ||
        (value.startsWith("'") && value.endsWith("'")))
    ) {
      value = value.slice(1, -1);
    }
    parsed[match[1]] = value;
  }
  return parsed;
}

function findEnv(explicitPath) {
  const candidates = explicitPath
    ? [resolve(explicitPath)]
    : [
        join(REPO_ROOT, '.env'),
        join(homedir(), 'post-once', '.env'),
        join(process.cwd(), '.env'),
        join(homedir(), 'social-agents', '.env'),
        join(homedir(), 'OneClickPostFactory', 'social-agents', '.env'),
      ];
  for (const path of candidates) {
    if (existsSync(path)) return path;
  }
  throw new Error('no_local_env_found');
}

function first(env, ...names) {
  for (const name of names) {
    const value = env[name] ?? process.env[name];
    if (value !== undefined && String(value).trim()) return String(value).trim();
  }
  return '';
}

function serviceHeaders(serviceRole) {
  const headers = { accept: 'application/json', apikey: serviceRole };
  if (!serviceRole.startsWith('sb_')) headers.authorization = `Bearer ${serviceRole}`;
  return headers;
}

async function requestJson(url, options = {}) {
  const response = await fetch(url, options);
  const text = await response.text();
  let payload = null;
  try { payload = text ? JSON.parse(text) : null; } catch {}
  if (!response.ok) {
    const message = payload && typeof payload === 'object'
      ? payload.message || payload.error?.message || payload.error || 'request_failed'
      : 'request_failed';
    throw new Error(`http_${response.status}:${String(message).slice(0, 180)}`);
  }
  return payload;
}

function decryptCredential(stored, encryptionKey) {
  if (!stored) return null;
  let value = String(stored);
  if (value.startsWith('\\x')) {
    const decoded = Buffer.from(value.slice(2), 'hex').toString('utf8');
    value = decoded || value;
  }
  if (!value.startsWith(ENC_PREFIX)) return value;
  const combined = Buffer.from(value.slice(ENC_PREFIX.length), 'base64');
  if (combined.length <= GCM_IV_BYTES + GCM_TAG_BYTES) {
    throw new Error('encrypted_credential_payload_is_malformed');
  }
  const iv = combined.subarray(0, GCM_IV_BYTES);
  const ciphertextWithTag = combined.subarray(GCM_IV_BYTES);
  const ciphertext = ciphertextWithTag.subarray(0, ciphertextWithTag.length - GCM_TAG_BYTES);
  const authTag = ciphertextWithTag.subarray(ciphertextWithTag.length - GCM_TAG_BYTES);
  const key = createHash('sha256').update(encryptionKey).digest();
  const decipher = createDecipheriv('aes-256-gcm', key, iv);
  decipher.setAuthTag(authTag);
  return Buffer.concat([decipher.update(ciphertext), decipher.final()]).toString('utf8');
}

function shortId(value) {
  const s = String(value || '');
  return s.length <= 12 ? s : `${s.slice(0, 6)}…${s.slice(-6)}`;
}

async function threadsIdentity(token) {
  const url = new URL('https://graph.threads.net/v1.0/me');
  url.searchParams.set('fields', 'id,username,name');
  return requestJson(url, {
    headers: { Authorization: `Bearer ${token}`, accept: 'application/json' },
  });
}

async function main() {
  const argv = process.argv.slice(2);
  let explicitEnv = null;
  for (let i = 0; i < argv.length; i += 1) {
    if (argv[i] === '--env') explicitEnv = argv[++i] || null;
    else if (argv[i] === '--help' || argv[i] === '-h') {
      console.log('Usage: ./scripts/discover-threads-db-credentials [--env PATH]');
      return;
    } else {
      throw new Error(`unknown_argument:${argv[i]}`);
    }
  }

  const envPath = findEnv(explicitEnv);
  const env = parseEnvText(readFileSync(envPath, 'utf8'));
  const supabaseUrl = first(env, 'SUPABASE_URL').replace(/\/+$/, '');
  const serviceRole = first(
    env,
    'SUPABASE_SERVICE_ROLE_KEY',
    'SUPABASE_SECRET_KEY',
    'SERVICE_ROLE_KEY',
    'SUPABASE_SERVICE_ROLE',
    'SUPABASE_SERVICE_KEY'
  );
  const encryptionKey = first(env, 'CREDENTIAL_ENCRYPTION_KEY');
  if (!supabaseUrl || !serviceRole || !encryptionKey) {
    throw new Error('missing_supabase_bootstrap');
  }

  const url = new URL(`${supabaseUrl}/rest/v1/user_credentials`);
  url.searchParams.set(
    'select',
    'user_id,threads_token_enc,threads_account_id,threads_verification_status,threads_verified_at'
  );
  url.searchParams.set('threads_token_enc', 'not.is.null');
  url.searchParams.set('limit', '1000');
  const rows = await requestJson(url, { headers: serviceHeaders(serviceRole) });
  const candidates = [];

  for (const row of Array.isArray(rows) ? rows : []) {
    const candidate = {
      tenant_user_id: shortId(row.user_id),
      database_account_id: row.threads_account_id || null,
      database_verification_status: row.threads_verification_status || null,
      database_verified_at: row.threads_verified_at || null,
      token_usable: false,
      live_account_id: null,
      username: null,
      name: null,
      error: null,
    };
    try {
      const token = decryptCredential(row.threads_token_enc, encryptionKey);
      if (!token) throw new Error('empty_token_after_decrypt');
      const identity = await threadsIdentity(token);
      candidate.token_usable = Boolean(identity?.id);
      candidate.live_account_id = identity?.id ? String(identity.id) : null;
      candidate.username = identity?.username ? String(identity.username) : null;
      candidate.name = identity?.name ? String(identity.name) : null;
    } catch (error) {
      candidate.error = error instanceof Error ? error.message : String(error);
    }
    candidates.push(candidate);
  }

  console.log(JSON.stringify({
    ok: true,
    env_file: envPath,
    database_writes: false,
    credential_values_printed: false,
    candidate_count: candidates.length,
    candidates,
  }, null, 2));
}

try {
  await main();
} catch (error) {
  console.error(JSON.stringify({
    ok: false,
    error: error instanceof Error ? error.message : String(error),
  }, null, 2));
  process.exitCode = 1;
}
