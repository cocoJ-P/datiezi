"""P4 编排。预览只抽样；执行保存规则快照和错误，不改 P0 单元格、P2 分组和 P3 映射。"""

from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import datetime, timezone

from app.services.l1_schema.definitions import FIELD_BY_ID
from app.services.l1_schema.repository import ensure_tables as ensure_schema_tables
from app.services.l1_standardization.convert import convert_cell
from app.services.l1_standardization.datetime_parser import read_excel_date_system
from app.services.l1_standardization.definitions import CELL_FAILURES, MISSING, RULE_ID, TIME_FAILURES, TYPE_FAILURES, UNIT_FAILURES
from app.services.l1_standardization.repository import ensure_tables
from app.services.l1_standardization.validator import validate_config
from app.services.l1_store import L1Error, _connect, _require, _require_indexed, raw_file

_SAMPLE_DEFAULT = 8
_SAMPLE_MAX = 50


def get_standardization_config(dataset_id: str) -> dict:
    dataset = _ready(dataset_id)
    connection = _open()
    try:
        compiled = _compile(connection, dataset, {})
        run = _latest_run(connection, dataset_id)
        body = _public(dataset, compiled, persisted=False)
        body["applied"] = run is not None
        body["run"] = None if run is None else _run_public(run)
        if run is not None:
            body["applied_summary"] = json.loads(run["summary_json"])
            body["applied_config"] = json.loads(run["config_json"])
        return body
    finally:
        connection.close()


def preview_standardization(dataset_id: str, config: dict) -> dict:
    dataset = _ready(dataset_id)
    connection = _open()
    try:
        compiled = _compile(connection, dataset, config)
        sample_limit = _sample_limit(config)
        rows, scanned = _convert_page(connection, dataset["dataset_id"], compiled, 0, sample_limit)
        summary = _summarize(compiled, scanned, len(rows), preview=True)
        summary["sample_record_count"] = len(rows)
        summary["sample_limit"] = sample_limit
        summary["input_record_count"] = _record_count(connection, dataset["dataset_id"], compiled["regions"])
        summary["note"] = "这是抽样预览。成功、失败和差异数量只覆盖样本，不是全量数据质量统计。"
    finally:
        connection.close()
    body = _public(dataset, compiled, persisted=False)
    body["preview"] = True
    body["summary"] = summary
    body["rows"] = rows
    body["sample_issues"] = _sample_issues(rows)
    body["sample_limited"] = True
    body["preview_scope"] = "sample"
    return body


def validate_standardization(dataset_id: str, config: dict) -> dict:
    dataset = _ready(dataset_id)
    connection = _open()
    try:
        compiled = _compile(connection, dataset, config)
    finally:
        connection.close()
    return {
        "dataset_id": dataset_id,
        "apply_allowed": compiled["apply_allowed"],
        "issues": compiled["issues"],
        "p3_run_id": compiled["p3_run_id"],
        "partial": compiled["partial"],
    }


def apply_standardization(dataset_id: str, config: dict) -> dict:
    dataset = _ready(dataset_id)
    connection = _open()
    started = False
    try:
        compiled = _compile(connection, dataset, config)
        if not compiled["apply_allowed"]:
            blocking = next(item["message"] for item in compiled["issues"] if item["severity"] == "blocking")
            raise L1Error("standardization_blocked", blocking, 409)
        _scanned, summary = _scan_all(connection, dataset["dataset_id"], compiled)
        now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        connection.execute("BEGIN IMMEDIATE")
        started = True
        current = connection.execute("SELECT MAX(revision) AS revision FROM p4_runs WHERE dataset_id = ?", (dataset_id,)).fetchone()
        revision = int(current["revision"] or 0) + 1
        run_id = "P4-" + uuid.uuid4().hex[:12]
        status = "partial" if summary["partial"] else "completed"
        connection.execute(
            """
            INSERT INTO p4_runs (
                run_id, dataset_id, revision, status, created_at, p3_run_id, p2_run_id, p1_run_id, p0_sha256,
                template_id, template_version, rule_id, excel_date_system, publish_ready, config_json, summary_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                run_id,
                dataset_id,
                revision,
                status,
                now,
                compiled["p3_run_id"],
                compiled["p2_run_id"],
                compiled["p1_run_id"],
                dataset["sha256"],
                compiled["template_id"],
                compiled["template_version"],
                RULE_ID,
                compiled["excel_date_system"],
                1 if summary["publish_ready"] else 0,
                json.dumps(compiled["config"], ensure_ascii=False, separators=(",", ":")),
                json.dumps(summary, ensure_ascii=False, separators=(",", ":")),
            ),
        )
        _insert_fields(connection, run_id, compiled["fields"])
        _insert_issues(connection, run_id, compiled, _scanned)
        connection.commit()
        started = False
        run = connection.execute("SELECT * FROM p4_runs WHERE run_id = ?", (run_id,)).fetchone()
        body = _public(dataset, compiled, persisted=True)
        body["run"] = _run_public(run)
        body["summary"] = summary
        body["applied"] = True
        return body
    except Exception:
        if started:
            connection.rollback()
        raise
    finally:
        connection.close()


def get_standardization_run(run_id: str) -> dict:
    _check_run_id(run_id)
    connection = _open()
    try:
        run = connection.execute("SELECT * FROM p4_runs WHERE run_id = ?", (run_id,)).fetchone()
        if run is None:
            raise L1Error("run_not_found", "没有这次 P4 标准化记录。", 404)
        return _artifact(run)
    finally:
        connection.close()


def list_standardization_rows(run_id: str, offset: int, limit: int, region_id: str | None, view: str) -> dict:
    _check_run_id(run_id)
    _check_page(offset, limit)
    connection = _open()
    try:
        run = _require_run(connection, run_id)
        compiled = _compiled_from_run(connection, run)
        if region_id:
            compiled = {**compiled, "regions": [item for item in compiled["regions"] if item["region_id"] == region_id]}
            if not compiled["regions"]:
                raise L1Error("region_not_found", "这个 P4 版本里没有该数据区域。", 404)
        rows, _scanned = _convert_page(connection, run["dataset_id"], compiled, offset, limit)
        total = _record_count(connection, run["dataset_id"], compiled["regions"])
    finally:
        connection.close()
    return {
        "run_id": run_id,
        "view": view if view in {"input", "output", "diff"} else "output",
        "offset": offset,
        "limit": limit,
        "total_records": total,
        "rows": rows,
        "values_keep_raw_source": True,
        "preview_scope": "full",
    }


def list_standardization_issues(run_id: str, offset: int, limit: int, code: str | None) -> dict:
    _check_run_id(run_id)
    _check_page(offset, limit)
    connection = _open()
    try:
        _require_run(connection, run_id)
        where = "run_id = ?"
        params: list[object] = [run_id]
        if code:
            where += " AND code = ?"
            params.append(code)
        total = int(connection.execute(f"SELECT COUNT(*) AS total FROM p4_issues WHERE {where}", params).fetchone()["total"])
        found = connection.execute(
            f"SELECT * FROM p4_issues WHERE {where} ORDER BY issue_id LIMIT ? OFFSET ?",
            (*params, limit, offset),
        ).fetchall()
    finally:
        connection.close()
    return {"run_id": run_id, "offset": offset, "limit": limit, "total": total, "issues": [_issue_public(row) for row in found]}


def list_standardization_diff(run_id: str, offset: int, limit: int, region_id: str | None) -> dict:
    page = list_standardization_rows(run_id, offset, limit, region_id, "diff")
    artifact = get_standardization_run(run_id)
    summary = artifact["summary"]
    page["value_change_cells"] = summary.get("value_change_cells", 0)
    page["type_change_cells"] = summary.get("type_change_cells", 0)
    page["diff_total_records"] = page["total_records"]
    page["note"] = "差异按业务记录分页，包含没有变化的字段。总数来自完整执行，不是 200 条上限。"
    return page


def get_standardization_summary(run_id: str) -> dict:
    artifact = get_standardization_run(run_id)
    return {"run_id": run_id, "status": artifact["status"], "publish_ready": artifact["publish_ready"], "summary": artifact["summary"]}


def _compile(connection: sqlite3.Connection, dataset: sqlite3.Row, config: dict) -> dict:
    if not isinstance(config, dict):
        raise L1Error("invalid_config", "标准化配置必须是 JSON 对象。", 422)
    p3 = _resolve_p3(connection, dataset["dataset_id"], config.get("p3_run_id"))
    regions = _regions(connection, p3["p1_run_id"])
    if not regions:
        raise L1Error("p1_required", "没有已确认的数据区域可以标准化。", 409)
    stored = _mapping_rows(connection, p3["run_id"])
    options = _options(config)
    overrides = _overrides(config.get("fields"))
    fields = []
    for item in stored:
        field = dict(item)
        override = overrides.get(item["source_column_id"])
        field["enabled"] = item["confirmation_status"] == "confirmed"
        if override:
            if "enabled" in override:
                field["enabled"] = bool(override["enabled"])
            for key in ("decimal_style", "thousands_separator", "missing_tokens", "datetime_formats", "source_timezone", "target_timezone"):
                if key in override:
                    field[key] = override[key]
            if "target_type" in override and override["target_type"] not in {None, "", item["target_type"]}:
                field["declared_target_type"] = override["target_type"]
            if "source_unit" in override and override["source_unit"] != item["source_unit"]:
                field["declared_target_type"] = field.get("declared_target_type") or item["target_type"]
                issues_unit = True
                field["source_unit_override"] = override["source_unit"]
            else:
                issues_unit = False
        else:
            issues_unit = False
        field["unit_override_rejected"] = issues_unit
        fields.append(field)
    issues = validate_config(fields, options)
    for field in fields:
        if field.get("unit_override_rejected"):
            issues.append(
                {
                    "severity": "blocking",
                    "code": "p3_unit_locked",
                    "message": f"{field['header']} 的单位属于 P3。要修改请回到 P3 生成新修订。",
                    "source_column_id": field["source_column_id"],
                }
            )
    partial = any(field["confirmation_status"] != "confirmed" or not field.get("enabled", True) or _field_hold(field) for field in fields)
    path, _name = raw_file(dataset["dataset_id"])
    return {
        "p3_run_id": p3["run_id"],
        "p3_revision": int(p3["revision"]),
        "p2_run_id": p3["p2_run_id"],
        "p1_run_id": p3["p1_run_id"],
        "template_id": p3["template_id"],
        "template_version": p3["template_version"],
        "regions": regions,
        "fields": fields,
        "options": options,
        "issues": issues,
        "apply_allowed": not any(item["severity"] == "blocking" for item in issues),
        "partial": partial,
        "excel_date_system": read_excel_date_system(path),
        "config": {
            "rule_id": RULE_ID,
            "p3_run_id": p3["run_id"],
            **options,
            "regions": regions,
            "fields": [_field_config(field) for field in fields],
        },
    }


def _scan_all(connection: sqlite3.Connection, dataset_id: str, compiled: dict) -> tuple[list[dict], dict]:
    failures: list[dict] = []
    counts = _empty_counts()
    total = 0
    for region in compiled["regions"]:
        count = _business_count(connection, dataset_id, region)
        total += count
        for field in compiled["fields"]:
            if field["region_id"] != region["region_id"] or not _should_scan(field):
                continue
            for cell in _column_cells(connection, dataset_id, region, int(field["column_number"])):
                converted = convert_cell(cell["raw_value"], cell["cell_type"], cell["number_format"], field, compiled["options"], compiled["excel_date_system"])
                _count(counts, converted["status"])
                raw_text = cell["raw_value"] or ""
                if converted["status"] == "success" and converted["normalized_text"] != raw_text:
                    counts["value_changed"] += 1
                if _representation_changed(cell["cell_type"], field, converted, raw_text):
                    counts["type_changed"] += 1
                if converted["status"] in CELL_FAILURES:
                    failures.append({**converted, **dict(field), **dict(cell)})
    summary = _summarize_counts(compiled, counts, total, total, preview=False)
    return failures, summary


def _convert_page(connection: sqlite3.Connection, dataset_id: str, compiled: dict, offset: int, limit: int) -> tuple[list[dict], dict]:
    selected = _page_rows(connection, dataset_id, compiled["regions"], offset, limit)
    counts = _empty_counts()
    if not selected:
        return [], counts
    by_sheet: dict[str, list[sqlite3.Row]] = {}
    for region, row in selected:
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
    grouped: dict[str, list[dict]] = {}
    for field in compiled["fields"]:
        grouped.setdefault(field["region_id"], []).append(field)
    records = []
    for region, row in selected:
        source_row_id = row["source_row_id"]
        values = []
        for field in grouped.get(region["region_id"], []):
            cell = cells.get((source_row_id, int(field["column_number"])))
            raw = None if cell is None else cell["raw_value"]
            converted = convert_cell(
                raw,
                None if cell is None else cell["cell_type"],
                None if cell is None else cell["number_format"],
                field,
                compiled["options"],
                compiled["excel_date_system"],
            )
            _count(counts, converted["status"])
            raw_text = raw or ""
            value_changed = converted["status"] == "success" and converted["normalized_text"] != raw_text
            type_changed = _representation_changed(None if cell is None else cell["cell_type"], field, converted, raw_text)
            values.append(
                {
                    **converted,
                    "source_column_id": field["source_column_id"],
                    "column_id": field["column_id"],
                    "header": field["header"],
                    "standard_field_id": field["standard_field_id"],
                    "standard_label": field["standard_label"],
                    "confirmation_status": field["confirmation_status"],
                    "cell_id": None if cell is None else cell["cell_id"],
                    "coordinate": None if cell is None else cell["coordinate"],
                    "value_changed": value_changed,
                    "type_changed": type_changed,
                    "changed": value_changed or type_changed,
                }
            )
        records.append(
            {
                "source_row_id": source_row_id,
                "region_id": region["region_id"],
                "sheet_id": region["sheet_id"],
                "sheet_name": region["sheet_name"],
                "row_number": int(row["row_number"]),
                "p3_run_id": compiled["p3_run_id"],
                "p4_input": "raw_cells",
                "fields": values,
            }
        )
    return records, counts


def _summarize(compiled: dict, counts: dict, record_count: int, preview: bool) -> dict:
    return _summarize_counts(compiled, counts, record_count, record_count, preview)


def _summarize_counts(compiled: dict, counts: dict, record_count: int, output_count: int, preview: bool) -> dict:
    unconfirmed_fields = [field for field in compiled["fields"] if field["confirmation_status"] != "confirmed"]
    held = [field for field in compiled["fields"] if _field_hold(field)]
    scanned = (
        counts["success"]
        + counts["missing"]
        + counts["type_failure"]
        + counts["time_failure"]
        + counts["unit_failure"]
    )
    converted_fields = [field for field in compiled["fields"] if _should_scan(field)]
    # 未确认/单位待确认字段不参与扫描；其“潜在单元格”是独立维度，不可与 scanned 相加
    unconfirmed_cells = None if preview else record_count * len(unconfirmed_fields)
    held_cells = None if preview else record_count * len(held)
    partial = bool(
        unconfirmed_fields
        or held
        or counts["type_failure"]
        or counts["time_failure"]
        or counts["unit_failure"]
        or any(not field.get("enabled", True) for field in compiled["fields"] if field["confirmation_status"] == "confirmed")
    )
    labels = [field["header"] for field in compiled["fields"] if field["role"] == "target_label"]
    diagnostics = [field["header"] for field in compiled["fields"] if field["role"] == "diagnostic_output"]
    return {
        "record_count": record_count,
        "output_record_count": output_count,
        "field_count": len(compiled["fields"]),
        "confirmed_field_count": sum(1 for field in compiled["fields"] if field["confirmation_status"] == "confirmed"),
        "unconfirmed_field_count": len(unconfirmed_fields),
        "converted_field_count": len(converted_fields),
        # 维度 A：已扫描业务单元格（互斥守恒）
        "scanned_cells": scanned,
        "success_cells": counts["success"],
        "missing_cells": counts["missing"],
        "type_failure_cells": counts["type_failure"],
        "time_failure_cells": counts["time_failure"],
        "unit_failure_cells": counts["unit_failure"],
        "scanned_balance_ok": scanned
        == counts["success"] + counts["missing"] + counts["type_failure"] + counts["time_failure"] + counts["unit_failure"],
        # 维度 B：未扫描潜在格（与 A 不可加总）
        "unconfirmed_cells": unconfirmed_cells,
        "unit_hold_cells": held_cells,
        "not_scanned_note": (
            "unconfirmed_cells / unit_hold_cells 按「业务记录 × 字段数」估算，表示未进入转换扫描的潜在格；"
            "不得与 success/missing/failure 相加。守恒式：success+missing+type_failure+time_failure+unit_failure = scanned_cells。"
        ),
        "unit_unconfirmed_fields": len(held),
        "value_change_cells": counts["value_changed"],
        "type_change_cells": counts["type_changed"],
        "counts_cover": "sample" if preview else "full",
        "label_fields": labels,
        "diagnostic_fields": diagnostics,
        "label_eligibility": "excluded" if labels and all(field["eligibility"] == "excluded" for field in compiled["fields"] if field["role"] in {"target_label", "diagnostic_output"}) else "review",
        "partial": partial or compiled["partial"],
        "publish_ready": not partial and not compiled["partial"] and counts["type_failure"] == 0 and counts["time_failure"] == 0 and counts["unit_failure"] == 0,
        "preview_scope": "sample" if preview else "full",
        "p3_run_id": compiled["p3_run_id"],
        "excel_date_system": compiled["excel_date_system"],
    }


def _empty_counts() -> dict:
    return {"success": 0, "missing": 0, "type_failure": 0, "time_failure": 0, "unit_failure": 0, "value_changed": 0, "type_changed": 0}


def _count(counts: dict, status: str) -> None:
    if status == "success":
        counts["success"] += 1
    elif status in MISSING:
        counts["missing"] += 1
    elif status in TYPE_FAILURES:
        counts["type_failure"] += 1
    elif status in TIME_FAILURES:
        counts["time_failure"] += 1
    elif status in UNIT_FAILURES:
        counts["unit_failure"] += 1


def _representation_changed(cell_type: str | None, field: dict, converted: dict, raw_text: str) -> bool:
    if converted["status"] != "success":
        return False
    target = field.get("target_type") or ""
    if target in {"float64", "integer"} and cell_type != "number":
        return True
    if target in {"timestamp", "datetime", "date"}:
        return True
    return converted["normalized_text"] != raw_text


def _sample_issues(rows: list[dict]) -> list[dict]:
    found = []
    for row in rows:
        for field in row["fields"]:
            if field["status"] not in CELL_FAILURES:
                continue
            found.append(
                {
                    "code": field["status"],
                    "message": field["message"],
                    "source_column_id": field["source_column_id"],
                    "source_row_id": row["source_row_id"],
                    "sheet_id": row["sheet_id"],
                    "sheet_name": row["sheet_name"],
                    "row_number": row["row_number"],
                    "column_id": field["column_id"],
                    "cell_id": field["cell_id"],
                    "coordinate": field["coordinate"],
                    "raw_value": field["raw_value"],
                    "target_type": field["target_type"],
                    "rule_id": field["rule_id"],
                    "sample_limited": True,
                }
            )
    return found


def _should_scan(field: dict) -> bool:
    return field.get("confirmation_status") == "confirmed" and field.get("enabled", True) and not _field_hold(field)


def _field_hold(field: dict) -> bool:
    return field.get("confirmation_status") == "confirmed" and field.get("enabled", True) and field.get("target_type") in {"float64", "integer"} and bool(field.get("target_unit")) and not str(field.get("source_unit") or "").strip()


def _field_config(field: dict) -> dict:
    kept = {
        "source_column_id": field["source_column_id"],
        "region_id": field["region_id"],
        "sheet_id": field["sheet_id"],
        "sheet_name": field["sheet_name"],
        "column_id": field["column_id"],
        "column_number": field["column_number"],
        "header": field["header"],
        "standard_field_id": field["standard_field_id"],
        "standard_label": field["standard_label"],
        "role": field["role"],
        "eligibility": field["eligibility"],
        "confirmation_status": field["confirmation_status"],
        "source_unit": field["source_unit"],
        "target_unit": field["target_unit"],
        "target_type": field["target_type"],
        "dimension": field["dimension"],
        "enabled": bool(field.get("enabled", True)) and field["confirmation_status"] == "confirmed" and not field.get("unit_override_rejected"),
    }
    for key in ("decimal_style", "thousands_separator", "missing_tokens", "datetime_formats", "source_timezone", "target_timezone"):
        if key in field:
            kept[key] = field[key]
    return kept


def _options(config: dict) -> dict:
    return {
        "decimal_style": config.get("decimal_style") or "point",
        "thousands_separator": config.get("thousands_separator") or "",
        "missing_tokens": config.get("missing_tokens") if isinstance(config.get("missing_tokens"), list) else [],
        "datetime_formats": config.get("datetime_formats") if isinstance(config.get("datetime_formats"), list) else [],
        "source_timezone": config.get("source_timezone") or "",
        "target_timezone": config.get("target_timezone") or "",
        "failure_policy": config.get("failure_policy") or "keep_record",
    }


def _overrides(fields: object) -> dict[str, dict]:
    if not isinstance(fields, list):
        return {}
    found = {}
    for item in fields:
        if isinstance(item, dict) and isinstance(item.get("source_column_id"), str):
            found[item["source_column_id"]] = item
    return found


def _insert_fields(connection: sqlite3.Connection, run_id: str, fields: list[dict]) -> None:
    connection.executemany(
        """
        INSERT INTO p4_fields (
            run_id, source_column_id, region_id, sheet_id, sheet_name, column_id, column_number, header,
            standard_field_id, standard_label, role, eligibility, confirmation_status, source_unit, target_unit,
            target_type, dimension, enabled, field_status, rule_id
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            (
                run_id,
                field["source_column_id"],
                field["region_id"],
                field["sheet_id"],
                field["sheet_name"],
                field["column_id"],
                field["column_number"],
                field["header"],
                field["standard_field_id"],
                field["standard_label"],
                field["role"],
                field["eligibility"],
                field["confirmation_status"],
                field["source_unit"],
                field["target_unit"],
                field["target_type"],
                field["dimension"],
                1 if field.get("enabled") and field["confirmation_status"] == "confirmed" else 0,
                "mapping_unconfirmed" if field["confirmation_status"] != "confirmed" else "unit_unconfirmed" if _field_hold(field) else "enabled" if field.get("enabled") else "not_executed",
                RULE_ID,
            )
            for field in fields
        ],
    )


def _insert_issues(connection: sqlite3.Connection, run_id: str, compiled: dict, failures: list[dict]) -> None:
    rows = []
    for item in compiled["issues"]:
        if item["severity"] == "blocking":
            continue
        rows.append(
            (
                run_id,
                item["severity"],
                item["code"],
                item["message"],
                item.get("source_column_id") or "",
                "",
                "",
                "",
                "",
                0,
                0,
                "",
                "",
                "",
                "",
                "",
                "",
            )
        )
    for item in failures:
        rows.append(
            (
                run_id,
                "error",
                item["status"],
                item.get("message") or item["status"],
                item["source_column_id"],
                item["source_row_id"],
                item["region_id"],
                item["sheet_id"],
                item["sheet_name"],
                int(item["row_number"]),
                int(item["column_number"]),
                item.get("cell_id") or "",
                item.get("coordinate") or "",
                item.get("raw_value") or "",
                item.get("cell_type") or "",
                item.get("target_type") or "",
                item.get("rule_id") or "",
            )
        )
    if rows:
        connection.executemany(
            """
            INSERT INTO p4_issues (
                run_id, severity, code, message, source_column_id, source_row_id, region_id, sheet_id, sheet_name,
                row_number, column_number, cell_id, coordinate, raw_value, cell_type, target_type, rule_id
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            rows,
        )


def _compiled_from_run(connection: sqlite3.Connection, run: sqlite3.Row) -> dict:
    config = json.loads(run["config_json"])
    fields = config.get("fields") or []
    regions = config.get("regions") or _regions(connection, run["p1_run_id"])
    options = _options(config)
    return {
        "p3_run_id": run["p3_run_id"],
        "p1_run_id": run["p1_run_id"],
        "regions": regions,
        "fields": fields,
        "options": options,
        "excel_date_system": run["excel_date_system"],
        "partial": run["status"] == "partial",
        "issues": [],
    }


def _mapping_rows(connection: sqlite3.Connection, run_id: str) -> list[dict]:
    rows = connection.execute("SELECT * FROM p3_mappings WHERE run_id = ? ORDER BY sheet_name, column_number", (run_id,)).fetchall()
    items = []
    for row in rows:
        definition = FIELD_BY_ID.get(row["standard_field_id"])
        items.append(
            {
                "source_column_id": row["source_column_id"],
                "region_id": row["region_id"],
                "sheet_id": row["sheet_id"],
                "sheet_name": row["sheet_name"],
                "column_id": row["column_id"],
                "column_number": int(row["column_number"]),
                "header": row["header"],
                "standard_field_id": row["standard_field_id"],
                "standard_label": "" if definition is None else definition.label,
                "role": row["role"],
                "eligibility": row["eligibility"],
                "confirmation_status": row["confirmation_status"],
                "source_unit": row["source_unit"],
                "target_unit": row["target_unit"],
                "target_type": row["target_type"],
                "dimension": "" if definition is None else definition.dimension,
            }
        )
    return items


def _regions(connection: sqlite3.Connection, run_id: str) -> list[dict]:
    rows = connection.execute(
        """
        SELECT r.*, s.name AS sheet_name
        FROM p1_regions r
        JOIN sheets s ON s.dataset_id = r.dataset_id AND s.sheet_id = r.sheet_id
        WHERE r.run_id = ? AND r.role = 'data_table' AND r.sheet_enabled = 1
        ORDER BY r.ordinal
        """,
        (run_id,),
    ).fetchall()
    return [
        {
            "region_id": row["region_id"],
            "sheet_id": row["sheet_id"],
            "sheet_name": row["sheet_name"],
            "start_row": int(row["start_row"]),
            "end_row": int(row["end_row"]),
            "header_start_row": row["header_start_row"],
            "header_row_count": int(row["header_row_count"]),
        }
        for row in rows
    ]


def _resolve_p3(connection: sqlite3.Connection, dataset_id: str, run_id: object) -> sqlite3.Row:
    if isinstance(run_id, str) and run_id:
        found = connection.execute("SELECT * FROM p3_runs WHERE dataset_id = ? AND run_id = ?", (dataset_id, run_id)).fetchone()
        if found is None:
            raise L1Error("p3_not_found", "没有这个 P3 版本。", 404)
        return found
    found = connection.execute("SELECT * FROM p3_runs WHERE dataset_id = ? ORDER BY revision DESC LIMIT 1", (dataset_id,)).fetchone()
    if found is None:
        raise L1Error("p3_required", "先完成 P3 字段映射，才能做类型和时间标准化。", 409)
    return found


def _business_count(connection: sqlite3.Connection, dataset_id: str, region: dict) -> int:
    header_count, header_start, header_end = _header(region)
    row = connection.execute(
        """
        SELECT COUNT(*) AS total FROM source_rows
        WHERE dataset_id = ? AND sheet_id = ? AND row_number BETWEEN ? AND ?
          AND (? = 0 OR row_number < ? OR row_number > ?)
        """,
        (dataset_id, region["sheet_id"], region["start_row"], region["end_row"], header_count, header_start, header_end),
    ).fetchone()
    return int(row["total"])


def _record_count(connection: sqlite3.Connection, dataset_id: str, regions: list[dict]) -> int:
    return sum(_business_count(connection, dataset_id, region) for region in regions)


def _page_rows(connection: sqlite3.Connection, dataset_id: str, regions: list[dict], offset: int, limit: int) -> list[tuple[dict, sqlite3.Row]]:
    selected = []
    cursor = offset
    remaining = limit
    seen = 0
    for region in regions:
        count = _business_count(connection, dataset_id, region)
        if remaining <= 0:
            break
        if cursor >= seen + count:
            seen += count
            continue
        local = max(0, cursor - seen)
        take = min(remaining, count - local)
        header_count, header_start, header_end = _header(region)
        rows = connection.execute(
            """
            SELECT source_row_id, row_number FROM source_rows
            WHERE dataset_id = ? AND sheet_id = ? AND row_number BETWEEN ? AND ?
              AND (? = 0 OR row_number < ? OR row_number > ?)
            ORDER BY row_number
            LIMIT ? OFFSET ?
            """,
            (dataset_id, region["sheet_id"], region["start_row"], region["end_row"], header_count, header_start, header_end, take, local),
        ).fetchall()
        selected.extend((region, row) for row in rows)
        remaining -= len(rows)
        cursor = seen + count
        seen += count
    return selected


def _column_cells(connection: sqlite3.Connection, dataset_id: str, region: dict, column_number: int) -> list[sqlite3.Row]:
    header_count, header_start, header_end = _header(region)
    return connection.execute(
        """
        SELECT s.source_row_id, s.row_number, c.raw_value, c.cell_type, c.cell_id, c.coordinate, c.number_format
        FROM source_rows s
        LEFT JOIN raw_cells c
          ON c.dataset_id = s.dataset_id AND c.sheet_id = s.sheet_id
         AND c.row_number = s.row_number AND c.column_number = ?
        WHERE s.dataset_id = ? AND s.sheet_id = ? AND s.row_number BETWEEN ? AND ?
          AND (? = 0 OR s.row_number < ? OR s.row_number > ?)
        ORDER BY s.row_number
        """,
        (column_number, dataset_id, region["sheet_id"], region["start_row"], region["end_row"], header_count, header_start, header_end),
    ).fetchall()


def _header(region: dict) -> tuple[int, int, int]:
    header_count = int(region["header_row_count"])
    header_start = int(region["header_start_row"] or 0)
    header_end = header_start + header_count - 1 if header_count else 0
    return header_count, header_start, header_end


def _public(dataset: sqlite3.Row, compiled: dict, persisted: bool) -> dict:
    return {
        "dataset_id": dataset["dataset_id"],
        "persisted": persisted,
        "p3_run_id": compiled["p3_run_id"],
        "p3_revision": compiled.get("p3_revision"),
        "template_id": compiled["template_id"],
        "template_version": compiled["template_version"],
        "rule_id": RULE_ID,
        "excel_date_system": compiled["excel_date_system"],
        "apply_allowed": compiled["apply_allowed"],
        "partial": compiled["partial"],
        "issues": compiled["issues"],
        "fields": [_field_config(field) for field in compiled["fields"]],
        "config": compiled["config"],
        "summary": None,
    }


def _artifact(run: sqlite3.Row) -> dict:
    summary = json.loads(run["summary_json"])
    return {
        "dataset_id": run["dataset_id"],
        "p4_run_id": run["run_id"],
        "p3_run_id": run["p3_run_id"],
        "p2_run_id": run["p2_run_id"],
        "p1_run_id": run["p1_run_id"],
        "p0_sha256": run["p0_sha256"],
        "schema_template_id": run["template_id"],
        "schema_template_version": run["template_version"],
        "status": run["status"],
        "publish_ready": bool(run["publish_ready"]),
        "excel_date_system": run["excel_date_system"],
        "revision": int(run["revision"]),
        "created_at": run["created_at"],
        "summary": summary,
        "config": json.loads(run["config_json"]),
        "rule_id": run["rule_id"],
    }


def _run_public(run: sqlite3.Row) -> dict:
    return {
        "run_id": run["run_id"],
        "revision": int(run["revision"]),
        "status": run["status"],
        "p3_run_id": run["p3_run_id"],
        "publish_ready": bool(run["publish_ready"]),
        "created_at": run["created_at"],
    }


def _issue_public(row: sqlite3.Row) -> dict:
    return {
        "issue_id": int(row["issue_id"]),
        "severity": row["severity"],
        "code": row["code"],
        "message": row["message"],
        "source_column_id": row["source_column_id"],
        "source_row_id": row["source_row_id"],
        "region_id": row["region_id"],
        "sheet_id": row["sheet_id"],
        "sheet_name": row["sheet_name"],
        "row_number": int(row["row_number"]),
        "column_number": int(row["column_number"]),
        "cell_id": row["cell_id"],
        "coordinate": row["coordinate"],
        "raw_value": row["raw_value"],
        "cell_type": row["cell_type"],
        "target_type": row["target_type"],
        "rule_id": row["rule_id"],
    }


def _latest_run(connection: sqlite3.Connection, dataset_id: str) -> sqlite3.Row | None:
    return connection.execute("SELECT * FROM p4_runs WHERE dataset_id = ? ORDER BY revision DESC LIMIT 1", (dataset_id,)).fetchone()


def _require_run(connection: sqlite3.Connection, run_id: str) -> sqlite3.Row:
    run = connection.execute("SELECT * FROM p4_runs WHERE run_id = ?", (run_id,)).fetchone()
    if run is None:
        raise L1Error("run_not_found", "没有这次 P4 标准化记录。", 404)
    return run


def _sample_limit(config: dict) -> int:
    raw = config.get("sample_records", _SAMPLE_DEFAULT)
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return _SAMPLE_DEFAULT
    return min(_SAMPLE_MAX, max(1, value))


def _check_run_id(run_id: str) -> None:
    if not run_id.startswith("P4-") or len(run_id) != 15:
        raise L1Error("run_not_found", "没有这次 P4 标准化记录。", 404)


def _check_page(offset: int, limit: int) -> None:
    if offset < 0 or limit < 1 or limit > 200:
        raise L1Error("invalid_page", "offset 必须大于等于 0，limit 必须是 1 到 200 的整数。", 422)


def _ready(dataset_id: str) -> sqlite3.Row:
    dataset = _require(dataset_id)
    _require_indexed(dataset)
    return dataset


def _open() -> sqlite3.Connection:
    connection = _connect()
    ensure_schema_tables(connection)
    ensure_tables(connection)
    return connection
