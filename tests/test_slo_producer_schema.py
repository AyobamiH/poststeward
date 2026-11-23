"""Additive SLO receipt migration and exact producer SQL contract (in memory only)."""
import pathlib
import re
import sqlite3
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
MIGRATIONS = ROOT / "migrations"

def new_db():
    db = sqlite3.connect(":memory:")
    for name in (
        "0005_external_effect_ledger.sql",
        "0014_advanced_slo_observations.sql",
        "0021_advanced_slo_receipt_provenance.sql",
    ):
        db.executescript((MIGRATIONS / name).read_text())
    return db

def producer_insert_sql():
    source = (ROOT / "src/slo-telemetry.ts").read_text()
    found = re.search(
        r"const result = await env\.IDENTITY\.prepare\(\s*`(INSERT INTO advanced_slo_events\(.*?ON CONFLICT DO NOTHING)`",
        source, re.S,
    )
    assert found, "cannot locate producer's actual SQL"
    return found.group(1)

class SloProducerSchemaTests(unittest.TestCase):
    def test_legacy_rows_remain_unverified_and_unbackfilled(self):
        db = new_db()
        columns = {r[1] for r in db.execute("PRAGMA table_info(advanced_slo_events)")}
        self.assertTrue({"canary_run_id", "evidence_origin", "evidence_outcome", "reference_hash"} <= columns)
        self.assertIn("published_at", {r[1] for r in db.execute("PRAGMA table_info(external_effects)")})
        db.execute(
            """INSERT INTO advanced_slo_events
            (id,workspace,event_type,observed_at,value,duration_ms,release,canary_bps)
            VALUES ('legacy','w','advanced_operation',1,1,NULL,?,100)""",
            ("a"*40,),
        )
        row = db.execute("SELECT canary_run_id,evidence_origin,evidence_outcome FROM advanced_slo_events WHERE id='legacy'").fetchone()
        self.assertEqual(row, (None, "legacy_unverified", "unverified"))
        db.close()

    def test_atomic_active_release_bps_seed_binding_and_retry(self):
        db = new_db()
        release = "a"*40
        seed = "s"*64
        run_sql = """INSERT INTO advanced_canary_runs
            (id,release,canary_bps,seed_hash,started_at,stopped_at,created_at)
            VALUES (?,?,?,?,?,NULL,?)"""
        db.execute(run_sql, ("run-1",release,100,seed,1000,1000))
        sql = producer_insert_sql()
        def add(event_id, run_seed=seed, run_release=release, bps=100, observed_at=2000):
            args=(event_id,"workspace-1","advanced_operation",observed_at,1,None,
                  release,100,"completed_operation","succeeded","receipt-hash",
                  run_release,bps,run_seed,observed_at)
            return db.execute(sql,args).rowcount
        self.assertEqual(add("event-1","wrong-seed"),0)
        self.assertEqual(add("event-2",seed,"b"*40),0)
        self.assertEqual(add("event-3",seed,release,500),0)
        self.assertEqual(add("event-4"),1)
        self.assertEqual(add("event-5"),0, "retry must not count a second operation")
        db.execute("UPDATE advanced_canary_runs SET stopped_at=3000 WHERE id='run-1'")
        self.assertEqual(add("event-6"),0, "stopped cohort must reject new evidence")
        db.execute(run_sql, ("run-2",release,100,seed,4000,4000))
        self.assertEqual(add("event-7", observed_at=5000),1, "new run can legitimately count own receipt")
        ids=[r[0] for r in db.execute(
            "SELECT canary_run_id FROM advanced_slo_events WHERE id LIKE 'event-%' ORDER BY id")]
        self.assertEqual(ids, ["run-1","run-2"])
        db.close()

if __name__ == "__main__":
    unittest.main()
