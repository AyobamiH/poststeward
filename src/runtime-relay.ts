import { requireValue } from "./common.ts";
import { credentialRoots, unseal } from "./crypto.ts";
import { EffectLedgerProviders } from "./effects.ts";
import { providerActorForIdentity, SocialProviders, type Credential } from "./providers.ts";
import { requireLocalExecutor, type RuntimeAuth } from "./runtime-coordination.ts";
import type { Account, Actor, Delivery, Env, Provider, Store } from "./types.ts";

export interface RuntimeRelayInput {
  action:
    | "account"
    | "container_create"
    | "container_status"
    | "publish"
    | "verify"
    | "metrics";
  authorityGeneration?: number;
  provider: Provider;
  accountId: string;
  effectId?: string;
  campaign?: string;
  text?: string;
  textDigest?: string;
  replyToId?: string;
  containerId?: string;
  postId?: string;
}

async function rawSha256(value: string) {
  return Array.from(
    new Uint8Array(
      await crypto.subtle.digest("SHA-256", new TextEncoder().encode(value)),
    ),
  )
    .map((byte) => byte.toString(16).padStart(2, "0"))
    .join("");
}

function activeAccount(store: Store, provider: Provider, accountId: string) {
  const matches = store
    .list<Account>("account:")
    .filter(
      (account) =>
        account.active &&
        account.provider === provider &&
        account.identity.id === accountId,
    );
  requireValue(
    matches.length === 1,
    "RUNTIME_ACCOUNT_BINDING_MISMATCH",
    matches.length
      ? "More than one active hosted alias matches this runtime identity."
      : "No active hosted provider identity matches this runtime account.",
    409,
  );
  return matches[0];
}

async function credential(store: Store, env: Env, workspace: string, account: Account) {
  return unseal<Credential>(
    account.secret,
    credentialRoots(env),
    workspace + ":" + account.alias,
  );
}

async function deliveryFrom(
  workspace: string,
  auth: RuntimeAuth,
  account: Account,
  input: RuntimeRelayInput,
): Promise<Delivery> {
  requireValue(
    typeof input.effectId === "string" &&
      /^[A-Za-z0-9:_-]{8,180}$/.test(input.effectId),
    "RUNTIME_EFFECT_ID_INVALID",
    "A stable local effect ID is required.",
  );
  requireValue(
    typeof input.campaign === "string" &&
      input.campaign.length >= 1 &&
      input.campaign.length <= 160,
    "RUNTIME_CAMPAIGN_INVALID",
    "A bounded campaign identity is required.",
  );
  requireValue(
    typeof input.text === "string" && input.text.trim().length > 0,
    "RUNTIME_TEXT_REQUIRED",
    "Exact publication text is required.",
  );
  const digest = await rawSha256(input.text);
  requireValue(
    input.textDigest === digest,
    "RUNTIME_TEXT_DIGEST_MISMATCH",
    "Runtime text digest does not match the exact supplied text.",
    409,
  );
  const now = Date.now();
  const actor: Actor = {
    workspace,
    id: "runtime:" + auth.installationId,
    scopes: ["publish"],
  };
  return {
    id: input.effectId,
    fingerprint: "runtime:" + input.effectId,
    campaign: input.campaign,
    project: "poststeward-local-runtime",
    account: account.alias,
    provider: account.provider,
    identity: account.identity,
    binding: account.version,
    text: input.text,
    digest,
    ...(input.containerId ? { containerId: input.containerId } : {}),
    ...(input.postId ? { postId: input.postId } : {}),
    dueAt: now,
    timezone: "UTC",
    status: "executing",
    createdAt: now,
    updatedAt: now,
    actor,
    automatic: true,
    claimId: input.effectId,
  };
}

export async function handleRuntimeRelay(
  store: Store,
  env: Env,
  workspace: string,
  auth: RuntimeAuth,
  input: RuntimeRelayInput,
) {
  requireValue(
    ["x", "threads", "linkedin"].includes(input.provider),
    "RUNTIME_PROVIDER_INVALID",
    "Unsupported provider.",
  );
  requireValue(
    typeof input.accountId === "string" &&
      input.accountId.length >= 1 &&
      input.accountId.length <= 256,
    "RUNTIME_ACCOUNT_ID_INVALID",
    "Exact provider account identity is required.",
  );

  const account = activeAccount(store, input.provider, input.accountId);
  const secret = await credential(store, env, workspace, account);
  const base = new SocialProviders(fetch, env.LINKEDIN_VERSION);
  const providers = new EffectLedgerProviders(base, env.IDENTITY, workspace);

  if (input.action === "account") {
    const observed = await providers.identity(
      account.provider,
      secret,
      providerActorForIdentity(account.provider, account.identity),
    );
    requireValue(
      observed.id === account.identity.id,
      "RUNTIME_PROVIDER_IDENTITY_DRIFT",
      "Provider identity no longer matches the paired runtime binding.",
      409,
    );
    return {
      schemaVersion: 1,
      provider: account.provider,
      account: { alias: account.alias, identity: observed, binding: account.version },
      capabilities: account.capabilities || null,
    };
  }

  if (["container_create", "publish"].includes(input.action)) {
    requireValue(
      Number.isInteger(input.authorityGeneration),
      "RUNTIME_GENERATION_REQUIRED",
      "Current executor generation is required for a provider write.",
    );
    await requireLocalExecutor(
      env.IDENTITY,
      auth,
      Number(input.authorityGeneration),
    );
  }

  if (input.action === "container_status") {
    requireValue(
      typeof input.containerId === "string" && input.containerId.length > 0,
      "RUNTIME_CONTAINER_ID_REQUIRED",
      "Container ID is required.",
    );
    return {
      schemaVersion: 1,
      status: await providers.containerStatus(input.containerId, secret),
    };
  }

  const delivery = await deliveryFrom(workspace, auth, account, input);

  if (input.action === "container_create") {
    return {
      schemaVersion: 1,
      id: await providers.createContainer(delivery, secret, {
        text: delivery.text,
        ...(input.replyToId ? { replyToId: input.replyToId } : {}),
      }),
    };
  }
  if (input.action === "publish") {
    const published = await providers.publish(delivery, secret, {
      text: delivery.text,
      ...(input.replyToId ? { replyToId: input.replyToId } : {}),
    });
    return { schemaVersion: 1, ...published };
  }
  if (input.action === "verify") {
    requireValue(
      typeof input.postId === "string" && input.postId.length > 0,
      "RUNTIME_POST_ID_REQUIRED",
      "Provider post ID is required for readback.",
    );
    return {
      schemaVersion: 1,
      ...(await providers.verify(delivery, secret)),
    };
  }
  if (input.action === "metrics")
    return {
      schemaVersion: 1,
      metrics: await providers.metrics(delivery, secret),
    };

  throw new Error("Unhandled runtime relay action.");
}
