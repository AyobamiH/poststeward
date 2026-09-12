const rotationIdPattern = /^[a-z0-9][a-z0-9._-]{7,79}$/;

function demand(condition, message) {
  if (!condition) throw new Error(message);
}

function validRoot(value) {
  if (typeof value !== "string" || !/^[A-Za-z0-9+/]{43}=$/.test(value))
    return false;
  const decoded = Buffer.from(value, "base64");
  return decoded.length === 32 && decoded.toString("base64") === value;
}

export function rotationMode(env) {
  const mode = env.ROOT_ROTATION_MODE || "off";
  demand(
    ["off", "rewrap", "verify"].includes(mode),
    "ROOT_ROTATION_MODE must be off, rewrap or verify.",
  );
  return mode;
}

export function configureRootRotation(base, env) {
  const c = structuredClone(base);
  const mode = rotationMode(env);
  const paused = env.PUBLISHING_PAUSED === "true" ? "true" : "false";
  const id = env.ROOT_ROTATION_ID || "";
  if (mode === "off") {
    demand(!id, "ROOT_ROTATION_ID must be empty while root rotation is off.");
  } else {
    demand(
      env.DEPLOY_ENV === "staging",
      "Root-secret rotation acceptance is restricted to staging.",
    );
    demand(paused === "true", "Publishing must be paused during root-secret rotation.");
    demand(rotationIdPattern.test(id), "ROOT_ROTATION_ID is invalid.");
  }
  c.vars.PUBLISHING_PAUSED = paused;
  c.vars.ROOT_ROTATION_MODE = mode;
  c.vars.ROOT_ROTATION_ID = id;
  return c;
}

export function appendRootRotationSecrets(baseSecrets, env) {
  const secrets = { ...baseSecrets };
  const mode = rotationMode(env);
  if (mode === "rewrap") {
    demand(
      env.DEPLOY_ENV === "staging" && env.PUBLISHING_PAUSED === "true",
      "Root rewrap secret may only be deployed to paused staging.",
    );
    demand(
      validRoot(env.ENCRYPTION_KEY) &&
        validRoot(env.ENCRYPTION_KEY_NEXT) &&
        env.ENCRYPTION_KEY !== env.ENCRYPTION_KEY_NEXT,
      "Rewrap requires distinct protected 32-byte current and next roots.",
    );
    secrets.ENCRYPTION_KEY_NEXT = env.ENCRYPTION_KEY_NEXT;
  } else {
    demand(
      !env.ENCRYPTION_KEY_NEXT,
      "Remove ENCRYPTION_KEY_NEXT from the protected environment before verify/off deployment.",
    );
  }
  return secrets;
}
