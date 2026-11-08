#!/usr/bin/env python3
"""Drive the real Hermes scheduler in a disposable, resumable home."""
import argparse
import fcntl
import json
import os
import re
import shutil
import subprocess
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

SOURCE = Path(__file__).resolve().parent
SCENARIOS = [("stale", "stale_report"), ("incomplete", "unpopulated_fields"),
             ("good", "verified"), ("good", "verified")]


def save(path, data):
    pending = path.with_suffix(".tmp")
    pending.write_text(json.dumps(data, indent=2) + "\n")
    pending.replace(path)


def run(root):
    deadline = time.monotonic() + 120
    home = root / "hermes"
    plugin = home / "plugins/runproof"
    scripts = home / "scripts"
    checkpoint = root / "checkpoint.json"
    state = json.loads(checkpoint.read_text()) if checkpoint.exists() else {
        "next": 0, "attempts": 0, "job": None}
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
    report = scripts / "report.json"
    hermes("plugins", "enable", "runproof")
    hermes("runproof", "--help")
    print(f"Registered RunProof in {home}", flush=True)
    for _ in range(8):
        index = state["next"]
        if index == len(SCENARIOS):
            print(f"Verified all {index} scenarios. Receipts: {root}", flush=True)
            return
        if state["attempts"] >= 2:
            raise RuntimeError(f"No progress at scenario {index + 1}. Inspect {checkpoint}")
        state["attempts"] += 1
        save(checkpoint, state)
        mode, reason = SCENARIOS[index]
        print(f"[{index + 1}/{len(SCENARIOS)}] Running {mode} producer", flush=True)
        try:
            (scripts / "mode").write_text(mode)
            if mode == "stale":
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
            receipt = json.loads(hermes("runproof", job, report, "--require", "report_id",
                                        "--require", "orders", "--require", "summary",
                                        expected=int(reason != "verified")))
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
            save(root / f"receipt-{index + 1}.json", receipt)
            state.update(next=index + 1, attempts=0, job=None)
            save(checkpoint, state)
            print(f"  Hermes completed · RunProof {receipt['verdict']} · {reason}", flush=True)
        except (RuntimeError, subprocess.TimeoutExpired, ValueError) as error:
            print(f"  Attempt {state['attempts']} stopped: {error}", flush=True)
    raise RuntimeError("Scenario attempt budget reached")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state-dir", type=Path, default=SOURCE / ".demo/local")
    root = parser.parse_args().state_dir.resolve()
    root.mkdir(parents=True, exist_ok=True)
    with (root / ".lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        run(root)
