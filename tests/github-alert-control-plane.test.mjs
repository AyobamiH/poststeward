import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { spawnSync } from "node:child_process";
import test from "node:test";
import { alertOutputDocument, readOnlyCloudflareFetch, jsonRequest, alertFailureDocument, surfaceDurableAlerts } from "../scripts/github-alert-control-plane.mjs";
const script = readFileSync("scripts/github-alert-control-plane.mjs", "utf8");
const workflow = readFileSync(".github/workflows/operational-alert-control-plane.yml", "utf8");
test("GitHub issue fallback emits only bounded durable alert metadata", () => {
  assert.match(script, /SELECT id,class,severity,code,release,occurrences,last_seen_at,status FROM operational_alerts/);
  assert.match(script, /No raw workspace identifier, content, credential or provider token is included/);
  assert.match(script, /existingIssue/);
  assert.match(script, /assignees: \["AyobamiH"\]/);
  assert.doesNotMatch(script, /SELECT \* FROM operational_alerts|subject_fingerprint.*body/);
});
test("fire drill exercises all reviewed classes and a distinct failed-path fallback", () => {
  assert.match(script, /REQUIRED_ALERT_CLASSES\.map/);
  assert.match(script, /synthetic_unreachable_primary/);
  assert.match(script, /fallbackPath: "github_issue"/);
  assert.match(script, /evaluateAlertEvidence\(observation\)/);
  assert.match(script, /closeIssue/);
});
test("fire drill writes the raw hosted observation for independent re-evaluation", () => {
  const observation = {
    schemaVersion: 1,
    evidenceClass: "hosted_observation",
    environment: "staging",
    release: "a".repeat(40),
    deliveries: [],
    configuredPaths: [],
    failedPath: {},
  };
  const result = { report: { ready: true }, observation };
  assert.equal(alertOutputDocument("fire-drill", result), observation);
  assert.deepEqual(alertOutputDocument("surface", { observed: 0 }), { observed: 0 });
  assert.match(script, /writeFileSync\(process\.env\.POSTSTEWARD_ALERT_OUTPUT, output/);
});
test("workflow is out-of-band across staging and production, issue-write only and never deploys", () => {
  assert.match(workflow, /contents: read/);
  assert.match(workflow, /issues: write/);
  assert.match(workflow, /environment:\n      name: \$\{\{ matrix\.environment \}\}/);
  assert.match(workflow, /- environment: staging/);
  assert.match(workflow, /- environment: production/);
  assert.match(workflow, /secrets\.CLOUDFLARE_API_TOKEN/);
  assert.match(workflow, /secrets\.GITHUB_TOKEN/);
  assert.ok(workflow.includes("types: [poststeward-monitor-alerts]"));
  assert.ok(readFileSync("ops/monitor-clock/poststeward-clock-alerts.timer", "utf8").includes("OnCalendar=*-*-* *:00/5:00 UTC"));
  assert.doesNotMatch(workflow, /contents: write|actions: write|secrets: inherit|wrangler deploy|publish_now|schedule_create/);
});

test("routine monitor dispatch cannot run or evaluate a fire drill", () => {
  for (const name of [
    "Exercise all reviewed alert classes and failed-path fallback",
    "Evaluate fire-drill evidence",
  ]) {
    const step = workflow.split(`- name: ${name}\n`)[1]?.split("\n      - ")[0];
    assert.ok(step, `Missing fire-drill step: ${name}`);
    const condition = step.match(/^        if: (.+)$/m)?.[1];
    assert.equal(condition, "github.event_name == 'push' || github.event_name == 'workflow_dispatch'");
  }
});
test("monitor clock durably fences interrupted submissions and reconciles without replay", () => {
  const result = spawnSync("python3", ["-B", "-m", "unittest", "discover", "-s", "ops/monitor-clock", "-p", "test_dispatch.py"], {
    encoding: "utf8",
    timeout: 20_000,
  });
  assert.equal(result.status, 0, result.error?.message || `${result.stdout}\n${result.stderr}`);
});

const alertRow = (overrides = {}) => ({
  id: "alert-one", class: "provider_or_oauth_failure", severity: "warning", code: "PROVIDER_UNAVAILABLE",
  release: "development", occurrences: 1, last_seen_at: 1, status: "pending", ...overrides,
});
const issueRecord = (number, body = "Unrelated issue") => ({ number, html_url: `https://github.com/example/repo/issues/${number}`, body, state: "open" });
const surfaceEnv = {
  POSTSTEWARD_REPOSITORY: "example/repo", GITHUB_TOKEN: "fixture-github-token-only", CLOUDFLARE_API_TOKEN: "fixture-cloudflare-token-only",
  CLOUDFLARE_ACCOUNT_ID: "test-account", D1_ID: "test-db",
};
async function mockedSurface(send) {
  const originalFetch = globalThis.fetch;
  globalThis.fetch = send;
  try {
    return await surfaceDurableAlerts(surfaceEnv);
  } finally {
    globalThis.fetch = originalFetch;
  }
}
const d1Body = (rows = []) => ({ success: true, result: [{ success: true, results: rows }] });
const jsonResponse = (body, status = 200) => ({ ok: status >= 200 && status < 300, status, json: async () => body });
const resetError = () => new TypeError("private request detail", { cause: Object.assign(new Error("private socket detail"), { code: "ECONNRESET" }) });
const noWait = async () => {};
const noRecord = () => {};
const d1Url = "https://api.cloudflare.com/client/v4/accounts/test-account/d1/database/test-db/query";
const d1Init = { method: "POST", body: JSON.stringify({ sql: "SELECT id,class,severity,code,release,occurrences,last_seen_at,status FROM operational_alerts WHERE status IN ('pending','dead') ORDER BY last_seen_at DESC LIMIT 50", params: [] }), redirect: "error" };

test("read-only D1 request recovery preserves the SELECT and uses a fresh deadline", async () => {
  const calls = [], delays = [], entries = [];
  const expected = d1Body([alertRow()]);
  const result = await readOnlyCloudflareFetch(d1Url, d1Init, async (url, init) => {
    calls.push({ url, init });
    if (calls.length === 1) throw resetError();
    return jsonResponse(expected);
  }, async (ms) => delays.push(ms), (entry) => entries.push(entry));
  assert.deepEqual(result, expected);
  assert.equal(calls.length, 2);
  assert.ok(calls.every(({ url, init }) => url === d1Url && init.method === "POST" && init.body === d1Init.body && init.redirect === "error"));
  assert.notEqual(calls[0].init.signal, calls[1].init.signal);
  assert.deepEqual(delays, [350]);
  assert.equal(entries[0].code, "ECONNRESET");
  assert.equal(entries[0].retrying, true);
});

test("D1 body transport loss retries the complete read and validates the second body", async () => {
  let calls = 0, bodyReads = 0;
  const entries = [];
  const expected = d1Body([alertRow({ id: "alert-two" })]);
  const result = await readOnlyCloudflareFetch(d1Url, d1Init, async () => {
    calls++;
    return { ok: true, status: 200, json: async () => {
      bodyReads++;
      if (bodyReads === 1) throw resetError();
      return expected;
    } };
  }, noWait, (entry) => entries.push(entry));
  assert.deepEqual(result, expected);
  assert.equal(calls, 2);
  assert.equal(bodyReads, 2);
  assert.equal(entries[0].stage, "response_body");
  assert.equal(entries[0].retrying, true);
});

test("a successful empty D1 SELECT requires an affirmative complete result", async () => {
  let calls = 0;
  const expected = d1Body();
  const result = await readOnlyCloudflareFetch(d1Url, d1Init, async () => { calls++; return jsonResponse(expected); }, noWait, noRecord);
  assert.deepEqual(result, expected);
  assert.equal(calls, 1);
});

test("malformed JSON and incomplete or failed D1 contracts fail without retry", async () => {
  const responses = [
    { ok: true, status: 200, json: async () => { throw new SyntaxError("private response body"); } },
    ...[{}, null, { success: false, result: [] }, { success: true }, { success: true, result: [] },
      { success: true, result: [{ results: [] }] }, { success: true, result: [{ success: false, results: [] }] },
      { success: true, result: [{ success: true }] }, { success: true, result: [{ success: true, results: {} }] },
      { success: true, result: [{ success: true, results: [null] }] },
    ].map((body) => jsonResponse(body)),
  ];
  for (const response of responses) {
    let calls = 0;
    await assert.rejects(readOnlyCloudflareFetch(d1Url, d1Init, async () => { calls++; return response; }, noWait, noRecord), (error) => {
      const failure = alertFailureDocument(error);
      assert.equal(failure.phase, "cloudflare_d1_observation");
      assert.ok(["invalid_json", "invalid_response_contract"].includes(failure.outcome));
      return true;
    });
    assert.equal(calls, 1);
  }
});

test("HTTP authorization, rate-limit and server failures are never retried or parsed", async () => {
  for (const status of [403, 429, 500]) {
    let calls = 0;
    await assert.rejects(readOnlyCloudflareFetch(d1Url, d1Init, async () => {
      calls++;
      return { ok: false, status, json: async () => { throw new Error("HTTP error body must not be consumed"); } };
    }, noWait, noRecord), (error) => {
      const failure = alertFailureDocument(error);
      assert.equal(failure.outcome, "http_failure");
      assert.equal(failure.status, status);
      return true;
    });
    assert.equal(calls, 1);
  }
});

test("redirect, configuration, certificate and programming errors are not retried", async () => {
  for (const error of [new Error("programming error"), new TypeError("invalid URL"),
    new TypeError("fetch failed", { cause: new Error("unexpected redirect") }),
    new TypeError("fetch failed", { cause: Object.assign(new Error("certificate error"), { code: "CERT_HAS_EXPIRED" }) }),
    new DOMException("caller cancelled", "AbortError")]) {
    let calls = 0;
    await assert.rejects(readOnlyCloudflareFetch(d1Url, d1Init, async () => { calls++; throw error; }, noWait, noRecord), (failure) => {
      assert.equal(alertFailureDocument(failure).outcome, "non_retryable_error");
      return true;
    });
    assert.equal(calls, 1);
  }
});

test("recognized timeout is retried but persistent transport loss is bounded", async () => {
  let calls = 0;
  await readOnlyCloudflareFetch(d1Url, d1Init, async () => {
    if (++calls === 1) throw new DOMException("deadline exceeded", "TimeoutError");
    return jsonResponse(d1Body());
  }, noWait, noRecord);
  assert.equal(calls, 2);
  const delays = [], entries = [];
  calls = 0;
  await assert.rejects(readOnlyCloudflareFetch(d1Url, d1Init, async () => { calls++; throw resetError(); }, async (ms) => delays.push(ms), (entry) => entries.push(entry)), (error) => {
    const failure = alertFailureDocument(error);
    assert.equal(failure.attempts, 3);
    assert.equal(failure.code, "ECONNRESET");
    assert.equal(failure.phase, "cloudflare_d1_observation");
    assert.doesNotMatch(JSON.stringify(failure), /private request|private socket/);
    return true;
  });
  assert.equal(calls, 3);
  assert.deepEqual(delays, [350, 1100]);
  assert.equal(entries.at(-1).retrying, false);
});

test("read-only GitHub lookup and readback recover request or body transport loss", async () => {
  for (const stage of ["request", "response_body"]) {
    let calls = 0;
    const entries = [];
    const expected = { number: 123, body: "reviewed marker" };
    const result = await jsonRequest("https://api.github.com/repos/example/repo/issues/123", "fixture-token", {}, {
      phase: "github_fire_drill_readback", wait: noWait, record: (entry) => entries.push(entry),
      send: async (_url, init) => {
        assert.equal(init.method, "GET");
        calls++;
        if (calls === 1 && stage === "request") throw resetError();
        return { ok: true, status: 200, json: async () => {
          if (calls === 1 && stage === "response_body") throw resetError();
          return expected;
        } };
      },
    });
    assert.deepEqual(result, expected);
    assert.equal(calls, 2);
    assert.equal(entries[0].phase, "github_fire_drill_readback");
    assert.equal(entries[0].stage, stage);
  }
});

test("GitHub GET invalid JSON is a visible non-retried failure", async () => {
  let calls = 0;
  await assert.rejects(jsonRequest("https://api.github.com/repos/example/repo/issues", "fixture-token", {}, {
    phase: "github_issue_lookup", wait: noWait, record: noRecord,
    send: async () => { calls++; return { ok: true, status: 200, json: async () => { throw new SyntaxError("private body"); } }; },
  }), (error) => {
    assert.equal(alertFailureDocument(error).phase, "github_issue_lookup");
    assert.equal(alertFailureDocument(error).outcome, "invalid_json");
    return true;
  });
  assert.equal(calls, 1);
});

test("GitHub POST and PATCH are single-attempt even after uncertain response failures", async () => {
  for (const method of ["POST", "PATCH"]) {
    for (const outcome of ["request_reset", "body_reset", "invalid_json", "invalid_contract", "http_failure"]) {
      let calls = 0, waits = 0;
      await assert.rejects(jsonRequest("https://api.github.com/repos/example/repo/issues", "fixture-token", { method, body: "{}" }, {
        phase: method === "POST" ? "github_issue_create" : "github_issue_close",
        wait: async () => { waits++; }, record: noRecord,
        validate: (body) => Number.isInteger(body?.number),
        send: async (_url, init) => {
          calls++;
          assert.equal(init.method, method);
          if (outcome === "request_reset") throw resetError();
          return { ok: outcome !== "http_failure", status: outcome === "http_failure" ? 403 : 200, json: async () => {
            if (outcome === "body_reset") throw resetError();
            if (outcome === "invalid_json") throw new SyntaxError("private body");
            return {};
          } };
        },
      }), (error) => {
        const failure = alertFailureDocument(error);
        assert.equal(failure.attempts, 1);
        assert.equal(failure.writeOutcome, "unknown");
        return true;
      });
      assert.equal(calls, 1);
      assert.equal(waits, 0);
    }
  }
});

test("request diagnostics expose phases and codes without headers, URLs or causes", async () => {
  const entries = [];
  const secret = "sensitive-fixture-material";
  await assert.rejects(jsonRequest("https://api.github.com/repos/example/repo/issues", secret, {}, {
    phase: "github_issue_lookup", wait: noWait, record: (entry) => entries.push(entry),
    send: async () => { throw new Error(`Authorization: Bearer ${secret}`); },
  }), (error) => {
    const output = JSON.stringify(alertFailureDocument(error));
    assert.doesNotMatch(output, /sensitive-fixture-material|Authorization|Bearer|https:/);
    return true;
  });
  assert.doesNotMatch(JSON.stringify(entries), /sensitive-fixture-material|Authorization|Bearer|https:/);
  assert.doesNotMatch(JSON.stringify(alertFailureDocument(new Error(secret))), /sensitive-fixture-material/);
});

test("a malformed D1 observation cannot become a green empty surface result", async () => {
  const calls = [];
  await assert.rejects(mockedSurface(async (url) => {
    calls.push(url);
    return jsonResponse({});
  }), /invalid_response_contract/);
  assert.deepEqual(calls, [d1Url]);
});

test("incomplete or invalid D1 alert rows fail before any GitHub request", async () => {
  const missingFields = Object.keys(alertRow()).map((field) => {
    const row = alertRow();
    delete row[field];
    return row;
  });
  const invalidRows = [{}, { id: "alert-only" }, ...missingFields, ...[
    { id: {} }, { id: "" }, { id: "  " }, { id: "x".repeat(101) },
    { class: "unknown" }, { severity: "info" }, { code: "not a machine label" },
    { release: null }, { release: 1 }, { occurrences: 0 }, { occurrences: 1.5 }, { occurrences: "1" },
    { occurrences: Number.MAX_SAFE_INTEGER + 1 }, { last_seen_at: null }, { last_seen_at: 1.5 },
    { last_seen_at: "1" }, { last_seen_at: Number.MAX_SAFE_INTEGER + 1 }, { status: "sent" }, { status: "sending" },
  ].map((overrides) => alertRow(overrides))];
  for (const row of invalidRows) {
    const calls = [];
    await assert.rejects(mockedSurface(async (url, init) => {
      calls.push({ url, method: init.method });
      if (url === d1Url) return jsonResponse(d1Body([alertRow({ id: "valid-before-invalid" }), row]));
      throw new Error("An incomplete observation must never reach GitHub");
    }), (error) => {
      const failure = alertFailureDocument(error);
      assert.equal(failure.phase, "cloudflare_d1_observation");
      assert.equal(failure.outcome, "invalid_response_contract");
      return true;
    });
    assert.deepEqual(calls, [{ url: d1Url, method: "POST" }]);
  }
});

test("complete pending and dead rows preserve non-SHA release strings and issue reuse", async () => {
  const rows = [
    alertRow(),
    alertRow({ id: "alert-two", class: "recovery_state", severity: "critical", code: "RECOVERY_ARMED_STALE", release: "", occurrences: 2, last_seen_at: 0, status: "dead" }),
  ];
  const issues = rows.map((row, index) => issueRecord(index + 1, `<!-- poststeward-alert:${row.id} -->`));
  const calls = [];
  const result = await mockedSurface(async (url, init) => {
    calls.push({ url, method: init.method });
    if (url === d1Url) return jsonResponse(d1Body(rows));
    if (init.method === "GET") return jsonResponse(issues);
    throw new Error("An existing marker must not cause an issue write");
  });
  assert.equal(result.observed, 2);
  assert.deepEqual(result.surfaced.map((entry) => entry.alertId), ["alert-one", "alert-two"]);
  assert.deepEqual(result.surfaced.map((entry) => entry.issue), [1, 2]);
  assert.equal(calls.length, 3);
  assert.ok(calls.slice(1).every((call) => call.method === "GET"));
});

test("missing, inherited or invalid GitHub lookup bodies cannot authorize issue creation", async () => {
  const withoutBody = { number: 123, html_url: "https://github.com/example/repo/issues/123" };
  const invalidIssues = [{}, withoutBody, ...[undefined, 1, false, {}].map((body) => ({ ...withoutBody, body })),
    Object.assign(Object.create({ body: "inherited body" }), withoutBody)];
  for (const issue of invalidIssues) {
    const calls = [];
    await assert.rejects(mockedSurface(async (url, init) => {
      calls.push({ url, method: init.method });
      if (url === d1Url) return jsonResponse(d1Body([alertRow()]));
      if (init.method === "GET") return jsonResponse([issue]);
      throw new Error("An incomplete lookup must never cause an issue write");
    }), (error) => {
      const failure = alertFailureDocument(error);
      assert.equal(failure.phase, "github_issue_lookup");
      assert.equal(failure.outcome, "invalid_response_contract");
      return true;
    });
    assert.equal(calls.length, 2);
    assert.equal(calls[1].method, "GET");
  }
});

test("a complete short lookup with explicit null or unrelated body permits one issue creation", async () => {
  for (const body of [null, "Unrelated issue"]) {
    const calls = [];
    const created = issueRecord(124);
    const result = await mockedSurface(async (url, init) => {
      calls.push({ url, method: init.method });
      if (url === d1Url) return jsonResponse(d1Body([alertRow()]));
      if (init.method === "GET") return jsonResponse([issueRecord(123, body)]);
      assert.equal(init.method, "POST");
      assert.match(JSON.parse(init.body).body, /<!-- poststeward-alert:alert-one -->/);
      return jsonResponse(created, 201);
    });
    assert.deepEqual(calls.map((call) => call.method), ["POST", "GET", "POST"]);
    assert.equal(result.observed, 1);
    assert.equal(result.surfaced[0].issue, created.number);
  }
});

test("three full GitHub lookup pages cannot establish absence or authorize an issue write", async () => {
  const calls = [];
  await assert.rejects(mockedSurface(async (url, init) => {
    calls.push({ url, method: init.method });
    if (url === d1Url) return jsonResponse(d1Body([alertRow()]));
    assert.equal(init.method, "GET");
    const page = Number(new URL(url).searchParams.get("page"));
    assert.ok(page >= 1 && page <= 3);
    return jsonResponse(Array.from({ length: 100 }, (_, index) => issueRecord((page - 1) * 100 + index + 1)));
  }), (error) => {
    const failure = alertFailureDocument(error);
    assert.equal(failure.phase, "github_issue_lookup");
    assert.equal(failure.stage, "pagination");
    assert.equal(failure.outcome, "lookup_limit_reached");
    assert.equal(failure.pages, 3);
    assert.doesNotMatch(JSON.stringify(failure), /https:|Authorization|Bearer/);
    return true;
  });
  assert.equal(calls.length, 4);
  assert.ok(calls.slice(1).every((call) => call.method === "GET"));
});

test("an existing marker on the last full lookup page is reused without a write", async () => {
  const calls = [];
  const result = await mockedSurface(async (url, init) => {
    calls.push({ url, method: init.method });
    if (url === d1Url) return jsonResponse(d1Body([alertRow()]));
    assert.equal(init.method, "GET");
    const page = Number(new URL(url).searchParams.get("page"));
    assert.ok(page >= 1 && page <= 3);
    const issues = Array.from({ length: 100 }, (_, index) => issueRecord((page - 1) * 100 + index + 1));
    if (page === 3) issues[99].body = "<!-- poststeward-alert:alert-one -->";
    return jsonResponse(issues);
  });
  assert.equal(calls.length, 4);
  assert.ok(calls.slice(1).every((call) => call.method === "GET"));
  assert.deepEqual(result, {
    observed: 1,
    surfaced: [{ alertId: "alert-one", issue: 300, url: "https://github.com/example/repo/issues/300", state: "open" }],
  });
});
