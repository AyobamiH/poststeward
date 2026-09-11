import assert from "node:assert/strict";
import test from "node:test";
import * as oauth from "oauth4webapi";
import { loginFailure } from "../src/login-failure.ts";
import { errorResponse, Fault } from "../src/common.ts";

test("login diagnostics log only bounded classifications and preserve existing intentional faults", async () => {
  const logs: string[] = [], previous = console.warn;
  console.warn = (value) => { logs.push(String(value)); };
  try {
    const secret = "private-credential-marker";
    const cases = [
      new oauth.ResponseBodyError(secret, { cause: { error: "invalid_client", error_description: secret }, response: Response.json({}, { status: 401 }) }),
      new oauth.ResponseBodyError(secret, { cause: { error: secret, error_description: secret }, response: Response.json({}, { status: 400 }) }),
      new oauth.AuthorizationResponseError(secret, { cause: new URLSearchParams({ error: "access_denied", code: secret, error_description: secret }) }),
      new oauth.OperationProcessingError(secret, { code: secret, cause: { claims: { email: secret }, parameters: { code: secret } } }),
      new Error(secret, { cause: { request: secret } }),
    ];
    for (const error of cases) {
      const failure = loginFailure(error, "token_validation", secret);
      const publicBody = await errorResponse(failure).text();
      assert.doesNotMatch(publicBody, /private-credential-marker|claims|parameters/);
      assert.match(publicBody, /"release":"development"/);
      assert.match(publicBody, /"reference":"[a-f0-9-]{36}"/);
    }
    assert.equal(logs.length, cases.length);
    assert.doesNotMatch(logs.join("\n"), /private-credential-marker|claims|parameters/);
    for (const line of logs)
      assert.deepEqual(Object.keys(JSON.parse(line)).sort(), ["code", "event", "reason", "reference", "release", "stage"]);
    const fault = new Fault("SIGNUP_RESTRICTED", "Invited owners only.", 403);
    assert.equal(loginFailure(fault, "signature", "development"), fault);
    assert.equal(logs.length, cases.length);
  } finally { console.warn = previous; }
});
