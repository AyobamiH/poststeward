import test from "node:test";
import assert from "node:assert/strict";
import { mkdtempSync, readFileSync, writeFileSync, renameSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { spawn } from "node:child_process";
import { collectEngagement } from "../src/engagement-collector.ts";

class DurableFileStore {
  constructor(file) { this.file = file; }
  read() { return JSON.parse(readFileSync(this.file, "utf8")); }
  get(key) { return this.read()[key]; }
  put(key, value) { const state = this.read(); state[key] = value; const tmp = this.file + ".new"; writeFileSync(tmp, JSON.stringify(state)); renameSync(tmp, this.file); }
  list(prefix) { return Object.entries(this.read()).filter(([key]) => key.startsWith(prefix)).map(([, value]) => value); }
  tx(fn) { return fn(); }
}
const scopes = [{ workspace: "workspace-a", account: "threads-a", provider: "threads", active: true, readbackGranted: true, rootIds: ["123"] }];
test("SIGKILL during in-flight GET leaves durable claim; expired claim recovers exactly once", async () => {
  const directory = mkdtempSync(join(tmpdir(), "poststeward-engagement-kill-"));
  const file = join(directory, "state.json");
  writeFileSync(file, "{}");
  try {
    const script = `import { collectEngagement } from ${JSON.stringify(new URL("../src/engagement-collector.ts", import.meta.url).href)};
import { readFileSync, writeFileSync, renameSync } from "node:fs";
const file=process.argv[1];
const store={read(){return JSON.parse(readFileSync(file,"utf8"))},get(k){return this.read()[k]},put(k,v){const s=this.read();s[k]=v;writeFileSync(file+".new",JSON.stringify(s));renameSync(file+".new",file)},list(p){return Object.entries(this.read()).filter(([k])=>k.startsWith(p)).map(([,v])=>v)},tx(f){return f()}};
await collectEngagement(store,"workspace-a",${JSON.stringify(scopes)},async()=>{process.stdout.write("GET_STARTED\\n");await new Promise(()=>{})},100,10000);`;
    const child = spawn(process.execPath, ["--experimental-transform-types", "--input-type=module", "-e", script, file], { stdio: ["ignore", "pipe", "pipe"] });
    await new Promise((resolve, reject) => {
      const timeout = setTimeout(() => reject(new Error("Child never began GET")), 10000);
      child.stdout.on("data", (chunk) => { if (String(chunk).includes("GET_STARTED")) { clearTimeout(timeout); resolve(); } });
      child.once("error", reject);
    });
    child.kill("SIGKILL");
    await new Promise((resolve) => child.once("close", resolve));
    const store = new DurableFileStore(file);
    assert.equal(store.list("engagement:reply:").length, 0);
    let calls = 0;
    const read = async () => { calls++; return { replies: [{ id: "456", rootId: "123", text: "reply", authorUsername: "visitor" }], nextCursor: "cursor2" }; };
    assert.equal((await collectEngagement(store, "workspace-a", scopes, read, 101, 10000)).attempted, 0);
    assert.equal((await collectEngagement(store, "workspace-a", scopes, read, 10101, 10000)).collected, 1);
    assert.equal(store.list("engagement:reply:").length, 1);
    assert.equal(calls, 1);
  } finally { rmSync(directory, { recursive: true, force: true }); }
});
