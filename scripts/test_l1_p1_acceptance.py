"""L1 P1 轻量化改造工程验收。使用临时目录，不修改官方开发集。"""

from __future__ import annotations

import hashlib
import io
import json
import os
import sys
import tempfile
import time
import tracemalloc
from datetime import date, datetime
from pathlib import Path

from openpyxl import Workbook

ROOT = Path(__file__).resolve().parents[1]
TMP = Path(tempfile.mkdtemp(prefix="l1-p1-accept-"))
os.environ["L1_DATA_DIR"] = str(TMP / "data")
os.environ["L1_DATABASE"] = str(TMP / "data" / "l1.sqlite3")
os.environ["L1_MAX_UPLOAD_BYTES"] = str(64 * 1024 * 1024)
os.environ["STARTUP_TOOL_SELFTEST"] = "0"
sys.path.insert(0, str(ROOT / "backend"))

from fastapi.testclient import TestClient

from app.main import app

OFFICIAL = ROOT / "data" / "official_dataset" / "钢铁设备数据集_开发集.xlsx"
RESULTS: list[tuple[str, bool, str]] = []
PERF: list[tuple[str, str]] = []


def check(name: str, passed: bool, detail: str = "") -> None:
    RESULTS.append((name, passed, detail))
    print(("PASS " if passed else "FAIL ") + name + ("" if not detail else " | " + detail), flush=True)


def note(name: str, detail: str) -> None:
    PERF.append((name, detail))
    print("PERF " + name + " | " + detail, flush=True)


def sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def upload(client: TestClient, filename: str, payload: bytes):
    return client.post(
        "/api/l1/datasets",
        params={"filename": filename},
        content=payload,
        headers={"content-type": "application/octet-stream"},
    )


def of(body: dict, sheet: str, role: str | None = "data_table") -> list[dict]:
    return [item for item in body["regions"] if item["sheet_name"] == sheet and (role is None or item["role"] == role)]


def value_of(rows: list[dict], row_number: int, column_number: int) -> dict | None:
    for row in rows:
        if row["row_number"] != row_number:
            continue
        for cell in row["values"]:
            if cell["column_number"] == column_number:
                return cell
    return None


def acceptance_book() -> bytes:
    book = Workbook()
    simple = book.active
    simple.title = "标准单表"
    simple.append(["设备编号", "振动"])
    simple.append(["MM-01", 0])
    simple.append(["0012", 1.5])

    side = book.create_sheet("相邻边界")
    side["A1"] = "设备"
    side["B1"] = "振动"
    side["D1"] = "项目"
    side["E1"] = "数值"
    side["A2"] = "甲"
    side["B2"] = 1
    side["D2"] = "子项"
    side["E2"] = 2
    side["A3"] = "乙"
    side["B3"] = 3
    side["D3"] = "子项2"
    side["E3"] = 4

    dated = book.create_sheet("日期表头")
    dated["A1"] = "设备"
    dated["B1"] = date(2024, 1, 1)
    dated["C1"] = "数值"
    dated["A2"] = "甲"
    dated["B2"] = 10
    dated["C2"] = 1.2
    dated["A3"] = "乙"
    dated["B3"] = 11
    dated["C3"] = 1.3

    formula = book.create_sheet("公式与零值")
    formula["A1"] = "设备"
    formula["B1"] = "振动"
    formula["C1"] = "合计"
    formula["A2"] = "主电机"
    formula["B2"] = 0
    formula["C2"] = "=B2+1"
    formula["A3"] = "0012"
    formula["B3"] = 2
    formula["C3"] = "=B3+1"

    miss = book.create_sheet("遗漏候选")
    miss["A1"] = "设备"
    miss["B1"] = "振动"
    miss["A2"] = "甲"
    miss["B2"] = 1
    miss["A3"] = "乙"
    miss["B3"] = 2
    miss["E1"] = "备注块"
    miss["E2"] = "说明文字"
    miss["G5"] = "孤立"
    miss["H5"] = 9
    miss["G6"] = "数值"
    miss["H6"] = 8

    buffer = io.BytesIO()
    book.save(buffer)
    book.close()
    return buffer.getvalue()


def large_csv(rows: int) -> bytes:
    lines = ["设备编号,振动,温度\n"]
    for index in range(1, rows + 1):
        lines.append(f"MM-{index:05d},{index % 17},{30 + (index % 9)}\n")
    return "".join(lines).encode("utf-8")


def apply_ready(body: dict) -> dict:
    return {
        "sheets": [{"sheet_id": item["sheet_id"], "enabled": item.get("enabled", True)} for item in body["sheet_settings"]],
        "regions": body["regions"],
    }


def main() -> None:
    official_before = sha256(OFFICIAL.read_bytes()) if OFFICIAL.is_file() else ""
    with TestClient(app) as client:
        # --- scenario matrix extensions ---
        payload = acceptance_book()
        started = time.perf_counter()
        tracemalloc.start()
        response = upload(client, "验收样例.xlsx", payload)
        peak = tracemalloc.get_traced_memory()[1]
        tracemalloc.stop()
        upload_ms = (time.perf_counter() - started) * 1000
        body = response.json()
        dataset_id = body["dataset_id"]
        note("上传并自动 P1", f"{upload_ms:.1f} ms, peak_alloc={peak / 1024:.1f} KiB, status={body['stages'][1]['status']}")

        applied = client.get(f"/api/l1/datasets/{dataset_id}/regions").json()
        suggestions = client.get(f"/api/l1/datasets/{dataset_id}/regions/suggestions").json()
        regions_bytes = len(json.dumps(applied, ensure_ascii=False).encode("utf-8"))
        raw_total = 0
        for sheet in applied["sheet_settings"]:
            raw = client.get(f"/api/l1/datasets/{dataset_id}/raw-rows", params={"sheet_id": sheet["sheet_id"], "limit": 200}).json()
            raw_total += len(json.dumps(raw, ensure_ascii=False).encode("utf-8"))
        note("默认 regions 响应体积", f"{regions_bytes} bytes")
        note("全部工作表 raw-rows(limit=200) 体积合计", f"{raw_total} bytes")
        check(
            "默认摘要不含单元格样例且小于全表 raw 页合计",
            applied["applied"] is True
            and all(item.get("sample_rows") == [] for item in applied["regions"])
            and all(not item.get("sample_rows") for item in suggestions["regions"])
            and regions_bytes < raw_total,
            f"regions={regions_bytes} raw_total={raw_total}",
        )

        simple = of(applied, "标准单表")
        zero = None
        coded = None
        if simple:
            page = client.get(f"/api/l1/regions/{simple[0]['region_id']}/rows", params={"limit": 20}).json()
            zero = value_of(page["rows"], 2, 2)
            coded = value_of(page["rows"], 3, 1)
            raw_page = client.get(f"/api/l1/datasets/{dataset_id}/raw-rows", params={"sheet_id": simple[0]["sheet_id"], "limit": 20}).json()
            raw_coded = next(cell for row in raw_page["rows"] if row["row_number"] == 3 for cell in row["cells"] if cell["column_number"] == 1)
        check(
            "标准单表范围、零值与前导零可追溯",
            len(simple) == 1
            and simple[0]["range_label"] == "A1:B3"
            and simple[0]["record_count"] == 2
            and zero is not None
            and zero["raw_value"] == "0"
            and coded is not None
            and coded["raw_value"] == "0012"
            and coded["cell_id"] == raw_coded["cell_id"]
            and coded["source_row_id"],
        )

        adjacent = of(applied, "相邻边界")
        check(
            "边界相邻但不重叠的左右区域",
            len(adjacent) == 2 and {item["range_label"] for item in adjacent} == {"A1:B3", "D1:E3"},
            str([item["range_label"] for item in adjacent]),
        )

        dated = of(suggestions, "日期表头")
        check(
            "日期表头不拆散数据表",
            len(dated) == 1 and dated[0]["header_row_count"] >= 1 and dated[0]["record_count"] == 2,
            str(dated[0]["header_row_count"] if dated else None),
        )

        formula_region = of(applied, "公式与零值")
        formula_ok = False
        if formula_region:
            page = client.get(f"/api/l1/regions/{formula_region[0]['region_id']}/rows", params={"limit": 20}).json()
            formula_cell = value_of(page["rows"], 2, 3)
            zero_cell = value_of(page["rows"], 2, 2)
            formula_ok = (
                formula_region[0]["record_count"] == 2
                and zero_cell is not None
                and zero_cell["raw_value"] == "0"
                and formula_cell is not None
                and formula_cell["cell_type"] == "formula"
                and formula_cell["source_row_id"]
            )
        check("公式单元格与数值 0 保留溯源", formula_ok)

        miss_tables = of(applied, "遗漏候选")
        miss_exceptions = [item for item in applied.get("exceptions", []) if item.get("sheet_name") == "遗漏候选"]
        covered = next((item for item in applied.get("sheet_summaries", []) if item.get("name") == "遗漏候选"), None)
        recovered = [item for item in miss_tables if item.get("detection_method") == "coverage" or any(warning.get("code") == "recovered_table" for warning in item.get("warnings", []))]
        check(
            "未纳入主表的内容可通过覆盖统计或补回表发现",
            len(miss_tables) >= 1
            and covered is not None
            and (
                int(covered["auxiliary_cells"]) > 0
                or len(recovered) > 0
                or any(item["code"] in {"auxiliary_content", "omitted_table", "recovered_table"} for item in miss_exceptions)
                or int(covered["data_table_count"]) > 1
            ),
            str({"summary": covered, "tables": len(miss_tables), "recovered": len(recovered), "exceptions": [item["code"] for item in miss_exceptions]}),
        )

        reuse_started = time.perf_counter()
        reused = client.post(f"/api/l1/datasets/{dataset_id}/regions/apply", json={"source": "auto"}).json()
        reuse_ms = (time.perf_counter() - reuse_started) * 1000
        note("重复自动执行缓存命中", f"{reuse_ms:.1f} ms, reused={reused.get('reused')}, revision={reused['run']['revision']}")
        check("相同输入重复执行复用修订", reused.get("reused") is True and reused["run"]["revision"] == applied["run"]["revision"])

        edited = apply_ready(applied)
        target = next(item for item in edited["regions"] if item["sheet_name"] == "标准单表")
        target["end_row"] = 2
        target["detection_method"] = "user"
        edit_started = time.perf_counter()
        preview = client.post(f"/api/l1/datasets/{dataset_id}/regions/preview", json=edited).json()
        preview_ms = (time.perf_counter() - edit_started) * 1000
        apply_started = time.perf_counter()
        changed = client.post(f"/api/l1/datasets/{dataset_id}/regions/apply", json=edited).json()
        apply_ms = (time.perf_counter() - apply_started) * 1000
        note("局部手工预览", f"{preview_ms:.1f} ms")
        note("局部手工应用", f"{apply_ms:.1f} ms, revision={changed['run']['revision']}, status={changed['status']}")
        check(
            "手工缩小范围生成新修订且可发现遗漏",
            preview["persisted"] is False
            and changed["reused"] is False
            and changed["run"]["revision"] == applied["run"]["revision"] + 1
            and changed["status"] == "needs_review"
            and any(item["code"] == "omitted_table" for item in changed["exceptions"]),
        )
        accepted = client.post(f"/api/l1/datasets/{dataset_id}/regions/apply", json={**edited, "accept_review": True}).json()
        check(
            "接受审核后下游可继续",
            accepted["downstream_ready"] is True and accepted["run"]["revision"] == changed["run"]["revision"] + 1,
        )

        # --- large file recognition ---
        large_payload = large_csv(20000)
        large_started = time.perf_counter()
        tracemalloc.start()
        large = upload(client, "两万行.csv", large_payload).json()
        large_peak = tracemalloc.get_traced_memory()[1]
        tracemalloc.stop()
        large_ms = (time.perf_counter() - large_started) * 1000
        large_regions = client.get(f"/api/l1/datasets/{large['dataset_id']}/regions").json()
        note("两万行 CSV 上传+自动 P1", f"{large_ms:.1f} ms, peak_alloc={large_peak / 1024:.1f} KiB")
        check(
            "大规模行数据自动识别",
            large["stages"][1]["status"] in {"completed", "completed_with_warnings"}
            and large_regions["applied"] is True
            and large_regions["run"]["record_count"] == 20000
            and all(item.get("sample_rows") == [] for item in large_regions["regions"]),
            str(large_regions["run"]["record_count"]),
        )

        # --- pipeline continuity P0→P4 ---
        pipe_payload = "设备编号,振动速度,采集时间\nMM-01,1.2,2024-01-02 08:00:00\n0012,0,2024-01-02 09:00:00\n".encode("utf-8")
        pipe = upload(client, "贯通.csv", pipe_payload).json()
        pipe_id = pipe["dataset_id"]
        p1 = client.get(f"/api/l1/datasets/{pipe_id}/regions").json()
        region = next(item for item in p1["regions"] if item["role"] == "data_table")
        p2 = client.post(f"/api/l1/datasets/{pipe_id}/grouping/apply", json={"use_suggestions": True, "cross_sheet": "separate"})
        p2_body = p2.json()
        schema = client.get(f"/api/l1/datasets/{pipe_id}/schema/suggestions").json()
        p3 = client.post(f"/api/l1/datasets/{pipe_id}/schema/apply", json={"batch_confirm": True, "mappings": schema.get("mappings", [])})
        p3_body = p3.json()
        p4 = client.post(f"/api/l1/datasets/{pipe_id}/standardization/apply", json={})
        p4_body = p4.json()
        page = client.get(f"/api/l1/regions/{region['region_id']}/rows", params={"limit": 10}).json()
        coded = value_of(page["rows"], 3, 1)
        detail = client.get(f"/api/l1/datasets/{pipe_id}").json()
        check(
            "P0→P1→P2→P3→P4 贯通且原始映射不变",
            p1["downstream_ready"] is True
            and p2.status_code in {200, 201}
            and p2_body.get("run")
            and p3.status_code in {200, 201}
            and p3_body.get("run")
            and p4.status_code in {200, 201}
            and p4_body.get("run")
            and coded is not None
            and coded["raw_value"] == "0012"
            and coded["source_row_id"]
            and detail["stages"][1]["status"] in {"completed", "completed_with_warnings"}
            and detail["stages"][2]["status"] == "completed",
            f"p2={p2.status_code} p3={p3.status_code} p4={p4.status_code} detail={p4_body.get('error_type') or p4_body.get('message') or ''}",
        )

        overlap = apply_ready(p1)
        left = next(item for item in overlap["regions"] if item["role"] == "data_table")
        overlap["regions"] = [
            dict(left),
            {
                **left,
                "client_region_id": "UR-overlap",
                "start_row": left["start_row"],
                "end_row": left["end_row"],
                "start_column": left["start_column"],
                "end_column": left["end_column"],
                "detection_method": "user",
            },
        ]
        blocked = client.post(f"/api/l1/datasets/{pipe_id}/regions/apply", json=overlap)
        check("非法重叠区域被拒绝", blocked.status_code == 422 and blocked.json().get("error_type") == "region_overlap", blocked.text)

        # frontend default request contract remains true after acceptance additions
        check(
            "前端默认不渲染完整工作簿契约仍在",
            "listRawRows(datasetId" in (ROOT / "frontend/src/diagnosis/l1/RegionWorkspace.tsx").read_text(encoding="utf-8")
            and 'mode !== "inspect"' in (ROOT / "frontend/src/diagnosis/l1/RegionWorkspace.tsx").read_text(encoding="utf-8")
            and 'stageId !== "p0"' in (ROOT / "frontend/src/diagnosis/l1/PipelinePanel.tsx").read_text(encoding="utf-8"),
        )

    official_after = sha256(OFFICIAL.read_bytes()) if OFFICIAL.is_file() else ""
    check("官方开发集未被修改", official_before == official_after)

    failed = [item for item in RESULTS if not item[1]]
    print(f"\n{len(RESULTS) - len(failed)} passed, {len(failed)} failed", flush=True)
    if PERF:
        print("\nPerformance notes:", flush=True)
        for name, detail in PERF:
            print(f"- {name}: {detail}", flush=True)
    if failed:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
