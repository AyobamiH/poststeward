#!/usr/bin/env node
import {
  createDecipheriv,
  createHash,
} from 'node:crypto';
import {
  chmodSync,
  existsSync,
  mkdirSync,
  readFileSync,
  renameSync,
  unlinkSync,
  writeFileSync,
} from 'node:fs';
import { homedir } from 'node:os';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { spawnSync } from 'node:child_process';

const ENC_PREFIX = 'enc:v1:';
const GCM_IV_BYTES = 12;
const GCM_TAG_BYTES = 16;
const SCRIPT_DIR = dirname(fileURLToPath(import.meta.url));
const REPO_ROOT = resolve(SCRIPT_DIR, '..');
const DEFAULT_LINKEDIN_VERSION = '202608';

function parseArgs(argv) {
  const out = {
    envPath: null,
    userId: null,
    ownerEmail: null,
    provider: 'all',
    verify: true,
  };
  for (let i = 0; i < argv.length; i += 1) {
    const arg = argv[i];
    if (arg === '--env') out.envPath = argv[++i] || null;
    else if (arg === '--user-id') out.userId = argv[++i] || null;
    else if (arg === '--owner-email') out.ownerEmail = argv[++i] || null;
    else if (arg === '--provider') out.provider = argv[++i] || 'all';
    else if (arg === '--no-verify') out.verify = false;
    else if (arg === '--help' || arg === '-h') out.help = true;
    else throw new Error(`unknown_argument:${arg}`);
  }
  if (!['all', 'threads', 'linkedin'].includes(out.provider)) {
    throw new Error('provider_must_be_all_threads_or_linkedin');
  }
  return out;
}

export function parseEnvText(text) {
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

export function discoverEnvFile(explicitPath, options = {}) {
  const cwd = options.cwd || process.cwd();
  const home = options.home || homedir();
  const repoRoot = options.repoRoot || REPO_ROOT;
  const candidates = explicitPath
    ? [resolve(cwd, explicitPath)]
    : [
        join(repoRoot, '.env'),
        join(home, 'post-once', '.env'),
        join(cwd, '.env'),
        join(home, 'social-agents', '.env'),
        join(home, 'OneClickPostFactory', 'social-agents', '.env'),
      ];
  const seen = new Set();
  for (const candidate of candidates) {
    const normalized = resolve(candidate);
    if (seen.has(normalized)) continue;
    seen.add(normalized);
    if (existsSync(normalized)) return normalized;
  }
  throw new Error(
    explicitPath
      ? `env_file_not_found:${resolve(cwd, explicitPath)}`
      : 'no_local_env_found_checked_post_once_and_social_agents_locations'
  );
}

function firstValue(localEnv, ...names) {
  for (const name of names) {
    const local = localEnv[name];
    if (local !== undefined && String(local).trim()) return String(local).trim();
    const runtime = process.env[name];
    if (runtime !== undefined && String(runtime).trim()) return String(runtime).trim();
  }
  return '';
}

function serviceHeaders(serviceRole) {
  const headers = {
    accept: 'application/json',
    apikey: serviceRole,
  };
  if (!serviceRole.startsWith('sb_')) {
    headers.authorization = `Bearer ${serviceRole}`;
  }
  return headers;
}

async function requestJson(url, { headers, method = 'GET', body } = {}) {
  const response = await fetch(url, { headers, method, body });
  const text = await response.text();
  let payload = null;
  try {
    payload = text ? JSON.parse(text) : null;
  } catch {
    payload = null;
  }
  if (!response.ok) {
    const providerMessage = payload && typeof payload === 'object'
      ? payload.message || payload.error_description || payload.error || null
      : null;
    throw new Error(
      `http_${response.status}:${providerMessage ? String(providerMessage).slice(0, 240) : 'request_failed'}`
    );
  }
  return payload;
}

async function resolveInternalOwnerUserId(supabaseUrl, headers) {
  const url = new URL(`${supabaseUrl}/rest/v1/internal_access_overrides`);
  url.searchParams.set('select', 'user_id');
  url.searchParams.set('access_level', 'eq.internal_owner');
  url.searchParams.set('status', 'eq.active');
  url.searchParams.set('limit', '2');
  try {
    const rows = await requestJson(url, { headers });
    if (Array.isArray(rows) && rows.length === 1 && rows[0]?.user_id) {
      return String(rows[0].user_id);
    }
  } catch {
    // Fall through to explicit email resolution. Older deployments may not have this table.
  }
  return null;
}

async function resolveUserByEmail(supabaseUrl, headers, email) {
  const matches = [];
  for (let page = 1; page <= 10; page += 1) {
    const url = new URL(`${supabaseUrl}/auth/v1/admin/users`);
    url.searchParams.set('page', String(page));
    url.searchParams.set('per_page', '1000');
    const payload = await requestJson(url, { headers });
    const users = Array.isArray(payload?.users) ? payload.users : [];
    for (const user of users) {
      if (String(user?.email || '').toLowerCase() === email.toLowerCase() && user?.id) {
        matches.push(String(user.id));
      }
    }
    if (users.length < 1000) break;
  }
  if (matches.length !== 1) {
    throw new Error(`owner_email_not_unique_or_missing:matches=${matches.length}`);
  }
  return matches[0];
}

async function resolveTenantUserId({ supabaseUrl, headers, localEnv, explicitUserId, explicitOwnerEmail }) {
  const configured = explicitUserId || firstValue(
    localEnv,
    'OCPF_POST_USER_ID',
    'ONECLICKPOSTFACTORY_USER_ID',
    'OCPF_USER_ID'
  );
  if (configured) return { userId: configured, source: 'explicit_user_id' };

  const internalOwner = await resolveInternalOwnerUserId(supabaseUrl, headers);
  if (internalOwner) return { userId: internalOwner, source: 'internal_access_overrides' };

  const ownerEmail = explicitOwnerEmail || firstValue(
    localEnv,
    'PERMANENT_OWNER_ACCOUNT_EMAIL',
    'OCPF_OWNER_EMAIL'
  );
  if (ownerEmail) {
    return {
      userId: await resolveUserByEmail(supabaseUrl, headers, ownerEmail),
      source: 'owner_email',
    };
  }

  throw new Error(
    'tenant_not_resolved:set_OCPF_POST_USER_ID_or_PERMANENT_OWNER_ACCOUNT_EMAIL_or_provision_one_internal_owner'
  );
}

async function fetchCredentialRow(supabaseUrl, headers, userId) {
  const url = new URL(`${supabaseUrl}/rest/v1/user_credentials`);
  url.searchParams.set('select', '*');
  url.searchParams.set('user_id', `eq.${userId}`);
  url.searchParams.set('limit', '2');
  const rows = await requestJson(url, { headers });
  if (!Array.isArray(rows) || rows.length !== 1) {
    throw new Error(`user_credentials_row_not_unique_or_missing:rows=${Array.isArray(rows) ? rows.length : 0}`);
  }
  return rows[0];
}

function normalizeStoredValue(stored) {
  if (stored === null || stored === undefined || stored === '') return null;
  const value = String(stored);
  if (value.startsWith(ENC_PREFIX)) return value;
  if (value.startsWith('\\x')) {
    const decoded = Buffer.from(value.slice(2), 'hex').toString('utf8');
    return decoded || value;
  }
  return value;
}

export function decryptCredential(stored, encryptionKey) {
  const normalized = normalizeStoredValue(stored);
  if (!normalized) return null;
  if (!normalized.startsWith(ENC_PREFIX)) return normalized;
  if (!encryptionKey) throw new Error('CREDENTIAL_ENCRYPTION_KEY_is_required_for_encrypted_rows');

  const combined = Buffer.from(normalized.slice(ENC_PREFIX.length), 'base64');
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

function epochSeconds(value) {
  if (!value) return 0;
  const ms = Date.parse(String(value));
  return Number.isFinite(ms) ? Math.floor(ms / 1000) : 0;
}

export function buildImportPlan(row, encryptionKey, localEnv = {}) {
  const threadsToken = decryptCredential(row.threads_token_enc, encryptionKey);
  const linkedinAccess = decryptCredential(row.linkedin_token_enc, encryptionKey);
  const linkedinRefresh = decryptCredential(row.linkedin_refresh_token_enc, encryptionKey);
  const linkedinClientId = decryptCredential(row.linkedin_client_id_enc, encryptionKey);
  const linkedinClientSecret = decryptCredential(row.linkedin_client_secret_enc, encryptionKey);
  const linkedinPersonUrn = decryptCredential(row.linkedin_person_urn_enc, encryptionKey);

  return {
    threads: threadsToken
      ? {
          access_token: threadsToken,
          token_type: 'bearer',
          obtained_at: Math.floor(Date.now() / 1000),
          expires_at: epochSeconds(row.threads_expires_at),
          account_id: row.threads_account_id || null,
          verification_status: row.threads_verification_status || null,
          verified_at: row.threads_verified_at || null,
          imported_from: 'ocpf_supabase',
        }
      : null,
    linkedin: linkedinAccess
      ? {
          token: {
            access_token: linkedinAccess,
            refresh_token: linkedinRefresh || null,
            token_type: 'bearer',
            obtained_at: Math.floor(Date.now() / 1000),
            expires_at: epochSeconds(row.linkedin_expires_at),
            refresh_token_expires_at: epochSeconds(row.linkedin_refresh_token_expires_at),
            imported_from: 'ocpf_supabase',
          },
          settings: {
            version: firstValue(localEnv, 'LINKEDIN_VERSION') || DEFAULT_LINKEDIN_VERSION,
            client_id: linkedinClientId || null,
            person_urn: linkedinPersonUrn || null,
            verification_status: row.linkedin_verification_status || null,
            verified_at: row.linkedin_verified_at || null,
            imported_from: 'ocpf_supabase',
          },
          client_secret: linkedinClientSecret || null,
        }
      : null,
  };
}

function ensurePrivateDir(path) {
  mkdirSync(path, { recursive: true, mode: 0o700 });
  try { chmodSync(path, 0o700); } catch {}
}

function writePrivateText(path, text) {
  ensurePrivateDir(dirname(path));
  const tmp = `${path}.tmp-${process.pid}`;
  writeFileSync(tmp, text, { encoding: 'utf8', mode: 0o600 });
  try { chmodSync(tmp, 0o600); } catch {}
  renameSync(tmp, path);
  try { chmodSync(path, 0o600); } catch {}
}

function writePrivateJson(path, value) {
  writePrivateText(path, `${JSON.stringify(value, null, 2)}\n`);
}

function configDir(localEnv) {
  const override = firstValue(localEnv, 'OCPF_POST_CONFIG_DIR');
  if (override) return resolve(override.replace(/^~(?=\/)/, homedir()));
  const xdg = firstValue(localEnv, 'XDG_CONFIG_HOME') || join(homedir(), '.config');
  return join(resolve(xdg.replace(/^~(?=\/)/, homedir())), 'oneclickpostfactory', 'post-once');
}

export function persistPlan(plan, localEnv = {}, provider = 'all') {
  const dir = configDir(localEnv);
  ensurePrivateDir(dir);
  const result = { config_dir: dir, threads: false, linkedin: false };

  if ((provider === 'all' || provider === 'threads') && plan.threads) {
    writePrivateJson(join(dir, 'threads-token.json'), plan.threads);
    result.threads = true;
  }

  if ((provider === 'all' || provider === 'linkedin') && plan.linkedin) {
    writePrivateJson(join(dir, 'linkedin-token.json'), plan.linkedin.token);
    writePrivateJson(join(dir, 'linkedin-settings.json'), plan.linkedin.settings);
    const secretPath = join(dir, 'linkedin-client-secret');
    if (plan.linkedin.client_secret) {
      writePrivateText(secretPath, plan.linkedin.client_secret);
    } else if (existsSync(secretPath)) {
      // Database is authoritative for this import. Do not retain a stale local secret.
      unlinkSync(secretPath);
    }
    result.linkedin = true;
  }
  return result;
}

function shortId(value) {
  const s = String(value || '');
  return s.length <= 12 ? s : `${s.slice(0, 6)}…${s.slice(-6)}`;
}

function verifyProvider(provider, providerConfigDir) {
  const child = spawnSync(join(REPO_ROOT, 'ocpf-post'), [provider, 'status'], {
    cwd: REPO_ROOT,
    stdio: 'inherit',
    env: { ...process.env, OCPF_POST_CONFIG_DIR: providerConfigDir },
  });
  return child.status === 0;
}

function help() {
  console.log(`Usage: ./scripts/import-ocpf-db-credentials [options]\n\n` +
    `Reads Supabase bootstrap credentials from a local .env, fetches the existing OCPF tenant provider credentials read-only, decrypts them with CREDENTIAL_ENCRYPTION_KEY, and writes post-once local provider files.\n\n` +
    `Options:\n` +
    `  --env PATH             Use an exact .env file (default: auto-discover post-once/.env first)\n` +
    `  --user-id UUID         Select an exact OCPF tenant\n` +
    `  --owner-email EMAIL    Resolve the tenant through Supabase Auth when needed\n` +
    `  --provider NAME        all, threads, or linkedin (default: all)\n` +
    `  --no-verify            Import only; skip non-consequential provider status checks\n`);
}

export async function runImport(args, dependencies = {}) {
  const originalFetch = globalThis.fetch;
  const fetchImpl = dependencies.fetchImpl || originalFetch;
  if (fetchImpl !== originalFetch) globalThis.fetch = fetchImpl;
  try {
    const envPath = discoverEnvFile(args.envPath);
    const localEnv = parseEnvText(readFileSync(envPath, 'utf8'));
    const supabaseUrl = firstValue(localEnv, 'SUPABASE_URL').replace(/\/+$/, '');
    const serviceRole = firstValue(
      localEnv,
      'SUPABASE_SERVICE_ROLE_KEY',
      'SUPABASE_SECRET_KEY',
      'SERVICE_ROLE_KEY',
      'SUPABASE_SERVICE_ROLE',
      'SUPABASE_SERVICE_KEY'
    );
    const encryptionKey = firstValue(localEnv, 'CREDENTIAL_ENCRYPTION_KEY');
    if (!supabaseUrl || !serviceRole || !encryptionKey) {
      throw new Error(
        `missing_supabase_bootstrap:supabase_url=${Boolean(supabaseUrl)},service_role=${Boolean(serviceRole)},encryption_key=${Boolean(encryptionKey)}`
      );
    }

    const headers = serviceHeaders(serviceRole);
    const tenant = await resolveTenantUserId({
      supabaseUrl,
      headers,
      localEnv,
      explicitUserId: args.userId,
      explicitOwnerEmail: args.ownerEmail,
    });
    const row = await fetchCredentialRow(supabaseUrl, headers, tenant.userId);
    const plan = buildImportPlan(row, encryptionKey, localEnv);
    const persisted = persistPlan(plan, localEnv, args.provider);

    const summary = {
      ok: true,
      env_file: envPath,
      tenant_resolution: tenant.source,
      tenant_user_id: shortId(tenant.userId),
      config_dir: persisted.config_dir,
      imported: {
        threads: persisted.threads,
        linkedin: persisted.linkedin,
        linkedin_refresh: Boolean(plan.linkedin?.token?.refresh_token),
        linkedin_client_credentials: Boolean(
          plan.linkedin?.settings?.client_id && plan.linkedin?.client_secret
        ),
        linkedin_person_urn: Boolean(plan.linkedin?.settings?.person_urn),
      },
      database_writes: false,
    };
    console.log(JSON.stringify(summary, null, 2));

    if (args.verify) {
      let ok = true;
      if (persisted.threads) ok = verifyProvider('threads', persisted.config_dir) && ok;
      if (persisted.linkedin) ok = verifyProvider('linkedin', persisted.config_dir) && ok;
      if (!ok) throw new Error('provider_status_verification_failed_after_import');
    }
    return summary;
  } finally {
    if (globalThis.fetch !== originalFetch) globalThis.fetch = originalFetch;
  }
}

async function main() {
  try {
    const args = parseArgs(process.argv.slice(2));
    if (args.help) {
      help();
      return;
    }
    await runImport(args);
  } catch (error) {
    console.error(JSON.stringify({
      ok: false,
      error: error instanceof Error ? error.message : String(error),
    }, null, 2));
    process.exitCode = 1;
  }
}

const invokedPath = process.argv[1] ? resolve(process.argv[1]) : '';
if (invokedPath === fileURLToPath(import.meta.url)) {
  await main();
}
