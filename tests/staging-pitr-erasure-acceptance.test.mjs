import assert from "node:assert/strict";
import test from "node:test";
import {
  canonicalStringDigest,
  configuredD1DatabaseName,
  flattenD1Results,
  sqlLiteral,
} from "../scripts/staging-pitr-erasure-acceptance.mjs";

test("canonicalStringDigest matches PostSteward string canonicalization", () => {
  assert.equal(
    canonicalStringDigest("owner@example.test"),
    "e16cd5afed45419922fe184c8ae1573ec522e83153478b7f983730446874b28f",
  );
});

test("sqlLiteral escapes apostrophes and preserves no executable delimiter", () => {
  assert.equal(sqlLiteral("owner's"), "'owner''s'");
});

test("flattenD1Results only returns result rows", () => {
  assert.deepEqual(
    flattenD1Results([
      { results: [{ state: "completed" }], success: true },
      { success: true },
      { results: [{ count: 1 }] },
    ]),
    [{ state: "completed" }, { count: 1 }],
  );
  assert.deepEqual(flattenD1Results({}), []);
});

test("configuredD1DatabaseName uses the reviewed environment database", () => {
  assert.equal(
    configuredD1DatabaseName({
      d1_databases: [{ database_name: "poststeward-identity-staging" }],
    }),
    "poststeward-identity-staging",
  );
  assert.throws(
    () =>
      configuredD1DatabaseName({
        d1_databases: [{ database_name: "poststeward-identity" }],
      }),
    /configured environment D1/,
  );
});
