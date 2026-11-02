"""L1 P4：类型、时间和单位标准化。使用临时目录，不修改官方开发集。"""

from __future__ import annotations

import hashlib
import os
import sqlite3
import sys
import tempfile
from datetime import datetime
from decimal import Decimal
from pathlib import Path

from openpyxl import Workbook
from openpyxl.utils.datetime import CALENDAR_MAC_1904

ROOT = Path(__file__).resolve().parents[1]
TMP = Path(tempfile.mkdtemp(prefix="l1-p4-"))
os.environ["L1_DATA_DIR"] = str(TMP / "data")
os.environ["L1_DATABASE"] = str(TMP / "data" / "l1.sqlite3")
os.environ["L1_MAX_UPLOAD_BYTES"] = str(32 * 1024 * 1024)
os.environ["STARTUP_TOOL_SELFTEST"] = "0"
sys.path.insert(0, str(ROOT / "backend"))

from fastapi.testclient import TestClient

from app.main import app
from app.services.l1_standardization.datetime_parser import parse_datetime, read_excel_date_system
from app.services.l1_standardization.numeric_parser import decimal_text, parse_number
from app.services.l1_standardization.unit_converter import convert_unit
from app.services.official_dataset import DATASET_FILE

OFFICIAL = DATASET_FILE
RESULTS: list[tuple[str, bool, str]] = []


def check(name: str, passed: bool, detail: str = "") -> None:
    RESULTS.append((name, passed, detail))
    print(("PASS " if passed else "FAIL ") + name + ("" if not detail else " | " + detail), flush=True)


def sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def upload(client: TestClient, filename: str, payload: bytes):
    return client.post("/api/l1/datasets", params={"filename": filename}, content=payload, headers={"content-type": "application/octet-stream"})


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


def mapping_by_header(suggestions: dict, header: str) -> dict:
    return next(item for item in suggestions["mappings"] if item["header"] == header)


def prepare(client: TestClient, name: str, payload: bytes, overrides: list[dict]):
    created = upload(client, name, payload)
    dataset_id = created.json()["dataset_id"]
    p1 = apply_p1(client, dataset_id)
    grouping = client.post(f"/api/l1/datasets/{dataset_id}/grouping/apply", json={"use_suggestions": True, "cross_sheet": "separate"})
    suggestions = client.get(f"/api/l1/datasets/{dataset_id}/schema/suggestions").json()
    schema = client.post(f"/api/l1/datasets/{dataset_id}/schema/apply", json={"batch_confirm": True, "mappings": overrides_for(suggestions, overrides)})
    return created, dataset_id, p1, grouping, schema


def overrides_for(suggestions: dict, specs: list[dict]) -> list[dict]:
    found = []
    for spec in specs:
        item = mapping_by_header(suggestions, spec["header"])
        payload = {"source_column_id": item["source_column_id"], **{key: value for key, value in spec.items() if key != "header"}}
        found.append(payload)
    return found


def workbook_bytes(path: Path) -> bytes:
    return path.read_bytes()


def build_main_book() -> bytes:
    book = Workbook()
    sheet = book.active
    sheet.title = "主轧机"
    headers = ["设备编号", "采集时间", "转速(rpm)", "转频(rpm)", "X向振动(m/s)", "驱动端轴承温度(K)", "电机电流", "振动均方根", "故障类型", "根因分析"]
    sheet.append(headers)
    sheet.append(["0012", "2026-03-14 00:05:00", "1498.6", "60", "1.5", "273.15", "1,5", "1.41", "正常", "润滑正常"])
    sheet.append(["0013", "2026-03-14T00:05:00+08:00", "0", "0", "0", "0", "0", "1.316", "正常", "无"])
    sheet.append(["0014", "", "-1.5", "120", " ", "300", "2", " ", "正常", "无"])
    sheet.append(["0015", "2026-02-31", "1,498.6", "30", "NA", "10", "-1.5", "3.749", "正常", "无"])
    sheet.append(["0016", "01/02/2026", "15OO.0", "90", "2", "20", "1.5e1", "0", "正常", "无"])
    sheet.append(["0017", None, " ", "60", "1", "273.15", "0", "1", "正常", "无"])
    sheet["B7"] = datetime(2026, 3, 14, 0, 5, 0)
    sheet["B7"].number_format = "yyyy-mm-dd hh:mm:ss"
    sheet["A8"] = "0018"
    sheet["B8"] = 60
    sheet["B8"].number_format = "yyyy-mm-dd"
    sheet["C8"] = "1.5e1"
    sheet["D8"] = "60"
    sheet["E8"] = "1"
    sheet["F8"] = "273.15"
    sheet["G8"] = "0"
    sheet["H8"] = "1"
    sheet["I8"] = "正常"
    sheet["J8"] = "无"
    target = TMP / "main.xlsx"
    book.save(target)
    return target.read_bytes()


def build_1904_book() -> bytes:
    book = Workbook()
    book.epoch = CALENDAR_MAC_1904
    sheet = book.active
    sheet.title = "日期"
    sheet["A1"] = "采集时间"
    sheet["B1"] = "设备编号"
    sheet["A2"] = datetime(2026, 3, 14, 0, 5, 0)
    sheet["A2"].number_format = "yyyy-mm-dd hh:mm:ss"
    sheet["B2"] = "MM-01"
    target = TMP / "mac.xlsx"
    book.save(target)
    return target.read_bytes()


def direct_checks() -> None:
    number, status = parse_number("1498.6", decimal="point", thousands="", missing_tokens=(), excel_number=False)
    zero, zero_status = parse_number("0", decimal="point", thousands="", missing_tokens=(), excel_number=False)
    negative, negative_status = parse_number("-1.5", decimal="point", thousands="", missing_tokens=(), excel_number=False)
    science, science_status = parse_number("1.5e1", decimal="point", thousands="", missing_tokens=(), excel_number=False)
    empty, empty_status = parse_number("", decimal="point", thousands="", missing_tokens=(), excel_number=False)
    blank, blank_status = parse_number(" ", decimal="point", thousands="", missing_tokens=(), excel_number=False)
    marker, marker_status = parse_number("NA", decimal="point", thousands="", missing_tokens=("NA",), excel_number=False)
    untouched, untouched_status = parse_number("NA", decimal="point", thousands="", missing_tokens=(), excel_number=False)
    bad, bad_status = parse_number("15OO.0", decimal="point", thousands="", missing_tokens=(), excel_number=False)
    grouped, grouped_status = parse_number("1,498.6", decimal="point", thousands=",", missing_tokens=(), excel_number=False)
    ambiguous, ambiguous_status = parse_number("1,498.6", decimal="point", thousands="", missing_tokens=(), excel_number=False)
    comma, comma_status = parse_number("1,5", decimal="comma", thousands="", missing_tokens=(), excel_number=False)
    precise, precise_status = parse_number("1498.6000000001", decimal="point", thousands="", missing_tokens=(), excel_number=False)
    check(
        "数值解析保留精度并且不猜测格式",
        status == "success"
        and decimal_text(number) == "1498.6"
        and zero_status == "success"
        and zero == 0
        and negative_status == "success"
        and negative == Decimal("-1.5")
        and science_status == "success"
        and science == Decimal("15")
        and empty_status == "missing_empty"
        and blank_status == "missing_blank"
        and marker_status == "missing_marker"
        and untouched_status == "parse_failed"
        and bad_status == "parse_failed"
        and grouped_status == "success"
        and grouped == Decimal("1498.6")
        and ambiguous_status == "numeric_ambiguous"
        and comma_status == "success"
        and comma == Decimal("1.5")
        and precise_status == "success"
        and decimal_text(precise) == "1498.6000000001",
    )
    naive = parse_datetime("2026-03-14 00:05:00", cell_type="text", number_format=None, formats=(), missing_tokens=(), source_timezone="", target_timezone="", excel_system="1900")
    offset = parse_datetime("2026-03-14T00:05:00+08:00", cell_type="text", number_format=None, formats=(), missing_tokens=(), source_timezone="", target_timezone="Z", excel_system="1900")
    invalid = parse_datetime("2026-02-31", cell_type="text", number_format=None, formats=(), missing_tokens=(), source_timezone="", target_timezone="", excel_system="1900")
    ambiguous_date = parse_datetime("01/02/2026", cell_type="text", number_format=None, formats=(), missing_tokens=(), source_timezone="", target_timezone="", excel_system="1900")
    explicit = parse_datetime("01/02/2026", cell_type="text", number_format=None, formats=("%d/%m/%Y",), missing_tokens=(), source_timezone="", target_timezone="", excel_system="1900")
    fake = parse_datetime("60", cell_type="number", number_format="yyyy-mm-dd", formats=(), missing_tokens=(), source_timezone="", target_timezone="", excel_system="1900")
    mac = parse_datetime("60", cell_type="number", number_format="yyyy-mm-dd", formats=(), missing_tokens=(), source_timezone="", target_timezone="", excel_system="1904")
    minute = parse_datetime("2026-03-14 00:05", cell_type="text", number_format=None, formats=(), missing_tokens=(), source_timezone="", target_timezone="", excel_system="1900")
    check(
        "时间解析区分时区、歧义和 Excel 日期系统",
        naive["status"] == "success"
        and naive["timezone_status"] == "unconfirmed"
        and naive["normalized_text"] == "2026-03-14T00:05:00"
        and offset["status"] == "success"
        and offset["timezone_status"] == "converted"
        and offset["normalized_text"].endswith("+00:00")
        and invalid["status"] == "datetime_failed"
        and ambiguous_date["status"] == "datetime_ambiguous"
        and explicit["status"] == "success"
        and explicit["normalized_text"] == "2026-02-01T00:00:00"
        and explicit["precision"] == "day"
        and fake["status"] == "datetime_excel_unreliable"
        and mac["status"] == "success"
        and minute["precision"] == "minute",
    )
    hz, hz_status, hz_rule = convert_unit(Decimal(60), "rpm", "Hz", "frequency")
    back, back_status, _back_rule = convert_unit(Decimal(1), "Hz", "rpm", "frequency")
    speed, speed_status, _speed_rule = convert_unit(Decimal(60), "rpm", "Hz", "rotational_speed")
    velocity, velocity_status, _velocity_rule = convert_unit(Decimal("1.5"), "m/s", "mm/s", "velocity")
    celsius, celsius_status, _celsius_rule = convert_unit(Decimal("273.15"), "K", "°C", "temperature")
    kelvin, kelvin_status, _kelvin_rule = convert_unit(Decimal(0), "°C", "K", "temperature")
    gravity, gravity_status, _gravity_rule = convert_unit(Decimal(1), "g", "m/s²", "acceleration")
    crossed, crossed_status, _crossed_rule = convert_unit(Decimal(1), "Hz", "g", "frequency")
    accel, accel_status, _accel_rule = convert_unit(Decimal(1), "mm/s", "m/s²", "velocity")
    missing_unit, missing_status, _missing_rule = convert_unit(Decimal("1.41"), "", "mm/s", "velocity")
    check(
        "单位换算检查物理维度和温度偏移",
        hz_status == "success"
        and hz_rule == "UNIT_CONVERT"
        and hz == 1
        and back_status == "success"
        and abs(back - Decimal(60)) < Decimal("0.0000001")
        and speed_status == "unit_incompatible"
        and velocity_status == "success"
        and velocity == 1500
        and celsius_status == "success"
        and celsius == 0
        and kelvin_status == "success"
        and kelvin == Decimal("273.15")
        and gravity_status == "success"
        and gravity == Decimal("9.80665")
        and crossed_status == "unit_incompatible"
        and accel_status == "unit_incompatible"
        and missing_status == "unit_unconfirmed",
    )


def main() -> None:
    print("开始 P4 测试", flush=True)
    official_before = sha256(OFFICIAL.read_bytes()) if OFFICIAL.is_file() else ""
    direct_checks()
    mac_payload = build_1904_book()
    check("1904 工作簿声明日期系统", read_excel_date_system(TMP / "mac.xlsx") == "1904")
    payload = build_main_book()
    payload_hash = sha256(payload)
    with TestClient(app) as client:
        created, dataset_id, p1, grouping, schema = prepare(
            client,
            "标准化样本.xlsx",
            payload,
            [
                {"header": "振动均方根", "confirmation_status": "needs_confirmation"},
                {"header": "转频(rpm)", "standard_field_id": "rotational_frequency", "source_unit": "r/min", "confirmation_status": "confirmed"},
                {"header": "X向振动(m/s)", "standard_field_id": "vibration_x", "source_unit": "m/s", "confirmation_status": "confirmed"},
                {"header": "驱动端轴承温度(K)", "standard_field_id": "bearing_temperature_drive_end", "source_unit": "K", "confirmation_status": "confirmed"},
                {"header": "电机电流", "confirmation_status": "confirmed", "source_unit": "A"},
            ],
        )
        suggestions = client.get(f"/api/l1/datasets/{dataset_id}/schema/suggestions").json()
        current_column = mapping_by_header(suggestions, "电机电流")["source_column_id"]
        rms_column = mapping_by_header(suggestions, "振动均方根")["source_column_id"]
        speed_column = mapping_by_header(suggestions, "转速(rpm)")["source_column_id"]
        groups_before = count_rows("p2_groups")
        membership_before = count_rows("p2_membership")
        cells_before = count_rows("raw_cells")
        preview = client.post(
            f"/api/l1/datasets/{dataset_id}/standardization/preview",
            json={"sample_records": 2, "fields": [{"source_column_id": current_column, "decimal_style": "comma"}]},
        )
        blocked_type = client.post(f"/api/l1/datasets/{dataset_id}/standardization/validate", json={"fields": [{"source_column_id": speed_column, "target_type": "string"}]})
        blocked_unit = client.post(f"/api/l1/datasets/{dataset_id}/standardization/validate", json={"fields": [{"source_column_id": speed_column, "source_unit": "g"}]})
        blocked_enable = client.post(f"/api/l1/datasets/{dataset_id}/standardization/validate", json={"fields": [{"source_column_id": rms_column, "enabled": True}]})
        validated = client.post(
            f"/api/l1/datasets/{dataset_id}/standardization/validate",
            json={"fields": [{"source_column_id": current_column, "decimal_style": "comma"}]},
        )
        applied = client.post(
            f"/api/l1/datasets/{dataset_id}/standardization/apply",
            json={"fields": [{"source_column_id": current_column, "decimal_style": "comma"}]},
        )
        body = applied.json()
        run_id = body["run"]["run_id"]
        rows = client.get(f"/api/l1/standardization-runs/{run_id}/rows", params={"limit": 20, "view": "output"}).json()
        diff = client.get(f"/api/l1/standardization-runs/{run_id}/diff", params={"limit": 1, "offset": 1}).json()
        issues = client.get(f"/api/l1/standardization-runs/{run_id}/issues", params={"limit": 5, "code": "parse_failed"}).json()
        bad_page = client.get(f"/api/l1/standardization-runs/{run_id}/rows", params={"limit": 0})
        summary = client.get(f"/api/l1/standardization-runs/{run_id}/summary").json()
        by_equipment = {field_of(row, "equipment_id")["raw_value"]: row for row in rows["rows"] if field_of(row, "equipment_id")}
        first = by_equipment["0012"]
        second = by_equipment["0013"]
        third = by_equipment["0014"]
        fourth = by_equipment["0015"]
        fifth = by_equipment["0016"]
        dated = next(row for row in rows["rows"] if field_of(row, "timestamp") and field_of(row, "timestamp")["cell_type"] == "date")
        serial = next(row for row in rows["rows"] if field_of(row, "timestamp") and field_of(row, "timestamp")["status"] == "datetime_excel_unreliable")
        speed = field_of(first, "rotational_speed")
        current = field_of(first, "motor_current")
        frequency = field_of(first, "rotational_frequency")
        vibration = field_of(first, "vibration_x")
        temperature = field_of(first, "bearing_temperature_drive_end")
        rms = field_of(first, "vibration_rms")
        fault = field_of(first, "fault_type")
        cause = field_of(first, "root_cause")
        raw = client.get(f"/api/l1/datasets/{dataset_id}/raw-rows", params={"sheet_id": first["sheet_id"], "limit": 20}).json()
        stored = (TMP / "data" / "files" / f"{dataset_id}.xlsx").read_bytes()
        check(
            "确认字段完成转换且未确认字段保持原值",
            created.status_code == 201
            and p1.status_code == 201
            and grouping.status_code == 201
            and schema.status_code == 201
            and preview.status_code == 200
            and preview.json()["persisted"] is False
            and preview.json()["preview_scope"] == "sample"
            and preview.json()["summary"]["sample_record_count"] == 2
            and preview.json()["summary"]["input_record_count"] == 7
            and blocked_type.json()["apply_allowed"] is False
            and blocked_unit.json()["apply_allowed"] is False
            and blocked_enable.json()["apply_allowed"] is False
            and validated.json()["apply_allowed"] is True
            and applied.status_code == 201
            and body["run"]["status"] == "partial"
            and body["summary"]["publish_ready"] is False
            and body["summary"]["record_count"] == 7
            and body["summary"]["output_record_count"] == 7
            and body["summary"]["unconfirmed_field_count"] >= 1
            and rows["total_records"] == 7
            and speed["raw_value"] == "1498.6"
            and speed["normalized_text"] == "1498.6"
            and speed["status"] == "success"
            and field_of(second, "rotational_speed")["normalized_text"] == "0"
            and field_of(third, "rotational_speed")["normalized_text"] == "-1.5"
            and field_of(by_equipment["0018"], "rotational_speed")["normalized_text"] == "15"
            and field_of(fifth, "rotational_speed")["status"] == "parse_failed"
            and field_of(fifth, "rotational_speed")["normalized_value"] is None
            and field_of(third, "timestamp")["status"] == "missing_empty"
            and field_of(by_equipment["0017"], "rotational_speed")["status"] == "missing_blank"
            and field_of(fourth, "vibration_x")["status"] == "parse_failed"
            and field_of(fourth, "rotational_speed")["status"] == "numeric_ambiguous"
            and current["normalized_text"] == "1.5"
            and frequency["normalized_text"] == "1"
            and frequency["rule_id"] == "UNIT_CONVERT"
            and vibration["normalized_text"] == "1500"
            and temperature["normalized_text"] == "0"
            and rms["status"] == "mapping_unconfirmed"
            and rms["normalized_text"] == ""
            and fault["eligibility"] == "excluded"
            and fault["raw_value"] == "正常"
            and cause["eligibility"] == "excluded"
            and field_of(first, "equipment_id")["normalized_text"] == "0012"
            and field_of(first, "timestamp")["timezone_status"] == "unconfirmed"
            and field_of(second, "timestamp")["timezone_status"] == "offset"
            and field_of(fourth, "timestamp")["status"] == "datetime_failed"
            and field_of(fifth, "timestamp")["status"] == "datetime_ambiguous"
            and dated["fields"]
            and field_of(dated, "timestamp")["status"] == "success"
            and field_of(dated, "timestamp")["timezone_status"] == "unconfirmed"
            and serial["fields"]
            and diff["total_records"] == 7
            and len(diff["rows"]) == 1
            and issues["total"] >= 1
            and issues["issues"][0]["raw_value"]
            and issues["issues"][0]["cell_id"]
            and bad_page.status_code == 422
            and summary["summary"]["output_record_count"] == summary["summary"]["record_count"]
            and raw["rows"][1]["cells"][0]["raw_value"] == "0012"
            and stored == payload
            and sha256(stored) == payload_hash
            and count_rows("p2_groups") == groups_before
            and count_rows("p2_membership") == membership_before
            and count_rows("raw_cells") == cells_before,
            f"records={body['summary']['record_count']} success={body['summary']['success_cells']} type={body['summary']['type_failure_cells']} time={body['summary']['time_failure_cells']} unit={body['summary']['unit_failure_cells']}",
        )
        marked = client.post(
            f"/api/l1/datasets/{dataset_id}/standardization/apply",
            json={"missing_tokens": ["NA"], "fields": [{"source_column_id": current_column, "decimal_style": "comma"}]},
        )
        marked_rows = client.get(f"/api/l1/standardization-runs/{marked.json()['run']['run_id']}/rows", params={"limit": 20}).json()
        marked_fourth = next(row for row in marked_rows["rows"] if field_of(row, "equipment_id") and field_of(row, "equipment_id")["raw_value"] == "0015")
        old = client.get(f"/api/l1/standardization-runs/{run_id}").json()
        check(
            "缺失标识可配置且旧版本不被覆盖",
            marked.status_code == 201
            and marked.json()["run"]["revision"] == 2
            and field_of(marked_fourth, "vibration_x")["status"] == "missing_marker"
            and old["revision"] == 1
            and old["p4_run_id"] == run_id,
        )
        dataset = client.get(f"/api/l1/datasets/{dataset_id}").json()
        p4_stage = next(item for item in dataset["stages"] if item["stage_id"] == "p4")
        p5_stage = next(item for item in dataset["stages"] if item["stage_id"] == "p5")
        check(
            "P4 部分完成且 P5 已接入但未执行",
            p4_stage["implemented"] is True
            and p4_stage["status"] == "partial"
            and p4_stage["depends_on"] == "p3"
            and p5_stage["implemented"] is True
            and p5_stage["status"] == "not_started"
            and p5_stage["depends_on"] == "p4",
        )
        reran = client.post(f"/api/l1/datasets/{dataset_id}/schema/apply", json={"batch_confirm": True, "mappings": overrides_for(suggestions, [{"header": "振动均方根", "confirmation_status": "needs_confirmation"}])})
        refreshed = client.get(f"/api/l1/datasets/{dataset_id}").json()
        stale = next(item for item in refreshed["stages"] if item["stage_id"] == "p4")
        check(
            "P3 新修订使旧 P4 过期",
            reran.status_code == 201
            and reran.json()["run"]["run_id"] != schema.json()["run"]["run_id"]
            and stale["status"] == "stale"
            and stale["stale"] is True,
        )
    with TestClient(app) as again:
        loaded = again.get(f"/api/l1/standardization-runs/{run_id}/rows", params={"limit": 5}).json()
        check("后端重新打开后仍能读取 P4", loaded["total_records"] == 7 and loaded["rows"][0]["fields"])
    mac_created = None
    with TestClient(app) as client:
        mac_created, mac_id, mac_p1, _grouping, mac_schema = prepare(client, "1904日期.xlsx", mac_payload, [])
        mac_apply = client.post(f"/api/l1/datasets/{mac_id}/standardization/apply", json={})
        mac_rows = client.get(f"/api/l1/standardization-runs/{mac_apply.json()['run']['run_id']}/rows", params={"limit": 5}).json()
        mac_time = field_of(mac_rows["rows"][0], "timestamp")
        check(
            "Excel 日期单元格和 1904 日期系统可解析",
            mac_created.status_code == 201
            and mac_p1.status_code == 201
            and mac_schema.status_code == 201
            and mac_apply.status_code == 201
            and mac_apply.json()["excel_date_system"] == "1904"
            and mac_time["cell_type"] == "date"
            and mac_time["status"] == "success"
            and mac_time["normalized_text"].startswith("2026-03-14")
            and mac_time["timezone_status"] == "unconfirmed",
            mac_time["normalized_text"] if mac_time else "",
        )
    official_after = sha256(OFFICIAL.read_bytes()) if OFFICIAL.is_file() else ""
    check("官方开发集字节没有变化", official_before == official_after and official_before != "")
    failed = [item for item in RESULTS if not item[1]]
    print(f"P4 测试 {len(RESULTS) - len(failed)}/{len(RESULTS)} 通过", flush=True)
    if failed:
        sys.exit(1)


def count_rows(table: str) -> int:
    connection = sqlite3.connect(TMP / "data" / "l1.sqlite3")
    try:
        return int(connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
    finally:
        connection.close()


if __name__ == "__main__":
    main()
