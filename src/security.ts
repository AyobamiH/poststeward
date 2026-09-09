import { digest, Fault, requireValue } from "./common.ts";
import type { Env, Store } from "./types.ts";

export async function boundedBody(
  request: Request,
  limit: number,
  timeout = 10000,
): Promise<Request> {
  requireValue(
    Number(request.headers.get("content-length") || 0) <= limit,
    "BODY_TOO_LARGE",
    "Request exceeds the endpoint size limit.",
    413,
  );
  if (!request.body) return request;
  const reader = request.body.getReader();
  let timer: ReturnType<typeof setTimeout>;
  const read = async () => {
    const chunks: Uint8Array[] = [];
    let size = 0;
    for (;;) {
      const { value, done } = await reader.read();
      if (done) break;
      size += value.byteLength;
      requireValue(
        size <= limit,
        "BODY_TOO_LARGE",
        "Request exceeds the endpoint size limit.",
        413,
      );
      chunks.push(value);
    }
    const body = new Uint8Array(size);
    let offset = 0;
    for (const chunk of chunks) {
      body.set(chunk, offset);
      offset += chunk.byteLength;
    }
    return new Request(request, { body });
  };
  try {
    return await Promise.race([
      read(),
      new Promise<never>((_, reject) => {
        timer = setTimeout(
          () =>
            reject(
              new Fault(
                "BODY_TIMEOUT",
                "Request body did not arrive in time.",
                408,
              ),
            ),
          timeout,
        );
      }),
    ]);
  } finally {
    clearTimeout(timer!);
    void reader.cancel().catch(() => {});
  }
}

export async function limitEdge(request: Request, env: Env) {
  const path = new URL(request.url).pathname;
  if (!(
    path.startsWith("/api/") ||
    path.startsWith("/auth/") ||
    path.startsWith("/payments/") ||
    path === "/mcp" ||
    path === "/webhooks/stripe"
  ))
    return;
  requireValue(
    env.EDGE_LIMITER && env.LOGIN_LIMITER,
    "SECURITY_UNCONFIGURED",
    "Request protection is unavailable.",
    503,
  );
  // Cloudflare sets this header at ingress. Never trust X-Forwarded-For.
  // The missing-IP bucket is shared and still limited.
  const key = await digest({
    service: "poststeward",
    origin: env.PUBLIC_ORIGIN,
    ip: request.headers.get("CF-Connecting-IP") || "missing-ip",
  });
  const limiter =
    path === "/auth/login" || path === "/auth/callback"
      ? env.LOGIN_LIMITER
      : env.EDGE_LIMITER;
  requireValue(
    (await limiter.limit({ key })).success,
    "RATE_LIMITED",
    "Request limit reached. Wait before retrying.",
    429,
  );
}

export function limitWorkspace(
  store: Store,
  maximum: string,
  now = Date.now(),
) {
  const limit = Number(maximum);
  requireValue(
    Number.isInteger(limit) && limit >= 1 && limit <= 1000,
    "SECURITY_UNCONFIGURED",
    "Workspace request protection is unavailable.",
    503,
  );
  // One atomic counter in the workspace's SQLite object covers every region,
  // token and transport. Edge rate limits alone are not a global quota.
  store.tx(() => {
    const window = Math.floor(now / 60000);
    const prior = store.get<{ window: number; count: number }>("security:rate");
    const count = prior?.window === window ? prior.count : 0;
    if (count >= limit)
      throw new Fault(
        "RATE_LIMITED",
        "Workspace request limit reached. Wait before retrying.",
        429,
      );
    store.put("security:rate", { window, count: count + 1 });
  });
}

export async function expireIdentity(env: Env, now = Date.now()) {
  // Bound each cron batch; retain grants beyond their longest scheduled-use
  // lifetime so absence continues to reject authority after revocation.
  for (const [table, column, cutoff] of [
    ["login_states", "expires_at", now],
    ["sessions", "expires_at", now],
    ["grants", "expires_at", now - 30 * 86400000],
  ] as const) {
    await env.IDENTITY.prepare(
      `DELETE FROM ${table} WHERE rowid IN (SELECT rowid FROM ${table} WHERE ${column} < ? LIMIT 1000)`,
    )
      .bind(cutoff)
      .run();
  }
}
