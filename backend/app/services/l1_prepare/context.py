"""P5–P8 共享上下文：P4 编译态、P2 成员与行级转换。"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field
from typing import Iterator

from app.services.l1_standardization.convert import convert_cell
from app.services.l1_standardization.definitions import CELL_FAILURES, MISSING
from app.services.l1_standardization.service import (
    _business_count,
    _compiled_from_run,
    _page_rows,
    _record_count,
    _should_scan,
)
from app.services.l1_store import L1Error


@dataclass
class RowRecord:
    source_row_id: str
    region_id: str
    sheet_id: str
    sheet_name: str
    row_number: int
    equipment_id: str
    membership_status: str
    fields: dict[str, dict]
    values_by_standard_id: dict[str, dict] = field(default_factory=dict)


def require_p4(connection: sqlite3.Connection, dataset_id: str, p4_run_id: object) -> sqlite3.Row:
    if isinstance(p4_run_id, str) and p4_run_id.strip():
        found = connection.execute(
            "SELECT * FROM p4_runs WHERE dataset_id = ? AND run_id = ?",
            (dataset_id, p4_run_id.strip()),
        ).fetchone()
        if found is None:
            raise L1Error("p4_not_found", "没有这个 P4 版本。", 404)
        if found["status"] not in {"completed", "partial"}:
            raise L1Error("p4_not_ready", "P4 尚未完成，不能执行数据准备。", 409)
        return found
    found = connection.execute(
        """
        SELECT * FROM p4_runs
        WHERE dataset_id = ? AND status IN ('completed', 'partial')
        ORDER BY revision DESC LIMIT 1
        """,
        (dataset_id,),
    ).fetchone()
    if found is None:
        raise L1Error("p4_required", "先完成 P4 类型与时间标准化，才能做数据准备。", 409)
    return found


def load_compiled(connection: sqlite3.Connection, p4_run: sqlite3.Row) -> dict:
    compiled = _compiled_from_run(connection, p4_run)
    compiled["p2_run_id"] = p4_run["p2_run_id"]
    compiled["p3_run_id"] = p4_run["p3_run_id"]
    compiled["template_id"] = p4_run["template_id"]
    compiled["template_version"] = p4_run["template_version"]
    compiled["p4_run_id"] = p4_run["run_id"]
    compiled["p4_summary"] = json.loads(p4_run["summary_json"])
    for item in compiled["fields"]:
        if isinstance(item.get("enabled"), int):
            item["enabled"] = bool(item["enabled"])
    return compiled


def membership_map(connection: sqlite3.Connection, p2_run_id: str | None) -> dict[str, dict]:
    if not p2_run_id:
        return {}
    rows = connection.execute(
        """
        SELECT m.source_row_id, m.membership_status, m.grouping_value, g.equipment_id
        FROM p2_membership m
        LEFT JOIN p2_groups g ON g.group_id = m.group_id AND g.run_id = m.run_id
        WHERE m.run_id = ?
        """,
        (p2_run_id,),
    ).fetchall()
    return {
        str(row["source_row_id"]): {
            "membership_status": row["membership_status"],
            "equipment_id": (row["equipment_id"] or row["grouping_value"] or "").strip(),
        }
        for row in rows
    }


def field_by_standard_id(compiled: dict) -> dict[str, dict]:
    found: dict[str, dict] = {}
    for item in compiled["fields"]:
        sid = item.get("standard_field_id") or ""
        if sid and sid not in found:
            found[sid] = item
    return found


def timestamp_field(compiled: dict) -> dict | None:
    for item in compiled["fields"]:
        if item.get("standard_field_id") == "timestamp" and item.get("role") == "timestamp":
            return item
    for item in compiled["fields"]:
        if item.get("standard_field_id") == "timestamp":
            return item
    return None


def timestamp_field_for_region(compiled: dict, region_id: str) -> dict | None:
    """按区域取时间戳映射；禁止跨区域复用 source_column_id。"""
    preferred: dict | None = None
    fallback: dict | None = None
    for item in compiled["fields"]:
        if str(item.get("region_id") or "") != str(region_id or ""):
            continue
        if item.get("standard_field_id") != "timestamp":
            continue
        if item.get("role") == "timestamp":
            preferred = item
            break
        if fallback is None:
            fallback = item
    return preferred or fallback


def numeric_scan_fields(compiled: dict) -> list[dict]:
    numeric_types = {"float64", "integer"}
    return [
        item
        for item in compiled["fields"]
        if _should_scan(item) and (item.get("target_type") in numeric_types)
    ]


def iter_row_records(
    connection: sqlite3.Connection,
    dataset_id: str,
    compiled: dict,
    membership: dict[str, dict],
    batch: int = 200,
) -> Iterator[RowRecord]:
    regions = compiled["regions"]
    total = _record_count(connection, dataset_id, regions)
    offset = 0
    fields = compiled["fields"]
    by_region: dict[str, list[dict]] = {}
    for f in fields:
        by_region.setdefault(f["region_id"], []).append(f)
    while offset < total:
        page = _page_rows(connection, dataset_id, regions, offset, batch)
        if not page:
            break
        by_sheet: dict[str, list[sqlite3.Row]] = {}
        for region, row in page:
            by_sheet.setdefault(region["sheet_id"], []).append(row)
        cells: dict[tuple[str, int], sqlite3.Row] = {}
        for sheet_id, rows in by_sheet.items():
            numbers = [int(item["row_number"]) for item in rows]
            marks = ",".join("?" for _ in numbers)
            found = connection.execute(
                f"""
                SELECT source_row_id, cell_id, column_number, coordinate, raw_value, cell_type, number_format
                FROM raw_cells
                WHERE dataset_id = ? AND sheet_id = ? AND row_number IN ({marks})
                """,
                (dataset_id, sheet_id, *numbers),
            ).fetchall()
            for cell in found:
                cells[(cell["source_row_id"], int(cell["column_number"]))] = cell
        for region, row in page:
            source_row_id = str(row["source_row_id"])
            mem = membership.get(source_row_id, {})
            equipment_id = str(mem.get("equipment_id") or "")
            values: dict[str, dict] = {}
            by_sid: dict[str, dict] = {}
            for fld in by_region.get(region["region_id"], []):
                cell = cells.get((source_row_id, int(fld["column_number"])))
                raw = None if cell is None else cell["raw_value"]
                converted = convert_cell(
                    raw,
                    None if cell is None else cell["cell_type"],
                    None if cell is None else cell["number_format"],
                    fld,
                    compiled["options"],
                    compiled["excel_date_system"],
                )
                payload = {
                    **converted,
                    "source_column_id": fld["source_column_id"],
                    "standard_field_id": fld["standard_field_id"],
                    "header": fld["header"],
                }
                values[fld["source_column_id"]] = payload
                sid = fld.get("standard_field_id") or ""
                if sid:
                    by_sid[sid] = payload
            yield RowRecord(
                source_row_id=source_row_id,
                region_id=region["region_id"],
                sheet_id=region["sheet_id"],
                sheet_name=region["sheet_name"],
                row_number=int(row["row_number"]),
                equipment_id=equipment_id,
                membership_status=str(mem.get("membership_status") or ""),
                fields=values,
                values_by_standard_id=by_sid,
            )
        offset += len(page)


def parse_timestamp_value(record: RowRecord, ts_field: dict | None = None) -> tuple[str, str]:
    """返回 (timestamp_text, parse_status)。优先本行区域字段，禁止误用其他 Sheet 的列。"""
    cell = None
    if ts_field is not None and str(ts_field.get("region_id") or "") in {"", str(record.region_id)}:
        cell = record.fields.get(ts_field["source_column_id"])
    if cell is None:
        cell = record.values_by_standard_id.get("timestamp")
    if cell is None:
        return "", "not_evaluable"
    status = cell.get("status") or ""
    if status == "success":
        return str(cell.get("normalized_text") or ""), "success"
    if status in MISSING:
        return "", "missing"
    if status in CELL_FAILURES or status in {"time_failure", "parse_failed"}:
        return str(cell.get("raw_value") or ""), "failure"
    return str(cell.get("normalized_text") or cell.get("raw_value") or ""), status or "failure"


def numeric_value(record: RowRecord, field: dict) -> float | None:
    cell = None
    if str(field.get("region_id") or "") in {"", str(record.region_id)}:
        cell = record.fields.get(field["source_column_id"])
    if cell is None:
        sid = field.get("standard_field_id") or ""
        if sid:
            cell = record.values_by_standard_id.get(sid)
    if cell is None or cell.get("status") != "success":
        return None
    value = cell.get("normalized_value")
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
