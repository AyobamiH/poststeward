import twitterText from "twitter-text";
import { Fault, requireValue } from "./common.ts";
import type { Delivery, Identity, Provider } from "./types.ts";
export interface Credential {
  accessToken: string;
  expiresAt?: number;
  funding?: "customer_app" | "service_app";
}
export interface Published {
  id: string;
  url?: string;
}
export interface ProviderAPI {
  identity(provider: Provider, credential: Credential): Promise<Identity>;
  createContainer(delivery: Delivery, credential: Credential): Promise<string>;
  containerStatus(id: string, credential: Credential): Promise<string>;
  publish(delivery: Delivery, credential: Credential): Promise<Published>;
  verify(delivery: Delivery, credential: Credential): Promise<{ verified: boolean; url?: string }>;
  metrics(delivery: Delivery, credential: Credential): Promise<unknown>;
}
export function validateText(provider: Provider, text: string) {
  requireValue(text.trim().length > 0, "EMPTY_CONTENT", "Content cannot be blank.");
  const count = provider === "x" ? twitterText.parseTweet(text).weightedLength : [...text].length;
  const limit = { x: 280, threads: 500, linkedin: 3000 }[provider];
  requireValue(count <= limit, "CONTENT_TOO_LONG", `${provider} content has ${count} characters; maximum ${limit}. Exact content was not changed.`);
  if (provider === "x") requireValue(twitterText.parseTweet(text).valid, "INVALID_X_CONTENT", "X rejected text validation.");
  return { provider, count, limit };
}

/** Bound the entire response, not only the time until HTTP headers arrive. */
export async function readProviderBody(response: Response, signal: AbortSignal): Promise<string> {
  if (!response.body) return "";
  const reader = response.body.getReader();
  let abort!: () => void;
  const stopped = new Promise<never>((_, reject) => { abort = () => reject(new Error("Provider deadline")); });
  signal.addEventListener("abort", abort, { once: true });
  let size = 0, text = "", done = false;
  const decoder = new TextDecoder("utf-8", { fatal: true });
  try {
    if (signal.aborted) throw new Error("Provider deadline");
    while (true) {
      const chunk = await Promise.race([reader.read(), stopped]);
      if (chunk.done) { done = true; return text + decoder.decode(); }
      size += chunk.value.byteLength;
      if (size > 262144) throw new Error("Provider response limit");
      text += decoder.decode(chunk.value, { stream: true });
    }
  } finally {
    signal.removeEventListener("abort", abort);
    if (!done) void reader.cancel().catch(() => {});
  }
}
export function safeThreadsUrl(value: unknown): string | undefined {
  if (typeof value !== "string") return;
  try {
    const u = new URL(value);
    if (u.protocol === "https:" && ["www.threads.net", "www.threads.com", "threads.net", "threads.com"].includes(u.hostname) &&
      !u.username && !u.password && !u.port && /^\/@[A-Za-z0-9._-]+\/post\/[A-Za-z0-9_-]+\/?$/.test(u.pathname)) {
      u.search = ""; u.hash = ""; return u.href;
    }
  } catch { /* An untrusted permalink is not navigation authority. */ }
}
function safeLinkedInUrl(id: string) {
  return `https://www.linkedin.com/feed/update/${encodeURIComponent(id)}/`;
}
const validId = (value: unknown): value is string => typeof value === "string" && value.length > 0 && value.length <= 256;

export class SocialProviders implements ProviderAPI {
  constructor(private http: typeof fetch = fetch, private linkedinVersion = "202608") {}
  private async request(url: string, token: string, init: RequestInit = {}, write = false): Promise<{ data: any; response: Response }> {
    const signal = AbortSignal.timeout(15000);
    let response: Response;
    try {
      const send = this.http;
      response = await send(url, { ...init, redirect: "manual", signal,
        headers: { Authorization: `Bearer ${token}`, ...init.headers } });
    } catch {
      throw new Fault(write ? "AMBIGUOUS_PROVIDER_WRITE" : "PROVIDER_UNAVAILABLE",
        write ? "Provider write outcome is unknown. Do not resubmit." : "Provider could not be reached.", 502);
    }
    if (!response.ok) {
      void response.body?.cancel().catch(() => {});
      const uncertain = write && ((response.status >= 300 && response.status < 400) || response.status >= 500 || [408, 409, 425].includes(response.status));
      throw new Fault(uncertain ? "AMBIGUOUS_PROVIDER_WRITE" : `PROVIDER_HTTP_${response.status}`,
        uncertain ? "Provider may have accepted this write. Inspect evidence." : `Provider returned HTTP ${response.status}.`, 502);
    }
    try {
      const raw = await readProviderBody(response, signal);
      const data: unknown = raw ? JSON.parse(raw) : {};
      if (!data || typeof data !== "object" || Array.isArray(data)) throw new Error("Provider response shape");
      return { data, response };
    } catch {
      // Acceptance followed by a truncated, oversized or timed-out body is NOT
      // a known failed write. Never let a generic parse failure invite a retry.
      throw new Fault(write ? "AMBIGUOUS_PROVIDER_WRITE" : "PROVIDER_INVALID_RESPONSE",
        write ? "Provider accepted the request but durable response evidence is unavailable. Do not resubmit." : "Provider returned unreadable or incomplete data.", 502);
    }
  }
  async identity(provider: Provider, c: Credential): Promise<Identity> {
    requireValue(!c.expiresAt || c.expiresAt > Date.now() + 30000, "CONNECTION_EXPIRED", "Reconnect this account before publishing.", 409);
    if (provider === "x") {
      const { data } = await this.request("https://api.x.com/2/users/me", c.accessToken);
      requireValue(validId(data.data?.id) && validId(data.data?.username), "IDENTITY_UNVERIFIED", "X identity unavailable.", 502);
      return { id: data.data.id, username: data.data.username };
    }
    if (provider === "threads") {
      const { data } = await this.request("https://graph.threads.net/v1.0/me?fields=id,username", c.accessToken);
      requireValue(validId(data.id) && validId(data.username), "IDENTITY_UNVERIFIED", "Threads identity unavailable.", 502);
      return { id: data.id, username: data.username };
    }
    const { data } = await this.request("https://api.linkedin.com/v2/userinfo", c.accessToken);
    requireValue(validId(data.sub), "IDENTITY_UNVERIFIED", "LinkedIn member identity unavailable. openid/profile permissions are required.", 502);
    return { id: `urn:li:person:${data.sub}`, username: String(data.name || data.sub) };
  }
  async createContainer(d: Delivery, c: Credential): Promise<string> {
    const { data } = await this.request(`https://graph.threads.net/v1.0/${encodeURIComponent(d.identity.id)}/threads`, c.accessToken,
      { method: "POST", body: new URLSearchParams({ media_type: "TEXT", text: d.text, auto_publish_text: "false" }) });
    requireValue(validId(data.id), "CONTAINER_ID_MISSING", "Container creation returned no ID; no publish was attempted.", 502);
    return data.id;
  }
  async containerStatus(id: string, c: Credential): Promise<string> {
    try {
      const { data } = await this.request(`https://graph.threads.net/v1.0/${encodeURIComponent(id)}?fields=id,status,error_message`, c.accessToken);
      return ["IN_PROGRESS", "FINISHED", "ERROR", "EXPIRED", "PUBLISHED"].includes(data.status) ? data.status : "UNKNOWN";
    } catch (e) {
      if (e instanceof Fault && e.code === "PROVIDER_HTTP_404") return "NOT_VISIBLE";
      throw e;
    }
  }
  async publish(d: Delivery, c: Credential): Promise<Published> {
    if (d.provider === "x") {
      const { data } = await this.request("https://api.x.com/2/tweets", c.accessToken,
        { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ text: d.text }) }, true);
      requireValue(validId(data.data?.id), "AMBIGUOUS_PROVIDER_WRITE", "X returned no post ID after submission.", 502);
      return { id: data.data.id, url: `https://x.com/i/web/status/${encodeURIComponent(data.data.id)}` };
    }
    if (d.provider === "threads") {
      requireValue(d.containerId, "CONTAINER_REQUIRED", "A finished container is required.");
      const { data } = await this.request(`https://graph.threads.net/v1.0/${encodeURIComponent(d.identity.id)}/threads_publish`, c.accessToken,
        { method: "POST", body: new URLSearchParams({ creation_id: d.containerId }) }, true);
      requireValue(validId(data.id), "AMBIGUOUS_PROVIDER_WRITE", "Threads returned no publication ID.", 502);
      return { id: data.id };
    }
    const { response } = await this.request("https://api.linkedin.com/rest/posts", c.accessToken, {
      method: "POST", headers: { "Content-Type": "application/json", "LinkedIn-Version": this.linkedinVersion, "X-Restli-Protocol-Version": "2.0.0" },
      body: JSON.stringify({ author: d.identity.id, commentary: d.text, visibility: "PUBLIC",
        distribution: { feedDistribution: "MAIN_FEED", targetEntities: [], thirdPartyDistributionChannels: [] },
        lifecycleState: "PUBLISHED", isReshareDisabledByAuthor: false }),
    }, true);
    const id = response.headers.get("x-restli-id");
    requireValue(id && id.length <= 256, "AMBIGUOUS_PROVIDER_WRITE", "LinkedIn returned no durable creation ID.", 502);
    return { id, url: safeLinkedInUrl(id) };
  }
  async verify(d: Delivery, c: Credential) {
    requireValue(validId(d.postId), "NO_READBACK_TARGET", "A durable provider ID is required.", 409);
    if (d.provider === "x") {
      const { data } = await this.request(`https://api.x.com/2/tweets/${encodeURIComponent(d.postId)}?tweet.fields=author_id`, c.accessToken);
      return { verified: data.data?.id === d.postId && data.data?.author_id === d.identity.id && data.data?.text === d.text,
        url: `https://x.com/i/web/status/${encodeURIComponent(d.postId)}` };
    }
    if (d.provider === "linkedin") {
      const { data } = await this.request(
        `https://api.linkedin.com/rest/posts/${encodeURIComponent(d.postId)}?viewContext=AUTHOR`,
        c.accessToken,
        { headers: { "LinkedIn-Version": this.linkedinVersion, "X-Restli-Protocol-Version": "2.0.0" } },
      );
      return {
        verified:
          data.id === d.postId &&
          data.author === d.identity.id &&
          data.commentary === d.text &&
          data.lifecycleState === "PUBLISHED",
        url: safeLinkedInUrl(d.postId),
      };
    }
    const { data } = await this.request(`https://graph.threads.net/v1.0/${encodeURIComponent(d.postId)}?fields=id,text,owner,username,permalink`, c.accessToken);
    return { verified: data.id === d.postId && data.owner?.id === d.identity.id && data.text === d.text,
      url: safeThreadsUrl(data.permalink) };
  }
  async metrics(d: Delivery, c: Credential): Promise<unknown> {
    if (!d.postId) return { availability: "unavailable", reason: "No durable provider post ID." };
    if (d.provider === "linkedin") return { availability: "unavailable", reason: "Member analytics permissions have not been enabled." };
    try {
      if (d.provider === "x") {
        const { data } = await this.request(`https://api.x.com/2/tweets/${encodeURIComponent(d.postId)}?tweet.fields=public_metrics`, c.accessToken);
        return data.data?.public_metrics ? { availability: "available", values: data.data.public_metrics, capturedAt: Date.now() }
          : { availability: "unavailable", reason: "Provider omitted metrics." };
      }
      const { data } = await this.request(`https://graph.threads.net/v1.0/${encodeURIComponent(d.postId)}/insights?metric=views,likes,replies,reposts,quotes`, c.accessToken);
      return Array.isArray(data.data) ? { availability: "available", values: data.data, capturedAt: Date.now() }
        : { availability: "unavailable", reason: "Provider omitted metrics." };
    } catch (e) {
      return { availability: "unavailable", reason: e instanceof Fault ? e.code : "PROVIDER_UNAVAILABLE" };
    }
  }
}
