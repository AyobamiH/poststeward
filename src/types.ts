export type Provider = "x" | "threads" | "linkedin";
export type Scope =
  | "read"
  | "campaign:write"
  | "publish"
  | "schedule"
  | "connections"
  | "automation"
  | "billing"
  | "admin";
export interface Actor {
  workspace: string;
  id: string;
  scopes: Scope[];
  grant?: string;
}
export interface Identity {
  id: string;
  username: string;
}
export interface Account {
  alias: string;
  provider: Provider;
  identity: Identity;
  version: number;
  secret: string;
  active: boolean;
  verifiedAt: number;
}
export interface Project {
  id: string;
  name: string;
  accounts: string[];
}
export interface Campaign {
  id: string;
  project: string;
  text: Record<string, string>;
  digest: string;
  createdAt: number;
  source?: { profile: string; sha: string; family: string };
}
export type DeliveryStatus =
  | "scheduled"
  | "executing"
  | "waiting_container"
  | "cancelled"
  | "published_verified"
  | "published_unverified"
  | "ambiguous_effect"
  | "failed"
  | "drift_blocked";
export interface Delivery {
  id: string;
  fingerprint: string;
  campaign: string;
  project: string;
  account: string;
  provider: Provider;
  identity: Identity;
  binding: number;
  text: string;
  digest: string;
  dueAt: number;
  timezone: string;
  status: DeliveryStatus;
  createdAt: number;
  updatedAt: number;
  actor: Actor;
  automatic: boolean;
  policy?: string;
  policyVersion?: number;
  phase?:
    "identity" | "container_create" | "container_wait" | "publish" | "readback";
  claimUntil?: number;
  containerId?: string;
  containerChecks?: number;
  nextCheck?: number;
  postId?: string;
  url?: string;
  reason?: string;
  metrics?: unknown;
}
export interface Entitlement {
  kind: "subscription" | "pass";
  until: number;
  reference: string;
  customer?: string;
  revoked?: boolean;
}
export interface Profile {
  id: string;
  revision: number;
  project: string;
  repository: string;
  branch: string;
  path: string;
  templates: Record<string, string>;
  family: string;
  intervalMinutes: number;
  minSpacingMinutes: number;
  enabled: boolean;
  sha?: string;
  lastCheck?: number;
  error?: string;
  nextRun: number;
  nextMetrics: number;
  authority: Actor;
}
export interface Store {
  get<T>(key: string): T | undefined;
  put(key: string, value: unknown): void;
  delete(key: string): void;
  list<T>(prefix: string): T[];
  tx<T>(fn: () => T): T;
}
export interface Env {
  WORKSPACES: DurableObjectNamespace;
  IDENTITY: D1Database;
  ASSETS: Fetcher;
  PUBLIC_ORIGIN: string;
  RELEASE_SHA: string;
  ENCRYPTION_KEY: string;
  ENCRYPTION_KEY_VERSION?: string;
  OIDC_ISSUER: string;
  OIDC_CLIENT_ID: string;
  OIDC_CLIENT_SECRET: string;
  STRIPE_SECRET_KEY?: string;
  STRIPE_WEBHOOK_SECRET?: string;
  STRIPE_PRICE_ID?: string;
  STRIPE_PROFILE_ID?: string;
  MPP_SECRET?: string;
  ADVANCED_ENABLED: string;
  MPP_ENABLED: string;
  PUBLISHING_PAUSED: string;
  DAILY_DELIVERY_LIMIT: string;
  ACTIVE_SCHEDULE_LIMIT: string;
  LINKEDIN_VERSION: string;
}
