"""Real subprocess crash after a durable refill reservation, with isolated state."""
from __future__ import annotations
import os
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timezone

from ocpf_post.portfolio import apply_refill, plan_refill, DEFAULT_POLICY
from ocpf_post.model import AccountIdentity
from ocpf_post.scheduler import create_schedule, schedule_records

NOW = datetime(2026, 9, 9, 7, 0, tzinfo=timezone.utc)
POLICY = DEFAULT_POLICY


class CrashInterruptedRefillTests(unittest.TestCase):
    def test_refill_restarts_without_duplicate_reservation(self):
        with tempfile.TemporaryDirectory(prefix="poststeward-refill-crash-") as root:
            env = os.environ.copy()
            env.update(OCPF_POST_STATE_DIR=root, OCPF_POST_CONFIG_DIR=root)
            child = '''from ocpf_post.portfolio import apply_refill,plan_refill
from ocpf_post.scheduler import create_schedule
from ocpf_post.model import AccountIdentity
from ocpf_post.campaigns import builtin_manifest
from datetime import datetime,timezone
import os,json
policy=json.loads(os.environ["TEST_REFILL_POLICY"])
def create_then_crash(**kwargs):
    matching=[i for i in plan_refill(now=datetime(2026,9,9,7,0,tzinfo=timezone.utc),horizon_minutes=180,policy=policy)["plan"] if i["campaign"]==kwargs["campaign"] and i["provider"]==kwargs["provider"]]
    assert matching
    class FakeProvider:
        def account(self):
            return AccountIdentity(provider=kwargs["provider"],account_id=matching[0]["account_id"],username="test")
    create_schedule(**kwargs,provider_factory=lambda _:FakeProvider())
    os._exit(87)  # crash after durable scheduler write, before allocator acknowledgement
result=apply_refill(now=datetime(2026,9,9,7,0,tzinfo=timezone.utc),horizon_minutes=180,policy=policy,schedule_creator=create_then_crash)
print("PLAN",len(result["plan"]),"ERRORS",result["errors"][:2],flush=True)
'''
            import json
            env["TEST_REFILL_POLICY"] = json.dumps(POLICY)
            proc = subprocess.run([sys.executable, "-c", child], env=env, capture_output=True, text=True, timeout=25)
            self.assertEqual(proc.returncode, 87, proc.stderr + proc.stdout[-3000:])
            old_state, old_config = os.environ.get("OCPF_POST_STATE_DIR"), os.environ.get("OCPF_POST_CONFIG_DIR")
            try:
                os.environ.update(OCPF_POST_STATE_DIR=root, OCPF_POST_CONFIG_DIR=root)
                before = list(schedule_records())
                self.assertEqual(len(before), 1)
                planned = {(item["campaign"], item["provider"]): item["account_id"] for item in plan_refill(now=NOW, horizon_minutes=180, policy=POLICY)["plan"]}
                def fake_schedule(**kwargs):
                    class FakeProvider:
                        def account(self):
                            return AccountIdentity(provider=kwargs["provider"], account_id=planned[(kwargs["campaign"], kwargs["provider"])], username="test")
                    return create_schedule(**kwargs, provider_factory=lambda _: FakeProvider())
                result = apply_refill(now=NOW, horizon_minutes=180, policy=POLICY, schedule_creator=fake_schedule)
                after = list(schedule_records())
                self.assertGreater(len(after), len(before), "Recovery did not replenish any remaining work")
                self.assertEqual(len({(r.get("campaign"), r.get("provider")) for r in after}), len(after),
                                 "Crash recovery created duplicate campaign/provider reservations")
                self.assertEqual(sum(r.get("schedule_id") == before[0].get("schedule_id") for r in after), 1)
                self.assertFalse(any(r.get("schedule_id") == before[0].get("schedule_id") for r in result["scheduled"]))
                repeated = apply_refill(now=NOW, horizon_minutes=180, policy=POLICY, schedule_creator=fake_schedule)
                self.assertEqual(len(schedule_records()), len(after), "Second recovery created duplicate reservations")
                self.assertEqual(repeated["scheduled"], [])
            finally:
                for key, old in (("OCPF_POST_STATE_DIR", old_state), ("OCPF_POST_CONFIG_DIR", old_config)):
                    if old is None: os.environ.pop(key, None)
                    else: os.environ[key] = old
