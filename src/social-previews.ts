/** Fixed public copy only: never derive a preview from a request, session or workspace. */
export const socialPages: Record<string, { title: string; description: string }> = {
  "/": { title: "PostSteward — Approved publishing for AI agents", description: "Give your agents approved copy, connected accounts and a schedule. PostSteward checks their authority and keeps delivery receipts." },
  "/docs/": { title: "Documentation — PostSteward", description: "Set up PostSteward, connect an agent and understand publishing permissions, schedules and delivery receipts." },
  "/docs/install": { title: "Install PostSteward", description: "Install the Linux runtime, pair your machine with PostSteward Cloud and review activation before local publishing." },
  "/docs/agent-guide": { title: "Agent setup guide — PostSteward", description: "Connect an agent through scoped HTTP, remote MCP or supported browser WebMCP. Keep owner approval and inspect each delivery result." },
  "/docs/operations": { title: "Operation reference — PostSteward", description: "Reference for PostSteward's 34 scoped operations, including approved publishing, schedules, receipts and local-runtime commands." },
  "/privacy": { title: "Privacy policy — PostSteward", description: "How PostSteward uses, protects, retains and deletes service data, and how to request access or deletion." },
  "/terms": { title: "Terms of service — PostSteward", description: "Terms for using PostSteward, including agent authority, external providers, subscriptions and service limits." },
  "/security": { title: "Security — PostSteward", description: "Understand PostSteward's account permissions, credential handling, publishing safeguards and security reporting process." },
  "/support": { title: "Support — PostSteward", description: "Get help with sign-in, connections, delivery evidence, billing and workspace recovery." },
  "/status": { title: "Service status — PostSteward", description: "Inspect current application configuration and reviewed release evidence. Provider approval and live acceptance are recorded separately." },
  "/app": { title: "Workspace — PostSteward", description: "Sign in to manage your publishing accounts, reviewed campaigns, schedules and agent permissions. Workspace details stay private." },
  "/advanced-inventory": { title: "Advanced inventory — PostSteward", description: "Sign in to inspect your reviewed sources and reserved work. This preview contains no private inventory or publishing content." },
  "/lifecycle": { title: "Workspace data controls — PostSteward", description: "Sign in to manage workspace export, retention and deletion. These actions require your authenticated workspace authority." },
  "/recovery": { title: "Workspace recovery — PostSteward", description: "Sign in to review recovery checkpoints and reconcile publishing authority. This preview contains no private recovery data." },
  "/pilot": { title: "Review a publication — PostSteward", description: "Sign in to review one approved publication and inspect its evidence. Opening this page does not publish anything." },
};
export const socialImagePath = "/social/poststeward-v2.png";
export const socialImageAlt = "PostSteward — Approved publishing. Clear results. For humans and AI agents.";
function attribute(value: string) {
  return value.replaceAll("&", "&amp;").replaceAll('"', "&quot;").replaceAll("<", "&lt;").replaceAll(">", "&gt;").replaceAll("'", "&#39;");
}
export function socialPreviewTags(path: string, origin: string) {
  const page = socialPages[path];
  if (!Object.hasOwn(socialPages, path)) return "";
  try {
    const url = new URL(origin);
    if (url.protocol !== "https:" || url.origin !== origin || url.username || url.password || url.hostname.endsWith(".invalid")) return "";
  } catch { return ""; }
  const image = origin + socialImagePath;
  const entries = [
    ["name", "description", page.description],
    ["property", "og:type", "website"], ["property", "og:site_name", "PostSteward"],
    ["property", "og:locale", "en_GB"], ["property", "og:title", page.title],
    ["property", "og:description", page.description], ["property", "og:url", origin + path],
    ["property", "og:image", image], ["property", "og:image:secure_url", image],
    ["property", "og:image:type", "image/png"], ["property", "og:image:width", "1200"],
    ["property", "og:image:height", "630"], ["property", "og:image:alt", socialImageAlt],
    ["name", "twitter:card", "summary_large_image"], ["name", "twitter:title", page.title],
    ["name", "twitter:description", page.description], ["name", "twitter:image", image],
    ["name", "twitter:image:alt", socialImageAlt],
  ];
  return `<link rel="canonical" href="${attribute(origin + path)}" />` + entries.map(([kind, key, value]) => `<meta ${kind}="${key}" content="${attribute(value)}" />`).join("");
}
