import assert from "node:assert/strict";
import test from "node:test";
import { seal, unseal } from "../src/crypto.ts";
import { digest } from "../src/common.ts";
import { readGitHubSource } from "../src/github-sources.ts";
import { environment } from "./helpers.ts";
import { runtime } from "./runtime-fixture.ts";
import { Response as RuntimeResponse } from "miniflare";

const origin = "https://publish.example";
const app = {
  GITHUB_APP_CLIENT_ID: "github-client-id",
  GITHUB_APP_CLIENT_SECRET: "github-client-secret",
  GITHUB_APP_SLUG: "poststeward-test",
};
const ownerRuntimeBindings = {
  ...app,
  OIDC_ISSUER: "https://accounts.google.com",
};

async function seedOwner(db: D1Database, suffix = "") {
  const session = `github-owner-session${suffix}`;
  const subject = `github-owner-subject${suffix}`;
  const workspace = `github-owner-workspace${suffix}`;
  const csrf = `github-owner-csrf${suffix}`;
  const sessionHash = await digest(session);
  const now = Date.now();
  await db.batch([
    db.prepare("INSERT INTO principals(subject,workspace,created_at) VALUES (?,?,?)").bind(subject, workspace, now),
    db.prepare("INSERT INTO sessions(token_hash,workspace,actor,expires_at,csrf) VALUES (?,?,?,?,?)").bind(sessionHash, workspace, subject, now + 3600000, csrf),
    db.prepare(
      "INSERT INTO owner_proofs(session_hash,id,issuer,client_id,email_hash,email_verified,authenticated_at,release) VALUES (?,?,?,?,?,?,?,?)",
    ).bind(
      sessionHash,
      `github-proof${suffix}`,
      "https://accounts.google.com",
      "poststeward-test",
      await digest("owner@example.com"),
      1,
      now,
      "test",
    ),
  ]);
  return { session, subject, workspace, csrf, sessionHash };
}

function ownerHeaders(owner: Awaited<ReturnType<typeof seedOwner>>) {
  return {
    Cookie: `__Host-session=${owner.session}`,
    Origin: origin,
    "Content-Type": "application/json",
    "X-CSRF-Token": owner.csrf,
  };
}

function installation(overrides: Record<string, unknown> = {}) {
  return {
    id: 42,
    app_slug: "poststeward-test",
    suspended_at: null,
    repository_selection: "selected",
    permissions: { contents: "read", metadata: "read" },
    account: { id: 7, login: "acme", type: "Organization" },
    ...overrides,
  };
}

function repo(id = 99, fullName = "acme/private-repo", privateRepo = true) {
  return { id, full_name: fullName, private: privateRepo };
}

function githubOutbound(options: {
  candidate?: number;
  permissions?: Record<string, string>;
  repositorySelection?: string;
  repositories?: Array<ReturnType<typeof repo>>;
} = {}) {
  const accessToken = "github-user-access-token-private-001";
  const refreshToken = "github-user-refresh-token-private-001";
  return async (request: Request) => {
    const url = new URL(request.url);
    if (url.hostname === "github.com" && url.pathname === "/login/oauth/access_token") {
      assert.equal(request.method, "POST");
      const body = new URLSearchParams(await request.text());
      assert.equal(body.get("client_id"), app.GITHUB_APP_CLIENT_ID);
      assert.equal(body.get("client_secret"), app.GITHUB_APP_CLIENT_SECRET);
      assert.equal(body.get("grant_type"), null);
      assert.equal(body.get("code"), "github-code");
      assert.equal(body.get("redirect_uri"), `${origin}/sources/github/callback`);
      assert.ok((body.get("code_verifier") || "").length >= 43);
      return RuntimeResponse.json({
        access_token: accessToken,
        refresh_token: refreshToken,
        expires_in: 28800,
        refresh_token_expires_in: 15811200,
      });
    }
    if (url.hostname === "api.github.com" && url.pathname === "/user") {
      assert.equal(request.headers.get("authorization"), `Bearer ${accessToken}`);
      return RuntimeResponse.json({ id: 101, login: "owner-login" });
    }
    if (url.hostname === "api.github.com" && url.pathname === "/user/installations") {
      assert.equal(request.headers.get("authorization"), `Bearer ${accessToken}`);
      return RuntimeResponse.json({
        installations: [
          installation({
            id: options.candidate ?? 42,
            permissions: options.permissions || { contents: "read", metadata: "read" },
            repository_selection: options.repositorySelection || "selected",
          }),
        ],
      });
    }
    if (
      url.hostname === "api.github.com" &&
      url.pathname === `/user/installations/${options.candidate ?? 42}/repositories`
    ) {
      assert.equal(request.headers.get("authorization"), `Bearer ${accessToken}`);
      const repositories = options.repositories || [repo()];
      return RuntimeResponse.json({ total_count: repositories.length, repositories });
    }
    throw new Error(`Unexpected GitHub endpoint ${url.href}`);
  };
}

async function startAndSetup(
  mf: Awaited<ReturnType<typeof runtime>>["mf"],
  owner: Awaited<ReturnType<typeof seedOwner>>,
  installationId = 42,
) {
  const start = await mf.dispatchFetch(`${origin}/api/sources/github/start`, {
    method: "POST",
    headers: ownerHeaders(owner),
    body: "{}",
  });
  assert.equal(start.status, 200, await start.clone().text());
  const started: any = await start.json();
  const installUrl = new URL(started.installationUrl);
  assert.equal(installUrl.origin, "https://github.com");
  assert.equal(installUrl.pathname, "/apps/poststeward-test/installations/new");
  const state = installUrl.searchParams.get("state");
  assert.ok(state);
  assert.match(start.headers.get("set-cookie") || "", /__Host-github-source=/);

  const setup = await mf.dispatchFetch(
    `${origin}/sources/github/setup?state=${encodeURIComponent(state!)}&installation_id=${installationId}&setup_action=install`,
    {
      redirect: "manual",
      headers: {
        Cookie: `__Host-session=${owner.session}; __Host-github-source=${state}`,
      },
    },
  );
  assert.equal(setup.status, 302, await setup.clone().text());
  const authorization = new URL(setup.headers.get("location")!);
  assert.equal(authorization.origin, "https://github.com");
  assert.equal(authorization.pathname, "/login/oauth/authorize");
  assert.equal(authorization.searchParams.get("client_id"), app.GITHUB_APP_CLIENT_ID);
  assert.equal(authorization.searchParams.get("state"), state);
  assert.equal(authorization.searchParams.get("code_challenge_method"), "S256");
  assert.ok(authorization.searchParams.get("code_challenge"));
  return state!;
}

test("owner GitHub App flow verifies installation ownership and stores only encrypted refreshable authority", async () => {
  const { mf, db } = await runtime(githubOutbound(), ownerRuntimeBindings, 100);
  try {
    const owner = await seedOwner(db);
    const state = await startAndSetup(mf, owner);
    const callback = await mf.dispatchFetch(
      `${origin}/sources/github/callback?state=${encodeURIComponent(state)}&code=github-code`,
      {
        redirect: "manual",
        headers: {
          Cookie: `__Host-session=${owner.session}; __Host-github-source=${state}`,
        },
      },
    );
    assert.equal(callback.status, 302, await callback.clone().text());
    assert.equal(callback.headers.get("location"), `${origin}/app?github=linked`);
    assert.match(callback.headers.get("set-cookie") || "", /Max-Age=0/);

    const stored = await db
      .prepare(
        "SELECT credential,credential_revision,status,repository_selection FROM github_installations WHERE workspace=? AND installation_id=42",
      )
      .bind(owner.workspace)
      .first<any>();
    assert.equal(stored?.status, "linked");
    assert.equal(stored?.repository_selection, "selected");
    assert.equal(stored?.credential_revision, 1);
    assert.ok(typeof stored?.credential === "string" && stored.credential.length > 30);
    assert.doesNotMatch(
      stored.credential,
      /github-user-access-token-private|github-user-refresh-token-private/,
    );
    const link = await db
      .prepare(
        "SELECT repository_id,full_name,private FROM github_repository_links WHERE workspace=?",
      )
      .bind(owner.workspace)
      .first<any>();
    assert.deepEqual(
      { id: link?.repository_id, name: link?.full_name, private: link?.private },
      { id: 99, name: "acme/private-repo", private: 1 },
    );

    const status = await mf.dispatchFetch(`${origin}/api/sources/github/status`, {
      headers: { Cookie: `__Host-session=${owner.session}` },
    });
    assert.equal(status.status, 200, await status.clone().text());
    const statusValue: any = await status.json();
    assert.equal(statusValue.configuration.ownerOnly, true);
    assert.equal(statusValue.configuration.repositorySelection, "selected_only");
    assert.equal(statusValue.repositories[0].full_name, "acme/private-repo");
    assert.doesNotMatch(
      JSON.stringify(statusValue),
      /credential|github-user-access-token-private|github-user-refresh-token-private|github-client-secret/,
    );
  } finally {
    await mf.dispose();
  }
});

test("a spoofed setup installation id cannot become repository authority", async () => {
  const { mf, db } = await runtime(
    githubOutbound({ candidate: 42 }),
    ownerRuntimeBindings,
    100,
  );
  try {
    const owner = await seedOwner(db, "-spoof");
    const state = await startAndSetup(mf, owner, 999);
    const callback = await mf.dispatchFetch(
      `${origin}/sources/github/callback?state=${encodeURIComponent(state)}&code=github-code`,
      {
        redirect: "manual",
        headers: {
          Cookie: `__Host-session=${owner.session}; __Host-github-source=${state}`,
        },
      },
    );
    assert.equal(callback.status, 403, await callback.clone().text());
    const value: any = await callback.json();
    assert.equal(value.error.code, "GITHUB_INSTALLATION_NOT_AUTHORISED");
    assert.equal(
      Number(
        (
          await db
            .prepare("SELECT count(*) AS n FROM github_installations WHERE workspace=?")
            .bind(owner.workspace)
            .first<any>()
        )?.n || 0,
      ),
      0,
    );
  } finally {
    await mf.dispose();
  }
});

test("installation-wide or write GitHub authority is rejected before credentials are retained", async () => {
  for (const [name, outbound, code] of [
    [
      "all repositories",
      githubOutbound({ repositorySelection: "all" }),
      "GITHUB_REPOSITORY_SCOPE_TOO_BROAD",
    ],
    [
      "contents write",
      githubOutbound({ permissions: { contents: "write", metadata: "read" } }),
      "GITHUB_APP_PERMISSION_TOO_BROAD",
    ],
  ] as const) {
    const { mf, db } = await runtime(outbound, ownerRuntimeBindings, 100);
    try {
      const owner = await seedOwner(db, `-${name.replaceAll(" ", "-")}`);
      const state = await startAndSetup(mf, owner);
      const callback = await mf.dispatchFetch(
        `${origin}/sources/github/callback?state=${encodeURIComponent(state)}&code=github-code`,
        {
          redirect: "manual",
          headers: {
            Cookie: `__Host-session=${owner.session}; __Host-github-source=${state}`,
          },
        },
      );
      assert.equal(callback.status, 409, `${name}: ${await callback.clone().text()}`);
      const value: any = await callback.json();
      assert.equal(value.error.code, code, name);
      assert.equal(
        Number(
          (
            await db
              .prepare("SELECT count(*) AS n FROM github_installations WHERE workspace=?")
              .bind(owner.workspace)
              .first<any>()
          )?.n || 0,
        ),
        0,
        name,
      );
    } finally {
      await mf.dispose();
    }
  }
});

test("every linked private read revalidates selected-only read authority before the commit endpoint", async () => {
  const { mf, db } = await runtime(githubOutbound(), ownerRuntimeBindings, 100);
  try {
    const owner = await seedOwner(db, "-drift");
    const state = await startAndSetup(mf, owner);
    const callback = await mf.dispatchFetch(
      `${origin}/sources/github/callback?state=${encodeURIComponent(state)}&code=github-code`,
      {
        redirect: "manual",
        headers: {
          Cookie: `__Host-session=${owner.session}; __Host-github-source=${state}`,
        },
      },
    );
    assert.equal(callback.status, 302, await callback.clone().text());

    let commitReads = 0;
    const send = async (request: RequestInfo | URL, init?: RequestInit) => {
      const req = request instanceof Request ? request : new Request(request, init);
      const url = new URL(req.url);
      if (url.pathname === "/user/installations")
        return Response.json({
          installations: [
            installation({ permissions: { contents: "write", metadata: "read" } }),
          ],
        });
      if (url.pathname === "/user/installations/42/repositories")
        return Response.json({ total_count: 1, repositories: [repo()] });
      if (url.pathname === "/repos/acme/private-repo/commits") {
        commitReads++;
        return Response.json([{ sha: "b".repeat(40) }]);
      }
      throw new Error(`Unexpected read endpoint ${url.href}`);
    };
    const sourceEnv = {
      IDENTITY: db,
      ENCRYPTION_KEY: environment.ENCRYPTION_KEY,
      ENCRYPTION_KEY_VERSION: "2",
      ...app,
    } as any;
    await assert.rejects(
      readGitHubSource(
        {
          repository: "acme/private-repo",
          branch: "main",
          path: "README.md",
        } as any,
        sourceEnv,
        owner.workspace,
        send as typeof fetch,
      ),
      { code: "GITHUB_APP_PERMISSION_TOO_BROAD" },
    );
    assert.equal(commitReads, 0);
  } finally {
    await mf.dispose();
  }
});

test("unlinked public GitHub sources remain anonymous and require no GitHub App configuration", async () => {
  const { mf, db } = await runtime(undefined, {}, 100);
  try {
    let calls = 0;
    const source = await readGitHubSource(
      {
        repository: "public/example",
        branch: "main",
        path: "README.md",
      } as any,
      { IDENTITY: db } as any,
      "public-workspace",
      (async (request: RequestInfo | URL, init?: RequestInit) => {
        calls++;
        const req = request instanceof Request ? request : new Request(request, init);
        assert.equal(new URL(req.url).pathname, "/repos/public/example/commits");
        assert.equal(req.headers.get("authorization"), null);
        return Response.json([{ sha: "a".repeat(40) }]);
      }) as typeof fetch,
    );
    assert.equal(source.sha, "a".repeat(40));
    assert.equal(calls, 1);
  } finally {
    await mf.dispose();
  }
});

const refreshProfile = { repository: "acme/private-repo", branch: "main", path: "README.md" } as any;

async function refreshFixture() {
  const { mf, db } = await runtime(undefined, {}, 100);
  const now = Date.now();
  const workspace = "refresh-workspace";
  const env = { IDENTITY: db, ENCRYPTION_KEY: environment.ENCRYPTION_KEY,
    ENCRYPTION_KEY_VERSION: "2", ...app } as any;
  const context = `${workspace}:github:42`;
  const credential = await seal({
    accessToken: "old-github-user-access-token-001",
    refreshToken: "old-github-user-refresh-token-001",
    expiresAt: now + 60_000, refreshExpiresAt: now + 86_400_000,
  }, env.ENCRYPTION_KEY, context, "2");
  await db.prepare(
    "INSERT INTO github_installations(workspace,installation_id,account_id,account_login,account_type,user_id,user_login,repository_selection,status,credential,credential_revision,token_expires_at,refresh_expires_at,linked_at,last_verified_at,updated_at) VALUES (?,42,7,'acme','Organization',101,'owner','selected','linked',?,1,?,?,?,?,?)",
  ).bind(workspace, credential, now + 60_000, now + 86_400_000, now, now, now).run();
  await db.prepare(
    "INSERT INTO github_repository_links(workspace,repository_id,installation_id,full_name,private,linked_at,verified_at) VALUES (?,99,42,'acme/private-repo',1,?,?)",
  ).bind(workspace, now, now).run();
  const row = () => db.prepare("SELECT * FROM github_installations WHERE workspace=? AND installation_id=42")
    .bind(workspace).first<any>();
  const read = (send: typeof fetch, at = now) =>
    readGitHubSource(refreshProfile, env, workspace, send, at);
  return { mf, db, env, now, workspace, context, row, read };
}

function rotatedResponse() {
  return Response.json({
    access_token: "new-github-user-access-token-002",
    refresh_token: "new-github-user-refresh-token-002",
    expires_in: 28800, refresh_token_expires_in: 15811200,
  });
}

function privateReadResponse(url: URL) {
  if (url.pathname === "/user/installations")
    return Response.json({ installations: [installation()] });
  if (url.pathname === "/user/installations/42/repositories")
    return Response.json({ total_count: 1, repositories: [repo()] });
  if (url.pathname === "/repos/acme/private-repo/commits")
    return Response.json([{ sha: "c".repeat(40) }]);
  throw new Error("Unexpected test endpoint");
}

function gate() {
  let release!: () => void;
  const promise = new Promise<void>((resolve) => { release = resolve; });
  return { promise, release };
}

test("concurrent private reads send a rotating refresh token only once", { timeout: 20_000 }, async () => {
  const f = await refreshFixture();
  const entered = gate(), finish = gate();
  let refreshes = 0;
  const send = (async (input: RequestInfo | URL, init?: RequestInit) => {
    const request = new Request(input, init);
    const url = new URL(request.url);
    if (url.hostname === "github.com") {
      refreshes++;
      assert.equal(new URLSearchParams(await request.text()).get("refresh_token"),
        "old-github-user-refresh-token-001");
      assert.ok((await f.row()).refresh_lease);
      entered.release();
      await finish.promise;
      return rotatedResponse();
    }
    assert.equal(request.headers.get("authorization"), "Bearer new-github-user-access-token-002");
    return privateReadResponse(url);
  }) as typeof fetch;
  const first = f.read(send);
  void first.catch(() => entered.release());
  try {
    await entered.promise;
    await assert.rejects(f.read(send), { code: "GITHUB_REFRESH_IN_PROGRESS" });
    assert.equal(refreshes, 1);
    assert.equal((await f.row()).status, "linked");
    finish.release();
    assert.equal((await first).sha, "c".repeat(40));
    const stored = await f.row();
    assert.equal(stored.credential_revision, 2);
    assert.equal(stored.refresh_lease, null);
    assert.equal(stored.refresh_lease_until, null);
    assert.equal(stored.status, "linked");
    const decrypted: any = await unseal(stored.credential, f.env.ENCRYPTION_KEY, f.context);
    assert.equal(decrypted.refreshToken, "new-github-user-refresh-token-002");
    assert.equal((await f.read(send)).sha, "c".repeat(40));
    assert.equal(refreshes, 1);
  } finally {
    finish.release();
    await first.catch(() => undefined);
    await f.mf.dispose();
  }
});

test("uncertain refresh outcomes are fenced and never replay the consumed token", async () => {
  const f = await refreshFixture();
  let calls = 0;
  const send = (async () => { calls++; throw new Error("response lost after consumption"); }) as typeof fetch;
  try {
    await assert.rejects(f.read(send), { code: "GITHUB_REAUTH_REQUIRED" });
    await assert.rejects(f.read(send), { code: "GITHUB_REAUTH_REQUIRED" });
    const stored = await f.row();
    assert.equal(calls, 1);
    assert.equal(stored.status, "stale");
    assert.equal(stored.last_error, "GITHUB_REFRESH_UNCERTAIN");
    assert.ok(stored.refresh_lease);
  } finally { await f.mf.dispose(); }
});

test("an expired refresh lease is not reclaimed and a late holder cannot commit", { timeout: 20_000 }, async () => {
  const f = await refreshFixture();
  const entered = gate(), finish = gate();
  let calls = 0;
  const send = (async () => {
    calls++; entered.release(); await finish.promise; return rotatedResponse();
  }) as typeof fetch;
  const first = f.read(send);
  void first.catch(() => entered.release());
  try {
    await entered.promise;
    await f.db.prepare("UPDATE github_installations SET refresh_lease_until=? WHERE workspace=?")
      .bind(f.now - 1, f.workspace).run();
    await assert.rejects(f.read(send), { code: "GITHUB_REAUTH_REQUIRED" });
    finish.release();
    await assert.rejects(first, { code: "GITHUB_REAUTH_REQUIRED" });
    assert.equal(calls, 1);
    assert.equal((await f.row()).status, "stale");
    assert.equal((await f.row()).credential_revision, 1);
  } finally { finish.release(); await first.catch(() => undefined); await f.mf.dispose(); }
});

test("reconnection supersedes both successful and failed in-flight refreshes", { timeout: 30_000 }, async () => {
  for (const failure of [false, true]) {
    const f = await refreshFixture();
    const entered = gate(), finish = gate();
    const send = (async () => {
      entered.release(); await finish.promise;
      if (failure) throw new Error("old refresh failed");
      return rotatedResponse();
    }) as typeof fetch;
    const first = f.read(send);
    void first.catch(() => entered.release());
    try {
      await entered.promise;
      const replacement = await seal({
        accessToken: "reconnected-github-access-token",
        refreshToken: "reconnected-github-refresh-token",
        expiresAt: f.now + 28_800_000, refreshExpiresAt: f.now + 86_400_000,
      }, f.env.ENCRYPTION_KEY, f.context, "2");
      await f.db.prepare(
        "UPDATE github_installations SET credential=?,credential_revision=credential_revision+1,refresh_lease=NULL,refresh_lease_until=NULL,token_expires_at=? WHERE workspace=?",
      ).bind(replacement, f.now + 28_800_000, f.workspace).run();
      finish.release();
      await assert.rejects(first, { code: "GITHUB_REAUTH_REQUIRED" });
      const stored = await f.row();
      assert.equal(stored.credential, replacement);
      assert.equal(stored.credential_revision, 2);
      assert.equal(stored.status, "linked");
      assert.equal(stored.last_error, null);
      assert.equal(stored.refresh_lease, null);
    } finally { finish.release(); await first.catch(() => undefined); await f.mf.dispose(); }
  }
});

test("unlink during refresh cannot recreate credentials or read repository content", { timeout: 20_000 }, async () => {
  const f = await refreshFixture();
  const entered = gate(), finish = gate();
  let calls = 0;
  const send = (async () => {
    calls++; entered.release(); await finish.promise; return rotatedResponse();
  }) as typeof fetch;
  const first = f.read(send);
  void first.catch(() => entered.release());
  try {
    await entered.promise;
    await f.db.prepare("DELETE FROM github_installations WHERE workspace=?").bind(f.workspace).run();
    finish.release();
    await assert.rejects(first, { code: "GITHUB_REAUTH_REQUIRED" });
    assert.equal(calls, 1);
    assert.equal(await f.row(), null);
  } finally { finish.release(); await first.catch(() => undefined); await f.mf.dispose(); }
});

test("removed and renamed private repositories remain fenced on subsequent reads", async () => {
  for (const renamed of [false, true]) {
    const f = await refreshFixture();
    let commits = 0;
    const send = (async (input: RequestInfo | URL, init?: RequestInit) => {
      const request = new Request(input, init);
      assert.ok(new URL(request.url).hostname === "github.com" || request.headers.get("authorization"));
      const url = new URL(request.url);
      if (url.hostname === "github.com") return rotatedResponse();
      if (url.pathname === "/user/installations/42/repositories")
        return Response.json({ total_count: 1, repositories: [renamed ? repo(99, "acme/renamed") : repo(100, "acme/other")] });
      if (url.pathname.includes("/commits")) commits++;
      return privateReadResponse(url);
    }) as typeof fetch;
    try {
      await assert.rejects(f.read(send), {
        code: renamed ? "GITHUB_REPOSITORY_RENAMED" : "GITHUB_REPOSITORY_ACCESS_REVOKED",
      });
      await assert.rejects(f.read(send), { code: "GITHUB_REAUTH_REQUIRED" });
      assert.equal(commits, 0);
      assert.equal((await f.row()).status, "stale");
      assert.equal((await f.db.prepare("SELECT full_name FROM github_repository_links WHERE workspace=?")
        .bind(f.workspace).first<any>()).full_name, "acme/private-repo");
    } finally { await f.mf.dispose(); }
  }
});
