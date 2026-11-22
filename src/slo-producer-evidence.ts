/**
 * Pure, fail-closed classification of a due automatic delivery. A provider
 * intent's created_at is NOT a publication receipt; only published_at set
 * after the provider accepted a post may establish scheduling latency.
 * Historic effects without that timestamp remain unverified.
 */
export function classifyAutomaticPublication(
  delivery: { automatic?: boolean; dueAt?: number },
  effect:
    | {
        status?: string;
        post_id?: string | null;
        published_at?: number | null;
        updated_at?: number;
      }
    | undefined,
  now: number,
) {
  const dueAt = Number(delivery.dueAt);
  if (
    !delivery.automatic ||
    !Number.isFinite(dueAt) ||
    dueAt < 1 ||
    dueAt > now
  )
    return {
      eligible: false,
      published: false,
      verified: false,
      dueAt: null,
      publishedAt: null,
      verifiedAt: null,
    };
  const publishedAt =
    typeof effect?.published_at === "number" &&
    Number.isFinite(effect.published_at) &&
    effect.published_at >= dueAt &&
    effect.published_at <= now &&
    !!effect.post_id
      ? effect.published_at
      : null;
  const verifiedAt =
    publishedAt !== null &&
    effect?.status === "verified" &&
    typeof effect.updated_at === "number" &&
    Number.isFinite(effect.updated_at) &&
    effect.updated_at >= publishedAt &&
    effect.updated_at <= now
      ? effect.updated_at
      : null;
  return {
    eligible: true,
    published: publishedAt !== null,
    verified: verifiedAt !== null,
    dueAt,
    publishedAt,
    verifiedAt,
  };
}
