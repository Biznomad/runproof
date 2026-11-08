"""Regression checks for verification and the atomic release boundary."""
import hashlib
import multiprocessing
import os
import sqlite3
import tempfile
import time
import unittest
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

from __init__ import release, verify


def instant(seconds):
    return datetime.fromtimestamp(seconds, timezone.utc).isoformat()


def race(ledger, key, receipt, payload, start, results):
    if start.wait(10):
        results.put(release(ledger, key, receipt, payload)["gate"])


class GateTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.report = Path(directory.name) / "report.json"
        self.ledger = Path(directory.name) / "gate.sqlite"
        now = time.time()
        self.run_record = {"id": "execution-1", "job_id": "job-1", "status": "completed",
                           "started_at": instant(now - 10), "finished_at": instant(now + 10)}

    def check_report(self):
        return verify(self.run_record, self.report, ["orders"], ["passed"])

    def good_report(self):
        self.report.write_bytes(b'{"orders":0,"passed":true}')
        return self.check_report()

    def test_failure_reasons_and_corrected_release(self):
        old = time.time() - 100
        for payload, reason in [(None, "missing_report"), (b'{', "invalid_json"),
                                (b'{"orders":0,"passed":false}', "expected_true"),
                                (b'{"orders":0,"passed":true}', "stale_report")]:
            with self.subTest(reason=reason):
                if payload is not None:
                    self.report.write_bytes(payload)
                if reason == "stale_report":
                    os.utime(self.report, (old, old))
                receipt, data = self.check_report()
                blocked = release(self.ledger, "daily", receipt, data)
                self.assertEqual((blocked["gate"], blocked["reason"]), ("blocked", reason))
                self.assertTrue(blocked["action"])
        receipt, payload = self.good_report()
        self.assertEqual(release(self.ledger, "daily", receipt, payload)["gate"], "released")
        with closing(sqlite3.connect(self.ledger)) as db:
            self.assertEqual(db.execute("SELECT count(*) FROM receipts").fetchone()[0], 5)
            self.assertEqual(db.execute("SELECT payload FROM outbox").fetchone()[0], payload)

    def test_concurrent_retries_and_policy_conflict(self):
        receipt, payload = self.good_report()
        ctx = multiprocessing.get_context("spawn")
        start, results = ctx.Event(), ctx.Queue()
        workers = [ctx.Process(target=race,
                               args=(self.ledger, "daily", receipt, payload, start, results))
                   for _ in range(4)]
        try:
            for worker in workers:
                worker.start()
            start.set()
            decisions = [results.get(timeout=15) for _ in workers]
            self.assertCountEqual(decisions, ["released"] + ["replay_skipped"] * 3)
        finally:
            for worker in workers:
                if worker.is_alive():
                    worker.terminate()
                if worker.pid is not None:
                    worker.join(5)
        conflict = release(self.ledger, "daily", dict(receipt, required=["different"]), payload)
        self.assertEqual(conflict["reason"], "release_key_policy_mismatch")
        with closing(sqlite3.connect(self.ledger)) as db:
            self.assertEqual(db.execute("SELECT sha256, payload FROM outbox").fetchall(),
                             [(hashlib.sha256(payload).hexdigest(), payload)])

    def test_receipt_write_failure_rolls_back_release(self):
        receipt, payload = self.good_report()
        release(self.ledger, "existing", receipt, payload)
        with closing(sqlite3.connect(self.ledger)) as db, db:
            db.execute("""CREATE TRIGGER reject_receipt BEFORE INSERT ON receipts
                       BEGIN SELECT RAISE(ABORT, 'test'); END""")
        with self.assertRaises(sqlite3.IntegrityError):
            release(self.ledger, "retry", receipt, payload)
        with closing(sqlite3.connect(self.ledger)) as db:
            self.assertIsNone(db.execute("SELECT 1 FROM outbox WHERE release_key = 'retry'").fetchone())
            db.execute("DROP TRIGGER reject_receipt")
        self.assertEqual(release(self.ledger, "retry", receipt, payload)["gate"], "released")


if __name__ == "__main__":
    unittest.main()
