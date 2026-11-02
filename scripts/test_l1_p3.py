"""L1 P3：字段语义映射。使用临时目录，不修改官方开发集。"""

from __future__ import annotations

import hashlib
import os
import sys
import tempfile
from pathlib import Path

from openpyxl import Workbook, load_workbook

ROOT = Path(__file__).resolve().parents[1]
TMP = Path(tempfile.mkdtemp(prefix="l1-p3-"))
os.environ["L1_DATA_DIR"] = str(TMP / "data")
os.environ["L1_DATABASE"] = str(TMP / "data" / "l1.sqlite3")
os.environ["L1_MAX_UPLOAD_BYTES"] = str(32 * 1024 * 1024)
os.environ["STARTUP_TOOL_SELFTEST"] = "0"
sys.path.insert(0, str(ROOT / "backend"))

from fastapi.testclient import TestClient

from app.main import app
from app.services.l1_schema.matcher import match_header
from app.services.official_dataset import DATASET_FILE, _EXPECTED_FIELDS

OFFICIAL = DATASET_FILE
RESULTS: list[tuple[str, bool, str]] = []


def check(name: str, passed: bool, detail: str = "") -> None:
    RESULTS.append((name, passed, detail))
    print(("PASS " if passed else "FAIL ") + name + ("" if not detail else " | " + detail), flush=True)


def sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def upload(client: TestClient, filename: str, payload: bytes):
    return client.post("/api/l1/datasets", params={"filename": filename}, content=payload, headers={"content-type": "application/octet-stream"})


def workbook(sheets: dict[str, list[list[str]]]) -> bytes:
    book = Workbook()
    first = True
    for name, rows in sheets.items():
        sheet = book.active if first else book.create_sheet(name)
        first = False
        sheet.title = name
        for row in rows:
            sheet.append(row)
    target = TMP / "book.xlsx"
    book.save(target)
    return target.read_bytes()


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
        json={"sheets": [{"sheet_id": item["sheet_id"], "enabled": True} for item in suggestions["sheet_settings"]], "regions": regions},
    )


def field_of(row: dict, standard_id: str) -> dict | None:
    for item in row["fields"]:
        if item["standard_field_id"] == standard_id:
            return item
    return None


def main() -> None:
    print("开始 P3 测试", flush=True)
    official_before = sha256(OFFICIAL.read_bytes()) if OFFICIAL.is_file() else ""
    with TestClient(app) as client:
        templates = client.get("/api/l1/schema/templates").json()
        template = client.get("/api/l1/schema/templates/steel_equipment_diagnosis_v1").json()
        again = client.get("/api/l1/schema/templates/steel_equipment_diagnosis_v1", params={"version": "1.0.0"}).json()
        check(
            "模板可版本化且不原地改写",
            templates["templates"][0]["template_id"] == "steel_equipment_diagnosis_v1"
            and template["version"] == "1.0.0"
            and again["version"] == "1.0.0"
            and len(template["fields"]) == len(again["fields"])
            and any(item["field_id"] == "rotational_speed" and item["target_type"] == "float64" and item["target_unit"] == "rpm" for item in template["fields"]),
        )

        official_headers = read_official_headers()
        official_matches = {header: match_header(header) for header in official_headers}
        print_official_stats(official_matches)
        check(
            "官方开发集表头都能被模板识别或明确未映射",
            bool(official_headers)
            and set(official_headers) == _EXPECTED_FIELDS
            and official_matches["设备编号"]["standard_field_id"] == "equipment_id"
            and official_matches["采集时间"]["standard_field_id"] == "timestamp"
            and official_matches["转速(rpm)"]["standard_field_id"] == "rotational_speed"
            and official_matches["转频(Hz)"]["standard_field_id"] == "rotational_frequency"
            and official_matches["X向振动(mm/s)"]["standard_field_id"] == "vibration_x"
            and official_matches["Y向振动(mm/s)"]["standard_field_id"] == "vibration_y"
            and official_matches["BPFO幅值(g)"]["standard_field_id"] == "bpfo_amplitude"
            and official_matches["BPFI幅值(g)"]["standard_field_id"] == "bpfi_amplitude"
            and official_matches["BSF幅值(g)"]["standard_field_id"] == "bsf_amplitude"
            and official_matches["BPFO频率(Hz)"]["standard_field_id"] == "bpfo_frequency"
            and official_matches["BPFI频率(Hz)"]["standard_field_id"] == "bpfi_frequency"
            and official_matches["BSF频率(Hz)"]["standard_field_id"] == "bsf_frequency"
            and official_matches["故障类型"]["role"] == "target_label"
            and official_matches["故障类型"]["eligibility"] == "excluded"
            and official_matches["根因分析"]["role"] == "diagnostic_output"
            and official_matches["根因分析"]["eligibility"] == "excluded"
            and official_matches["BPFO幅值(g)"]["standard_field_id"] != official_matches["BPFO频率(Hz)"]["standard_field_id"],
        )

        payload = workbook(
            {
                "振动": [
                    ["设备编号", "采集时间", "转速(rpm)", "X向振动(mm/s)", "振动均方根", "故障类型"],
                    ["MM-01", "2024-01-01 00:00:00", "1498.6", "1.2", "0.3", "不平衡"],
                ],
                "温度": [
                    ["设备编号", "采集时间", "驱动端轴承温度(°C)", "BPFO幅值(g)", "BPFO频率(Hz)", "根因分析", "未知列"],
                    ["MM-01", "2024-01-01 00:00:00", "40", "0.2", "87.5", "润滑不足", "保留"],
                ],
            }
        )
        created = upload(client, "异构映射.xlsx", payload)
        dataset_id = created.json()["dataset_id"]
        stored = (TMP / "data" / "files" / f"{dataset_id}.xlsx").read_bytes()
        p1 = apply_p1(client, dataset_id)
        suggestions = client.get(f"/api/l1/datasets/{dataset_id}/schema/suggestions").json()
        by_header = {(item["sheet_name"], item["header"]): item for item in suggestions["mappings"]}
        preview = client.post(f"/api/l1/datasets/{dataset_id}/schema/preview", json={"batch_confirm": True}).json()
        applied = client.post(f"/api/l1/datasets/{dataset_id}/schema/apply", json={"batch_confirm": True})
        body = applied.json()
        rows = client.get(f"/api/l1/schema-runs/{body['run']['run_id']}/rows", params={"view": "output", "limit": 20}).json()
        speed = next(field_of(row, "rotational_speed") for row in rows["rows"] if field_of(row, "rotational_speed"))
        temperature = next(field_of(row, "bearing_temperature_drive_end") for row in rows["rows"] if field_of(row, "bearing_temperature_drive_end"))
        unknown = next(item for item in body["mappings"] if item["header"] == "未知列")
        fault = by_header[("振动", "故障类型")]
        cause = by_header[("温度", "根因分析")]
        raw = client.get(f"/api/l1/datasets/{dataset_id}/raw-rows", params={"sheet_id": speed and rows["rows"][0]["sheet_id"], "limit": 5}).json()
        check(
            "异构工作表按区域映射且原始值不转换",
            created.status_code == 201
            and p1.status_code == 201
            and suggestions["summary"]["column_count"] == 13
            and by_header[("振动", "转速(rpm)")]["standard_field_id"] == "rotational_speed"
            and by_header[("振动", "X向振动(mm/s)")]["standard_field_id"] == "vibration_x"
            and by_header[("温度", "BPFO幅值(g)")]["standard_field_id"] == "bpfo_amplitude"
            and by_header[("温度", "BPFO频率(Hz)")]["standard_field_id"] == "bpfo_frequency"
            and fault["role"] == "target_label"
            and fault["eligibility"] == "excluded"
            and cause["role"] == "diagnostic_output"
            and unknown["standard_field_id"] == ""
            and by_header[("振动", "设备编号")]["region_id"] != by_header[("温度", "设备编号")]["region_id"]
            and preview["persisted"] is False
            and applied.status_code == 201
            and body["summary"]["column_count"] == body["summary"]["matched_count"] + body["summary"]["unmapped_count"]
            and speed["raw_value"] == "1498.6"
            and speed["cell_type"] == "text"
            and temperature["raw_value"] == "40"
            and rows["values_are_raw_text"] is True
            and raw["rows"][1]["cells"][2]["raw_value"] == "1498.6"
            and stored == (TMP / "data" / "files" / f"{dataset_id}.xlsx").read_bytes(),
        )
        print(
            "样例映射统计",
            {key: body["summary"][key] for key in ("column_count", "auto_matched_count", "confirmed_count", "pending_count", "unmapped_count", "conflict_count")},
            "标签",
            body["summary"]["label_fields"],
            "诊断输出",
            body["summary"]["diagnostic_fields"],
            flush=True,
        )

        speed_column = by_header[("振动", "转速(rpm)")]["source_column_id"]
        changed = client.post(
            f"/api/l1/datasets/{dataset_id}/schema/preview",
            json={"batch_confirm": True, "mappings": [{"source_column_id": speed_column, "standard_field_id": "motor_current", "role": "numerical_feature"}]},
        ).json()
        blocked = client.post(
            f"/api/l1/datasets/{dataset_id}/schema/validate",
            json={"mappings": [{"source_column_id": fault["source_column_id"], "eligibility": "candidate_input"}]},
        )
        remapped = client.post(
            f"/api/l1/datasets/{dataset_id}/schema/apply",
            json={"batch_confirm": True, "mappings": [{"source_column_id": speed_column, "standard_field_id": "", "role": "unmapped"}]},
        )
        reloaded = client.get(f"/api/l1/datasets/{dataset_id}/schema/mappings").json()
        previous = client.get(f"/api/l1/schema-runs/{body['run']['run_id']}/diff").json()
        check(
            "修改映射会预览、校验并生成新版本",
            changed["persisted"] is False
            and any(item["change"] == "field" and item["source_column_id"] == speed_column for item in changed["diff"]["changes"])
            and blocked.status_code == 200
            and blocked.json()["apply_allowed"] is False
            and any(item["code"] == "label_leakage" for item in blocked.json()["issues"])
            and remapped.status_code == 201
            and remapped.json()["run"]["revision"] == 2
            and reloaded["run"]["revision"] == 2
            and any(item["source_column_id"] == speed_column and item["standard_field_id"] == "" for item in reloaded["mappings"])
            and previous["baseline_revision"] is None or previous["changed_count"] >= 0
            and any(cell["raw_value"] == "1498.6" for row in client.get(f"/api/l1/datasets/{dataset_id}/raw-rows", params={"limit": 5}).json()["rows"] for cell in row["cells"]),
        )

        ambiguous = upload(
            client,
            "歧义映射.xlsx",
            workbook(
                {
                    "测点": [
                        ["设备编号", "X向振动速度(mm/s)", "X向振动加速度(g)", "BPFO", "转速", "转速(rpm)"],
                        ["MM-01", "1.0", "0.2", "12", "1500", "1500"],
                    ]
                }
            ),
        )
        ambiguous_id = ambiguous.json()["dataset_id"]
        apply_p1(client, ambiguous_id)
        ambiguous_body = client.get(f"/api/l1/datasets/{ambiguous_id}/schema/suggestions").json()
        found = {item["header"]: item for item in ambiguous_body["mappings"]}
        refused = client.post(f"/api/l1/datasets/{ambiguous_id}/schema/apply", json={})
        missing = upload(client, "缺标识.csv", "温度,备注\n20,甲\n".encode("utf-8"))
        missing_id = missing.json()["dataset_id"]
        apply_p1(client, missing_id)
        missing_body = client.post(f"/api/l1/datasets/{missing_id}/schema/validate", json={}).json()
        check(
            "歧义、重复映射和缺必需字段有明确状态",
            found["X向振动速度(mm/s)"]["standard_field_id"] == "vibration_x"
            and found["X向振动加速度(g)"]["standard_field_id"] == ""
            and found["X向振动加速度(g)"]["standard_field_id"] != found["X向振动速度(mm/s)"]["standard_field_id"]
            and found["BPFO"]["match_method"] == "ambiguous"
            and "bpfo_amplitude" in found["BPFO"]["candidates"]
            and "bpfo_frequency" in found["BPFO"]["candidates"]
            and refused.status_code == 409
            and refused.json()["error_type"] == "mapping_blocked"
            and any(item["code"] == "missing_required" and item["severity"] == "warning" for item in missing_body["issues"])
            and missing_body["apply_allowed"] is True,
        )

        identity = upload(client, "标识冲突.csv", "设备编号,记录编号,采集时间\nMM-01,R1,2024-01-01 00:00:00\n".encode("utf-8"))
        identity_id = identity.json()["dataset_id"]
        apply_p1(client, identity_id)
        grouped = client.post(f"/api/l1/datasets/{identity_id}/grouping/apply", json={"use_suggestions": True, "cross_sheet": "separate"})
        identity_fields = {item["header"]: item for item in client.get(f"/api/l1/datasets/{identity_id}/schema/suggestions").json()["mappings"]}
        conflict = client.post(
            f"/api/l1/datasets/{identity_id}/schema/apply",
            json={"mappings": [{"source_column_id": identity_fields["记录编号"]["source_column_id"], "standard_field_id": "equipment_id"}]},
        )
        groups_before = client.get(f"/api/l1/datasets/{identity_id}/equipment-groups").json()
        check(
            "P3 不改写 P2 设备标识",
            grouped.status_code == 201
            and conflict.status_code == 409
            and conflict.json()["error_type"] == "mapping_blocked"
            and groups_before["balance"]["grouped_count"] == 1,
        )

    with TestClient(app) as restarted:
        kept = restarted.get(f"/api/l1/datasets/{dataset_id}/schema/mappings").json()
        template_again = restarted.get("/api/l1/schema/templates/steel_equipment_diagnosis_v1").json()
        page = restarted.get(f"/api/l1/schema-runs/{kept['run']['run_id']}/rows", params={"limit": 10}).json()
        check(
            "重启后仍能读取模板和 P3",
            kept["applied"] is True
            and kept["run"]["revision"] == 2
            and template_again["version"] == "1.0.0"
            and page["total_records"] == 2
            and any(field_of(row, "bpfo_frequency") and field_of(row, "bpfo_frequency")["raw_value"] == "87.5" for row in page["rows"]),
        )
    official_after = sha256(OFFICIAL.read_bytes()) if OFFICIAL.is_file() else ""
    check("官方开发集未被修改", official_before == official_after)
    failed = [item for item in RESULTS if not item[1]]
    print(f"\n{len(RESULTS) - len(failed)} passed, {len(failed)} failed", flush=True)
    if failed:
        raise SystemExit(1)


def read_official_headers() -> list[str]:
    if not OFFICIAL.is_file():
        return []
    book = load_workbook(OFFICIAL, read_only=True, data_only=True)
    try:
        headers: list[str] = []
        seen = set()
        for sheet in book.worksheets:
            row = next(sheet.iter_rows(min_row=1, max_row=1, values_only=True))
            for value in row:
                if value is None:
                    continue
                text = str(value)
                if text not in seen:
                    seen.add(text)
                    headers.append(text)
        return headers
    finally:
        book.close()


def print_official_stats(matches: dict[str, dict]) -> None:
    matched = [item for item in matches.values() if item["standard_field_id"]]
    print(
        "官方表头映射统计",
        {
            "column_count": len(matches),
            "auto_matched_count": len(matched),
            "pending_count": sum(1 for item in matches.values() if item["confirmation_status"] == "needs_confirmation"),
            "unmapped_count": sum(1 for item in matches.values() if not item["standard_field_id"]),
            "label_fields": [item["header"] for item in matches.values() if item["role"] == "target_label"],
            "diagnostic_fields": [item["header"] for item in matches.values() if item["role"] == "diagnostic_output"],
        },
        flush=True,
    )


if __name__ == "__main__":
    main()
