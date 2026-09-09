import { requireValue } from "./common.ts";
const bytes = (text: string) =>
  Uint8Array.from(atob(text), (c) => c.charCodeAt(0));
const base64 = (data: Uint8Array) => btoa(String.fromCharCode(...data));
async function key(encoded: string) {
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
  return crypto.subtle.importKey("raw", raw, "AES-GCM", false, [
    "encrypt",
    "decrypt",
  ]);
}
export async function seal(
  value: unknown,
  encoded: string,
  context: string,
  version = "1",
): Promise<string> {
  const iv = crypto.getRandomValues(new Uint8Array(12));
  const result = await crypto.subtle.encrypt(
    { name: "AES-GCM", iv, additionalData: new TextEncoder().encode(context) },
    await key(encoded),
    new TextEncoder().encode(JSON.stringify(value)),
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
  const { iv, data } = JSON.parse(value);
  const result = await crypto.subtle.decrypt(
    {
      name: "AES-GCM",
      iv: bytes(iv),
      additionalData: new TextEncoder().encode(context),
    },
    await key(encoded),
    bytes(data),
  );
  return JSON.parse(new TextDecoder().decode(result));
}
