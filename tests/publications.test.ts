import { test } from "node:test";
import assert from "node:assert/strict";
import {
  freezePublication,
  validateFrozenPublication,
} from "../src/publications.ts";
import { validateText } from "../src/providers.ts";

test("long X copy is frozen as bounded immutable parts without splitting a URL", async () => {
  const url = "https://example.com/" + "a".repeat(320);
  const text = `${"A reviewed sentence. ".repeat(18)} ${url} ${"Final thought. ".repeat(18)}`;
  const publication = await freezePublication("x", text);

  assert.equal(publication.type, "thread");
  assert.ok(publication.parts.length > 1);
  assert.ok(publication.parts.length <= 25);
  assert.equal(
    publication.parts.filter((part) => part.text.includes(url)).length,
    1,
  );
  for (const part of publication.parts)
    assert.doesNotThrow(() => validateText("x", part.text));
  await assert.doesNotReject(validateFrozenPublication("x", publication));
});

test("Threads copy freezes deterministic part text and rejects later mutation", async () => {
  const publication = await freezePublication(
    "threads",
    "One sentence. ".repeat(90),
  );
  assert.equal(publication.type, "thread");
  assert.ok(publication.parts.length >= 2);
  assert.ok(publication.parts.every((part) => [...part.text].length <= 500));

  const changed = structuredClone(publication);
  changed.parts[0].text += " changed";
  await assert.rejects(validateFrozenPublication("threads", changed), {
    code: "PAYLOAD_DRIFT",
  });
});

test("LinkedIn remains one exact post and enforces its provider limit", async () => {
  const publication = await freezePublication("linkedin", "Page update");
  assert.equal(publication.type, "single");
  assert.equal(publication.parts.length, 1);
  await assert.rejects(freezePublication("linkedin", "x".repeat(3001)), {
    code: "CONTENT_TOO_LONG",
  });
});
