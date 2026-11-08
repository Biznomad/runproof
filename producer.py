"""Synthetic cron worker used by the demo."""
import json
import os
from pathlib import Path

root = Path(__file__).resolve().parent
mode = (root / "mode").read_text().strip()
if mode != "stale":
    data = {"report_id": "demo-001", "orders": 0}
    if mode == "good":
        data["summary"] = "Synthetic daily report"
    pending = root / "report.tmp"
    pending.write_text(json.dumps(data))
    os.replace(pending, root / "report.json")
print("Producer completed")
