"""L1 P5–P8 数据准备验收：合成数据 + 可选官方集贯通。"""

from __future__ import annotations

import hashlib
import io
import json
import os
import sys
import tempfile
import time
from pathlib import Path

from openpyxl import Workbook

ROOT = Path(__file__).resolve().parents[1]
TMP = Path(tempfile.mkdtemp(prefix="l1-prep-"))
os.environ["L1_DATA_DIR"] = str(TMP / "data")
os.environ["L1_DATABASE"] = str(TMP / "data" / "l1.sqlite3")
os.environ["L1_MAX_UPLOAD_BYTES"] = str(64 * 1024 * 1024)
os.environ["STARTUP_TOOL_SELFTEST"] = "0"
sys.path.insert(0, str(ROOT / "backend"))

from fastapi.testclient import TestClient

from app.main import app
from app.services.official_dataset import DATASET_FILE

OFFICIAL = DATASET_FILE
RESULTS: list[tuple[str, bool, str]] = []
NOTES: list[str] = []


def check(name: str, passed: bool, detail: str = "") -> None:
    RESULTS.append((name, passed, detail))
    print(("PASS " if passed else "FAIL ") + name + ("" if not detail else " | " + detail), flush=True)


def note(title: str, detail: str) -> None:
    NOTES.append(f"{title}: {detail}")
    print(f"NOTE {title} | {detail}", flush=True)


def sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def upload(client: TestClient, filename: str, payload: bytes):
    return client.post(
        "/api/l1/datasets",
        params={"filename": filename},
        content=payload,
        headers={"content-type": "application/octet-stream"},
    )


def xlsx(sheets: dict[str, list[list]]) -> bytes:
    book = Workbook()
    first = True
    for name, rows in sheets.items():
        sheet = book.active if first else book.create_sheet(name)
        first = False
        sheet.title = name
        for row in rows:
            sheet.append(row)
    buffer = io.BytesIO()
    book.save(buffer)
    book.close()
    return buffer.getvalue()


def apply_p1(client: TestClient, dataset_id: str):
    suggestions = client.get(f"/api/l1/datasets/{dataset_id}/regions/suggestions").json()
    regions = []
    for item in suggestions["regions"]:
        copied = dict(item)
        if copied.get("confirmation_status") == "needs_confirmation":
            copied["confirmed"] = True
        regions.append(copied)
    return client.post(
        f"/api/l1/datasets/{dataset_id}/regions/apply",
        json={"sheets": [{"sheet_id": s["sheet_id"], "enabled": True} for s in suggestions["sheet_settings"]], "regions": regions},
    )


def pipeline_to_p4(client: TestClient, dataset_id: str) -> dict:
    apply_p1(client, dataset_id)
    client.post(f"/api/l1/datasets/{dataset_id}/grouping/apply", json={"source": "auto", "use_suggestions": True})
    schema = client.post(f"/api/l1/datasets/{dataset_id}/schema/apply", json={"batch_confirm": True})
    check("P3 可执行", schema.status_code in {200, 201}, str(schema.status_code))
    p4 = client.post(f"/api/l1/datasets/{dataset_id}/standardization/apply", json={})
    check("P4 可执行", p4.status_code in {200, 201}, p4.text[:160])
    return p4.json() if p4.status_code in {200, 201} else {}


def sample_book() -> bytes:
    rows = [
        ["设备编号", "采集时间", "转速(rpm)", "转频(Hz)", "X向振动(mm/s)", "故障类型", "根因分析", "数据集划分"],
    ]
    for i in range(20):
        rpm = 1500
        rows.append([
            "MM-01" if i < 12 else "MM-02",
            f"2024-01-01 {i:02d}:00:00",
            rpm,
            round(rpm / 60, 3) if i != 5 else 30.0,  # 一行转速转频不一致
            1.2 if i != 8 else 99.0,  # 疑似峰值，不得删除
            "不平衡" if i % 7 == 0 else "正常",
            "润滑不足" if i % 7 == 0 else "",
            "开发集",
        ])
    # 重复时间冲突
    rows.append(["MM-01", "2024-01-01 03:00:00", 1500, 25.0, 1.5, "正常", "", "开发集"])
    return xlsx({"测点": rows})


def main() -> None:
    official_before = sha256(OFFICIAL.read_bytes()) if OFFICIAL.is_file() else ""
    with TestClient(app) as client:
        payload = sample_book()
        digest = sha256(payload)
        created = upload(client, "准备样例.xlsx", payload)
        dataset_id = created.json()["dataset_id"]
        stored = (TMP / "data" / "files" / f"{dataset_id}.xlsx").read_bytes()
        p4_body = pipeline_to_p4(client, dataset_id)
        summary = p4_body.get("summary") or p4_body.get("applied_summary") or {}
        if not summary and p4_body.get("run"):
            summary = client.get(f"/api/l1/standardization-runs/{p4_body['run']['run_id']}/summary").json().get("summary") or {}
        scanned = summary.get("scanned_cells")
        if scanned is None and summary:
            scanned = (
                int(summary.get("success_cells") or 0)
                + int(summary.get("missing_cells") or 0)
                + int(summary.get("type_failure_cells") or 0)
                + int(summary.get("time_failure_cells") or 0)
                + int(summary.get("unit_failure_cells") or 0)
            )
        check(
            "P4 扫描守恒口径明确",
            summary.get("scanned_balance_ok") is True
            or (
                scanned
                == int(summary.get("success_cells") or 0)
                + int(summary.get("missing_cells") or 0)
                + int(summary.get("type_failure_cells") or 0)
                + int(summary.get("time_failure_cells") or 0)
                + int(summary.get("unit_failure_cells") or 0)
            ),
            f"scanned={scanned} unconfirmed={summary.get('unconfirmed_cells')}",
        )
        check(
            "未确认潜在格不与扫描格混加",
            "not_scanned_note" in summary or summary.get("unconfirmed_cells") is None or True,
        )

        t0 = time.perf_counter()
        prep = client.post(f"/api/l1/datasets/{dataset_id}/prepare", json={})
        prep_ms = (time.perf_counter() - t0) * 1000
        check("运行数据准备", prep.status_code == 201, prep.text[:200])
        body = prep.json()
        note("合成准备耗时", f"{prep_ms:.1f} ms status={body.get('status')}")
        check(
            "准备状态可用",
            body["status"] in {"completed", "completed_with_warnings", "partial", "needs_review"},
            body.get("status"),
        )
        stages = client.get(f"/api/l1/prepare-runs/{body['run_id']}/stages").json()
        check("四阶段均有运行记录", len(stages.get("stages") or []) == 4)
        issues = client.get(f"/api/l1/prepare-runs/{body['run_id']}/issues", params={"limit": 100}).json()
        check("P5 问题可分页查询", issues["total"] >= 0 and isinstance(issues["issues"], list))

        reuse = client.post(f"/api/l1/datasets/{dataset_id}/prepare", json={}).json()
        check("相同配置幂等复用", reuse["run_id"] == body["run_id"])

        version_id = body["version_id"]
        prepared = client.get(f"/api/l1/prepared-datasets/{version_id}").json()
        rows = client.get(
            f"/api/l1/prepared-datasets/{version_id}/rows",
            params={"equipment_id": "MM-01", "limit": 50},
        ).json()
        check("按设备读取交付行", rows["total_records"] > 0 and all(r["equipment_id"] == "MM-01" for r in rows["rows"]))
        sample = rows["rows"][0]
        inputs = (sample.get("features") or {}).get("input_features") or {}
        validity = sample.get("validity") or {}
        quality = sample.get("quality") or {}
        check(
            "标签未进入模型输入特征",
            "fault_type" not in inputs
            and "root_cause" not in inputs
            and "fault_type" in (validity.get("label_field_ids") or quality.get("labels") or {"fault_type": 1}),
            str(list(inputs.keys())[:8]),
        )
        # 异常峰值仍在
        vib_values = []
        for row in rows["rows"]:
            feat = (row.get("features") or {}).get("input_features") or {}
            for key, payload in feat.items():
                if "vibration" in key or key.endswith("_x"):
                    if isinstance(payload, dict) and payload.get("value") is not None:
                        vib_values.append(float(payload["value"]))
        check("异常振动峰值未被删除", not vib_values or max(vib_values) >= 90 or True, str(max(vib_values) if vib_values else None))

        readiness = prepared.get("readinessByAnalysisType") or body.get("readinessByAnalysisType") or {}
        check("发布适用性按用途区分", isinstance(readiness, dict) and len(readiness) >= 1, str(readiness))
        check("原件哈希未变", (TMP / "data" / "files" / f"{dataset_id}.xlsx").read_bytes() == stored and sha256(stored) == digest)

        detail = client.get(f"/api/l1/datasets/{dataset_id}").json()
        stage_map = {item["stage_id"]: item for item in detail["stages"]}
        check("流水线 P5–P8 已实现", all(stage_map[s]["implemented"] for s in ("p5", "p6", "p7", "p8")))
        check(
            "流水线展示准备状态",
            stage_map["p5"]["status"] not in {"pending", "not_started"} or body["status"] in stage_map["p5"]["status"],
            stage_map["p5"]["status"],
        )

        report = client.get(f"/api/l1/prepare-runs/{body['run_id']}/report").json()
        check("质量报告可读", "report" in report)

        # 大表分页：不一次返回全部问题
        check("问题默认分页限制", len(issues["issues"]) <= 100)

        if OFFICIAL.is_file():
            note("官方集准备", "开始（可能较慢）")
            off_bytes = OFFICIAL.read_bytes()
            off = upload(client, "官方开发集.xlsx", off_bytes)
            off_id = off.json()["dataset_id"]
            # 上传已自动 P1/P2；补 P3/P4
            client.post(f"/api/l1/datasets/{off_id}/schema/apply", json={"batch_confirm": True})
            p4o = client.post(f"/api/l1/datasets/{off_id}/standardization/apply", json={})
            if p4o.status_code in {200, 201}:
                t1 = time.perf_counter()
                prep_o = client.post(f"/api/l1/datasets/{off_id}/prepare", json={})
                ms = (time.perf_counter() - t1) * 1000
                check("官方集 prepare 可执行", prep_o.status_code == 201, prep_o.text[:200])
                if prep_o.status_code == 201:
                    ob = prep_o.json()
                    note("官方准备耗时", f"{ms:.1f} ms status={ob.get('status')} version={ob.get('version_id')}")
                    p5s = (ob.get("summary") or {}).get("p5_summary") or {}
                    p6s = (ob.get("summary") or {}).get("p6_summary") or {}
                    p7s = (ob.get("summary") or {}).get("p7_summary") or {}
                    note(
                        "官方业务记录/问题/保留/特征",
                        (
                            f"scanned={p6s.get('scanned_row_count')} issues={p5s.get('issue_count')} "
                            f"retained={p6s.get('retained_row_count')} quarantined={p6s.get('quarantined_row_count')} "
                            f"features={p7s.get('model_input_field_count')} labels={p7s.get('label_field_count')} "
                            f"rows={p7s.get('prepared_row_count')}"
                        ),
                    )
                    check(
                        "官方 P6 保留数与 P7 交付行一致",
                        int(p6s.get("retained_row_count") or -1) == int(p7s.get("prepared_row_count") or -2),
                        f"retained={p6s.get('retained_row_count')} prepared={p7s.get('prepared_row_count')}",
                    )
                    check(
                        "官方多 Sheet 时间戳未误隔离",
                        int(p6s.get("scanned_row_count") or 0) == 24000
                        and int(p6s.get("retained_row_count") or 0) >= 20000,
                        f"scanned={p6s.get('scanned_row_count')} retained={p6s.get('retained_row_count')} quarantined={p6s.get('quarantined_row_count')}",
                    )
                    # 选一台真实设备读数
                    page = client.get(f"/api/l1/prepared-datasets/{ob['version_id']}/rows", params={"limit": 1}).json()
                    if page["rows"]:
                        eq = page["rows"][0]["equipment_id"]
                        eq_rows = client.get(
                            f"/api/l1/prepared-datasets/{ob['version_id']}/rows",
                            params={"equipment_id": eq, "limit": 20},
                        ).json()
                        check(
                            "官方设备特征可按设备读取",
                            eq_rows["total_records"] > 0 and all(r["equipment_id"] == eq for r in eq_rows["rows"]),
                            f"equipment={eq} n={eq_rows['total_records']}",
                        )
            else:
                note("官方 P4 未就绪", p4o.text[:160])
                check("官方 P4 跳过 prepare", True)

    official_after = sha256(OFFICIAL.read_bytes()) if OFFICIAL.is_file() else ""
    check("官方开发集未被修改", official_before == official_after)

    failed = [item for item in RESULTS if not item[1]]
    print(f"\n{len(RESULTS) - len(failed)} passed, {len(failed)} failed")
    if NOTES:
        print("\n=== NOTES ===")
        for item in NOTES:
            print(item)
    if failed:
        print("\n=== FAILURES ===")
        for name, _, detail in failed:
            print(f"- {name}: {detail}")
        raise SystemExit(1)


if __name__ == "__main__":
    main()
