"""P2 equipment_grouping_v2 基础验收：自动执行、跨表隔离、摘要、幂等。"""

from __future__ import annotations

import io
import os
import sys
import tempfile
from pathlib import Path

from openpyxl import Workbook

ROOT = Path(__file__).resolve().parents[1]
TMP = Path(tempfile.mkdtemp(prefix="l1-p2-v2-"))
os.environ["L1_DATA_DIR"] = str(TMP / "data")
os.environ["L1_DATABASE"] = str(TMP / "data" / "l1.sqlite3")
os.environ["L1_MAX_UPLOAD_BYTES"] = str(32 * 1024 * 1024)
os.environ["STARTUP_TOOL_SELFTEST"] = "0"
sys.path.insert(0, str(ROOT / "backend"))

from fastapi.testclient import TestClient

from app.main import app
from app.services.l1_grouping_detect import select_equipment_field
from app.services.l1_grouping_detect import FieldScore

RESULTS: list[tuple[str, bool, str]] = []


def check(name: str, passed: bool, detail: str = "") -> None:
    RESULTS.append((name, passed, detail))
    print(("PASS " if passed else "FAIL ") + name + ("" if not detail else " | " + detail), flush=True)


def workbook() -> bytes:
    book = Workbook()
    sheet = book.active
    sheet.title = "振动"
    sheet.append(["设备编号", "振动"])
    sheet.append(["MM-01", 1])
    other = book.create_sheet("温度")
    other.append(["温度", "设备ID"])
    other.append([40, "MM-01"])
    buffer = io.BytesIO()
    book.save(buffer)
    book.close()
    return buffer.getvalue()


def main() -> None:
    # 纯函数：模糊唯一候选可自动选定
    scores = [
        FieldScore(1, "c1", "设备编码", "fuzzy", "设备编码", 0.72, "medium", "header_fuzzy_validated", [], {}),
        FieldScore(2, "c2", "振动", "none", "", 0.1, "none", "value_stats", ["looks_like_measurement"], {}),
    ]
    decision = select_equipment_field(scores)
    check("模糊唯一候选自动选定", decision["selected_field"] == "c1" and decision["requires_review"] is False)

    conflict = [
        FieldScore(1, "c1", "设备编号", "exact", "设备编号", 0.9, "high", "header_exact", [], {}),
        FieldScore(2, "c2", "设备ID", "exact", "设备ID", 0.88, "high", "header_exact", [], {}),
    ]
    conflicted = select_equipment_field(conflict)
    check("双 exact 冲突待审核", conflicted["requires_review"] is True and conflicted["selected_field"] is None)

    with TestClient(app) as client:
        uploaded = client.post(
            "/api/l1/datasets",
            params={"filename": "auto.xlsx"},
            content=workbook(),
            headers={"content-type": "application/octet-stream"},
        )
        dataset_id = uploaded.json()["dataset_id"]
        detail = client.get(f"/api/l1/datasets/{dataset_id}").json()
        groups = client.get(f"/api/l1/datasets/{dataset_id}/equipment-groups").json()
        summary = client.get(f"/api/l1/datasets/{dataset_id}/grouping/summary").json()
        check(
            "上传后自动完成 P2",
            uploaded.status_code == 201
            and groups["applied"] is True
            and groups["run"]["rule_id"] == "equipment_grouping_v2"
            and groups["status"] in {"completed", "completed_with_warnings"}
            and groups["balance"]["equipment_count"] == 2
            and groups["cross_sheet_decision_required"] is False
            and summary["summary"]["equipment_count"] == 2
            and any(item.get("code") == "potential_cross_sheet_match" for item in (groups.get("warnings") or [])),
            str(groups.get("status")),
        )
        again = client.post(f"/api/l1/datasets/{dataset_id}/grouping/apply", json={"source": "auto", "use_suggestions": True}).json()
        check("相同签名幂等复用", again.get("reused") is True and again["run"]["run_id"] == groups["run"]["run_id"])
        detail2 = client.get(f"/api/l1/datasets/{dataset_id}").json()
        p2 = next(item for item in detail2["stages"] if item.get("stage_id") == "p2")
        check("流水线展示 P2 状态", p2["status"] in {"completed", "completed_with_warnings"}, p2["status"])

    failed = [item for item in RESULTS if not item[1]]
    print(f"\n{len(RESULTS) - len(failed)} passed, {len(failed)} failed")
    if failed:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
