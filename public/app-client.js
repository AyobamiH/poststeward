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
      threads: ["threads.net", "www.threads.net", "threads.com", "www.threads.com"],
      linkedin: ["www.linkedin.com"],
    }[provider] || []
  );
}
