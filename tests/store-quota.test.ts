import assert from "node:assert/strict";
import test from "node:test";
import { DatabaseSync } from "node:sqlite";
import { SQLiteStore, workspaceStorageLimits } from "../src/store.ts";

class SqlResult<T> {
  constructor(private rows: T[] = []) {}
  toArray() {
    return this.rows;
  }
}
class StorageFixture {
  db = new DatabaseSync(":memory:");
  sql = {
    exec: <T>(query: string, ...args: unknown[]) => {
      if (!args.length) {
        this.db.exec(query);
        return new SqlResult<T>();
      }
      const statement = this.db.prepare(query);
      if (/^\s*SELECT/i.test(query))
        return new SqlResult<T>(statement.all(...(args as any[])) as T[]);
      statement.run(...(args as any[]));
      return new SqlResult<T>();
    },
  };
  transactionSync<T>(fn: () => T) {
    this.db.exec("BEGIN IMMEDIATE");
    try {
      const result = fn();
      this.db.exec("COMMIT");
      return result;
    } catch (error) {
      this.db.exec("ROLLBACK");
      throw error;
    }
  }
}

test("store tracks retained records and bytes across insert, update and delete", () => {
  const storage = new StorageFixture();
  const store = new SQLiteStore(storage as unknown as DurableObjectStorage);
  assert.deepEqual(store.usage(), { records: 0, bytes: 0 });
  store.put("a", { value: "one" });
  const inserted = store.usage();
  assert.equal(inserted.records, 1);
  assert.ok(inserted.bytes > 1);
  store.put("a", { value: "a larger value" });
  const updated = store.usage();
  assert.equal(updated.records, 1);
  assert.ok(updated.bytes > inserted.bytes);
  store.delete("a");
  assert.deepEqual(store.usage(), { records: 0, bytes: 0 });
});

test("record ceiling blocks new retained state but permits an existing record to be reduced", () => {
  const storage = new StorageFixture();
  const store = new SQLiteStore(storage as unknown as DurableObjectStorage);
  store.put("existing", { value: "large enough" });
  storage.db
    .prepare("UPDATE record_usage SET records=? WHERE id=1")
    .run(workspaceStorageLimits.records);
  assert.throws(() => store.put("new", { value: 1 }), {
    code: "WORKSPACE_STORAGE_LIMIT",
  });
  assert.doesNotThrow(() => store.put("existing", { value: "x" }));
});

test("single-record and aggregate byte ceilings fail before state is retained", () => {
  const storage = new StorageFixture();
  const store = new SQLiteStore(storage as unknown as DurableObjectStorage);
  assert.throws(
    () => store.put("huge", "x".repeat(workspaceStorageLimits.valueBytes + 1)),
    { code: "WORKSPACE_STORAGE_LIMIT" },
  );
  assert.equal(store.get("huge"), undefined);
  store.put("small", "ok");
  storage.db
    .prepare("UPDATE record_usage SET bytes=? WHERE id=1")
    .run(workspaceStorageLimits.bytes - 1);
  assert.throws(() => store.put("another", "value"), {
    code: "WORKSPACE_STORAGE_LIMIT",
  });
});

test("quota trigger accounting is rebuilt safely for pre-existing stores", () => {
  const storage = new StorageFixture();
  storage.db.exec(
    "CREATE TABLE records (key TEXT PRIMARY KEY, value TEXT NOT NULL); INSERT INTO records VALUES ('legacy','{\"ok\":true}')",
  );
  const store = new SQLiteStore(storage as unknown as DurableObjectStorage);
  assert.equal(store.usage().records, 1);
  assert.deepEqual(store.get("legacy"), { ok: true });
});
