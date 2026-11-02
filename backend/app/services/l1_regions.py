"""L1 P1：工作表与数据区域识别。

识别只读取 P0 索引。预览不写正式结果。
相同执行签名复用已有修订；手工修订不会被随后的自动识别覆盖。
v1 修订仍按原表读取。
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone

from app.services.l1_region_detect import RULE_ID, RULE_SUMMARY, Draft, OccRow, boundary_row_numbers, detect_sheet
from app.services.l1_store import L1Error, _column_letter, _connect, _require, _require_indexed

_ROLES = {"data_table", "note", "blank", "unclassified"}
_METHODS = {"heuristic", "user", "coverage"}
_READY = {"completed", "completed_with_warnings"}
_WARNING_CODES = {
    "header_ambiguous",
    "multi_row_header",
    "uneven_width",
    "hidden_rows",
    "header_only",
    "auxiliary_content",
    "empty_sheet",
    "merge_overlap",
    "recovered_table",
    "header_boundary_word",
}


@dataclass
class _Region:
    client_region_id: str
    sheet_id: str
    sheet_name: str
    role: str
    start_row: int
    end_row: int
    start_column: int
    end_column: int
    header_start_row: int | None
    header_row_count: int
    data_start_row: int | None
    data_end_row: int | None
    detection_method: str
    confidence: str
    confirmation_status: str
    confirmed: bool
    record_count: int
    columns: list[dict]
    warnings: list[dict]
    sheet_enabled: bool = True
    region_id: str | None = None
    review: bool = False

    def geometry(self) -> tuple:
        return (
            self.sheet_id,
            self.role,
            self.start_row,
            self.end_row,
            self.start_column,
            self.end_column,
            self.header_start_row or 0,
            self.header_row_count,
            self.sheet_enabled,
        )


@dataclass
class _Plan:
    regions: list[_Region]
    settings: list[dict]
    exceptions: list[dict]
    summaries: list[dict]
    status: str
    source: str


def suggest_regions(dataset_id: str) -> dict:
    dataset = _ready(dataset_id)
    connection = _connect()
    try:
        plan = _detect_plan(connection, dataset, {}, "auto")
    finally:
        connection.close()
    return _public_plan(dataset, plan, persisted=False, preview=False, diff=None, reused=False)


def preview_regions(dataset_id: str, config: dict) -> dict:
    dataset = _ready(dataset_id)
    connection = _connect()
    try:
        latest = _latest_run(connection, dataset_id)
        if latest is not None and _same_request(connection, dataset, latest, config):
            body = _run_body(connection, dataset_id, str(latest["run_id"]), reused=False)
            diff = _empty_diff(latest, body["regions"])
        else:
            plan = _compile(connection, dataset, config, latest)
            diff = _diff(connection, dataset_id, plan.regions)
            body = _public_plan(dataset, plan, persisted=False, preview=True, diff=diff, reused=False)
            return body
    finally:
        connection.close()
    body["persisted"] = False
    body["preview"] = True
    body["diff"] = diff
    body["reused"] = False
    return body


def apply_regions(dataset_id: str, config: dict) -> dict:
    dataset = _ready(dataset_id)
    connection = _connect()
    reused = False
    try:
        latest = _latest_run(connection, dataset_id)
        if _auto_request(config) and _reuse_auto(dataset, latest, config):
            body = _run_body(connection, dataset_id, str(latest["run_id"]), reused=True)
            reused = True
        elif latest is not None and _same_request(connection, dataset, latest, config):
            body = _run_body(connection, dataset_id, str(latest["run_id"]), reused=True)
            reused = True
        else:
            plan = _compile(connection, dataset, config, latest)
            signature = _signature(dataset, plan)
            found = _find_signature(connection, dataset_id, dataset["sha256"], signature)
            if found is not None:
                body = _run_body(connection, dataset_id, str(found["run_id"]), reused=True)
                reused = True
            else:
                body = _insert_run(connection, dataset, plan, config, signature)
                reused = False
    except L1Error:
        if connection.in_transaction:
            connection.rollback()
        raise
    except sqlite3.Error as exc:
        if connection.in_transaction:
            connection.rollback()
        raise L1Error("storage_failed", f"P1 区域索引没有写入：{exc}", 500) from exc
    finally:
        connection.close()
    body["reused"] = reused
    if body.get("status") in _READY:
        from app.services.l1_grouping import auto_apply_grouping

        auto_apply_grouping(dataset_id)
    return body


def auto_apply_regions(dataset_id: str) -> dict | None:
    dataset = _require(dataset_id)
    if dataset["parse_status"] == "failed" or dataset["p0_status"] != "completed":
        return None
    try:
        return apply_regions(dataset_id, {"source": "auto"})
    except L1Error as exc:
        _record_failure(dataset, exc.message)
        return None


def list_regions(dataset_id: str, run_id: str | None = None) -> dict:
    dataset = _require(dataset_id)
    connection = _connect()
    try:
        if run_id:
            found = connection.execute("SELECT run_id FROM p1_runs WHERE dataset_id = ? AND run_id = ?", (dataset_id, run_id)).fetchone()
            if found is None:
                raise L1Error("run_not_found", "没有这个 P1 执行版本。", 404)
            chosen = run_id
        else:
            found = connection.execute("SELECT run_id FROM p1_runs WHERE dataset_id = ? ORDER BY revision DESC LIMIT 1", (dataset_id,)).fetchone()
            chosen = None if found is None else str(found["run_id"])
        if chosen is None:
            return {
                "dataset_id": dataset_id,
                "applied": False,
                "persisted": False,
                "preview": False,
                "reused": False,
                "run": None,
                "regions": [],
                "exceptions": [],
                "sheet_summaries": [],
                "sheet_settings": _sheet_settings(connection, dataset_id, {}),
                "needs_confirmation": [],
                "p0_sha256": dataset["sha256"],
                "p0_index_version": _index_version(dataset),
                "downstream_ready": False,
                "status": "not_started",
            }
        return _run_body(connection, dataset_id, chosen, reused=False)
    finally:
        connection.close()


def list_region_rows(region_id: str, offset: int, limit: int) -> dict:
    if not region_id.startswith("RG-") or len(region_id) != 15:
        raise L1Error("region_not_found", "没有这个数据区域。", 404)
    if offset < 0:
        raise L1Error("invalid_offset", "offset 必须是大于等于 0 的整数。", 422)
    if limit < 1 or limit > 200:
        raise L1Error("invalid_limit", "limit 必须是 1 到 200 的整数。", 422)
    connection = _connect()
    try:
        region = connection.execute("SELECT * FROM p1_regions WHERE region_id = ?", (region_id,)).fetchone()
        if region is None:
            raise L1Error("region_not_found", "没有这个数据区域。", 404)
        sheet = connection.execute("SELECT name FROM sheets WHERE dataset_id = ? AND sheet_id = ?", (region["dataset_id"], region["sheet_id"])).fetchone()
        total = _count_records(connection, region)
        rows = _record_page(connection, region, offset, limit)
    finally:
        connection.close()
    return {
        "region_id": region_id,
        "dataset_id": region["dataset_id"],
        "sheet_id": region["sheet_id"],
        "sheet_name": sheet["name"] if sheet else "",
        "role": region["role"],
        "region_type": region["role"],
        "range_label": _range_label(region["start_row"], region["start_column"], region["end_row"], region["end_column"]),
        "columns": json.loads(region["column_json"]),
        "total_records": total,
        "offset": offset,
        "limit": limit,
        "rows": rows,
    }


def _ready(dataset_id: str) -> sqlite3.Row:
    dataset = _require(dataset_id)
    _require_indexed(dataset)
    return dataset


def _compile(connection: sqlite3.Connection, dataset: sqlite3.Row, config: dict, latest: sqlite3.Row | None) -> _Plan:
    if not isinstance(config, dict):
        raise L1Error("invalid_config", "区域配置必须是 JSON 对象。", 422)
    if _auto_request(config):
        plan = _detect_plan(connection, dataset, config, "auto")
        if config.get("accept_review"):
            plan.status = _gate(plan.regions, plan.exceptions, True)
            plan.source = "user"
        return plan
    return _plan_from_user(connection, dataset, config, latest)


def _detect_plan(connection: sqlite3.Connection, dataset: sqlite3.Row, config: dict, source: str) -> _Plan:
    sheets = _sheet_rows(connection, dataset["dataset_id"])
    enabled = {row["sheet_id"]: True for row in sheets}
    _apply_enabled(enabled, sheets, config.get("sheets"))
    regions: list[_Region] = []
    exceptions: list[dict] = []
    summaries: list[dict] = []
    settings = []
    for sheet in sheets:
        detected, summary = _detect_sheet(connection, dataset["dataset_id"], sheet)
        for region in detected:
            region.sheet_enabled = enabled.get(sheet["sheet_id"], True)
        regions.extend(detected)
        exceptions.extend(summary["exceptions"])
        summaries.append({**summary["summary"], "enabled": enabled.get(sheet["sheet_id"], True)})
        settings.append(_setting(sheet, enabled))
    _reject_overlap(regions)
    status = _gate(regions, exceptions, bool(config.get("accept_review")))
    return _Plan(regions, settings, exceptions, summaries, status, source)


def _plan_from_user(connection: sqlite3.Connection, dataset: sqlite3.Row, config: dict, latest: sqlite3.Row | None) -> _Plan:
    sheets = _sheet_rows(connection, dataset["dataset_id"])
    enabled = {row["sheet_id"]: True for row in sheets}
    _apply_enabled(enabled, sheets, config.get("sheets"))
    previous = _load_run_regions(connection, str(latest["run_id"])) if latest is not None else []
    previous_by_key = {region.geometry()[:8]: region for region in previous}
    submitted = _regions_from_config(connection, dataset, config.get("regions"), previous_by_key)
    for region in submitted:
        region.sheet_enabled = enabled.get(region.sheet_id, True)
    _reject_overlap(submitted)
    changed = _changed_sheets(previous, submitted)
    previous_config = _config_of(latest) if latest is not None else {}
    exceptions = [item for item in previous_config.get("exceptions") or [] if item.get("sheet_id") not in changed]
    summaries = [item for item in previous_config.get("sheet_summaries") or [] if item.get("sheet_id") not in changed]
    for sheet in sheets:
        if sheet["sheet_id"] not in changed:
            continue
        occupied, nonempty = _load_occupied(connection, dataset["dataset_id"], sheet)
        sheet_regions = [region for region in submitted if region.sheet_id == sheet["sheet_id"] and region.role == "data_table"]
        uncovered = _uncovered_count(occupied, sheet_regions)
        exceptions.extend(_user_exceptions(sheet, occupied, sheet_regions))
        summaries.append(
            {
                "sheet_id": sheet["sheet_id"],
                "name": sheet["name"],
                "visibility": sheet["visibility"],
                "enabled": enabled.get(sheet["sheet_id"], True),
                "nonempty_cells": nonempty,
                "covered_cells": nonempty - uncovered,
                "auxiliary_cells": uncovered,
                "data_table_count": len(sheet_regions),
                "empty": nonempty == 0,
            }
        )
    if not summaries:
        summaries = list(previous_config.get("sheet_summaries") or [])
    settings = [_setting(sheet, enabled) for sheet in sheets]
    status = _gate(submitted, exceptions, bool(config.get("accept_review")))
    return _Plan(submitted, settings, exceptions, summaries, status, "user")


def _detect_sheet(connection: sqlite3.Connection, dataset_id: str, sheet: sqlite3.Row) -> tuple[list[_Region], dict]:
    occupied, nonempty = _load_occupied(connection, dataset_id, sheet)
    labels = _boundary_labels(connection, dataset_id, sheet["sheet_id"], boundary_row_numbers(occupied))
    detected = detect_sheet(sheet["sheet_id"], sheet["name"], occupied, labels, nonempty)
    merges = _json_list(sheet["merge_json"])
    regions = []
    for index, draft in enumerate(detected.drafts, start=1):
        draft.warnings.extend(_merge_warnings(merges, draft.start_row, draft.end_row, draft.start_column, draft.end_column))
        regions.append(_region_from_draft(connection, dataset_id, sheet, draft, index))
    summary = {
        "sheet_id": sheet["sheet_id"],
        "name": sheet["name"],
        "visibility": sheet["visibility"],
        "nonempty_cells": detected.nonempty_cells,
        "covered_cells": detected.covered_cells,
        "auxiliary_cells": detected.nonempty_cells - detected.covered_cells,
        "data_table_count": len(regions),
        "empty": detected.nonempty_cells == 0,
    }
    return regions, {"summary": summary, "exceptions": detected.exceptions}


def _region_from_draft(connection: sqlite3.Connection, dataset_id: str, sheet: sqlite3.Row, draft: Draft, index: int) -> _Region:
    header_rows = draft.rows[: draft.header_count]
    data_rows = draft.data_rows
    header_start = header_rows[0].row_number if header_rows else None
    columns = _columns(connection, dataset_id, sheet["sheet_id"], draft.start_column, draft.end_column, header_start, draft.header_count) if draft.header_count else _columns(connection, dataset_id, sheet["sheet_id"], draft.start_column, draft.end_column, None, 0)
    return _Region(
        client_region_id=f"R2-{int(sheet['sheet_index']):02d}-{index:02d}",
        sheet_id=sheet["sheet_id"],
        sheet_name=sheet["name"],
        role="data_table",
        start_row=draft.start_row,
        end_row=draft.end_row,
        start_column=draft.start_column,
        end_column=draft.end_column,
        header_start_row=header_start,
        header_row_count=draft.header_count,
        data_start_row=data_rows[0].row_number if data_rows else None,
        data_end_row=data_rows[-1].row_number if data_rows else None,
        detection_method=draft.detection_method if draft.detection_method in _METHODS else "heuristic",
        confidence=draft.confidence,
        confirmation_status="suggested",
        confirmed=False,
        record_count=len(data_rows),
        columns=columns,
        warnings=draft.warnings,
        review=draft.review,
    )


def _regions_from_config(connection: sqlite3.Connection, dataset: sqlite3.Row, payload: object, previous: dict[tuple, _Region]) -> list[_Region]:
    if not isinstance(payload, list):
        raise L1Error("invalid_config", "regions 必须是数组。", 422)
    if len(payload) > 400:
        raise L1Error("invalid_config", "一次最多提交 400 个区域。", 422)
    sheets = {row["sheet_id"]: row for row in _sheet_rows(connection, dataset["dataset_id"])}
    regions: list[_Region] = []
    for index, item in enumerate(payload):
        if not isinstance(item, dict):
            raise L1Error("invalid_config", "每个区域必须是对象。", 422)
        sheet = sheets.get(item.get("sheet_id"))
        if sheet is None:
            raise L1Error("sheet_not_found", "区域引用了不存在的工作表。", 422, {"index": index})
        role = item.get("role") or item.get("region_type")
        if role not in _ROLES:
            raise L1Error("invalid_region", "区域类型只支持数据表、说明、空行或未分类。", 422, {"index": index})
        start_row = _integer(item.get("start_row"), "起始行", index)
        end_row = _integer(item.get("end_row"), "结束行", index)
        start_column = _integer(item.get("start_column", item.get("start_col")), "起始列", index)
        end_column = _integer(item.get("end_column", item.get("end_col")), "结束列", index)
        if end_row < start_row or end_column < start_column:
            raise L1Error("invalid_region", "区域结束位置不能早于起始位置。", 422, {"index": index})
        if int(sheet["max_row"]) and end_row > int(sheet["max_row"]):
            raise L1Error("invalid_region", "区域超出了工作表的原始行范围。", 422, {"index": index})
        if int(sheet["max_column"]) and end_column > int(sheet["max_column"]):
            raise L1Error("invalid_region", "区域超出了工作表的原始列范围。", 422, {"index": index})
        header_count = item.get("header_row_count", 0)
        if isinstance(header_count, bool) or not isinstance(header_count, int) or header_count < 0:
            raise L1Error("invalid_region", "表头行数必须是大于等于 0 的整数。", 422, {"index": index})
        header_start = item.get("header_start_row")
        if role != "data_table":
            header_count = 0
            header_start = None
        elif header_count == 0:
            header_start = None
        else:
            header_start = _integer(header_start, "表头起始行", index)
            if header_start < start_row or header_start + header_count - 1 > end_row:
                raise L1Error("invalid_region", "表头范围必须落在数据区域内。", 422, {"index": index})
        key = (str(sheet["sheet_id"]), str(role), start_row, end_row, start_column, end_column, header_start or 0, header_count)
        known = previous.get(key)
        if known is not None:
            regions.append(_copy_region(known, str(item.get("client_region_id") or known.client_region_id)))
            continue
        data_start, data_end, record_count = _measure(connection, dataset["dataset_id"], str(sheet["sheet_id"]), str(role), start_row, end_row, header_start, header_count)
        columns = _columns(connection, dataset["dataset_id"], str(sheet["sheet_id"]), start_column, end_column, header_start, header_count) if role == "data_table" else []
        warnings = [{"code": "user_range", "message": "这个范围由用户配置。"}]
        warnings.extend(_merge_warnings(_json_list(sheet["merge_json"]), start_row, end_row, start_column, end_column))
        regions.append(
            _Region(
                client_region_id=str(item.get("client_region_id") or f"UR-{index:03d}"),
                sheet_id=str(sheet["sheet_id"]),
                sheet_name=str(sheet["name"]),
                role=str(role),
                start_row=start_row,
                end_row=end_row,
                start_column=start_column,
                end_column=end_column,
                header_start_row=header_start,
                header_row_count=header_count,
                data_start_row=data_start,
                data_end_row=data_end,
                detection_method="user",
                confidence=item.get("confidence") if item.get("confidence") in {"high", "medium", "low"} else "medium",
                confirmation_status="suggested",
                confirmed=bool(item.get("confirmed")),
                record_count=record_count,
                columns=columns,
                warnings=warnings,
            )
        )
    return regions


def _copy_region(region: _Region, client_id: str) -> _Region:
    return _Region(
        client_region_id=client_id,
        sheet_id=region.sheet_id,
        sheet_name=region.sheet_name,
        role=region.role,
        start_row=region.start_row,
        end_row=region.end_row,
        start_column=region.start_column,
        end_column=region.end_column,
        header_start_row=region.header_start_row,
        header_row_count=region.header_row_count,
        data_start_row=region.data_start_row,
        data_end_row=region.data_end_row,
        detection_method=region.detection_method,
        confidence=region.confidence,
        confirmation_status=region.confirmation_status,
        confirmed=region.confirmed,
        record_count=region.record_count,
        columns=region.columns,
        warnings=list(region.warnings),
        sheet_enabled=region.sheet_enabled,
        review=region.review,
    )


def _insert_run(connection: sqlite3.Connection, dataset: sqlite3.Row, plan: _Plan, config: dict, signature: str) -> dict:
    dataset_id = str(dataset["dataset_id"])
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    connection.execute("BEGIN IMMEDIATE")
    current = connection.execute("SELECT MAX(revision) AS revision FROM p1_runs WHERE dataset_id = ?", (dataset_id,)).fetchone()
    revision = int(current["revision"] or 0) + 1
    run_id = "P1-" + uuid.uuid4().hex[:12]
    unclassified = sum(int(item.get("cell_count") or 0) for item in plan.exceptions if item.get("code") != "empty_sheet")
    records = sum(item.record_count for item in plan.regions if item.role == "data_table")
    stored = {
        "rule_id": RULE_ID,
        "rule_version": RULE_ID,
        "source": plan.source,
        "accept_review": bool(config.get("accept_review")),
        "execution_signature": signature,
        "p0_sha256": dataset["sha256"],
        "p0_index_version": _index_version(dataset),
        "sheet_settings": plan.settings,
        "sheet_summaries": plan.summaries,
        "exceptions": plan.exceptions,
        "regions": [_geometry_dict(region) for region in plan.regions],
    }
    connection.execute(
        """
        INSERT INTO p1_runs (
            run_id, dataset_id, revision, status, created_at, rule_id, p0_sha256, config_json,
            region_count, unclassified_cell_count, record_count
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            run_id,
            dataset_id,
            revision,
            plan.status,
            now,
            RULE_ID,
            dataset["sha256"],
            json.dumps(stored, ensure_ascii=False, separators=(",", ":")),
            len(plan.regions),
            unclassified,
            records,
        ),
    )
    for ordinal, region in enumerate(plan.regions):
        region.region_id = "RG-" + uuid.uuid4().hex[:12]
        region.confirmation_status = "confirmed"
        connection.execute(
            """
            INSERT INTO p1_regions (
                region_id, run_id, dataset_id, client_region_id, sheet_id, sheet_enabled, role,
                start_row, end_row, start_column, end_column, header_start_row, header_row_count,
                data_start_row, data_end_row, detection_method, confidence, confirmation_status,
                record_count, column_json, warning_json, ordinal
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                region.region_id,
                run_id,
                dataset_id,
                region.client_region_id,
                region.sheet_id,
                1 if region.sheet_enabled else 0,
                region.role,
                region.start_row,
                region.end_row,
                region.start_column,
                region.end_column,
                region.header_start_row,
                region.header_row_count,
                region.data_start_row,
                region.data_end_row,
                region.detection_method,
                region.confidence,
                region.confirmation_status,
                region.record_count,
                json.dumps(region.columns, ensure_ascii=False, separators=(",", ":")),
                json.dumps(region.warnings, ensure_ascii=False, separators=(",", ":")),
                ordinal,
            ),
        )
    connection.execute("COMMIT")
    return _run_body(connection, dataset_id, run_id, reused=False)


def _record_failure(dataset: sqlite3.Row, message: str) -> None:
    connection = _connect()
    try:
        now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        connection.execute("BEGIN IMMEDIATE")
        current = connection.execute("SELECT MAX(revision) AS revision FROM p1_runs WHERE dataset_id = ?", (dataset["dataset_id"],)).fetchone()
        revision = int(current["revision"] or 0) + 1
        connection.execute(
            """
            INSERT INTO p1_runs (
                run_id, dataset_id, revision, status, created_at, rule_id, p0_sha256, config_json,
                region_count, unclassified_cell_count, record_count
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "P1-" + uuid.uuid4().hex[:12],
                dataset["dataset_id"],
                revision,
                "failed",
                now,
                RULE_ID,
                dataset["sha256"],
                json.dumps({"source": "auto", "rule_version": RULE_ID, "error": message, "p0_index_version": _index_version(dataset)}, ensure_ascii=False),
                0,
                0,
                0,
            ),
        )
        connection.execute("COMMIT")
    except sqlite3.Error:
        if connection.in_transaction:
            connection.rollback()
    finally:
        connection.close()


def _load_occupied(connection: sqlite3.Connection, dataset_id: str, sheet: sqlite3.Row) -> tuple[list[OccRow], int]:
    cached = connection.execute(
        "SELECT nonempty_cells, occupancy_json FROM p0_sheet_occupancy WHERE dataset_id = ? AND sheet_id = ?",
        (dataset_id, sheet["sheet_id"]),
    ).fetchone()
    if cached is not None:
        try:
            payload = json.loads(cached["occupancy_json"])
        except json.JSONDecodeError:
            payload = None
        if isinstance(payload, list):
            occupied = [
                OccRow(
                    int(item["row_number"]),
                    bool(item.get("hidden")),
                    frozenset(int(column) for column in item.get("columns") or []),
                    {int(column): str(kind) for column, kind in (item.get("kinds") or {}).items()},
                    int(item.get("max_merge") or 1),
                )
                for item in payload
                if isinstance(item, dict)
            ]
            return occupied, int(cached["nonempty_cells"])
    hidden = {
        int(row["row_number"]): bool(row["hidden"])
        for row in connection.execute("SELECT row_number, hidden FROM source_rows WHERE dataset_id = ? AND sheet_id = ?", (dataset_id, sheet["sheet_id"]))
    }
    cells = connection.execute(
        """
        SELECT row_number, column_number, cell_type, merge_range
        FROM raw_cells
        WHERE dataset_id = ? AND sheet_id = ? AND raw_value <> ''
        ORDER BY row_number, column_number
        """,
        (dataset_id, sheet["sheet_id"]),
    ).fetchall()
    grouped: dict[int, dict] = {}
    for cell in cells:
        bucket = grouped.setdefault(int(cell["row_number"]), {"kinds": {}, "merge": 1})
        bucket["kinds"][int(cell["column_number"])] = cell["cell_type"]
        bucket["merge"] = max(bucket["merge"], _merge_span(cell["merge_range"]))
    for ref in _json_list(sheet["merge_json"]):
        if not isinstance(ref, str) or ":" not in ref:
            continue
        (left, top), (right, bottom) = _range_bounds(ref)
        span = abs(right - left) + 1
        for row_number, bucket in grouped.items():
            if top <= row_number <= bottom and any(left <= column <= right for column in bucket["kinds"]):
                bucket["merge"] = max(bucket["merge"], span)
    occupied = [
        OccRow(row_number, hidden.get(row_number, False), frozenset(bucket["kinds"]), dict(bucket["kinds"]), bucket["merge"])
        for row_number, bucket in sorted(grouped.items())
    ]
    payload = [
        {
            "row_number": row.row_number,
            "hidden": row.hidden,
            "columns": sorted(row.columns),
            "kinds": {str(column): kind for column, kind in row.kinds.items()},
            "max_merge": row.max_merge,
        }
        for row in occupied
    ]
    connection.execute(
        """
        INSERT INTO p0_sheet_occupancy (dataset_id, sheet_id, nonempty_cells, occupancy_json)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(dataset_id, sheet_id) DO UPDATE SET
            nonempty_cells = excluded.nonempty_cells,
            occupancy_json = excluded.occupancy_json
        """,
        (dataset_id, sheet["sheet_id"], len(cells), json.dumps(payload, ensure_ascii=False, separators=(",", ":"))),
    )
    return occupied, len(cells)


def _boundary_labels(connection: sqlite3.Connection, dataset_id: str, sheet_id: str, rows: list[int]) -> dict[int, str]:
    unique = sorted({row for row in rows if row > 0})
    if not unique:
        return {}
    placeholders = ",".join("?" for _ in unique)
    found = connection.execute(
        f"""
        SELECT row_number, column_number, raw_value
        FROM raw_cells
        WHERE dataset_id = ? AND sheet_id = ? AND raw_value <> '' AND row_number IN ({placeholders})
        ORDER BY row_number, column_number
        """,
        [dataset_id, sheet_id, *unique],
    ).fetchall()
    labels: dict[int, str] = {}
    for cell in found:
        labels.setdefault(int(cell["row_number"]), str(cell["raw_value"]))
    return labels


def _measure(connection, dataset_id: str, sheet_id: str, role: str, start_row: int, end_row: int, header_start: int | None, header_count: int) -> tuple[int | None, int | None, int]:
    header_end = header_start + header_count - 1 if role == "data_table" and header_count and header_start else 0
    rows = connection.execute(
        """
        SELECT DISTINCT row_number FROM raw_cells
        WHERE dataset_id = ? AND sheet_id = ? AND raw_value <> ''
          AND row_number BETWEEN ? AND ?
          AND (? = 0 OR row_number < ? OR row_number > ?)
        ORDER BY row_number
        """,
        (dataset_id, sheet_id, start_row, end_row, header_count if role == "data_table" else 0, header_start or 0, header_end),
    ).fetchall()
    numbers = [int(row["row_number"]) for row in rows]
    if not numbers:
        return None, None, 0
    return numbers[0], numbers[-1], len(numbers)


def _columns(connection, dataset_id: str, sheet_id: str, start_column: int, end_column: int, header_start: int | None, header_count: int) -> list[dict]:
    header_cells: dict[tuple[int, int], sqlite3.Row] = {}
    if header_start and header_count:
        found = connection.execute(
            """
            SELECT row_number, column_number, raw_value, cell_id, coordinate
            FROM raw_cells
            WHERE dataset_id = ? AND sheet_id = ? AND row_number BETWEEN ? AND ?
              AND column_number BETWEEN ? AND ?
            ORDER BY row_number, column_number
            """,
            (dataset_id, sheet_id, header_start, header_start + header_count - 1, start_column, end_column),
        ).fetchall()
        header_cells = {(int(cell["row_number"]), int(cell["column_number"])): cell for cell in found}
    columns = []
    for column in range(start_column, end_column + 1):
        parts = []
        if header_start and header_count:
            for row_number in range(header_start, header_start + header_count):
                cell = header_cells.get((row_number, column))
                parts.append(
                    {
                        "row_number": row_number,
                        "raw_value": "" if cell is None else cell["raw_value"],
                        "cell_id": None if cell is None else cell["cell_id"],
                        "coordinate": _coordinate(column, row_number) if cell is None else cell["coordinate"],
                    }
                )
        columns.append(
            {
                "column_number": column,
                "column_id": f"C{column:04d}",
                "letter": _column_letter(column),
                "header": [part["raw_value"] for part in parts if part["raw_value"] != ""],
                "header_parts": parts,
            }
        )
    return columns


def _user_exceptions(sheet: sqlite3.Row, occupied: list[OccRow], regions: list[_Region]) -> list[dict]:
    items = []
    for row in occupied:
        outside = [
            column
            for column in row.columns
            if not any(region.start_row <= row.row_number <= region.end_row and region.start_column <= column <= region.end_column for region in regions)
        ]
        if len(outside) >= 2 and row.value_cells > 0:
            items.append(
                {
                    "code": "omitted_table",
                    "sheet_id": sheet["sheet_id"],
                    "sheet_name": sheet["name"],
                    "start_row": row.row_number,
                    "end_row": row.row_number,
                    "start_column": min(outside),
                    "end_column": max(outside),
                    "cell_count": len(outside),
                    "message": f"工作表「{sheet['name']}」第 {row.row_number} 行有数据列没有进入数据表。",
                }
            )
    if items:
        return items[:20]
    return []


def _uncovered_count(occupied: list[OccRow], regions: list[_Region]) -> int:
    total = 0
    for row in occupied:
        for column in row.columns:
            if not any(region.start_row <= row.row_number <= region.end_row and region.start_column <= column <= region.end_column for region in regions):
                total += 1
    return total


def _changed_sheets(previous: list[_Region], submitted: list[_Region]) -> set[str]:
    def grouped(regions: list[_Region]) -> dict[str, set[tuple]]:
        found: dict[str, set[tuple]] = {}
        for region in regions:
            found.setdefault(region.sheet_id, set()).add(region.geometry()[:8])
        return found

    old = grouped(previous)
    new = grouped(submitted)
    return {sheet_id for sheet_id in set(old) | set(new) if old.get(sheet_id) != new.get(sheet_id)}


def _gate(regions: list[_Region], exceptions: list[dict], accept: bool) -> str:
    blocking = [item for item in exceptions if item.get("code") == "omitted_table"]
    if any(region.review for region in regions) and not accept:
        blocking.append({"code": "omitted_table"})
    if blocking and not accept:
        return "needs_review"
    codes = [str(item.get("code") or "") for item in exceptions]
    codes.extend(str(item.get("code") or "") for region in regions for item in region.warnings)
    if any(code in _WARNING_CODES for code in codes):
        return "completed_with_warnings"
    return "completed"


def _signature(dataset: sqlite3.Row, plan: _Plan) -> str:
    body = {
        "p0": dataset["sha256"],
        "index": _index_version(dataset),
        "rule": RULE_ID,
        "source": plan.source,
        "status": plan.status,
        "sheets": [[item["sheet_id"], bool(item["enabled"])] for item in plan.settings],
        "regions": [list(region.geometry()) for region in plan.regions],
    }
    encoded = json.dumps(body, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _index_version(dataset: sqlite3.Row) -> str:
    return f"{int(dataset['sheet_count'])}:{int(dataset['row_count'])}:{int(dataset['column_count'])}:{int(dataset['cell_count'])}"


def _auto_request(config: dict) -> bool:
    return config.get("source") == "auto" or bool(config.get("use_suggestions")) or "regions" not in config


def _reuse_auto(dataset: sqlite3.Row, latest: sqlite3.Row | None, config: dict) -> bool:
    if latest is None or latest["p0_sha256"] != dataset["sha256"] or latest["status"] == "failed":
        return False
    stored = _config_of(latest)
    if stored.get("p0_index_version") not in {None, _index_version(dataset)} and stored.get("rule_version") == RULE_ID:
        return False
    if stored.get("source") == "user":
        return True
    if stored.get("rule_version") not in {None, RULE_ID} and latest["rule_id"] != RULE_ID:
        return False
    if latest["rule_id"] != RULE_ID:
        return False
    if config.get("accept_review"):
        return False
    enables = {item["sheet_id"]: item["enabled"] for item in stored.get("sheet_settings") or []}
    requested = config.get("sheets") or []
    if not requested:
        return stored.get("source") == "auto"
    for item in requested:
        if not isinstance(item, dict):
            return False
        if bool(item.get("enabled", True)) != bool(enables.get(item.get("sheet_id"), True)):
            return False
    return stored.get("source") == "auto"


def _same_request(connection: sqlite3.Connection, dataset: sqlite3.Row, latest: sqlite3.Row, config: dict) -> bool:
    if _auto_request(config) or config.get("accept_review"):
        return False
    if latest["p0_sha256"] != dataset["sha256"] or latest["rule_id"] != RULE_ID:
        return False
    stored = _load_run_regions(connection, str(latest["run_id"]))
    try:
        submitted = _geometry_keys(config.get("regions"), stored)
    except L1Error:
        return False
    if submitted is None or submitted != {region.geometry()[:8] for region in stored}:
        return False
    enabled = {region.sheet_id: region.sheet_enabled for region in stored}
    for item in config.get("sheets") or []:
        if isinstance(item, dict) and bool(item.get("enabled", True)) != bool(enabled.get(item.get("sheet_id"), True)):
            return False
    return True


def _geometry_keys(payload: object, stored: list[_Region]) -> set[tuple] | None:
    if not isinstance(payload, list):
        return None
    keys = []
    for item in payload:
        if not isinstance(item, dict):
            return None
        role = item.get("role") or item.get("region_type")
        header_count = item.get("header_row_count") or 0
        header_start = item.get("header_start_row") or 0
        if role != "data_table":
            header_count = 0
            header_start = 0
        elif not header_count:
            header_start = 0
        keys.append(
            (
                item.get("sheet_id"),
                role,
                item.get("start_row"),
                item.get("end_row"),
                item.get("start_column", item.get("start_col")),
                item.get("end_column", item.get("end_col")),
                header_start or 0,
                header_count,
            )
        )
    unique = set(keys)
    if len(unique) != len(keys):
        return None
    if not unique and stored:
        return None
    return unique


def _find_signature(connection: sqlite3.Connection, dataset_id: str, digest: str, signature: str) -> sqlite3.Row | None:
    rows = connection.execute(
        "SELECT * FROM p1_runs WHERE dataset_id = ? AND p0_sha256 = ? ORDER BY revision DESC",
        (dataset_id, digest),
    ).fetchall()
    for row in rows:
        if _config_of(row).get("execution_signature") == signature and row["status"] in _READY:
            return row
    return None


def _latest_run(connection: sqlite3.Connection, dataset_id: str) -> sqlite3.Row | None:
    return connection.execute("SELECT * FROM p1_runs WHERE dataset_id = ? ORDER BY revision DESC LIMIT 1", (dataset_id,)).fetchone()


def _config_of(run: sqlite3.Row | None) -> dict:
    if run is None or not run["config_json"]:
        return {}
    try:
        parsed = json.loads(run["config_json"])
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _run_body(connection: sqlite3.Connection, dataset_id: str, run_id: str, reused: bool) -> dict:
    run = connection.execute("SELECT * FROM p1_runs WHERE run_id = ?", (run_id,)).fetchone()
    regions = connection.execute("SELECT * FROM p1_regions WHERE run_id = ? ORDER BY ordinal", (run_id,)).fetchall()
    config = _config_of(run)
    enabled = {}
    for region in regions:
        enabled[region["sheet_id"]] = bool(region["sheet_enabled"])
    for item in config.get("sheet_settings") or []:
        if item.get("sheet_id") not in enabled:
            enabled[item["sheet_id"]] = bool(item.get("enabled", True))
    status = str(run["status"])
    return {
        "dataset_id": dataset_id,
        "applied": True,
        "persisted": True,
        "preview": False,
        "reused": reused,
        "rule_id": run["rule_id"],
        "rule_summary": RULE_SUMMARY if run["rule_id"] == RULE_ID else "历史识别规则 " + str(run["rule_id"]),
        "p0_sha256": run["p0_sha256"],
        "p0_index_version": config.get("p0_index_version"),
        "execution_signature": config.get("execution_signature"),
        "status": status,
        "downstream_ready": status in _READY,
        "run": {
            "run_id": run["run_id"],
            "revision": int(run["revision"]),
            "status": status,
            "created_at": run["created_at"],
            "rule_id": run["rule_id"],
            "rule_version": run["rule_id"],
            "p0_sha256": run["p0_sha256"],
            "region_count": int(run["region_count"]),
            "unclassified_cell_count": int(run["unclassified_cell_count"]),
            "record_count": int(run["record_count"]),
            "source": config.get("source") or "unknown",
        },
        "regions": [_public_stored(connection, region, str(run["rule_id"])) for region in regions],
        "exceptions": list(config.get("exceptions") or []),
        "sheet_summaries": list(config.get("sheet_summaries") or []),
        "sheet_settings": _sheet_settings(connection, dataset_id, enabled),
        "needs_confirmation": [],
        "unclassified_cell_count": int(run["unclassified_cell_count"]),
    }


def _public_plan(dataset: sqlite3.Row, plan: _Plan, persisted: bool, preview: bool, diff: dict | None, reused: bool) -> dict:
    records = sum(region.record_count for region in plan.regions if region.role == "data_table")
    unclassified = sum(int(item.get("cell_count") or 0) for item in plan.exceptions if item.get("code") != "empty_sheet")
    body = {
        "dataset_id": dataset["dataset_id"],
        "persisted": persisted,
        "preview": preview,
        "applied": False,
        "reused": reused,
        "rule_id": RULE_ID,
        "rule_summary": RULE_SUMMARY,
        "p0_sha256": dataset["sha256"],
        "p0_index_version": _index_version(dataset),
        "status": plan.status,
        "downstream_ready": plan.status in _READY,
        "sheet_settings": plan.settings,
        "sheet_summaries": plan.summaries,
        "regions": [_public_region(region, str(dataset["dataset_id"])) for region in plan.regions],
        "exceptions": plan.exceptions,
        "needs_confirmation": [],
        "unclassified_cell_count": unclassified,
        "record_count": records,
    }
    if diff is not None:
        body["diff"] = diff
    return body


def _public_region(region: _Region, dataset_id: str) -> dict:
    warnings = [_warning(item) for item in region.warnings]
    return {
        "region_id": region.region_id,
        "client_region_id": region.client_region_id,
        "dataset_id": dataset_id,
        "sheet_id": region.sheet_id,
        "sheet_name": region.sheet_name,
        "sheet_enabled": region.sheet_enabled,
        "role": region.role,
        "region_type": region.role,
        "start_row": region.start_row,
        "end_row": region.end_row,
        "start_column": region.start_column,
        "end_column": region.end_column,
        "start_col": region.start_column,
        "end_col": region.end_column,
        "header_start_row": region.header_start_row,
        "header_row_count": region.header_row_count,
        "data_start_row": region.data_start_row,
        "data_end_row": region.data_end_row,
        "data_record_count": region.record_count,
        "detection_method": region.detection_method,
        "recognition_source": region.detection_method,
        "confidence": region.confidence,
        "confidence_level": region.confidence,
        "confirmation_status": region.confirmation_status,
        "confirmed": region.confirmed,
        "record_count": region.record_count,
        "columns": region.columns,
        "warnings": warnings,
        "warning_codes": [item["code"] for item in warnings if item.get("code")],
        "warning_details": warnings,
        "rule_version": RULE_ID,
        "range_label": _range_label(region.start_row, region.start_column, region.end_row, region.end_column),
        "sample_rows": [],
    }


def _public_stored(connection: sqlite3.Connection, region: sqlite3.Row, rule_id: str) -> dict:
    sheet = connection.execute("SELECT name FROM sheets WHERE dataset_id = ? AND sheet_id = ?", (region["dataset_id"], region["sheet_id"])).fetchone()
    warnings = [_warning(item) for item in _json_list(region["warning_json"])]
    start_column = int(region["start_column"])
    end_column = int(region["end_column"])
    return {
        "region_id": region["region_id"],
        "client_region_id": region["client_region_id"],
        "dataset_id": region["dataset_id"],
        "sheet_id": region["sheet_id"],
        "sheet_name": sheet["name"] if sheet else "",
        "sheet_enabled": bool(region["sheet_enabled"]),
        "role": region["role"],
        "region_type": region["role"],
        "start_row": int(region["start_row"]),
        "end_row": int(region["end_row"]),
        "start_column": start_column,
        "end_column": end_column,
        "start_col": start_column,
        "end_col": end_column,
        "header_start_row": region["header_start_row"],
        "header_row_count": int(region["header_row_count"]),
        "data_start_row": region["data_start_row"],
        "data_end_row": region["data_end_row"],
        "data_record_count": int(region["record_count"]),
        "detection_method": region["detection_method"],
        "recognition_source": region["detection_method"],
        "confidence": region["confidence"],
        "confidence_level": region["confidence"],
        "confirmation_status": region["confirmation_status"],
        "confirmed": region["confirmation_status"] == "confirmed",
        "record_count": int(region["record_count"]),
        "columns": json.loads(region["column_json"]),
        "warnings": warnings,
        "warning_codes": [item["code"] for item in warnings if item.get("code")],
        "warning_details": warnings,
        "rule_version": rule_id,
        "range_label": _range_label(int(region["start_row"]), start_column, int(region["end_row"]), end_column),
        "sample_rows": [],
    }


def _warning(item: object) -> dict:
    if isinstance(item, dict):
        return {"code": str(item.get("code") or ""), "message": str(item.get("message") or "")}
    return {"code": "", "message": str(item)}


def _geometry_dict(region: _Region) -> dict:
    return {
        "sheet_id": region.sheet_id,
        "role": region.role,
        "start_row": region.start_row,
        "end_row": region.end_row,
        "start_column": region.start_column,
        "end_column": region.end_column,
        "header_start_row": region.header_start_row,
        "header_row_count": region.header_row_count,
        "sheet_enabled": region.sheet_enabled,
        "detection_method": region.detection_method,
    }


def _load_run_regions(connection: sqlite3.Connection, run_id: str) -> list[_Region]:
    rows = connection.execute("SELECT * FROM p1_regions WHERE run_id = ? ORDER BY ordinal", (run_id,)).fetchall()
    regions = []
    for row in rows:
        sheet = connection.execute("SELECT name FROM sheets WHERE dataset_id = ? AND sheet_id = ?", (row["dataset_id"], row["sheet_id"])).fetchone()
        regions.append(
            _Region(
                client_region_id=row["client_region_id"],
                sheet_id=row["sheet_id"],
                sheet_name=sheet["name"] if sheet else "",
                role=row["role"],
                start_row=int(row["start_row"]),
                end_row=int(row["end_row"]),
                start_column=int(row["start_column"]),
                end_column=int(row["end_column"]),
                header_start_row=row["header_start_row"],
                header_row_count=int(row["header_row_count"]),
                data_start_row=row["data_start_row"],
                data_end_row=row["data_end_row"],
                detection_method=row["detection_method"],
                confidence=row["confidence"],
                confirmation_status=row["confirmation_status"],
                confirmed=True,
                record_count=int(row["record_count"]),
                columns=json.loads(row["column_json"]),
                warnings=_json_list(row["warning_json"]),
                sheet_enabled=bool(row["sheet_enabled"]),
                region_id=row["region_id"],
            )
        )
    return regions


def _diff(connection: sqlite3.Connection, dataset_id: str, regions: list[_Region]) -> dict:
    current = _latest_run(connection, dataset_id)
    after = sum(region.record_count for region in regions if region.role == "data_table")
    if current is None:
        return _empty_diff(None, []) | {
            "added": [_diff_item(region) for region in regions],
            "record_count_after": after,
            "changed_data_row_count": after,
            "unclassified_cell_count": 0,
        }
    stored = _load_run_regions(connection, str(current["run_id"]))
    old_keys = {region.geometry()[:8] for region in stored}
    new_keys = {region.geometry()[:8] for region in regions}
    header_changes = []
    for region in regions:
        if region.role != "data_table":
            continue
        for previous in stored:
            separate = previous.end_row < region.start_row or region.end_row < previous.start_row or previous.end_column < region.start_column or region.end_column < previous.start_column
            if previous.role == "data_table" and previous.sheet_id == region.sheet_id and not separate:
                if (previous.header_start_row, previous.header_row_count) != (region.header_start_row, region.header_row_count):
                    header_changes.append({"sheet_name": region.sheet_name, "before": _header_text(previous.header_start_row, previous.header_row_count), "after": _header_text(region.header_start_row, region.header_row_count)})
                break
    return {
        "baseline_run_id": current["run_id"],
        "baseline_revision": int(current["revision"]),
        "added": [_diff_item(region) for region in regions if region.geometry()[:8] not in old_keys],
        "removed": [_diff_item(region) for region in stored if region.geometry()[:8] not in new_keys],
        "header_changes": header_changes,
        "record_count_before": sum(region.record_count for region in stored if region.role == "data_table"),
        "record_count_after": after,
        "changed_data_row_count": len(_data_rows(stored).symmetric_difference(_data_rows(regions))),
        "unclassified_cell_count": int(current["unclassified_cell_count"]),
    }


def _empty_diff(latest: sqlite3.Row | None, regions: list[dict]) -> dict:
    records = sum(int(item.get("record_count") or 0) for item in regions if item.get("role") == "data_table")
    return {
        "baseline_run_id": None if latest is None else latest["run_id"],
        "baseline_revision": None if latest is None else int(latest["revision"]),
        "added": [],
        "removed": [],
        "header_changes": [],
        "record_count_before": records,
        "record_count_after": records,
        "changed_data_row_count": 0,
        "unclassified_cell_count": 0 if latest is None else int(latest["unclassified_cell_count"]),
    }


def _data_rows(regions: list[_Region]) -> set[tuple[str, int]]:
    found = set()
    for region in regions:
        if region.role != "data_table" or region.data_start_row is None or region.data_end_row is None:
            continue
        for number in range(region.data_start_row, region.data_end_row + 1):
            found.add((region.sheet_id, number))
    return found


def _diff_item(region: _Region) -> dict:
    return {
        "sheet_name": region.sheet_name,
        "role": region.role,
        "range_label": _range_label(region.start_row, region.start_column, region.end_row, region.end_column),
        "record_count": region.record_count,
        "header": _header_text(region.header_start_row, region.header_row_count),
    }


def _header_text(start: int | None, count: int) -> str:
    if not count or start is None:
        return "无表头"
    if count == 1:
        return f"第 {start} 行"
    return f"第 {start}–{start + count - 1} 行"


def _record_page(connection: sqlite3.Connection, region, offset: int, limit: int) -> list[dict]:
    if int(region["end_row"]) < int(region["start_row"]):
        return []
    rows = connection.execute(
        f"""
        SELECT source_row_id, row_number, hidden, parse_status
        FROM source_rows
        WHERE dataset_id = ? AND sheet_id = ? AND row_number BETWEEN ? AND ?
          AND {_header_clause()}
        ORDER BY row_number
        LIMIT ? OFFSET ?
        """,
        _record_params(region) + [limit, offset],
    ).fetchall()
    if not rows:
        return []
    first = int(rows[0]["row_number"])
    last = int(rows[-1]["row_number"])
    cells = connection.execute(
        """
        SELECT cell_id, row_number, column_number, column_id, coordinate, raw_value, cell_type, source_row_id
        FROM raw_cells
        WHERE dataset_id = ? AND sheet_id = ? AND row_number BETWEEN ? AND ?
          AND column_number BETWEEN ? AND ?
        ORDER BY row_number, column_number
        """,
        (region["dataset_id"], region["sheet_id"], first, last, int(region["start_column"]), int(region["end_column"])),
    ).fetchall()
    by_row: dict[int, list[dict]] = {}
    for cell in cells:
        by_row.setdefault(int(cell["row_number"]), []).append(
            {
                "cell_id": cell["cell_id"],
                "column_number": int(cell["column_number"]),
                "column_id": cell["column_id"],
                "coordinate": cell["coordinate"],
                "raw_value": cell["raw_value"],
                "cell_type": cell["cell_type"],
                "source_row_id": cell["source_row_id"],
            }
        )
    page = []
    for index, row in enumerate(rows, start=offset + 1):
        page.append(
            {
                "record_index": index,
                "source_row_id": row["source_row_id"],
                "sheet_id": region["sheet_id"],
                "row_number": int(row["row_number"]),
                "hidden": bool(row["hidden"]),
                "parse_status": row["parse_status"],
                "values": by_row.get(int(row["row_number"]), []),
            }
        )
    return page


def _count_records(connection: sqlite3.Connection, region: sqlite3.Row) -> int:
    if int(region["end_row"]) < int(region["start_row"]):
        return 0
    found = connection.execute(
        f"""
        SELECT COUNT(*) AS total FROM source_rows
        WHERE dataset_id = ? AND sheet_id = ? AND row_number BETWEEN ? AND ?
          AND {_header_clause()}
        """,
        _record_params(region),
    ).fetchone()
    return int(found["total"])


def _header_clause() -> str:
    return "(? = 0 OR row_number < ? OR row_number > ?)"


def _record_params(region) -> list:
    header_count = int(region["header_row_count"] or 0)
    header_start = int(region["header_start_row"] or 0)
    header_end = header_start + header_count - 1 if header_count else 0
    return [region["dataset_id"], region["sheet_id"], int(region["start_row"]), int(region["end_row"]), header_count, header_start, header_end]


def _sheet_rows(connection: sqlite3.Connection, dataset_id: str) -> list[sqlite3.Row]:
    return connection.execute("SELECT * FROM sheets WHERE dataset_id = ? ORDER BY sheet_index", (dataset_id,)).fetchall()


def _apply_enabled(enabled: dict[str, bool], sheets: list[sqlite3.Row], payload: object) -> None:
    known = {row["sheet_id"] for row in sheets}
    for item in payload or []:
        if not isinstance(item, dict) or not isinstance(item.get("sheet_id"), str):
            raise L1Error("invalid_config", "工作表配置缺少 sheet_id。", 422)
        if item["sheet_id"] not in known:
            raise L1Error("sheet_not_found", "配置引用了不存在的工作表。", 422)
        enabled[item["sheet_id"]] = bool(item.get("enabled", True))


def _setting(sheet: sqlite3.Row, enabled: dict[str, bool]) -> dict:
    return {
        "sheet_id": sheet["sheet_id"],
        "name": sheet["name"],
        "visibility": sheet["visibility"],
        "enabled": enabled.get(sheet["sheet_id"], True),
        "max_row": int(sheet["max_row"]),
        "max_column": int(sheet["max_column"]),
        "merges": _json_list(sheet["merge_json"]),
    }


def _sheet_settings(connection: sqlite3.Connection, dataset_id: str, enabled: dict[str, bool]) -> list[dict]:
    return [_setting(sheet, enabled) for sheet in _sheet_rows(connection, dataset_id)]


def _reject_overlap(regions: list[_Region]) -> None:
    real = [region for region in regions if region.role == "data_table" and region.start_row >= 1 and region.start_column >= 1]
    for index, left in enumerate(real):
        for right in real[index + 1 :]:
            separate = left.sheet_id != right.sheet_id or left.end_row < right.start_row or right.end_row < left.start_row or left.end_column < right.start_column or right.end_column < left.start_column
            if not separate:
                raise L1Error("region_overlap", f"工作表「{left.sheet_name}」的区域 {_range_label(left.start_row, left.start_column, left.end_row, left.end_column)} 与 {_range_label(right.start_row, right.start_column, right.end_row, right.end_column)} 重叠。", 422)


def _merge_warnings(merges: list, start_row: int, end_row: int, start_column: int, end_column: int) -> list[dict]:
    warnings = []
    for ref in merges:
        if not isinstance(ref, str) or ":" not in ref:
            continue
        (left, top), (right, bottom) = _range_bounds(ref)
        separated = bottom < start_row or top > end_row or right < start_column or left > end_column
        if not separated:
            warnings.append({"code": "merge_overlap", "message": f"合并单元格 {ref} 与这个区域相交，原合并关系已保留。"})
    return warnings


def _range_bounds(ref: str) -> tuple[tuple[int, int], tuple[int, int]]:
    start, end = ref.split(":", 1)
    return (_column_number(start), _row_number(start)), (_column_number(end), _row_number(end))


def _merge_span(ref: str | None) -> int:
    if not ref or ":" not in ref:
        return 1
    (left, _), (right, _) = _range_bounds(ref)
    return abs(right - left) + 1


def _column_number(coordinate: str) -> int:
    number = 0
    for char in coordinate:
        if char.isalpha():
            number = number * 26 + ord(char.upper()) - 64
    return number


def _row_number(coordinate: str) -> int:
    digits = "".join(char for char in coordinate if char.isdigit())
    return int(digits or "0")


def _coordinate(column: int, row_number: int) -> str:
    return f"{_column_letter(column)}{row_number}"


def _range_label(start_row: int, start_column: int, end_row: int, end_column: int) -> str:
    if start_row < 1 or start_column < 1 or end_row < start_row or end_column < start_column:
        return "空工作表"
    return f"{_column_letter(start_column)}{start_row}:{_column_letter(end_column)}{end_row}"


def _integer(value: object, name: str, index: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise L1Error("invalid_region", f"{name}必须是大于等于 1 的整数。", 422, {"index": index})
    return value


def _json_list(value: str | list | None) -> list:
    if isinstance(value, list):
        return value
    if not value:
        return []
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        return []
    return parsed if isinstance(parsed, list) else []
