export function trustedExternal(value, hosts) {
  try {
    const url = new URL(value);
    if (
      url.protocol === "https:" &&
      hosts.includes(url.hostname) &&
      !url.username &&
      !url.password &&
      !url.port
    )
      return url.href;
  } catch {
    // Untrusted server/provider text is never browser navigation authority.
  }
}

export function oauthHosts(provider) {
  return (
    {
      x: ["x.com"],
      threads: ["threads.net", "www.threads.net"],
      linkedin: ["www.linkedin.com"],
    }[provider] || []
  );
}

export function providerPostHosts(provider) {
  return (
    {
      x: ["x.com"],
      threads: [
        "threads.net",
        "www.threads.net",
        "threads.com",
        "www.threads.com",
      ],
      linkedin: ["www.linkedin.com"],
    }[provider] || []
  );
}

export function linkedinConnectionPlan(mode, actor, configuration = {}) {
  const page = mode !== "member";
  const value = String(actor || "").trim();
  const validPage =
    /^[1-9][0-9]{0,29}$/.test(value) ||
    /^urn:li:(?:organization|organizationBrand):[1-9][0-9]{0,29}$/.test(value);
  if (!page)
    return {
      page: false,
      enabled: configuration.memberAvailable === true,
      actorDisabled: true,
      actorRequired: false,
      label: "Connect LinkedIn profile",
      help: "Personal-profile publishing is separate from Page publishing and uses PostSteward's member application.",
      payload: {},
    };
  const configured = configuration.organizationAvailable === true;
  return {
    page: true,
    enabled: configured && validPage,
    actorDisabled: false,
    actorRequired: true,
    label: "Connect LinkedIn Page",
    help: configured
      ? validPage
        ? "Ready for a Page admin to authorise this destination. PostSteward supplies the application."
        : "Enter the numeric Page ID or exact Page URN. The authorising member must hold an eligible role on that Page."
      : "LinkedIn Page connections await PostSteward's central application approval and configuration. Customers do not need their own developer app.",
    payload: validPage ? { actorUrn: value } : {},
  };
}
