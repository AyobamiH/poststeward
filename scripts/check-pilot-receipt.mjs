import { createHash } from 'node:crypto';
import { readFile } from 'node:fs/promises';
import { pathToFileURL } from 'node:url';

// Offline consistency check, never a signature or a fresh provider observation.
export function checkPilotReceipt(receipt) {
  const fail = (condition, message) => { if (!condition) throw new Error(message); };
  const { record: r, delivery: d } = receipt || {};
  fail(r && d, 'Missing record or delivery');
  fail(receipt.completed === true && d.status === 'published_verified', 'Publication is not completed and verified');
  fail(r.deliveryId === d.id && r.id === d.campaign && r.fingerprint === d.fingerprint, 'Delivery binding mismatch');
  fail(r.release === d.reviewedRelease && r.owner?.release === r.release, 'Release mismatch');
  fail(r.account?.provider === 'threads' && d.provider === 'threads', 'Expected Threads');
  fail(r.account.alias === d.account && r.account.version === d.binding &&
    r.account.identity?.id && r.account.identity.id === d.identity?.id, 'Account identity mismatch');
  fail(typeof r.text === 'string' && r.text === d.text, 'Exact text mismatch');
  const digest = createHash('sha256').update(JSON.stringify(r.text)).digest('hex');
  fail(digest === r.textDigest && digest === d.digest, 'Text digest mismatch');
  fail(typeof d.postId === 'string' && d.postId.length > 0 && d.postId !== d.containerId, 'Missing distinct publication ID');
  fail(Number.isFinite(r.approvedAt) && Number.isFinite(r.owner.authenticatedAt) &&
    r.approvedAt >= r.owner.authenticatedAt && r.approvedAt - r.owner.authenticatedAt <= 15 * 60_000 &&
    r.approvedAt >= r.createdAt && r.approvedAt <= r.expiresAt, 'Approval timing mismatch');
  fail(Number.isFinite(d.dueAt) && d.dueAt >= r.approvedAt + 30_000, 'Cancellation window mismatch');
  const observations = r.observations || [];
  const valid = o => o?.verified === true && o.outcome === 'EXACT_PROVIDER_READBACK' &&
    o.url === d.url && Number.isFinite(o.at) && o.at >= d.dueAt;
  fail(valid(r.firstVerified) && observations.some(o => valid(o) && o.at === r.firstVerified.at), 'Missing matching independent readback');
  fail(Array.isArray(receipt.notVerified), 'Missing verification boundary');
  return {
    provenance: 'owner-supplied receipt; offline consistency checked, not authenticated',
    release: r.release,
    publication: 'published_verified',
    independentReadback: 'recorded',
    repeatPublication: false,
    // Do not echo arbitrary receipt fields or owner/session identifiers.
    liveProviderOAuth: 'not established by this check',
    otherLiveAcceptance: 'not established by this check',
  };
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  try {
    if (!process.argv[2]) throw new Error('Usage: node scripts/check-pilot-receipt.mjs /private/receipt.json');
    console.log(JSON.stringify(checkPilotReceipt(JSON.parse(await readFile(process.argv[2], 'utf8'))), null, 2));
  } catch (error) {
    console.error(error instanceof SyntaxError ? 'Invalid receipt JSON' : error.code ? 'Could not read receipt file' : error.message);
    process.exitCode = 1;
  }
}
