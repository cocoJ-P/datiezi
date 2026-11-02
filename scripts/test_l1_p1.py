"""L1 P1：工作表与数据区域识别。使用临时目录，不修改官方开发集。"""

from __future__ import annotations

import hashlib
import io
import os
import sqlite3
import sys
import tempfile
from pathlib import Path

from openpyxl import Workbook

ROOT = Path(__file__).resolve().parents[1]
TMP = Path(tempfile.mkdtemp(prefix="l1-p1-"))
os.environ["L1_DATA_DIR"] = str(TMP / "data")
os.environ["L1_DATABASE"] = str(TMP / "data" / "l1.sqlite3")
os.environ["L1_MAX_UPLOAD_BYTES"] = str(32 * 1024 * 1024)
os.environ["STARTUP_TOOL_SELFTEST"] = "0"
sys.path.insert(0, str(ROOT / "backend"))

from fastapi.testclient import TestClient

from app.main import app

OFFICIAL = ROOT / "data" / "official_dataset" / "钢铁设备数据集_开发集.xlsx"
RESULTS: list[tuple[str, bool, str]] = []


def check(name: str, passed: bool, detail: str = "") -> None:
    RESULTS.append((name, passed, detail))
    print(("PASS " if passed else "FAIL ") + name + ("" if not detail else " | " + detail))


def sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def upload(client: TestClient, filename: str, payload: bytes):
    return client.post(
        "/api/l1/datasets",
        params={"filename": filename},
        content=payload,
        headers={"content-type": "application/octet-stream"},
    )


def workbook() -> bytes:
    book = Workbook()
    one = book.active
    one.title = "一行表头"
    one.append(["设备", "测点", "振动"])
    one.append(["主电机", "驱动端", 0.12])
    one.append(["风机", "轴承", 0])

    other = book.create_sheet("另一结构")
    other.append(["时间", "温度"])
    other.append(["08:00", 40])

    two = book.create_sheet("两张表")
    two["A1"] = "设备"
    two["B1"] = "振动"
    two["A2"] = "A"
    two["B2"] = 1
    two["A4"] = "项目"
    two["B4"] = "数值"
    two["A5"] = "x"
    two["B5"] = 2

    multi = book.create_sheet("多行表头")
    multi["A1"] = "设备"
    multi["C1"] = "测点"
    multi.merge_cells("A1:B1")
    multi["A2"] = "名称"
    multi["B2"] = "编号"
    multi["C2"] = "位置"
    multi["A3"] = "主电机"
    multi["B3"] = "0012"
    multi["C3"] = "驱动端"

    title = book.create_sheet("合并标题")
    title["A1"] = "2024年检修说明"
    title.merge_cells("A1:C1")
    title["A2"] = "设备"
    title["B2"] = "振动"
    title["C2"] = "温度"
    title["A3"] = "主电机"
    title["B3"] = 1.2
    title["C3"] = 40

    notes = book.create_sheet("说明与汇总")
    notes["A1"] = "本表用于验收"
    notes["A2"] = "设备"
    notes["B2"] = "振动"
    notes["C2"] = "温度"
    notes["A3"] = "主电机"
    notes["B3"] = 1
    notes["C3"] = 40
    notes["A4"] = "风机"
    notes["B4"] = 2
    notes["C4"] = 41
    notes["A5"] = "合计"
    notes["B5"] = 3
    notes["C5"] = 81
    notes["A6"] = "备注"
    notes["B6"] = "人工录入"

    hidden = book.create_sheet("隐藏行")
    hidden["A1"] = "设备"
    hidden["B1"] = "振动"
    hidden["A2"] = "主电机"
    hidden["B2"] = 1
    hidden["A3"] = "风机"
    hidden["B3"] = 2
    hidden.row_dimensions[3].hidden = True
    hidden["A5"] = "临时记录"
    hidden["B5"] = "不要丢"

    naked = book.create_sheet("无表头")
    naked.append([1, 2, 3])
    naked.append([4, 5, 6])
    naked.append([7, 8, 9])

    side = book.create_sheet("左右并排")
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

    gap = book.create_sheet("表内空行")
    gap["A1"] = "设备"
    gap["B1"] = "振动"
    gap["A2"] = "甲"
    gap["B2"] = 1
    gap["A4"] = "乙"
    gap["B4"] = 2

    single = book.create_sheet("单列")
    single["A1"] = "设备"
    single["A2"] = "主电机"
    single["A3"] = "风机"

    numeric = book.create_sheet("数字表头")
    numeric["A1"] = "测点"
    numeric["B1"] = 1
    numeric["C1"] = "数值"
    numeric["A2"] = "甲"
    numeric["B2"] = 10
    numeric["C2"] = 1.2
    numeric["A3"] = "乙"
    numeric["B3"] = 11
    numeric["C3"] = 1.3

    empty = book.create_sheet("空表")
    empty.sheet_state = "hidden"
    buffer = io.BytesIO()
    book.save(buffer)
    book.close()
    return buffer.getvalue()


def of(body: dict, sheet: str, role: str | None = None) -> list[dict]:
    found = [item for item in body["regions"] if item["sheet_name"] == sheet and (role is None or item["role"] == role)]
    return found


def value_of(rows: list[dict], row_number: int, column_number: int) -> dict | None:
    for row in rows:
        if row["row_number"] != row_number:
            continue
        for cell in row["values"]:
            if cell["column_number"] == column_number:
                return cell
    return None


def confirmed(body: dict) -> dict:
    regions = []
    for item in body["regions"]:
        copied = dict(item)
        if copied["confirmation_status"] == "needs_confirmation":
            copied["confirmed"] = True
        regions.append(copied)
    return {"sheets": [{"sheet_id": item["sheet_id"], "enabled": item["enabled"]} for item in body["sheet_settings"]], "regions": regions}


def main() -> None:
    official_before = sha256(OFFICIAL.read_bytes()) if OFFICIAL.is_file() else ""
    payload = workbook()
    digest = sha256(payload)
    with TestClient(app) as client:
        response = upload(client, "区域识别.xlsx", payload)
        body = response.json()
        dataset_id = body["dataset_id"]
        stored = (TMP / "data" / "files" / f"{dataset_id}.xlsx").read_bytes()
        suggestions = client.get(f"/api/l1/datasets/{dataset_id}/regions/suggestions").json()
        names = [item["name"] for item in suggestions["sheet_settings"]]
        expected_names = ["一行表头", "另一结构", "两张表", "多行表头", "合并标题", "说明与汇总", "隐藏行", "无表头", "左右并排", "表内空行", "单列", "数字表头", "空表"]
        simple = of(suggestions, "一行表头", "data_table")
        other = of(suggestions, "另一结构", "data_table")
        check(
            "上传后自动识别且摘要不含单元格",
            response.status_code == 201
            and body["stages"][1]["status"] in {"completed", "completed_with_warnings"}
            and suggestions["persisted"] is False
            and suggestions["rule_id"] == "sheet_region_heuristic_v2"
            and names == expected_names
            and suggestions["sheet_settings"][-1]["visibility"] == "hidden"
            and all(item["role"] == "data_table" for item in suggestions["regions"])
            and all(item["sample_rows"] == [] for item in suggestions["regions"])
            and any(item["code"] == "empty_sheet" and item["sheet_name"] == "空表" for item in suggestions["exceptions"]),
            str(names),
        )
        check(
            "一行表头",
            len(simple) == 1
            and simple[0]["header_row_count"] == 1
            and simple[0]["header_start_row"] == 1
            and simple[0]["record_count"] == 2
            and simple[0]["confidence"] == "high"
            and [column["header"] for column in simple[0]["columns"]] == [["设备"], ["测点"], ["振动"]],
        )
        check(
            "不同工作表保持各自的列",
            len(other) == 1 and [column["header"] for column in other[0]["columns"]] == [["时间"], ["温度"]] and other[0]["record_count"] == 1,
        )
        tables = of(suggestions, "两张表", "data_table")
        check(
            "同一工作表上下两张表",
            len(tables) == 2 and {item["range_label"] for item in tables} == {"A1:B2", "A4:B5"} and not of(suggestions, "两张表", "blank"),
        )
        side = of(suggestions, "左右并排", "data_table")
        check(
            "左右并排的两张表",
            len(side) == 2 and {item["range_label"] for item in side} == {"A1:B3", "D1:E3"},
            str([item["range_label"] for item in side]),
        )
        gap = of(suggestions, "表内空行", "data_table")
        check(
            "表内空行仍属于同一张表",
            len(gap) == 1 and gap[0]["start_row"] == 1 and gap[0]["end_row"] == 4 and gap[0]["record_count"] == 2 and gap[0]["header_row_count"] == 1,
        )
        column = of(suggestions, "单列", "data_table")
        check(
            "单列表格",
            len(column) == 1 and column[0]["start_column"] == 1 and column[0]["end_column"] == 1 and column[0]["header_row_count"] == 1 and column[0]["record_count"] == 2,
        )
        numeric = of(suggestions, "数字表头", "data_table")
        check(
            "表头中的数字不拆散表",
            len(numeric) == 1 and numeric[0]["header_row_count"] >= 1 and numeric[0]["record_count"] == 2 and numeric[0]["columns"][1]["header"] == ["1"],
            str(numeric[0]["header_row_count"] if numeric else None),
        )
        multi = of(suggestions, "多行表头", "data_table")
        check(
            "多行表头",
            len(multi) == 1
            and multi[0]["header_row_count"] == 2
            and multi[0]["header_start_row"] == 1
            and multi[0]["columns"][1]["header"] == ["编号"]
            and any("A1:B1" in item["message"] for item in multi[0]["warnings"]),
        )
        title_table = of(suggestions, "合并标题", "data_table")
        check(
            "合并标题不进入数据表",
            len(title_table) == 1 and title_table[0]["header_start_row"] == 2 and title_table[0]["record_count"] == 1 and not of(suggestions, "合并标题", "note"),
        )
        summary_table = of(suggestions, "说明与汇总", "data_table")
        check(
            "说明和合计留在数据表外",
            len(summary_table) == 1
            and summary_table[0]["record_count"] == 2
            and summary_table[0]["data_start_row"] == 3
            and summary_table[0]["end_row"] == 4
            and any(item["sheet_name"] == "说明与汇总" and item["code"] == "auxiliary_content" for item in suggestions["exceptions"]),
        )
        hidden_table = of(suggestions, "隐藏行", "data_table")
        check(
            "隐藏行保留且不为每个空行建区域",
            len(hidden_table) == 1
            and hidden_table[0]["record_count"] == 2
            and any(item["code"] == "hidden_rows" for item in hidden_table[0]["warnings"])
            and not of(suggestions, "隐藏行", "blank")
            and not of(suggestions, "隐藏行", "note"),
        )
        naked = of(suggestions, "无表头", "data_table")
        check(
            "表头歧义不阻断自动执行",
            len(naked) == 1
            and naked[0]["header_row_count"] == 0
            and naked[0]["confidence"] == "low"
            and naked[0]["confirmation_status"] == "suggested"
            and naked[0]["record_count"] == 3
            and any(item["code"] == "header_ambiguous" for item in naked[0]["warnings"])
            and suggestions["needs_confirmation"] == []
            and suggestions["downstream_ready"] is True,
        )

        before = client.get(f"/api/l1/datasets/{dataset_id}/regions").json()
        edited = confirmed(suggestions)
        target = next(item for item in edited["regions"] if item["sheet_name"] == "一行表头" and item["role"] == "data_table")
        target["header_start_row"] = 1
        target["header_row_count"] = 2
        target["detection_method"] = "user"
        preview = client.post(f"/api/l1/datasets/{dataset_id}/regions/preview", json=edited).json()
        preview_table = of(preview, "一行表头", "data_table")[0]
        after_preview = client.get(f"/api/l1/datasets/{dataset_id}/regions").json()
        check(
            "预览不写入正式结果",
            before["applied"] is True
            and before["run"]["revision"] == 1
            and before["downstream_ready"] is True
            and preview["persisted"] is False
            and preview["preview"] is True
            and preview_table["header_row_count"] == 2
            and preview_table["record_count"] == 1
            and any(item["sheet_name"] == "一行表头" for item in preview["diff"]["header_changes"])
            and after_preview["run"]["revision"] == 1,
        )

        applied = client.post(f"/api/l1/datasets/{dataset_id}/regions/apply", json=confirmed(suggestions))
        applied_body = applied.json()
        loaded = client.get(f"/api/l1/datasets/{dataset_id}/regions").json()
        detail = client.get(f"/api/l1/datasets/{dataset_id}").json()
        table = next(item for item in loaded["regions"] if item["sheet_name"] == "多行表头" and item["role"] == "data_table")
        page = client.get(f"/api/l1/regions/{table['region_id']}/rows", params={"limit": 20}).json()
        coded = value_of(page["rows"], 3, 2)
        raw = client.get(f"/api/l1/datasets/{dataset_id}/raw-rows", params={"sheet_id": table["sheet_id"], "limit": 20}).json()
        raw_coded = next(cell for row in raw["rows"] if row["row_number"] == 3 for cell in row["cells"] if cell["column_number"] == 2)
        simple_region = next(item for item in loaded["regions"] if item["sheet_name"] == "一行表头")
        simple_page = client.get(f"/api/l1/regions/{simple_region['region_id']}/rows", params={"limit": 20}).json()
        zero = value_of(simple_page["rows"], 3, 3)
        hidden_region = next(item for item in loaded["regions"] if item["sheet_name"] == "隐藏行")
        hidden_page = client.get(f"/api/l1/regions/{hidden_region['region_id']}/rows", params={"limit": 20}).json()
        hidden_row = next(row for row in hidden_page["rows"] if row["row_number"] == 3)
        hidden_sheet = next(item["sheet_id"] for item in loaded["sheet_settings"] if item["name"] == "隐藏行")
        scratch = client.get(f"/api/l1/datasets/{dataset_id}/raw-rows", params={"sheet_id": hidden_sheet, "limit": 20}).json()
        scratch_value = next(cell["raw_value"] for row in scratch["rows"] if row["row_number"] == 5 for cell in row["cells"] if cell["column_number"] == 1)
        title_sheet = next(item["sheet_id"] for item in loaded["sheet_settings"] if item["name"] == "合并标题")
        title_raw = client.get(f"/api/l1/datasets/{dataset_id}/raw-rows", params={"sheet_id": title_sheet, "limit": 5}).json()
        title_text = next(cell["raw_value"] for row in title_raw["rows"] if row["row_number"] == 1 for cell in row["cells"] if cell["column_number"] == 1)
        file_again = (TMP / "data" / "files" / f"{dataset_id}.xlsx").read_bytes()
        check(
            "自动执行可回溯且原件不变",
            applied.status_code == 201
            and applied_body["reused"] is True
            and applied_body["run"]["revision"] == 1
            and applied_body["run"]["rule_id"] == "sheet_region_heuristic_v2"
            and applied_body["run"]["p0_sha256"] == digest
            and loaded["run"]["run_id"] == applied_body["run"]["run_id"]
            and coded is not None
            and coded["raw_value"] == "0012"
            and coded["cell_type"] == "text"
            and coded["cell_id"] == raw_coded["cell_id"]
            and coded["source_row_id"]
            and zero is not None
            and zero["raw_value"] == "0"
            and hidden_row["hidden"] is True
            and value_of([hidden_row], 3, 2)["raw_value"] == "2"
            and scratch_value == "临时记录"
            and title_text == "2024年检修说明"
            and file_again == stored
            and sha256(file_again) == digest
            and detail["stages"][1]["status"] == "completed_with_warnings"
            and detail["cell_count"] == body["cell_count"],
        )

        review_config = confirmed(suggestions)
        trimmed = next(item for item in review_config["regions"] if item["sheet_name"] == "一行表头" and item["role"] == "data_table")
        trimmed["end_row"] = 2
        trimmed["data_end_row"] = 2
        trimmed["detection_method"] = "user"
        review = client.post(f"/api/l1/datasets/{dataset_id}/regions/apply", json=review_config).json()
        blocked = client.get(f"/api/l1/datasets/{dataset_id}/equipment-fields")
        accepted = client.post(f"/api/l1/datasets/{dataset_id}/regions/apply", json={**review_config, "accept_review": True}).json()
        check(
            "遗漏数据进入待审核且接受后可继续",
            review["run"]["revision"] == 2
            and review["status"] == "needs_review"
            and review["downstream_ready"] is False
            and any(item["code"] == "omitted_table" and item["sheet_name"] == "一行表头" for item in review["exceptions"])
            and blocked.status_code == 409
            and blocked.json().get("error_type") == "p1_needs_review"
            and accepted["run"]["revision"] == 3
            and accepted["status"] in {"completed", "completed_with_warnings"}
            and accepted["downstream_ready"] is True
            and accepted["reused"] is False,
            f"review={review.get('status')} blocked={blocked.status_code}:{blocked.json().get('error_type')} accepted={accepted.get('status')}/{accepted.get('reused')}/{accepted.get('run', {}).get('revision')}",
        )

        second_config = confirmed(suggestions)
        changed = next(item for item in second_config["regions"] if item["sheet_name"] == "无表头" and item["role"] == "data_table")
        changed["header_start_row"] = 1
        changed["header_row_count"] = 1
        changed["confirmed"] = True
        changed["detection_method"] = "user"
        second = client.post(f"/api/l1/datasets/{dataset_id}/regions/apply", json=second_config).json()
        repeated = client.post(f"/api/l1/datasets/{dataset_id}/regions/apply", json=second_config).json()
        untouched = client.post(f"/api/l1/datasets/{dataset_id}/regions/apply", json={"source": "auto"}).json()
        first = client.get(f"/api/l1/datasets/{dataset_id}/regions", params={"run_id": applied_body["run"]["run_id"]}).json()
        latest = client.get(f"/api/l1/datasets/{dataset_id}/regions").json()
        first_naked = next(item for item in first["regions"] if item["sheet_name"] == "无表头" and item["role"] == "data_table")
        latest_naked = next(item for item in latest["regions"] if item["sheet_name"] == "无表头" and item["role"] == "data_table")
        check(
            "改范围生成新修订且自动识别不覆盖",
            second["run"]["revision"] == 4
            and second["reused"] is False
            and repeated["reused"] is True
            and repeated["run"]["revision"] == 4
            and untouched["reused"] is True
            and untouched["run"]["revision"] == 4
            and first["run"]["revision"] == 1
            and first["rule_id"] == "sheet_region_heuristic_v2"
            and first_naked["header_row_count"] == 0
            and latest_naked["header_row_count"] == 1
            and latest_naked["record_count"] == 2
            and latest["run"]["source"] == "user"
            and sha256((TMP / "data" / "files" / f"{dataset_id}.xlsx").read_bytes()) == digest,
        )

        overlap = confirmed(suggestions)
        first_table = next(item for item in overlap["regions"] if item["sheet_name"] == "一行表头" and item["role"] == "data_table")
        overlap["regions"] = [dict(first_table), {**first_table, "client_region_id": "UR-overlap", "detection_method": "user"}]
        blocked_overlap = client.post(f"/api/l1/datasets/{dataset_id}/regions/apply", json=overlap)
        check(
            "重复重叠区域不会被误复用",
            blocked_overlap.status_code == 422 and blocked_overlap.json().get("error_type") == "region_overlap",
            blocked_overlap.text,
        )

        connection = sqlite3.connect(os.environ["L1_DATABASE"])
        connection.execute(
            """
            INSERT INTO p1_runs (
                run_id, dataset_id, revision, status, created_at, rule_id, p0_sha256, config_json,
                region_count, unclassified_cell_count, record_count
            ) VALUES (?, ?, 0, 'completed', '2020-01-01T00:00:00Z', 'sheet_region_heuristic_v1', ?, '{}', 1, 0, 0)
            """,
            ("P1-v1history01", dataset_id, digest),
        )
        connection.execute(
            """
            INSERT INTO p1_regions (
                region_id, run_id, dataset_id, client_region_id, sheet_id, sheet_enabled, role,
                start_row, end_row, start_column, end_column, header_start_row, header_row_count,
                data_start_row, data_end_row, detection_method, confidence, confirmation_status,
                record_count, column_json, warning_json, ordinal
            ) VALUES (?, 'P1-v1history01', ?, 'SG-00-01', ?, 1, 'note', 1, 1, 1, 1, NULL, 0, 1, 1, 'heuristic', 'medium', 'confirmed', 1, '[]', '[{"message":"v1 说明"}]', 0)
            """,
            ("RG-v1history01", dataset_id, simple_region["sheet_id"]),
        )
        connection.commit()
        connection.close()
        historic = client.get(f"/api/l1/datasets/{dataset_id}/regions", params={"run_id": "P1-v1history01"}).json()
        check(
            "v1 历史修订仍可读取",
            historic["applied"] is True and historic["rule_id"] == "sheet_region_heuristic_v1" and historic["regions"][0]["role"] == "note" and historic["regions"][0]["warnings"][0]["message"] == "v1 说明",
        )

        csv_payload = "设备,备注\n主电机,0012\n".encode("utf-8")
        csv_response = upload(client, "区域.csv", csv_payload)
        csv_id = csv_response.json()["dataset_id"]
        csv_applied = client.get(f"/api/l1/datasets/{csv_id}/regions").json()
        csv_table = of(csv_applied, "CSV", "data_table")
        csv_page = client.get(f"/api/l1/regions/{csv_table[0]['region_id']}/rows").json()
        check(
            "CSV 虚拟工作表按区域识别",
            csv_response.json()["stages"][1]["status"] == "completed"
            and len(csv_applied["sheet_settings"]) == 1
            and len(csv_table) == 1
            and csv_table[0]["columns"][1]["header"] == ["备注"]
            and value_of(csv_page["rows"], 2, 2)["raw_value"] == "0012"
            and csv_table[0]["sample_rows"] == [],
        )

    with TestClient(app) as restarted:
        again = restarted.get(f"/api/l1/datasets/{dataset_id}/regions").json()
        old = restarted.get(f"/api/l1/datasets/{dataset_id}/regions", params={"run_id": "P1-v1history01"}).json()
        check(
            "重启后仍能读取 P1 结果",
            again["applied"] is True and again["run"]["revision"] == 4 and again["run"]["p0_sha256"] == digest and old["rule_id"] == "sheet_region_heuristic_v1",
        )
    official_after = sha256(OFFICIAL.read_bytes()) if OFFICIAL.is_file() else ""
    check("官方开发集未被修改", official_before == official_after)
    failed = [item for item in RESULTS if not item[1]]
    print(f"\n{len(RESULTS) - len(failed)} passed, {len(failed)} failed")
    if failed:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
