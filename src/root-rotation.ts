import { envelopeVersion, seal, unseal } from "./crypto.ts";

/**
 * Rewrap one credential envelope from a backed-up old root to a new root.
 *
 * This primitive deliberately does not discover credentials or mutate storage.
 * Callers must enumerate an exact, reviewed credential inventory and commit each
 * replacement with the surrounding store's existing compare-and-swap rules.
 * Keeping discovery/mutation outside this module prevents a root-rotation tool
 * from silently becoming broad credential authority.
 */
export async function rewrapCredentialEnvelope<T>(
  envelope: string,
  oldRoot: string,
  newRoot: string,
  context: string,
  targetVersion = "2",
): Promise<string> {
  const plaintext = await unseal<T>(envelope, oldRoot, context);
  return seal(plaintext, newRoot, context, targetVersion);
}

/**
 * Verify a rewrapped envelope without returning the decrypted credential.
 * A rehearsal can therefore prove old -> new root continuity without writing
 * tokens, secrets or plaintext into logs/evidence.
 */
export async function verifyRewrappedEnvelope<T>(
  original: string,
  rewrapped: string,
  oldRoot: string,
  newRoot: string,
  context: string,
): Promise<{
  originalVersion: string;
  rewrappedVersion: string;
  newRootReads: true;
  oldRootRejectedForRewrapped: boolean;
}> {
  const before = await unseal<T>(original, oldRoot, context);
  const after = await unseal<T>(rewrapped, newRoot, context);
  if (JSON.stringify(before) !== JSON.stringify(after))
    throw new Error("Rewrapped credential plaintext changed.");

  let oldRootRejectedForRewrapped = false;
  try {
    await unseal<T>(rewrapped, oldRoot, context);
  } catch {
    oldRootRejectedForRewrapped = true;
  }
  if (!oldRootRejectedForRewrapped)
    throw new Error("Rewrapped credential is still readable by the old root.");

  return {
    originalVersion: envelopeVersion(original),
    rewrappedVersion: envelopeVersion(rewrapped),
    newRootReads: true,
    oldRootRejectedForRewrapped,
  };
}
