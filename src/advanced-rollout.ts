import type { Env } from "./types.ts";

export type AdvancedRolloutMode = "disabled" | "canary" | "global";

export interface AdvancedRolloutDecision {
  masterEnabled: boolean;
  mode: AdvancedRolloutMode;
  canaryBps: number;
  eligible: boolean;
}

function fnv1a32(value: string) {
  let hash = 0x811c9dc5;
  for (let i = 0; i < value.length; i++) {
    hash ^= value.charCodeAt(i);
    hash = Math.imul(hash, 0x01000193) >>> 0;
  }
  return hash >>> 0;
}

export function advancedCanaryBucket(workspace: string, seed: string) {
  return fnv1a32(`${seed}\0${workspace}`) % 10000;
}

function mode(env: Env): AdvancedRolloutMode {
  return ["disabled", "canary", "global"].includes(
    env.ADVANCED_ROLLOUT_MODE || "disabled",
  )
    ? (env.ADVANCED_ROLLOUT_MODE as AdvancedRolloutMode)
    : "disabled";
}

function canaryBps(env: Env) {
  const value = Number(env.ADVANCED_CANARY_BPS || "0");
  return Number.isInteger(value) && value >= 0 && value <= 10000 ? value : 0;
}

export function advancedRolloutDecision(
  env: Env,
  workspace: string,
): AdvancedRolloutDecision {
  const masterEnabled = env.ADVANCED_ENABLED === "true";
  const rolloutMode = mode(env);
  const bps = canaryBps(env);

  if (!masterEnabled || rolloutMode === "disabled")
    return {
      masterEnabled,
      mode: rolloutMode,
      canaryBps: bps,
      eligible: false,
    };

  if (rolloutMode === "global")
    return {
      masterEnabled,
      mode: rolloutMode,
      canaryBps: bps,
      eligible: true,
    };

  const seed = env.ADVANCED_CANARY_SEED || "";
  const eligible =
    bps > 0 && seed.length >= 8 && advancedCanaryBucket(workspace, seed) < bps;
  return {
    masterEnabled,
    mode: rolloutMode,
    canaryBps: bps,
    eligible,
  };
}
