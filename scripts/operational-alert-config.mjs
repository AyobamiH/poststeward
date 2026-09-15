export function operationalAlertSecrets(env) {
  const url = env.OPERATIONAL_ALERT_WEBHOOK_URL || "";
  const token = env.OPERATIONAL_ALERT_WEBHOOK_TOKEN || "";
  if (!url && !token) return {};
  if (!url)
    throw new Error(
      "OPERATIONAL_ALERT_WEBHOOK_TOKEN cannot be configured without OPERATIONAL_ALERT_WEBHOOK_URL.",
    );
  let parsed;
  try {
    parsed = new URL(url);
  } catch {
    throw new Error("OPERATIONAL_ALERT_WEBHOOK_URL must be a valid HTTPS URL.");
  }
  if (
    parsed.protocol !== "https:" ||
    parsed.username ||
    parsed.password ||
    url.length > 2048
  )
    throw new Error(
      "OPERATIONAL_ALERT_WEBHOOK_URL must be a bounded HTTPS URL without embedded credentials.",
    );
  if (token && (token.trim().length < 8 || token.length > 4096 || /[\r\n]/.test(token)))
    throw new Error("OPERATIONAL_ALERT_WEBHOOK_TOKEN is not a usable bounded secret.");
  return {
    OPERATIONAL_ALERT_WEBHOOK_URL: url,
    ...(token ? { OPERATIONAL_ALERT_WEBHOOK_TOKEN: token } : {}),
  };
}
