export function trustedExternal(value, hosts) {
  try {
    const url = new URL(value);
    if (
      url.protocol === "https:" &&
      hosts.includes(url.hostname) &&
      !url.username &&
      !url.password &&
      !url.port
    ) {
      return url.href;
    }
  } catch {
    /* Untrusted response data is not navigation authority. */
  }
}

export function oauthHosts(provider) {
  return {
    x: ["x.com"],
    threads: ["threads.net", "www.threads.net"],
    linkedin: ["www.linkedin.com"],
  }[provider] || [];
}

export function recoveryControlState(recovery, now = Date.now()) {
  const plan = recovery?.plan;
  const quarantined = recovery?.control?.quarantined === true;
  return {
    cancel: plan?.state === "prepared",
    execute: plan?.state === "prepared" && Number(plan.expiresAt) > now,
    reconcile: plan?.state === "armed",
    resume: plan?.state === "reconciled" && quarantined,
    undo: plan?.state === "reconciled" && plan?.undoAvailable === true,
  };
}

export function recoveryActionPayload(plan, flag) {
  if (!plan?.id || !/^[a-f0-9]{64}$/.test(plan.digest || ""))
    throw new Error("The recovery plan is missing its immutable action binding.");
  return { id: plan.id, digest: plan.digest, [flag]: true };
}
