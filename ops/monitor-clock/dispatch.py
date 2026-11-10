"""Dispatch one observation for the latest due UTC slot, including after downtime."""
import json
import fcntl
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

PERIOD_OFFSET = {"alerts": (300, 0), "capacity": (3600, 41 * 60), "slo": (14400, 29 * 60)}
REPO = "AyobamiH/poststeward"
WORKFLOWS = {"alerts": "operational-alert-control-plane", "capacity": "staging-capacity-observation", "slo": "staging-advanced-slo-observe"}


def sync_directory(path):
    descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def ensure_state_directory(root):
    missing = []
    directory = root
    while not directory.exists():
        missing.append(directory)
        directory = directory.parent
    for directory in reversed(missing):
        directory.mkdir(exist_ok=True)
        # Persist newly created directory entries as well as receipt files.
        sync_directory(directory.parent)


def dispatch(kind):
    # Serialise manual recovery with timer dispatch for the same monitor.
    root = Path.home() / ".local/state/poststeward-monitor-clock"
    ensure_state_directory(root)
    with (root / (kind + ".lock")).open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        return dispatch_locked(kind)


def dispatch_locked(kind):
    period, offset = PERIOD_OFFSET[kind]
    stamp = (int(time.time()) - offset) // period * period + offset
    slot = datetime.fromtimestamp(stamp, timezone.utc).strftime("%Y-%m-%dT%H:%MZ")
    root = Path.home() / ".local/state/poststeward-monitor-clock"
    ensure_state_directory(root)
    receipt = root / (kind + ".json")
    previous = json.loads(receipt.read_text()) if receipt.exists() else {}
    if previous.get("slot") == slot and previous.get("status") == "accepted":
        return
    if previous and previous.get("slot") != slot and previous.get("status") != "accepted":
        # Retain failed/uncertain slots instead of erasing them at the next tick.
        history = root / "history"
        ensure_state_directory(history)
        archived = history / (kind + "-" + previous["slot"].replace(":", "") + ".json")
        with archived.open("w", encoding="utf-8") as stream:
            stream.write(json.dumps(previous) + "\n")
            stream.flush()
            os.fsync(stream.fileno())
        sync_directory(history)
    def record(status):
        tmp = receipt.with_suffix(".tmp")
        with tmp.open("w", encoding="utf-8") as stream:
            stream.write(json.dumps({"slot": slot, "status": status}) + "\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(tmp, receipt)
        # The submitting fence must survive host failure before HTTP is sent.
        sync_directory(root)
    if previous.get("slot") == slot and previous.get("status") != "retryable":
        # An interrupted request can be accepted before the response reaches us.
        output = subprocess.check_output(["gh", "run", "list", "-R", REPO, "--workflow", WORKFLOWS[kind]+".yml", "--event", "repository_dispatch", "--limit", "20", "--json", "displayTitle"], timeout=25)
        if any(r["displayTitle"].endswith(" — " + slot) for r in json.loads(output)):
            record("accepted")
            return
        raise SystemExit("Uncertain submission for " + slot + ". Inspect GitHub before replay.")
    for attempt in range(1, 4):
        record("submitting")
        try:
            subprocess.run(
                ["gh", "api", "--method", "POST", "repos/"+REPO+"/dispatches", "--input", "-"],
                input=json.dumps({"event_type": "poststeward-monitor-"+kind, "client_payload": {"slot": slot}}),
                text=True, check=True, timeout=35, capture_output=True,
            )
        except subprocess.CalledProcessError as error:
            # Only this exact pre-HTTP failure proves the POST was not sent.
            # HTTP errors, EOF and request timeouts retain the uncertain fence.
            expected = 'Post "https://api.github.com/repos/'+REPO+'/dispatches": net/http: TLS handshake timeout'
            if (error.stderr or "").strip() != expected:
                raise SystemExit("Uncertain submission for " + slot + ". Inspect GitHub before replay.") from None
            record("retryable")
            print(kind, slot, "pre_http_tls_timeout", "attempt", attempt, flush=True)
            if attempt == 3:
                raise SystemExit("Dispatch transport retries exhausted for " + slot) from None
            time.sleep(attempt)
            continue
        except subprocess.TimeoutExpired:
            raise
        record("accepted")
        print(kind, slot, "accepted")
        return


if __name__ == "__main__":
    dispatch(sys.argv[1])
