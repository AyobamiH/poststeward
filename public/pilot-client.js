export function confirmation(record) {
  if (!record || record.state !== "prepared" || record.deliveryId) throw new Error("No unreserved review is available.");
  return { reviewId: record.id, reviewDigest: record.reviewDigest, approve: true };
}
export function mayApprove(snapshot, checked, now = Date.now()) {
  const r = snapshot?.record;
  return Boolean(checked && r?.state === "prepared" && !r.deliveryId && r.expiresAt > now &&
    r.owner.id === snapshot.owner?.id && snapshot.owner.expiresAt > now &&
    now >= snapshot.owner.authenticatedAt && now - snapshot.owner.authenticatedAt <= 15 * 60000);
}
export function mayReadback(snapshot, now = Date.now()) {
  return Boolean(snapshot?.delivery?.postId && ["published_verified", "published_unverified"].includes(snapshot.delivery.status) &&
    snapshot.record.readbackAttempts < 8 && (snapshot.record.nextReadbackAt || 0) <= now);
}
export function receiptLabel(snapshot) {
  if (snapshot?.completed && snapshot.record?.firstVerified) return "Verified: a separate provider read matched the publication ID, stable author ID and exact text.";
  const d = snapshot?.delivery;
  if (!d) return snapshot?.record ? "Prepared only. No delivery or provider write has been created." : "No publication has been reserved.";
  const labels = {
    scheduled: "Reserved once. Waiting for its scheduled dispatch; cancellation may still win.",
    executing: "Executing. Do not resubmit; cancellation is not guaranteed after a provider write starts.",
    waiting_container: "Threads container recorded. Waiting for readiness; no second publication will be created.",
    published_verified: "Provider creation and initial readback recorded. Separate acceptance readback is still required.",
    published_unverified: "Provider creation ID recorded. Exact readback has not yet passed; use readback, not publish again.",
    ambiguous_effect: "Unknown provider write outcome. Do not republish. Preserve this receipt for reconciliation.",
    failed: "Execution stopped. Inspect the reason; this pilot will not create a replacement publication.",
    drift_blocked: "Stopped because the captured account, content or authority changed.",
    cancelled: "Cancelled before dispatch. This pilot slot remains consumed; no automatic replacement is created.",
  };
  return labels[d.status] || "Inspect the server receipt. An unfamiliar state is not proof of success.";
}
export function safePostLink(value) {
  try {
    const u = new URL(value);
    if (u.protocol !== "https:" || u.username || u.password || u.port) return;
    const threads = ["www.threads.com", "www.threads.net", "threads.com", "threads.net"].includes(u.hostname) && /^\/@[A-Za-z0-9._-]+\/post\/[A-Za-z0-9_-]+\/?$/.test(u.pathname);
    const x = u.hostname === "x.com" && /^\/i\/web\/status\/[A-Za-z0-9_-]+$/.test(u.pathname);
    const linkedin = u.hostname === "www.linkedin.com" && /^\/feed\/update\/urn%3Ali%3A(?:share|ugcPost)%3A[A-Za-z0-9_-]+\/$/i.test(u.pathname);
    if (threads || x || linkedin) { u.search = ""; u.hash = ""; return u.href; }
  } catch { /* Never turn untrusted receipt text into navigation authority. */ }
}
export function createClient(send, csrf) {
  return async function request(path, input) {
    if (!path.startsWith("/api/") && path !== "/auth/logout") throw new Error("Unsupported application path.");
    const response = await send(path, {
      method: input === undefined ? "GET" : "POST", credentials: "same-origin", mode: "same-origin",
      cache: "no-store", redirect: "error", signal: AbortSignal.timeout(20000),
      headers: { "Content-Type": "application/json", ...(csrf() ? { "X-CSRF-Token": csrf() } : {}) },
      ...(input === undefined ? {} : { body: JSON.stringify(input) }),
    });
    let data;
    try { data = await response.json(); } catch { throw new Error("The response was incomplete. Refresh the existing receipt before taking another action."); }
    if (!response.ok) {
      const error = new Error(data.error?.message || "The request did not complete. Inspect the existing receipt.");
      error.status = response.status; error.code = data.error?.code; throw error;
    }
    return data;
  };
}
