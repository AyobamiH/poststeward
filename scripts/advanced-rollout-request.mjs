import { appendFileSync } from "node:fs";
import { pathToFileURL } from "node:url";

const requests = Object.freeze({
  environment: null,
  start_100bps: 100,
  start_500bps: 500,
  start_1000bps: 1000,
  stop: 0,
});

function demand(condition, message) {
  if (!condition) throw new Error(message);
}

export function resolveAdvancedRolloutRequest(request, env = process.env) {
  const selected = request || "environment";
  demand(
    Object.prototype.hasOwnProperty.call(requests, selected),
    "Advanced rollout request is not one of the reviewed bounded actions.",
  );

  const current = {
    ADVANCED_ENABLED: env.ADVANCED_ENABLED === "true" ? "true" : "false",
    ADVANCED_ROLLOUT_MODE: env.ADVANCED_ROLLOUT_MODE || "disabled",
    ADVANCED_CANARY_BPS: String(env.ADVANCED_CANARY_BPS || "0"),
    ADVANCED_CANARY_SEED: env.ADVANCED_CANARY_SEED || "",
  };
  if (selected === "environment") return { request: selected, ...current };

  demand(env.DEPLOY_ENV === "staging", "Advanced rollout changes are staging-only.");
  demand(
    env.GITHUB_REF === "refs/heads/main" &&
      env.GITHUB_REPOSITORY === "AyobamiH/poststeward" &&
      env.GITHUB_ACTOR === "AyobamiH",
    "Advanced rollout changes require AyobamiH on reviewed main.",
  );

  if (selected === "stop")
    return {
      request: selected,
      ADVANCED_ENABLED: "false",
      ADVANCED_ROLLOUT_MODE: "disabled",
      ADVANCED_CANARY_BPS: "0",
      // Preserve the stable seed so a later reviewed restart selects the same cohort.
      ADVANCED_CANARY_SEED: current.ADVANCED_CANARY_SEED,
    };

  demand(
    /^[A-Za-z0-9._:-]{8,128}$/.test(current.ADVANCED_CANARY_SEED),
    "Starting Advanced requires a pre-reviewed stable ADVANCED_CANARY_SEED in the staging environment.",
  );
  const bps = requests[selected];
  demand(
    Number.isInteger(bps) && bps >= 1 && bps <= 1000,
    "Advanced canary may cover at most ten percent.",
  );
  return {
    request: selected,
    ADVANCED_ENABLED: "true",
    ADVANCED_ROLLOUT_MODE: "canary",
    ADVANCED_CANARY_BPS: String(bps),
    ADVANCED_CANARY_SEED: current.ADVANCED_CANARY_SEED,
  };
}

export function writeGithubEnvironment(resolved, githubEnv) {
  demand(githubEnv, "GITHUB_ENV is required when applying a rollout request in Actions.");
  for (const name of [
    "ADVANCED_ENABLED",
    "ADVANCED_ROLLOUT_MODE",
    "ADVANCED_CANARY_BPS",
    "ADVANCED_CANARY_SEED",
  ]) {
    const value = String(resolved[name] ?? "");
    demand(!/[\r\n]/.test(value), `Unsafe newline in ${name}.`);
    appendFileSync(githubEnv, `${name}=${value}\n`, "utf8");
  }
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  try {
    const resolved = resolveAdvancedRolloutRequest(
      process.env.POSTSTEWARD_ADVANCED_ROLLOUT_REQUEST || "environment",
      process.env,
    );
    writeGithubEnvironment(resolved, process.env.GITHUB_ENV);
    console.log(
      "POSTSTEWARD_ADVANCED_ROLLOUT_REQUEST " +
        JSON.stringify({
          request: resolved.request,
          enabled: resolved.ADVANCED_ENABLED === "true",
          mode: resolved.ADVANCED_ROLLOUT_MODE,
          bps: Number(resolved.ADVANCED_CANARY_BPS),
          seedConfigured: resolved.ADVANCED_CANARY_SEED.length >= 8,
        }),
    );
  } catch (error) {
    console.error(
      "POSTSTEWARD_ADVANCED_ROLLOUT_REJECTED " +
        JSON.stringify({ message: error instanceof Error ? error.message : "Invalid request." }),
    );
    process.exitCode = 1;
  }
}
