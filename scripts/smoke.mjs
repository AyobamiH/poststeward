const origin = process.env.APP_ORIGIN;
if (!origin || !process.env.GITHUB_SHA)
  throw new Error("APP_ORIGIN and GITHUB_SHA are required.");
for (const path of ["/health", "/help.json"]) {
  const response = await fetch(new URL(path, origin), {
    signal: AbortSignal.timeout(15000),
  });
  if (!response.ok) throw new Error(path + " returned " + response.status);
  const body = await response.json();
  if (body.release !== process.env.GITHUB_SHA)
    throw new Error("Deployed revision mismatch at " + path);
}
console.log(
  "Deployed revision and capability catalogue agree. Customer, provider and payment acceptance are separate gates.",
);
