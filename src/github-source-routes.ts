import { z } from "zod";
import { authenticate } from "./auth.ts";
import { json, requireValue } from "./common.ts";
import {
  completeGitHubSourceLink,
  continueGitHubSourceSetup,
  githubSourceStatus,
  readGitHubSource,
  startGitHubSourceLink,
  unlinkGitHubSource,
} from "./github-sources.ts";
import { demandFreshOwner, ownerAuthority } from "./owner-proof.ts";
import { boundedBody, limitEdge } from "./security.ts";
import type { Env } from "./types.ts";

const probeInput = z.strictObject({
  repository: z.string().regex(/^[A-Za-z0-9_.-]+\/[A-Za-z0-9_.-]+$/).max(200),
  branch: z.string().min(1).max(200),
  path: z.string().min(1).max(300),
});

const unlinkInput = z.strictObject({
  installationId: z.number().int().positive().safe(),
});

export function isGitHubSourcePath(path: string) {
  return (
    path === "/sources/github/setup" ||
    path === "/sources/github/callback" ||
    path.startsWith("/api/sources/github/")
  );
}

export async function githubSourceRoute(request: Request, env: Env) {
  const url = new URL(request.url);
  requireValue(
    url.origin === env.PUBLIC_ORIGIN && !env.PUBLIC_ORIGIN.includes(".invalid"),
    "HOST_REJECTED",
    "This hostname is not configured.",
    403,
  );
  if (request.headers.has("origin"))
    requireValue(
      request.headers.get("origin") === env.PUBLIC_ORIGIN,
      "ORIGIN_REJECTED",
      "Cross-origin requests are not allowed.",
      403,
    );
  await limitEdge(request, env);

  const path = url.pathname;
  const jsonMutation =
    path.startsWith("/api/sources/github/") && request.method === "POST";
  if (request.body && jsonMutation)
    requireValue(
      /^application\/json(?:\s*;|$)/i.test(
        request.headers.get("content-type") || "",
      ),
      "JSON_REQUIRED",
      "Use application/json.",
      415,
    );
  request = await boundedBody(request, 32768);

  const auth = await authenticate(request, env);
  requireValue(
    auth.browser && !auth.actor.grant && auth.actor.scopes.includes("admin"),
    "OWNER_SESSION_REQUIRED",
    "GitHub source controls are available only to the signed-in owner browser.",
    403,
  );
  const owner = await ownerAuthority(request, env, auth);

  if (path === "/api/sources/github/status" && request.method === "GET")
    return json(await githubSourceStatus(env, auth.actor.workspace));

  if (path === "/api/sources/github/probe" && request.method === "POST") {
    demandFreshOwner(owner, Date.now());
    const parsed = probeInput.safeParse(await request.json());
    requireValue(parsed.success, "INVALID_INPUT",
      "Choose a linked private repository, branch and path.", 400);
    const snapshot = await readGitHubSource(
      parsed.data, env, auth.actor.workspace, fetch, Date.now(), true,
    );
    return json({
      ...parsed.data, sha: snapshot.sha, observedAt: Date.now(),
      release: env.RELEASE_SHA, private: true,
    });
  }

  if (path === "/api/sources/github/start" && request.method === "POST") {
    demandFreshOwner(owner, Date.now());
    return startGitHubSourceLink(env, owner);
  }

  if (path === "/api/sources/github/unlink" && request.method === "POST") {
    demandFreshOwner(owner, Date.now());
    const parsed = unlinkInput.safeParse(await request.json());
    requireValue(
      parsed.success,
      "INVALID_INPUT",
      "Choose a valid linked GitHub installation.",
      400,
    );
    return json(
      await unlinkGitHubSource(
        env,
        auth.actor.workspace,
        parsed.data.installationId,
      ),
    );
  }

  if (path === "/sources/github/setup" && request.method === "GET") {
    demandFreshOwner(owner, Date.now());
    return continueGitHubSourceSetup(request, env, owner);
  }

  if (path === "/sources/github/callback" && request.method === "GET") {
    demandFreshOwner(owner, Date.now());
    return completeGitHubSourceLink(request, env, owner);
  }

  return json(
    {
      error: {
        code: "NOT_FOUND",
        message: "Unknown GitHub source route or HTTP method.",
      },
    },
    404,
  );
}
