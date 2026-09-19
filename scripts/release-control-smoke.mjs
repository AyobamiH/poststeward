export async function verifyReleaseControl(origin, release, send = fetch) {
  const checks = [];
  const secure = (response) =>
    response.headers.get("strict-transport-security")?.includes("max-age=") &&
    response.headers.get("x-content-type-options") === "nosniff" &&
    response.headers.get("x-frame-options") === "DENY";

  async function check(name, path, predicate) {
    let status = 0;
    let passed = false;
    try {
      const response = await send(new URL(path, origin), {
        redirect: "manual",
        cache: "no-store",
        headers: { "Cache-Control": "no-cache" },
        signal: AbortSignal.timeout(15_000),
      });
      status = response.status;
      if (status === 200 && secure(response)) {
        const value = await response.json();
        passed = Boolean(await predicate(value, response));
      }
      if (response.body && !response.bodyUsed)
        await response.body.cancel().catch(() => {});
    } catch {
      // Evidence contains fixed names/status only; never response bodies or secrets.
    }
    checks.push({ name, status, expected: 200, passed });
  }

  await check("runtime readiness exposes healthy typed release gates", "/readiness.json", (value, response) =>
    response.headers.get("cache-control")?.includes("no-store") &&
    value.schemaVersion === 2 &&
    value.release === release &&
    value.policy?.healthy === true &&
    Array.isArray(value.policy?.violations) &&
    value.policy.violations.length === 0 &&
    value.gates?.owner_google_signin?.state === "live_verified" &&
    value.gates?.private_github_authority?.state === "live_verified" &&
    value.gates?.stripe_sandbox_lifecycle?.state === "live_verified" &&
    value.gates?.protected_root_cutover?.state === "live_verified" &&
    value.gates?.exact_recovery_checkpoints?.state === "live_verified" &&
    value.gates?.github_main_ruleset?.state === "live_verified" &&
    value.gates?.github_main_ruleset?.blocking === false &&
    value.gates?.public_signup?.scope === "public_launch" &&
    value.gates?.public_signup?.state === "disabled_policy" &&
    value.gates?.threads_oauth_callback?.state === "external_setup_required" &&
    value.gates?.advanced_rollout?.state === "disabled_policy" &&
    value.runtimeCapabilities?.policies?.advancedEnabled === false &&
    value.runtimeCapabilities?.policies?.advancedRolloutMode === "disabled" &&
    value.runtimeCapabilities?.policies?.advancedCanaryBps === 0 &&
    value.runtimeCapabilities?.policies?.signupMode === "restricted" &&
    Array.isArray(value.evidenceStillExternal) &&
    !value.evidenceStillExternal.includes("private_github_authority") &&
    !value.evidenceStillExternal.includes("exact_recovery_checkpoints") &&
    !value.evidenceStillExternal.includes("github_main_ruleset")
  );

  await check("generated public release gate ledger matches reviewed evidence", "/release-gates.json", (value) => {
    const gates = Object.fromEntries((value.gates || []).map((gate) => [gate.id, gate]));
    return value.schemaVersion === 1 &&
      gates.owner_google_signin?.state === "live_verified" &&
      gates.exact_recovery_checkpoints?.state === "live_verified" &&
      gates.approximate_timestamp_pitr?.state === "blocked_external" &&
      gates.native_webmcp?.state === "unavailable_capability" &&
      gates.github_main_ruleset?.state === "live_verified" &&
      gates.github_main_ruleset?.blocking === false &&
      gates.public_signup?.scope === "public_launch" &&
      gates.public_signup?.state === "disabled_policy" &&
      gates.public_signup?.blocking === true;
  });

  return {
    release,
    observedAt: new Date().toISOString(),
    checks,
    passed: checks.every((item) => item.passed),
    boundary: "Read-only release/capability reconciliation. Configuration never upgrades a reviewed evidence state.",
  };
}
