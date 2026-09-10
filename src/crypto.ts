import { requireValue } from "./common.ts";

const bytes = (text: string) =>
  Uint8Array.from(atob(text), (character) => character.charCodeAt(0));
const base64 = (data: Uint8Array) => btoa(String.fromCharCode(...data));
const versionPattern = /^[1-9][0-9]{0,5}$/;
const encoder = new TextEncoder();

function root(encoded: string) {
  requireValue(
    encoded,
    "ENCRYPTION_UNCONFIGURED",
    "Credential storage is not configured.",
    503,
  );
  const raw = bytes(encoded);
  requireValue(
    raw.length === 32,
    "ENCRYPTION_UNCONFIGURED",
    "Encryption key must be 32 bytes.",
    503,
  );
  return raw;
}

async function key(encoded: string, version: string) {
  requireValue(
    versionPattern.test(version),
    "ENCRYPTION_VERSION_INVALID",
    "Credential envelope key version is invalid.",
    500,
  );
  const raw = root(encoded);
  // Version 1 is the deployed legacy format: preserve exact raw-key
  // compatibility so rotation support never strands existing credentials.
  if (version === "1")
    return crypto.subtle.importKey("raw", raw, "AES-GCM", false, [
      "encrypt",
      "decrypt",
    ]);

  // Later versions derive independent AES keys from the backed-up root. This
  // changes cryptographic key material without replacing the root secret in
  // the same deployment. A future root-secret rotation can be handled as a
  // separate, explicitly rehearsed keyring migration.
  const material = await crypto.subtle.importKey("raw", raw, "HKDF", false, [
    "deriveKey",
  ]);
  return crypto.subtle.deriveKey(
    {
      name: "HKDF",
      hash: "SHA-256",
      salt: encoder.encode("poststeward-credential-key-schedule"),
      info: encoder.encode(`aes-gcm:${version}`),
    },
    material,
    { name: "AES-GCM", length: 256 },
    false,
    ["encrypt", "decrypt"],
  );
}

export function envelopeVersion(value: string) {
  try {
    const parsed = JSON.parse(value);
    return typeof parsed.version === "string" ? parsed.version : "1";
  } catch {
    return "invalid";
  }
}

export async function seal(
  value: unknown,
  encoded: string,
  context: string,
  version = "1",
): Promise<string> {
  requireValue(
    versionPattern.test(version),
    "ENCRYPTION_VERSION_INVALID",
    "Credential encryption version is invalid.",
    503,
  );
  const iv = crypto.getRandomValues(new Uint8Array(12));
  const result = await crypto.subtle.encrypt(
    {
      name: "AES-GCM",
      iv,
      additionalData: encoder.encode(context),
    },
    await key(encoded, version),
    encoder.encode(JSON.stringify(value)),
  );
  return JSON.stringify({
    version,
    iv: base64(iv),
    data: base64(new Uint8Array(result)),
  });
}

export async function unseal<T>(
  value: string,
  encoded: string,
  context: string,
): Promise<T> {
  let parsed: { version?: unknown; iv?: unknown; data?: unknown };
  try {
    parsed = JSON.parse(value);
  } catch {
    throw new Error("Credential envelope is invalid.");
  }
  const version =
    typeof parsed.version === "string" ? parsed.version : "1";
  requireValue(
    versionPattern.test(version) &&
      typeof parsed.iv === "string" &&
      typeof parsed.data === "string",
    "CREDENTIAL_ENVELOPE_INVALID",
    "Credential envelope is invalid.",
    500,
  );
  const result = await crypto.subtle.decrypt(
    {
      name: "AES-GCM",
      iv: bytes(parsed.iv),
      additionalData: encoder.encode(context),
    },
    await key(encoded, version),
    bytes(parsed.data),
  );
  return JSON.parse(new TextDecoder().decode(result));
}
