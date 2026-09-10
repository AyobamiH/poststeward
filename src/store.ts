import { Fault } from "./common.ts";
import type { Store } from "./types.ts";

export const workspaceStorageLimits = Object.freeze({
  records: 20_000,
  bytes: 16 * 1024 * 1024,
  valueBytes: 128 * 1024,
});

type Usage = { records: number; bytes: number };

export class SQLiteStore implements Store {
  constructor(private storage: DurableObjectStorage) {
    storage.sql.exec(
      "CREATE TABLE IF NOT EXISTS records (key TEXT PRIMARY KEY, value TEXT NOT NULL)",
    );
    storage.sql.exec(
      "CREATE TABLE IF NOT EXISTS record_usage (id INTEGER PRIMARY KEY CHECK(id=1), records INTEGER NOT NULL, bytes INTEGER NOT NULL)",
    );
    storage.sql.exec(
      "INSERT OR IGNORE INTO record_usage(id,records,bytes) SELECT 1,count(*),COALESCE(sum(length(CAST(key AS BLOB))+length(CAST(value AS BLOB))),0) FROM records",
    );
    storage.sql.exec(
      `CREATE TRIGGER IF NOT EXISTS records_quota_insert BEFORE INSERT ON records
       WHEN length(CAST(NEW.value AS BLOB)) > ${workspaceStorageLimits.valueBytes}
         OR (SELECT records FROM record_usage WHERE id=1) + 1 > ${workspaceStorageLimits.records}
         OR (SELECT bytes FROM record_usage WHERE id=1) + length(CAST(NEW.key AS BLOB)) + length(CAST(NEW.value AS BLOB)) > ${workspaceStorageLimits.bytes}
       BEGIN SELECT RAISE(ABORT, 'WORKSPACE_STORAGE_LIMIT'); END`,
    );
    storage.sql.exec(
      `CREATE TRIGGER IF NOT EXISTS records_quota_update BEFORE UPDATE OF key,value ON records
       WHEN length(CAST(NEW.value AS BLOB)) > ${workspaceStorageLimits.valueBytes}
         OR (SELECT bytes FROM record_usage WHERE id=1)
            - length(CAST(OLD.key AS BLOB)) - length(CAST(OLD.value AS BLOB))
            + length(CAST(NEW.key AS BLOB)) + length(CAST(NEW.value AS BLOB)) > ${workspaceStorageLimits.bytes}
       BEGIN SELECT RAISE(ABORT, 'WORKSPACE_STORAGE_LIMIT'); END`,
    );
    storage.sql.exec(
      `CREATE TRIGGER IF NOT EXISTS records_usage_insert AFTER INSERT ON records
       BEGIN UPDATE record_usage SET records=records+1, bytes=bytes+length(CAST(NEW.key AS BLOB))+length(CAST(NEW.value AS BLOB)) WHERE id=1; END`,
    );
    storage.sql.exec(
      `CREATE TRIGGER IF NOT EXISTS records_usage_update AFTER UPDATE OF key,value ON records
       BEGIN UPDATE record_usage SET bytes=bytes-length(CAST(OLD.key AS BLOB))-length(CAST(OLD.value AS BLOB))+length(CAST(NEW.key AS BLOB))+length(CAST(NEW.value AS BLOB)) WHERE id=1; END`,
    );
    storage.sql.exec(
      `CREATE TRIGGER IF NOT EXISTS records_usage_delete AFTER DELETE ON records
       BEGIN UPDATE record_usage SET records=records-1, bytes=bytes-length(CAST(OLD.key AS BLOB))-length(CAST(OLD.value AS BLOB)) WHERE id=1; END`,
    );
  }

  get<T>(key: string): T | undefined {
    const row = this.storage.sql
      .exec<{ value: string }>("SELECT value FROM records WHERE key=?", key)
      .toArray()[0];
    return row ? JSON.parse(row.value) : undefined;
  }

  put(key: string, value: unknown) {
    const serialized = JSON.stringify(value);
    if (serialized === undefined)
      throw new Fault(
        "WORKSPACE_VALUE_INVALID",
        "Workspace state must be JSON serialisable.",
        400,
      );
    try {
      const exists = this.storage.sql
        .exec<{ key: string }>("SELECT key FROM records WHERE key=?", key)
        .toArray()[0];
      if (exists)
        this.storage.sql.exec(
          "UPDATE records SET value=? WHERE key=?",
          serialized,
          key,
        );
      else
        this.storage.sql.exec(
          "INSERT INTO records(key,value) VALUES (?,?)",
          key,
          serialized,
        );
    } catch (error) {
      if (String(error).includes("WORKSPACE_STORAGE_LIMIT"))
        throw new Fault(
          "WORKSPACE_STORAGE_LIMIT",
          "Workspace retained-state capacity is full. Export and remove unnecessary state before creating more retained records.",
          507,
        );
      throw error;
    }
  }

  delete(key: string) {
    this.storage.sql.exec("DELETE FROM records WHERE key=?", key);
  }

  list<T>(prefix: string): T[] {
    return this.storage.sql
      .exec<{ value: string }>(
        "SELECT value FROM records WHERE key>=? AND key<? ORDER BY key",
        prefix,
        prefix + "\uffff",
      )
      .toArray()
      .map((row) => JSON.parse(row.value));
  }

  usage(): Usage {
    const row = this.storage.sql
      .exec<Usage>("SELECT records,bytes FROM record_usage WHERE id=1")
      .toArray()[0];
    return row
      ? { records: Number(row.records), bytes: Number(row.bytes) }
      : { records: 0, bytes: 0 };
  }

  tx<T>(fn: () => T): T {
    return this.storage.transactionSync(fn);
  }
}
