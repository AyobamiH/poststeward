import type { Store } from "./types.ts";
export class SQLiteStore implements Store {
  constructor(private storage: DurableObjectStorage) {
    storage.sql.exec(
      "CREATE TABLE IF NOT EXISTS records (key TEXT PRIMARY KEY, value TEXT NOT NULL)",
    );
  }
  get<T>(key: string): T | undefined {
    const row = this.storage.sql
      .exec<{ value: string }>("SELECT value FROM records WHERE key=?", key)
      .toArray()[0];
    return row ? JSON.parse(row.value) : undefined;
  }
  put(key: string, value: unknown) {
    this.storage.sql.exec(
      "INSERT INTO records(key,value) VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
      key,
      JSON.stringify(value),
    );
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
      .map((r) => JSON.parse(r.value));
  }
  tx<T>(fn: () => T): T {
    return this.storage.transactionSync(fn);
  }
}
