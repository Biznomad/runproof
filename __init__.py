"""Verify Hermes cron output and release it to a local outbox."""
import hashlib
import json
import os
import stat
import sqlite3
from contextlib import closing
from datetime import datetime
from pathlib import Path

MAX_BYTES = 1_048_576


def timestamp(value):
    return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()


def verify(run, path, fields, expected_true=()):
    receipt = {"report": str(path), "required": fields, "expected_true": expected_true}

    def fail(reason, **details):
        return dict(receipt, verdict="FAIL", reason=reason, **details), None

    if run is None:
        return fail("no_execution")
    receipt.update(execution=run["id"], job=run["job_id"], cron_status=run["status"])
    if run["status"] != "completed":
        return fail("cron_not_completed")

    start, finish = run.get("started_at"), run.get("finished_at")
    if not start or not finish:
        return fail("missing_execution_window")
    scheduled = run.get("scheduled_instant")
    receipt.update(scheduled_at=scheduled, started_at=start, finished_at=finish)
    try:
        if not stat.S_ISREG(path.stat().st_mode):
            return fail("not_regular_file")
        fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK)
        with os.fdopen(fd, "rb") as report:
            before = os.fstat(report.fileno())
            if not stat.S_ISREG(before.st_mode):
                return fail("not_regular_file")
            content = report.read(MAX_BYTES + 1)
            after = os.fstat(report.fileno())
        current = path.stat()
    except FileNotFoundError:
        return fail("missing_report")
    except OSError:
        return fail("unreadable_report")

    def signature(info):
        return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns)

    if signature(before) != signature(after) or signature(after) != signature(current):
        return fail("report_changed_during_check")
    receipt["mtime_epoch"] = after.st_mtime
    if after.st_mtime < max(timestamp(start), timestamp(scheduled or start)):
        return fail("stale_report")
    if after.st_mtime > timestamp(finish):
        return fail("written_after_execution")
    if len(content) > MAX_BYTES:
        return fail("report_too_large")

    def invalid_constant(value):
        raise ValueError(value)

    try:
        data = json.loads(content, parse_constant=invalid_constant)
    except (ValueError, UnicodeError, RecursionError):
        return fail("invalid_json")
    if not isinstance(data, dict):
        return fail("expected_json_object")
    empty = [field for field in fields if data.get(field) in (None, [], {})
             or isinstance(data[field], str) and not data[field].strip()]
    if empty:
        return fail("unpopulated_fields", fields=empty)
    false_fields = [field for field in expected_true if data.get(field) is not True]
    if false_fields:
        return fail("expected_true", fields=false_fields)
    return dict(receipt, verdict="PASS", reason="verified",
                sha256=hashlib.sha256(content).hexdigest()), content


ACTIONS = {
    "no_execution": "Check the job ID and wait for a producer execution.",
    "execution_job_mismatch": "Use an execution belonging to the configured job.",
    "cron_not_completed": "Inspect Hermes cron history, then rerun the producer.",
    "missing_execution_window": "Inspect Hermes cron history for a complete run record.",
    "stale_report": "Rerun the producer so it writes a new report during its execution.",
    "written_after_execution": "Use a dedicated report path and rerun the producer.",
    "expected_true": "Resolve the failing upstream checks, then rerun the producer.",
    "release_key_policy_mismatch": "Reuse the original report path and field policy or use a new slot key.",
}


def release(ledger, key, receipt, content):
    """Commit one payload per logical slot and a receipt for every gate attempt."""
    policy = json.dumps([receipt["report"], sorted(set(receipt["required"])),
                         sorted(set(receipt["expected_true"]))])
    # Restrict permissions for newly created databases. Use an operator-owned directory.
    fd = os.open(ledger, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    os.close(fd)
    with closing(sqlite3.connect(ledger, timeout=10)) as db, db:
        db.execute("BEGIN IMMEDIATE")
        db.execute("""CREATE TABLE IF NOT EXISTS outbox (
            release_key TEXT PRIMARY KEY, policy TEXT NOT NULL, execution TEXT NOT NULL,
            sha256 TEXT NOT NULL, payload BLOB NOT NULL)""")
        db.execute("""CREATE TABLE IF NOT EXISTS receipts (
            id INTEGER PRIMARY KEY, release_key TEXT NOT NULL, receipt TEXT NOT NULL)""")
        existing = db.execute(
            "SELECT policy, execution, sha256 FROM outbox WHERE release_key = ?", (key,)
        ).fetchone()
        result = dict(receipt, release_key=key, gate="blocked")
        if existing and existing[0] != policy:
            result.update(verdict="FAIL", reason="release_key_policy_mismatch")
        elif receipt["verdict"] == "PASS":
            if existing:
                result.update(gate="replay_skipped", released_execution=existing[1],
                              released_sha256=existing[2])
            else:
                db.execute("INSERT INTO outbox VALUES (?, ?, ?, ?, ?)",
                           (key, policy, receipt["execution"], receipt["sha256"], content))
                result.update(gate="released", released_execution=receipt["execution"],
                              released_sha256=receipt["sha256"])
        if result["gate"] == "blocked":
            result["action"] = ACTIONS.get(
                result["reason"], "Fix the report path or content and rerun the producer.")
        db.execute("INSERT INTO receipts (release_key, receipt) VALUES (?, ?)",
                   (key, json.dumps(result, sort_keys=True)))
    return result


def setup(parser):
    parser.add_argument("job", help="Exact Hermes cron job ID")
    parser.add_argument("report", type=Path, help="JSON report path")
    parser.add_argument("--require", action="append", required=True,
                        metavar="FIELD", help="Required top-level field (repeatable)")
    parser.add_argument("--expect-true", action="append", default=[], metavar="FIELD",
                        help="Top-level field that must be JSON true (repeatable)")
    parser.add_argument("--execution", help="Pin an exact Hermes execution instead of latest")
    parser.add_argument("--ledger", type=Path, help="SQLite receipt and release outbox path")
    parser.add_argument("--key", help="Stable logical slot key, shared across producer retries")


def check(args):
    from cron.executions import get_execution, latest_execution

    if bool(args.ledger) != bool(args.key):
        raise SystemExit("--ledger and --key must be supplied together")
    report = args.report.resolve()
    if args.ledger and args.ledger.resolve() == report:
        raise SystemExit("Use separate report and ledger paths")
    run = get_execution(args.execution) if args.execution else latest_execution(args.job)
    receipt, content = verify(run, report, args.require, args.expect_true)
    if run and run["job_id"] != args.job:
        receipt.update(verdict="FAIL", reason="execution_job_mismatch")
        content = None
    if args.ledger:
        try:
            receipt = release(args.ledger, args.key, receipt, content)
        except (OSError, sqlite3.Error):
            receipt = dict(receipt, verdict="FAIL", gate="unknown", reason="ledger_unavailable",
                           action="Check ledger permissions or contention and retry the same key.")
    print(json.dumps(receipt, sort_keys=True))
    raise SystemExit(0 if receipt["verdict"] == "PASS" else 1)


def register(ctx):
    ctx.register_cli_command("runproof", "Verify and release a Hermes cron report", setup, check)
