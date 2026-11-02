import os
import sqlite3
import json
from pathlib import Path

os.environ["L1_DATA_DIR"] = str(Path(r"d:\钢铁诊断\steel-diagnosis-agent-main\data\runtime\l1"))
from app.services.l1_store import data_dir

c = sqlite3.connect(str(Path(data_dir()) / "l1.sqlite3"))
c.row_factory = sqlite3.Row
r = c.execute(
    "SELECT features_json, quality_json, source_row_id, equipment_id FROM prepared_feature_rows WHERE version_id=? LIMIT 1",
    ("PD-4c2a075ce5f8",),
).fetchone()
f = json.loads(r["features_json"])
print("context", f.get("context"))
print("source_row_id", r["source_row_id"])
print("equipment_id", r["equipment_id"])
print("fault", json.loads(r["quality_json"])["labels"]["fault_type"])
