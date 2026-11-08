"""RunProof's read-only Hermes CLI extension."""
import hashlib
import json
import os
import stat
from datetime import datetime
from pathlib import Path

MAX_BYTES = 1_048_576


def timestamp(value):
    return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()


def verify(run, path, fields):
    receipt = {"report": str(path), "required": fields}
    if run is None:
        return dict(receipt, verdict="FAIL", reason="no_execution")
    receipt.update(execution=run["id"], job=run["job_id"], cron_status=run["status"])
    if run["status"] != "completed":
        return dict(receipt, verdict="FAIL", reason="cron_not_completed")

    start, finish = run.get("started_at"), run.get("finished_at")
    if not start or not finish:
        return dict(receipt, verdict="FAIL", reason="missing_execution_window")
    scheduled = run.get("scheduled_instant")
    receipt.update(scheduled_at=scheduled, started_at=start, finished_at=finish)
    try:
        if not stat.S_ISREG(path.stat().st_mode):
            return dict(receipt, verdict="FAIL", reason="not_regular_file")
        fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK)
        with os.fdopen(fd, "rb") as report:
            before = os.fstat(report.fileno())
            if not stat.S_ISREG(before.st_mode):
                return dict(receipt, verdict="FAIL", reason="not_regular_file")
            content = report.read(MAX_BYTES + 1)
            after = os.fstat(report.fileno())
        current = path.stat()
    except FileNotFoundError:
        return dict(receipt, verdict="FAIL", reason="missing_report")
    except OSError:
        return dict(receipt, verdict="FAIL", reason="unreadable_report")

    def signature(info):
        return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns)

    if signature(before) != signature(after) or signature(after) != signature(current):
        return dict(receipt, verdict="FAIL", reason="report_changed_during_check")
    receipt["mtime_epoch"] = after.st_mtime
    if after.st_mtime < max(timestamp(start), timestamp(scheduled or start)):
        return dict(receipt, verdict="FAIL", reason="stale_report")
    if after.st_mtime > timestamp(finish):
        return dict(receipt, verdict="FAIL", reason="written_after_execution")
    if len(content) > MAX_BYTES:
        return dict(receipt, verdict="FAIL", reason="report_too_large")

    def invalid_constant(value):
        raise ValueError(value)

    try:
        data = json.loads(content, parse_constant=invalid_constant)
    except (ValueError, UnicodeError, RecursionError):
        return dict(receipt, verdict="FAIL", reason="invalid_json")
    if not isinstance(data, dict):
        return dict(receipt, verdict="FAIL", reason="expected_json_object")
    empty = [field for field in fields if data.get(field) in (None, [], {})
             or isinstance(data[field], str) and not data[field].strip()]
    if empty:
        return dict(receipt, verdict="FAIL", reason="unpopulated_fields", fields=empty)
    return dict(receipt, verdict="PASS", reason="verified",
                sha256=hashlib.sha256(content).hexdigest())


def setup(parser):
    parser.add_argument("job", help="Exact Hermes cron job ID")
    parser.add_argument("report", type=Path, help="JSON report path")
    parser.add_argument("--require", action="append", required=True,
                        metavar="FIELD", help="Required top-level field (repeatable)")


def check(args):
    from cron.executions import latest_execution

    receipt = verify(latest_execution(args.job), args.report.resolve(), args.require)
    print(json.dumps(receipt, sort_keys=True))
    raise SystemExit(0 if receipt["verdict"] == "PASS" else 1)


def register(ctx):
    ctx.register_cli_command("runproof", "Verify a Hermes cron report", setup, check)
