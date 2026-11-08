#!/usr/bin/env python3
"""Drive the real Hermes scheduler in a disposable, resumable home."""
import argparse
import fcntl
import hashlib
import json
import os
import re
import shutil
import sqlite3
import subprocess
import time
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path

SOURCE = Path(__file__).resolve().parent
SCENARIOS = [("stale", "stale_report"), ("incomplete", "unpopulated_fields"),
             ("good", "verified"), ("good", "verified")]


def save(path, data):
    pending = path.with_suffix(".tmp")
    pending.write_text(json.dumps(data, indent=2) + "\n")
    pending.replace(path)


def run(root, captures):
    deadline = time.monotonic() + 120
    home = root / "hermes"
    plugin = home / "plugins/runproof"
    scripts = home / "scripts"
    checkpoint = root / "checkpoint.json"
    state = json.loads(checkpoint.read_text()) if checkpoint.exists() else {
        "next": 0, "attempts": 0, "job": None}
    ledger = root / "gate.sqlite"
    scenarios = SCENARIOS
    fields = ["--require", "report_id", "--require", "orders", "--require", "summary"]
    capture_hashes = [hashlib.sha256(content).hexdigest() for content in captures]
    if state.get("captures", capture_hashes) != capture_hashes:
        raise RuntimeError("Use a fresh state directory when changing replay inputs")
    state["captures"] = capture_hashes
    if captures:
        scenarios = [("stale", "stale_report"), ("incomplete", "expected_true"),
                     ("good", "verified"), ("good", "verified")]
        fields = ["--require", "checks", "--expect-true", "checks_ok"]
    binary = shutil.which("hermes")
    if not binary:
        raise RuntimeError("hermes is required on PATH")
    env = {key: os.environ[key] for key in ("PATH", "LANG", "TMPDIR") if key in os.environ}
    env.update(HOME=str(root), HERMES_HOME=str(home), HERMES_CRON_TIMEOUT="10",
               HERMES_DISABLE_LAZY_INSTALLS="1")

    def hermes(*args, expected=0, missing_job_ok=False):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise RuntimeError("120-second budget reached")
        process = subprocess.run([binary, *map(str, args)], env=env, cwd=root,
                                 capture_output=True, text=True, timeout=min(remaining, 20))
        if (missing_job_ok and process.returncode == 1
                and "Failed to remove job:" in process.stdout
                and "not found" in process.stdout):
            return process.stdout
        if process.returncode != expected:
            raise RuntimeError(f"{args}: exit {process.returncode}\n"
                               f"{process.stdout}{process.stderr}")
        return process.stdout

    plugin.mkdir(parents=True, exist_ok=True)
    scripts.mkdir(parents=True, exist_ok=True)
    for name in ("plugin.yaml", "__init__.py"):
        shutil.copyfile(SOURCE / name, plugin / name)
    shutil.copyfile(SOURCE / "producer.py", scripts / "producer.py")
    for mode, content in zip(("incomplete", "good"), captures):
        (scripts / f"capture-{mode}.json").write_bytes(content)
    report = scripts / "report.json"

    def check_gate(job, expected):
        return json.loads(hermes("runproof", job, report, *fields, "--ledger", ledger,
                                 "--key", "report:demo-slot", expected=expected))

    def inspect_outbox(receipt=None):
        with closing(sqlite3.connect(ledger)) as db:
            rows = db.execute("SELECT execution, sha256, payload FROM outbox").fetchall()
            releases = db.execute("SELECT receipt FROM receipts").fetchall()
        committed = [json.loads(row[0]) for row in releases
                     if json.loads(row[0])["gate"] == "released"]
        if receipt is None:
            if rows or committed:
                raise RuntimeError("Blocked evidence created a release")
            return
        if len(rows) != 1 or len(committed) != 1:
            raise RuntimeError("Expected one committed release")
        execution, digest, content = rows[0]
        if (execution != receipt["released_execution"] or digest != receipt["released_sha256"]
                or hashlib.sha256(content).hexdigest() != digest
                or captures and content != captures[1]):
            raise RuntimeError("Outbox payload does not match the verified report")

    hermes("plugins", "enable", "runproof")
    hermes("runproof", "--help")
    print(f"Registered RunProof in {home}", flush=True)
    for _ in range(8):
        index = state["next"]
        if index == len(scenarios):
            print(f"Verified all {index} scenarios. Receipts: {root}", flush=True)
            return
        if state["attempts"] >= 2:
            raise RuntimeError(f"No progress at scenario {index + 1}. Inspect {checkpoint}")
        state["attempts"] += 1
        save(checkpoint, state)
        mode, reason = scenarios[index]
        print(f"[{index + 1}/{len(scenarios)}] Running {mode} producer", flush=True)
        try:
            (scripts / "mode").write_text(mode)
            if mode == "stale":
                if captures:
                    report.write_bytes(captures[1])
                else:
                    report.write_text('{"report_id":"old","orders":0,"summary":"Old report"}')
                old = time.time() - 3600
                os.utime(report, (old, old))
            if state["job"]:
                hermes("cron", "remove", state["job"], missing_job_ok=True)
            due = datetime.now(timezone.utc) + timedelta(seconds=3)
            output = hermes("cron", "create", due.isoformat(), "--name", "RunProof report",
                            "--no-agent", "--script", "producer.py", "--deliver", "local")
            match = re.search(r"Created job: ([a-zA-Z0-9-]+)", output)
            if not match:
                raise RuntimeError(f"Hermes did not return a job ID: {output}")
            job = state["job"] = match[1]
            save(checkpoint, state)
            time.sleep(max(0, due.timestamp() - time.time()) + 0.1)
            hermes("cron", "tick")
            receipt = check_gate(job, int(reason != "verified"))
            expected_gate = ("blocked", "blocked", "released", "replay_skipped")[index]
            if receipt["gate"] != expected_gate:
                # A crash after the atomic release can leave the checkpoint one step behind.
                if index != 2 or receipt["gate"] != "replay_skipped":
                    raise RuntimeError(f"Unexpected gate decision: {receipt}")
                with closing(sqlite3.connect(ledger)) as db:
                    rows = db.execute("SELECT receipt FROM receipts ORDER BY id").fetchall()
                receipt = next(json.loads(row[0]) for row in rows
                               if json.loads(row[0])["gate"] == "released")
            inspect_outbox(receipt if index >= 2 else None)
            if receipt.get("cron_status") != "completed" or receipt["reason"] != reason:
                raise RuntimeError(f"Unexpected receipt: {receipt}")
            if not receipt.get("scheduled_at"):
                raise RuntimeError(f"Expected a scheduled occurrence: {receipt}")
            if index:
                prior = json.loads((root / f"receipt-{index}.json").read_text())
                if receipt["execution"] == prior["execution"]:
                    raise RuntimeError("Expected a new Hermes execution")
                if index == 3 and receipt["sha256"] != prior["sha256"]:
                    raise RuntimeError("Corrected report was not reproducible")
            if index == 2:
                replay = check_gate(job, 0)
                if replay["gate"] != "replay_skipped":
                    raise RuntimeError(f"Exact execution replay failed: {replay}")
                inspect_outbox(replay)
                save(root / "replay.json", replay)
            save(root / f"receipt-{index + 1}.json", receipt)
            state.update(next=index + 1, attempts=0, job=None)
            save(checkpoint, state)
            print(f"  Hermes completed · RunProof {receipt['gate']} · {reason}", flush=True)
        except (RuntimeError, subprocess.TimeoutExpired, ValueError) as error:
            print(f"  Attempt {state['attempts']} stopped: {error}", flush=True)
    raise RuntimeError("Scenario attempt budget reached")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state-dir", type=Path, default=SOURCE / ".demo/gate")
    parser.add_argument("--replay", nargs=2, type=Path, metavar=("FAILED_JSON", "PASSED_JSON"),
                        help="Replay two captured checks/checks_ok reports without editing inputs")
    args = parser.parse_args()
    if args.replay and any(path.resolve().is_relative_to(args.state_dir.resolve())
                           for path in args.replay):
        parser.error("Store captured inputs outside the demo state directory")
    captures = [path.read_bytes() for path in args.replay or []]
    root = args.state_dir.resolve()
    root.mkdir(parents=True, exist_ok=True)
    with (root / ".lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        run(root, captures)
