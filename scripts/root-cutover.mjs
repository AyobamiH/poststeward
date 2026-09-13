/** Called only by the protected deployment process. Never logs a bearer or workspace identifier. */
export function assertRetainedRoots(current, desired) {
  for (const name of ["ENCRYPTION_LEGACY_ROOT_ID", "ENCRYPTION_NEXT_ROOT_ID"])
    if (current[name] && current[name] !== desired[name])
      throw new Error("A retained root cannot be replaced or removed by ordinary deployment.");
  if (current.ENCRYPTION_ROOT_WRITE === "next" && desired.ENCRYPTION_ROOT_WRITE !== "next")
    throw new Error("Do not roll credential writes back to the legacy root after cutover.");
}

export async function runRootCutover({ origin, release, token, send = fetch }) {
  if (!/^[a-f0-9]{40}$/.test(release) || !/^[a-f0-9]{64}$/.test(token)) throw new Error("Invalid protected cutover context.");
  async function call(input) {
    const response = await send(`${origin}/internal/root-cutover`, {
      method: "POST", redirect: "error", signal: AbortSignal.timeout(45000),
      headers: { Authorization: `Bearer ${token}`, "Content-Type": "application/json" },
      body: JSON.stringify({ ...input, release }),
    });
    if (!response.ok) throw new Error(`Root cutover stopped (HTTP ${response.status}). Committed batches stay readable; resume through a reviewed deployment.`);
    const value = await response.json();
    if (value.release !== release) throw new Error("Root cutover release changed.");
    return value;
  }
  let cursor = "", pages = 0, changed = 0, credentials = 0;
  const receipts = [], seen = new Set();
  let targetRoot;
  do {
    if (++pages > 100) throw new Error("Root workspace inventory exceeds the bounded run.");
    const page = await call({ action: "list", cursor });
    if (!Array.isArray(page.workspaces) || page.workspaces.length > 100 ||
      !/^[a-f0-9]{64}$/.test(page.targetRoot)) throw new Error("Invalid root inventory response.");
    targetRoot ||= page.targetRoot;
    if (targetRoot !== page.targetRoot) throw new Error("Target root changed during inventory.");
    for (const workspace of page.workspaces) {
      if (typeof workspace !== "string" || seen.has(workspace)) throw new Error("Duplicate or invalid workspace in root inventory.");
      seen.add(workspace);
      let verified = false;
      for (let attempt = 0; attempt < 100; attempt++) {
        const observed = await call({ action: "inspect", workspace });
        if (observed.targetRoot !== targetRoot) throw new Error("Workspace root differs from the deployment target.");
        if (observed.verifiedComplete === true && observed.pending === 0) {
          credentials += observed.credentialCount;
          receipts.push({ inventoryDigest: observed.inventoryDigest, credentialCount: observed.credentialCount, observedAt: observed.observedAt });
          verified = true; break;
        }
        if (!(observed.pending > 0) || !/^[a-f0-9]{64}$/.test(observed.inventoryDigest)) throw new Error("Invalid root batch observation.");
        const result = await call({ action: "migrate", workspace, expectedDigest: observed.inventoryDigest });
        if (!(result.changed > 0 && result.changed <= 25) || result.targetRoot !== targetRoot) throw new Error("Root batch made no verifiable progress.");
        changed += result.changed;
      }
      if (!verified) throw new Error("Root workspace did not converge within the bounded run.");
    }
    if (page.next !== null && (typeof page.next !== "string" || page.next <= cursor)) throw new Error("Root inventory cursor did not advance.");
    cursor = page.next;
  } while (cursor !== null);
  return { release, targetRoot, workspaceCount: seen.size, credentialCount: credentials, changed,
    currentInventoryVerified: true, oldRootRetained: true, receipts,
    boundary: "Complete registry traversal and per-workspace readback during this run; not an atomic global snapshot or permission to retire the old root. No provider action repeated." };
}
