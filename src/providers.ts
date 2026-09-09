import twitterText from "twitter-text";
import { Fault, requireValue } from "./common.ts";
import type { Delivery, Identity, Provider } from "./types.ts";
export interface Credential {
  accessToken: string;
  expiresAt?: number;
  funding?: "customer_app";
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
  verify(
    delivery: Delivery,
    credential: Credential,
  ): Promise<{ verified: boolean; url?: string }>;
  metrics(delivery: Delivery, credential: Credential): Promise<unknown>;
}
export function validateText(provider: Provider, text: string) {
  requireValue(
    text.trim().length > 0,
    "EMPTY_CONTENT",
    "Content cannot be blank.",
  );
  const count =
    provider === "x"
      ? twitterText.parseTweet(text).weightedLength
      : [...text].length;
  const limit = { x: 280, threads: 500, linkedin: 3000 }[provider];
  requireValue(
    count <= limit,
    "CONTENT_TOO_LONG",
    `${provider} content has ${count} characters; maximum ${limit}. Exact content was not changed.`,
  );
  if (provider === "x")
    requireValue(
      twitterText.parseTweet(text).valid,
      "INVALID_X_CONTENT",
      "X rejected text validation.",
    );
  return { provider, count, limit };
}
export class SocialProviders implements ProviderAPI {
  constructor(
    private http: typeof fetch = fetch,
    private linkedinVersion = "202608",
  ) {}
  private async request(
    url: string,
    token: string,
    init: RequestInit = {},
    write = false,
  ): Promise<{ data: any; response: Response }> {
    let response: Response;
    try {
      const send = this.http;
      response = await send(url, {
        ...init,
        redirect: "manual",
        signal: AbortSignal.timeout(15000),
        headers: { Authorization: `Bearer ${token}`, ...init.headers },
      });
    } catch {
      throw new Fault(
        write ? "AMBIGUOUS_PROVIDER_WRITE" : "PROVIDER_UNAVAILABLE",
        write
          ? "Provider write outcome is unknown. Do not resubmit."
          : "Provider could not be reached.",
        502,
      );
    }
    if (!response.ok) {
      const uncertain =
        write &&
        ((response.status >= 300 && response.status < 400) ||
          response.status >= 500 ||
          [408, 409, 425].includes(response.status));
      throw new Fault(
        uncertain
          ? "AMBIGUOUS_PROVIDER_WRITE"
          : `PROVIDER_HTTP_${response.status}`,
        uncertain
          ? "Provider may have accepted this write. Inspect evidence."
          : `Provider returned HTTP ${response.status}.`,
        502,
      );
    }
    const raw = await response.text();
    let data: any = {};
    try {
      data = raw ? JSON.parse(raw) : {};
    } catch {
      if (write)
        throw new Fault(
          "AMBIGUOUS_PROVIDER_WRITE",
          "Provider accepted the request but returned unreadable evidence.",
          502,
        );
      throw new Fault(
        "PROVIDER_INVALID_RESPONSE",
        "Provider returned unreadable data.",
        502,
      );
    }
    return { data, response };
  }
  async identity(provider: Provider, c: Credential): Promise<Identity> {
    requireValue(
      !c.expiresAt || c.expiresAt > Date.now() + 30000,
      "CONNECTION_EXPIRED",
      "Reconnect this account before publishing.",
      409,
    );
    if (provider === "x") {
      const { data } = await this.request(
        "https://api.x.com/2/users/me",
        c.accessToken,
      );
      requireValue(
        data.data?.id,
        "IDENTITY_UNVERIFIED",
        "X identity unavailable.",
        502,
      );
      return { id: String(data.data.id), username: String(data.data.username) };
    }
    if (provider === "threads") {
      const { data } = await this.request(
        "https://graph.threads.net/v1.0/me?fields=id,username",
        c.accessToken,
      );
      requireValue(
        data.id && data.username,
        "IDENTITY_UNVERIFIED",
        "Threads identity unavailable.",
        502,
      );
      return { id: String(data.id), username: String(data.username) };
    }
    const { data } = await this.request(
      "https://api.linkedin.com/v2/userinfo",
      c.accessToken,
    );
    requireValue(
      data.sub,
      "IDENTITY_UNVERIFIED",
      "LinkedIn member identity unavailable. openid/profile permissions are required.",
      502,
    );
    return {
      id: `urn:li:person:${data.sub}`,
      username: String(data.name || data.sub),
    };
  }
  async createContainer(d: Delivery, c: Credential): Promise<string> {
    const { data } = await this.request(
      `https://graph.threads.net/v1.0/${encodeURIComponent(d.identity.id)}/threads`,
      c.accessToken,
      {
        method: "POST",
        body: new URLSearchParams({ media_type: "TEXT", text: d.text }),
      },
    );
    requireValue(
      data.id,
      "CONTAINER_ID_MISSING",
      "Container creation returned no ID; no publish was attempted.",
      502,
    );
    return String(data.id);
  }
  async containerStatus(id: string, c: Credential): Promise<string> {
    try {
      const { data } = await this.request(
        `https://graph.threads.net/v1.0/${encodeURIComponent(id)}?fields=id,status,error_message`,
        c.accessToken,
      );
      return String(data.status || "UNKNOWN");
    } catch (e) {
      if (e instanceof Fault && e.code === "PROVIDER_HTTP_404")
        return "NOT_VISIBLE";
      throw e;
    }
  }
  async publish(d: Delivery, c: Credential): Promise<Published> {
    if (d.provider === "x") {
      const { data } = await this.request(
        "https://api.x.com/2/tweets",
        c.accessToken,
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ text: d.text }),
        },
        true,
      );
      requireValue(
        data.data?.id,
        "AMBIGUOUS_PROVIDER_WRITE",
        "X returned no post ID after submission.",
        502,
      );
      return {
        id: String(data.data.id),
        url: `https://x.com/i/web/status/${data.data.id}`,
      };
    }
    if (d.provider === "threads") {
      requireValue(
        d.containerId,
        "CONTAINER_REQUIRED",
        "A finished container is required.",
      );
      const { data } = await this.request(
        `https://graph.threads.net/v1.0/${encodeURIComponent(d.identity.id)}/threads_publish`,
        c.accessToken,
        {
          method: "POST",
          body: new URLSearchParams({ creation_id: d.containerId }),
        },
        true,
      );
      requireValue(
        data.id,
        "AMBIGUOUS_PROVIDER_WRITE",
        "Threads returned no publication ID.",
        502,
      );
      return { id: String(data.id) };
    }
    const { response } = await this.request(
      "https://api.linkedin.com/rest/posts",
      c.accessToken,
      {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "LinkedIn-Version": this.linkedinVersion,
          "X-Restli-Protocol-Version": "2.0.0",
        },
        body: JSON.stringify({
          author: d.identity.id,
          commentary: d.text,
          visibility: "PUBLIC",
          distribution: {
            feedDistribution: "MAIN_FEED",
            targetEntities: [],
            thirdPartyDistributionChannels: [],
          },
          lifecycleState: "PUBLISHED",
          isReshareDisabledByAuthor: false,
        }),
      },
      true,
    );
    const id = response.headers.get("x-restli-id");
    requireValue(
      id,
      "AMBIGUOUS_PROVIDER_WRITE",
      "LinkedIn returned no durable creation ID.",
      502,
    );
    return {
      id,
      url: `https://www.linkedin.com/feed/update/${encodeURIComponent(id)}/`,
    };
  }
  async verify(d: Delivery, c: Credential) {
    if (d.provider === "linkedin") return { verified: false, url: d.url };
    if (d.provider === "x") {
      const { data } = await this.request(
        `https://api.x.com/2/tweets/${encodeURIComponent(d.postId!)}?tweet.fields=author_id`,
        c.accessToken,
      );
      return {
        verified:
          String(data.data?.id) === d.postId &&
          String(data.data?.author_id) === d.identity.id &&
          data.data?.text === d.text,
        url: d.url,
      };
    }
    const { data } = await this.request(
      `https://graph.threads.net/v1.0/${encodeURIComponent(d.postId!)}?fields=id,text,username,permalink`,
      c.accessToken,
    );
    return {
      verified:
        String(data.id) === d.postId &&
        data.username === d.identity.username &&
        data.text === d.text,
      url:
        typeof data.permalink === "string" &&
        data.permalink.startsWith("https://www.threads.")
          ? data.permalink
          : undefined,
    };
  }
  async metrics(d: Delivery, c: Credential): Promise<unknown> {
    if (!d.postId)
      return {
        availability: "unavailable",
        reason: "No durable provider post ID.",
      };
    if (d.provider === "linkedin")
      return {
        availability: "unavailable",
        reason: "Member analytics permissions have not been enabled.",
      };
    try {
      if (d.provider === "x") {
        const { data } = await this.request(
          `https://api.x.com/2/tweets/${encodeURIComponent(d.postId)}?tweet.fields=public_metrics`,
          c.accessToken,
        );
        return data.data?.public_metrics
          ? {
              availability: "available",
              values: data.data.public_metrics,
              capturedAt: Date.now(),
            }
          : {
              availability: "unavailable",
              reason: "Provider omitted metrics.",
            };
      }
      const { data } = await this.request(
        `https://graph.threads.net/v1.0/${encodeURIComponent(d.postId)}/insights?metric=views,likes,replies,reposts,quotes`,
        c.accessToken,
      );
      return Array.isArray(data.data)
        ? {
            availability: "available",
            values: data.data,
            capturedAt: Date.now(),
          }
        : { availability: "unavailable", reason: "Provider omitted metrics." };
    } catch (e) {
      return {
        availability: "unavailable",
        reason: e instanceof Fault ? e.code : "PROVIDER_UNAVAILABLE",
      };
    }
  }
}
