import type { Account, Actor, Env, Store } from "../src/types.ts";
import type { ProviderAPI } from "../src/providers.ts";
import { Engine } from "../src/engine.ts";
export class MemoryStore implements Store {
  data = new Map<string, unknown>();
  get<T>(key: string): T | undefined {
    return structuredClone(this.data.get(key)) as T | undefined;
  }
  put(key: string, value: unknown) {
    this.data.set(key, structuredClone(value));
  }
  delete(key: string) {
    this.data.delete(key);
  }
  list<T>(prefix: string): T[] {
    return [...this.data.entries()]
      .filter(([k]) => k.startsWith(prefix))
      .map(([, v]) => structuredClone(v) as T);
  }
  tx<T>(fn: () => T): T {
    const before = structuredClone(this.data);
    try {
      return fn();
    } catch (e) {
      this.data = before;
      throw e;
    }
  }
}
export const environment = {
  ENCRYPTION_KEY: Buffer.alloc(32, 7).toString("base64"),
  PUBLIC_ORIGIN: "https://publish.example",
  ADVANCED_ENABLED: "false",
  MPP_ENABLED: "false",
  PUBLISHING_PAUSED: "false",
  DAILY_DELIVERY_LIMIT: "20",
  ACTIVE_SCHEDULE_LIMIT: "100",
  LINKEDIN_VERSION: "202608",
  RELEASE_SHA: "test",
} as Env;
export const owner: Actor = {
  workspace: "00000000-0000-4000-8000-000000000001",
  id: "owner",
  scopes: ["admin"],
};
export function harness() {
  let now = Date.UTC(2026, 8, 9, 12),
    authorized = true,
    sourceSha = "a".repeat(40);
  const store = new MemoryStore();
  store.put("workspace", owner.workspace);
  const env = { ...environment };
  const calls = { publish: 0, container: 0, identity: 0, metrics: 0 };
  const alarms: number[] = [];
  const provider: ProviderAPI = {
    identity: async () => {
      calls.identity++;
      return { id: "user-1", username: "test_user" };
    },
    createContainer: async () => {
      calls.container++;
      return "container-1";
    },
    containerStatus: async () => "FINISHED",
    publish: async () => {
      calls.publish++;
      return { id: "post-1", url: "https://x.com/i/web/status/post-1" };
    },
    verify: async () => ({ verified: true }),
    metrics: async () => {
      calls.metrics++;
      return { availability: "available", values: { likes: 3 } };
    },
  };
  const options = {
    now: () => now,
    wake: async (at: number) => {
      alarms.push(at);
    },
    authorized: async () => authorized,
    source: async () => ({ sha: sourceSha }),
    billing: {
      status: async () => ({}),
      quote: async () => ({}),
      checkout: async () => ({}),
      portal: async () => ({}),
    },
  };
  const engine = new Engine(store, env, provider, options);
  const run = (name: string, input: any = {}, actor = owner) =>
    engine.run(name, input, actor) as Promise<any>;
  async function setup(
    providerName: Account["provider"] = "x",
    alias = "account",
  ) {
    await engine.connect(owner, {
      alias,
      provider: providerName,
      accessToken: "test-token-only",
      funding: "customer_app",
    });
    await run("project_put", {
      id: "project",
      name: "Project",
      accounts: [alias],
      idempotencyKey: "project-" + alias,
    });
  }
  async function campaign(
    text = "An exact approved update.",
    aliases = ["account"],
  ) {
    return run("campaign_create", {
      project: "project",
      text: Object.fromEntries(aliases.map((a) => [a, text])),
      idempotencyKey: crypto.randomUUID(),
    });
  }
  return {
    engine,
    store,
    env,
    provider,
    calls,
    alarms,
    options,
    run,
    setup,
    campaign,
    now: () => now,
    advance: (ms: number) => {
      now += ms;
    },
    authorize: (value: boolean) => {
      authorized = value;
    },
    source: (sha: string) => {
      sourceSha = sha;
    },
  };
}
