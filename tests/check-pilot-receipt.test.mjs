import test from 'node:test';
import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import { checkPilotReceipt } from '../scripts/check-pilot-receipt.mjs';
function fixture() {
  const text = 'A controlled fixture';
  const digest = createHash('sha256').update(JSON.stringify(text)).digest('hex');
  const observation = { verified: true, outcome: 'EXACT_PROVIDER_READBACK', url: 'https://www.threads.com/example', at: 32_000 };
  return { completed: true, notVerified: ['live provider OAuth grant'],
    record: { id: 'review', deliveryId: 'delivery', fingerprint: 'binding', release: 'release',
      state: 'reserved', owner: { release: 'release', authenticatedAt: 0, subject: 'PRIVATE' },
      account: { provider: 'threads', alias: 'test', version: 2, identity: { id: 'author' } },
      text, textDigest: digest, createdAt: 0, approvedAt: 1000, expiresAt: 600_000,
      firstVerified: observation, observations: [observation] },
    delivery: { id: 'delivery', campaign: 'review', fingerprint: 'binding', reviewedRelease: 'release',
      provider: 'threads', account: 'test', binding: 2, identity: { id: 'author' }, text, digest,
      postId: 'post', containerId: 'container', status: 'published_verified', dueAt: 31_000, url: observation.url } };
}
test('reserved slot can contain completed delivery; summary excludes private owner data', () => {
  const result = checkPilotReceipt(fixture());
  assert.equal(result.repeatPublication, false);
  assert.equal(JSON.stringify(result).includes('PRIVATE'), false);
});
test('rejects mismatched and incomplete acceptance evidence', () => {
  for (const mutate of [
    r => r.delivery.identity.id = 'other', r => r.delivery.text = 'changed',
    r => r.record.textDigest = 'wrong', r => r.delivery.postId = 'container',
    r => r.delivery.status = 'reserved', r => r.record.observations = [],
    r => r.record.approvedAt = 999_999, r => r.delivery.dueAt = 1000,
  ]) { const receipt = fixture(); mutate(receipt); assert.throws(() => checkPilotReceipt(receipt)); }
});
