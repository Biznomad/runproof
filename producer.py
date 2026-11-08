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
    captured = root / f"capture-{mode}.json"
    if captured.exists():
        pending.write_bytes(captured.read_bytes())
    else:
        pending.write_text(json.dumps(data))
    os.replace(pending, root / "report.json")
print("Producer completed")
