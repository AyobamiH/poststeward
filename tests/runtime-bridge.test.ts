import assert from "node:assert/strict";
import test from "node:test";
import {
  beginWorkspaceDeletion,
  completeWorkspaceDeletion,
} from "../src/lifecycle.ts";
import { digest } from "../src/common.ts";
import { requireCommandEffect } from "../src/runtime-bridge.ts";
import { environment } from "./helpers.ts";
import { runtime } from "./runtime-fixture.ts";
import {
  setExecutor,
  renewExecutorLease,
} from "../src/runtime-coordination.ts";

const origin = "https://publish.example";
const installation = "11111111-1111-4111-8111-111111111111";
async function seed(db: D1Database) {
  const token = "runtime-token-" + "a".repeat(48);
  const grant = "agent-token-" + "b".repeat(48);
  await db
    .prepare("INSERT INTO grants VALUES (?,?,?,?,?,NULL)")
    .bind(
      await digest(grant),
      "bridge-workspace",
      "bridge-agent",
      '["read","schedule"]',
      Date.now() + 3600000,
    )
    .run();
  await db
    .prepare(
      `INSERT INTO runtime_installations (installation_id,workspace,label,platform,runtime_version,token_hash,status,created_at,approved_at,token_expires_at) VALUES (?,'bridge-workspace','test','Linux','test',?,'active',?,?,?)`,
    )
    .bind(
      installation,
      await digest(token),
      Date.now(),
      Date.now(),
      Date.now() + 3600000,
    )
    .run();
  await setExecutor(db, "bridge-workspace", {
    mode: "local",
    installationId: installation,
    reason: "reviewed test handoff",
  });
  return { token, grant };
}

test("HTTP and MCP route scoped local commands with one claim, immutable completion and no hosted planner", async () => {
  const { mf, db } = await runtime();
  try {
    const { token, grant } = await seed(db);
    async function post(path: string, input: unknown, credential = grant) {
      return mf.dispatchFetch(origin + path, {
        method: "POST",
        headers: {
          Authorization: "Bearer " + credential,
          "Content-Type": "application/json",
          Accept: "application/json, text/event-stream",
        },
        body: JSON.stringify(input),
      });
    }
    const input = { view: "schedules", idempotencyKey: "bridge-inspect-001" };
    const response = await post("/api/operations/runtime_inspect", input);
    assert.equal(response.status, 200, await response.clone().text());
    const command: any = await response.json();
    assert.equal(command.status, "queued");
    assert.equal(
      (
        (await (
          await post("/api/operations/runtime_inspect", input)
        ).json()) as any
      ).commandId,
      command.commandId,
    );
    assert.equal(
      (
        await post("/api/operations/runtime_inspect", {
          ...input,
          view: "projects",
        })
      ).status,
      409,
    );
    assert.equal(
      (
        await post("/api/operations/schedule_create", {
          campaign: "test",
          at: "2026-10-01T12:00:00Z",
          idempotencyKey: "hosted-001",
        })
      ).status,
      409,
    );
    const list: any = await (
      await post("/mcp", {
        jsonrpc: "2.0",
        id: 1,
        method: "tools/list",
        params: {},
      })
    ).json();
    const names = list.result.tools.map((tool: { name: string }) => tool.name);
    assert.ok(names.includes("runtime_inspect"));
    assert.ok(names.includes("runtime_schedule_create"));
    assert.ok(!names.includes("schedule_create"));
    const claimResponse = await post(
      "/api/runtime/commands/claim",
      { authorityGeneration: 2 },
      token,
    );
    assert.equal(claimResponse.status, 200, await claimResponse.clone().text());
    const claim: any = await claimResponse.json();
    assert.equal(claim.command.commandId, command.commandId);
    assert.equal(
      (
        (await (
          await post(
            "/api/runtime/commands/claim",
            { authorityGeneration: 2 },
            token,
          )
        ).json()) as any
      ).command,
      null,
    );
    assert.equal(
      (
        await post(
          "/api/runtime/commands/authorize",
          { commandId: command.commandId, authorityGeneration: 2 },
          token,
        )
      ).status,
      200,
    );
    const completion = {
      commandId: command.commandId,
      authorityGeneration: 2,
      status: "completed",
      result: { schedules: [] },
    };
    assert.equal(
      (await post("/api/runtime/commands/complete", completion, token)).status,
      200,
    );
    assert.equal(
      (await post("/api/runtime/commands/complete", completion, token)).status,
      200,
    );
    assert.equal(
      (
        await post(
          "/api/runtime/commands/complete",
          { ...completion, result: { schedules: [1] } },
          token,
        )
      ).status,
      409,
    );
    const receipt: any = await (
      await post("/api/operations/runtime_command_get", {
        commandId: command.commandId,
      })
    ).json();
    assert.deepEqual(receipt.result, { schedules: [] });
    assert.doesNotMatch(JSON.stringify(receipt), /grant_hash|token_hash/);
  } finally {
    await mf.dispose();
  }
});

test("scope, revocation, command expiry and generation changes refuse local execution", async () => {
  const { mf, db } = await runtime();
  try {
    const { token, grant } = await seed(db);
    async function post(path: string, input: unknown, credential = grant) {
      return mf.dispatchFetch(origin + path, {
        method: "POST",
        headers: {
          Authorization: "Bearer " + credential,
          "Content-Type": "application/json",
        },
        body: JSON.stringify(input),
      });
    }
    await db
      .prepare(
        "UPDATE grants SET scopes='[\"read\"]' WHERE actor='bridge-agent'",
      )
      .run();
    assert.equal(
      (
        await post("/api/operations/runtime_schedule_cancel", {
          scheduleId: "sch_" + "a".repeat(32),
          idempotencyKey: "cancel-001",
        })
      ).status,
      403,
    );
    const command: any = await (
      await post("/api/operations/runtime_inspect", {
        view: "status",
        idempotencyKey: "inspect-revoke-001",
      })
    ).json();
    await post(
      "/api/runtime/commands/claim",
      { authorityGeneration: 2 },
      token,
    );
    await db
      .prepare("UPDATE grants SET revoked_at=? WHERE actor='bridge-agent'")
      .bind(Date.now())
      .run();
    assert.equal(
      (
        await post(
          "/api/runtime/commands/authorize",
          { commandId: command.commandId, authorityGeneration: 2 },
          token,
        )
      ).status,
      403,
    );
    await db
      .prepare("UPDATE grants SET revoked_at=NULL WHERE actor='bridge-agent'")
      .run();
    await db
      .prepare("UPDATE runtime_commands SET expires_at=0 WHERE id=?")
      .bind(command.commandId)
      .run();
    assert.equal(
      (
        await post(
          "/api/runtime/commands/authorize",
          { commandId: command.commandId, authorityGeneration: 2 },
          token,
        )
      ).status,
      409,
    );
    await setExecutor(db, "bridge-workspace", {
      mode: "local",
      installationId: installation,
      reason: "review new generation",
    });
    assert.equal(
      (
        await post(
          "/api/runtime/commands/claim",
          { authorityGeneration: 2 },
          token,
        )
      ).status,
      409,
    );
  } finally {
    await mf.dispose();
  }
});

test("reviewed migration/recovery targets a new identity and fences the old machine even after it returns", async () => {
  const { mf, db } = await runtime();
  try {
    await seed(db);
    const target = "22222222-2222-4222-8222-222222222222";
    await db
      .prepare(
        `INSERT INTO runtime_installations (installation_id,workspace,label,platform,runtime_version,token_hash,status,created_at,approved_at,token_expires_at) VALUES (?,'bridge-workspace','new','Linux','test',?,'active',?,?,?)`,
      )
      .bind(
        target,
        await digest("new-runtime-token"),
        Date.now(),
        Date.now(),
        Date.now() + 3600000,
      )
      .run();
    await assert.rejects(
      setExecutor(db, "bridge-workspace", {
        mode: "local",
        installationId: target,
        reason: "recover",
        purpose: "recover",
        sourceInstallationId: installation,
        recoveryReviewSha256: "a".repeat(64),
      }),
      { code: "RUNTIME_EXECUTOR_DEACTIVATION_REQUIRED" },
    );
    await db
      .prepare(
        "UPDATE workspace_executors SET lease_expires_at=0 WHERE workspace='bridge-workspace'",
      )
      .run();
    await assert.rejects(
      setExecutor(db, "bridge-workspace", {
        mode: "local",
        installationId: installation,
        reason: "recover",
        purpose: "recover",
        sourceInstallationId: installation,
        recoveryReviewSha256: "a".repeat(64),
      }),
      { code: "RUNTIME_RECOVERY_REVIEW_REQUIRED" },
    );
    const state = await setExecutor(db, "bridge-workspace", {
      mode: "local",
      installationId: target,
      reason: "reviewed dead-host recovery",
      purpose: "recover",
      sourceInstallationId: installation,
      recoveryReviewSha256: "a".repeat(64),
    });
    assert.equal(state.authorityGeneration, 3);
    assert.equal(state.transition?.reviewSha256, "a".repeat(64));
    await assert.rejects(
      renewExecutorLease(
        db,
        {
          workspace: "bridge-workspace",
          installationId: installation,
          tokenHash: "unused",
        },
        2,
      ),
      { code: "RUNTIME_EXECUTOR_FENCED" },
    );
  } finally {
    await mf.dispose();
  }
});

test("future scheduled effects retain original grant and exact schedule identity", async () => {
  const { mf, db } = await runtime();
  try {
    const { grant, token } = await seed(db);
    const env = { ...environment, IDENTITY: db };
    const auth = {
      workspace: "bridge-workspace",
      installationId: installation,
      tokenHash: await digest(token),
    };
    const scheduleId = "sch_" + "a".repeat(32);
    const response = await mf.dispatchFetch(
      origin + "/api/operations/runtime_schedule_create",
      {
        method: "POST",
        headers: {
          Authorization: "Bearer " + grant,
          "Content-Type": "application/json",
        },
        body: JSON.stringify({
          campaign: "PRODUCT-001",
          provider: "threads",
          at: "2026-10-01T12:00:00Z",
          idempotencyKey: "schedule-effect-001",
        }),
      },
    );
    assert.equal(response.status, 200, await response.clone().text());
    const command: any = await response.json();
    await db
      .prepare(
        "UPDATE runtime_commands SET status='completed',result=? WHERE id=?",
      )
      .bind(JSON.stringify({ scheduleId }), command.commandId)
      .run();
    await requireCommandEffect(
      env,
      auth,
      command.commandId,
      "PRODUCT-001:" + scheduleId,
      "threads",
      2,
    );
    await assert.rejects(
      requireCommandEffect(
        env,
        auth,
        command.commandId,
        "PRODUCT-001:other",
        "threads",
        2,
      ),
      { code: "RUNTIME_COMMAND_EFFECT_REFUSED" },
    );
    await db
      .prepare("UPDATE grants SET revoked_at=? WHERE actor='bridge-agent'")
      .bind(Date.now())
      .run();
    await assert.rejects(
      requireCommandEffect(
        env,
        auth,
        command.commandId,
        "PRODUCT-001:" + scheduleId,
        "threads",
        2,
      ),
      { code: "RUNTIME_COMMAND_EFFECT_REFUSED" },
    );
  } finally {
    await mf.dispose();
  }
});

test("workspace erasure removes runtime credentials and command data, and pending erasure refuses heartbeats", async () => {
  const { mf, db } = await runtime();
  try {
    const { token, grant } = await seed(db);
    await mf.dispatchFetch(origin + "/api/operations/runtime_inspect", {
      method: "POST",
      headers: {
        Authorization: "Bearer " + grant,
        "Content-Type": "application/json",
      },
      body: JSON.stringify({
        view: "projects",
        idempotencyKey: "erase-inspect-001",
      }),
    });
    await beginWorkspaceDeletion(db, "bridge-workspace");
    const heartbeat = await mf.dispatchFetch(
      origin + "/api/runtime/heartbeat",
      {
        method: "POST",
        headers: {
          Authorization: "Bearer " + token,
          "Content-Type": "application/json",
        },
        body: JSON.stringify({ authorityGeneration: 2 }),
      },
    );
    assert.equal(heartbeat.status, 410);
    await completeWorkspaceDeletion(db, "bridge-workspace");
    for (const table of [
      "runtime_commands",
      "runtime_transitions",
      "runtime_installations",
      "runtime_pairings",
      "workspace_executors",
    ]) {
      const row = await db
        .prepare(`SELECT count(*) AS count FROM ${table} WHERE workspace=?`)
        .bind("bridge-workspace")
        .first<{ count: number }>();
      assert.equal(row?.count, 0, table);
    }
    const denied = await mf.dispatchFetch(origin + "/api/runtime/bindings", {
      headers: { Authorization: "Bearer " + token },
    });
    assert.equal(denied.status, 401);
  } finally {
    await mf.dispose();
  }
});
