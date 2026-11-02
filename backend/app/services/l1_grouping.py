"""L1 P2：按 P1 业务记录建立设备分组索引。

不改 P0 原件，不改 P1 区域，不清洗字段，也不把跨表相同编号自动当成同一资产。
默认 conservative：cross_sheet=separate；跨表同号记候选，不阻断自动执行。
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone

from app.services.l1_grouping_detect import _PLACEHOLDERS, select_equipment_field, score_columns
from app.services.l1_store import L1Error, _connect, _require, _require_indexed

RULE_ID = "equipment_grouping_v2"
RULE_SUMMARY = (
    "多特征自动识别设备编号字段；同表按规范化编号分组；默认跨表隔离（separate）。"
    "跨表同号记为潜在候选关联，不自动合并。只有显式 same_text 或可靠台账映射才跨表关联。"
    "空值、占位符与无法识别字段进入未归组；group_id 不等于 asset_id。"
)
_MATCH_MODES = {"exact", "trim_casefold"}
_CROSS = {"separate", "same_text", "ledger"}
_READY = {"completed", "completed_with_warnings"}
_PERSISTABLE = {"completed", "completed_with_warnings", "needs_review"}


@dataclass
class _Candidate:
    column_number: int
    column_id: str
    header: str
    match: str
    alias: str
    score: float = 0.0
    confidence_level: str = "none"
    recognition_source: str = "none"
    warning_codes: list[str] = field(default_factory=list)


@dataclass
class _FieldRegion:
    region_id: str
    sheet_id: str
    sheet_name: str
    range_label: str
    record_count: int
    columns: list[dict]
    candidates: list[_Candidate]
    suggested_column_id: str | None
    confirmation_status: str
    start_row: int
    end_row: int
    start_column: int
    end_column: int
    header_start_row: int | None
    header_row_count: int
    confidence_level: str = "none"
    recognition_source: str = "none"
    warning_codes: list[str] = field(default_factory=list)
    requires_review: bool = False
    candidate_fields: list[dict] = field(default_factory=list)


@dataclass
class _Member:
    source_row_id: str
    region_id: str
    sheet_id: str
    sheet_name: str
    row_number: int
    status: str
    reason: str
    original_value: str
    grouping_value: str
    match_key: str
    column_id: str
    field_header: str
    cell_id: str | None
    coordinate: str
    override_applied: bool = False
    asset_id: str | None = None
    group_id: str | None = None


@dataclass
class _Group:
    group_id: str
    equipment_id: str
    match_key: str
    scope: str
    sheet_id: str | None
    sheet_name: str
    asset_id: str | None
    record_count: int
    original_values: list[str]
    label: str
    members: list[_Member] = field(default_factory=list)


def equipment_fields(dataset_id: str) -> dict:
    dataset = _ready(dataset_id)
    connection = _connect()
    try:
        p1 = _latest_p1(connection, dataset_id)
        regions = _field_regions(connection, p1["run_id"])
        business = sum(item.record_count for item in regions)
        collisions = _cross_sheet_keys(connection, regions, "exact")
    finally:
        connection.close()
    return {
        "dataset_id": dataset_id,
        "p1_run_id": p1["run_id"],
        "p0_sha256": dataset["sha256"],
        "rule_id": RULE_ID,
        "rule_summary": RULE_SUMMARY,
        "aliases": ["设备编号", "设备ID", "资产编号", "EquipmentID", "DeviceID"],
        "placeholders": sorted(_PLACEHOLDERS),
        "business_count": business,
        "regions": [_public_field_region(item) for item in regions],
        "cross_sheet_keys": collisions,
        "potential_cross_sheet_matches": collisions,
        "cross_sheet_decision_required": False,
        "default_cross_sheet": "separate",
    }


def preview_grouping(dataset_id: str, config: dict) -> dict:
    dataset = _ready(dataset_id)
    connection = _connect()
    try:
        compiled = _compile(connection, dataset, config)
        diff = _diff(connection, dataset_id, compiled)
    finally:
        connection.close()
    body = _public_compiled(dataset, compiled, persisted=False)
    body["preview"] = True
    body["diff"] = diff
    body["sample_groups"] = [_public_group(group) for group in compiled["groups"][:20]]
    body["sample_rows"] = [_public_member(item, compiled["run_id"]) for item in compiled["members"][:8]]
    return body


def auto_apply_grouping(dataset_id: str) -> dict | None:
    """P1 成功后由调度器调用；相同签名复用已有有效结果。"""
    dataset = _require(dataset_id)
    if dataset["parse_status"] == "failed" or dataset["p0_status"] != "completed":
        return None
    connection = _connect()
    try:
        try:
            _latest_p1(connection, dataset_id)
        except L1Error:
            return None
    finally:
        connection.close()
    try:
        return apply_grouping(dataset_id, {"source": "auto", "use_suggestions": True})
    except L1Error:
        return None


def apply_grouping(dataset_id: str, config: dict) -> dict:
    dataset = _ready(dataset_id)
    connection = _connect()
    try:
        compiled = _compile(connection, dataset, config)
        if not compiled["balance"]["ok"]:
            raise L1Error("balance_failed", "分组数量没有对上 P1 业务记录。", 500)
        if compiled["status"] == "failed":
            raise L1Error("grouping_failed", _block_message(compiled), 500)
        signature = compiled["config"].get("execution_signature")
        found = _find_signature(connection, dataset_id, dataset["sha256"], signature) if signature else None
        if found is not None:
            body = _run_body(connection, dataset_id, found["run_id"], group_offset=0, group_limit=50)
            body["reused"] = True
            body["persisted"] = True
            return body
        now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        connection.execute("BEGIN IMMEDIATE")
        current = connection.execute(
            "SELECT MAX(revision) AS revision FROM p2_runs WHERE dataset_id = ?",
            (dataset_id,),
        ).fetchone()
        revision = int(current["revision"] or 0) + 1
        run_id = "P2-" + uuid.uuid4().hex[:12]
        compiled["run_id"] = run_id
        for group in compiled["groups"]:
            group.group_id = "EG-" + uuid.uuid4().hex[:12]
            for member in group.members:
                member.group_id = group.group_id
        balance = compiled["balance"]
        connection.execute(
            """
            INSERT INTO p2_runs (
                run_id, dataset_id, p1_run_id, revision, status, created_at, rule_id, p0_sha256,
                config_json, equipment_count, grouped_count, ungrouped_count, business_count,
                sheet_count, balance_ok
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                run_id,
                dataset_id,
                compiled["p1_run_id"],
                revision,
                compiled["status"],
                now,
                RULE_ID,
                dataset["sha256"],
                json.dumps(compiled["config"], ensure_ascii=False, separators=(",", ":")),
                balance["equipment_count"],
                balance["grouped_count"],
                balance["ungrouped_count"],
                balance["business_count"],
                balance["sheet_count"],
                1 if balance["ok"] else 0,
            ),
        )
        if compiled["groups"]:
            connection.executemany(
                """
                INSERT INTO p2_groups (
                    group_id, run_id, dataset_id, equipment_id, match_key, scope, sheet_id, asset_id,
                    record_count, original_values_json, label
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        group.group_id,
                        run_id,
                        dataset_id,
                        group.equipment_id,
                        group.match_key,
                        group.scope,
                        group.sheet_id,
                        group.asset_id,
                        group.record_count,
                        json.dumps(group.original_values, ensure_ascii=False, separators=(",", ":")),
                        group.label,
                    )
                    for group in compiled["groups"]
                ],
            )
        rows = [
            (
                run_id,
                dataset_id,
                item.source_row_id,
                item.region_id,
                item.sheet_id,
                item.row_number,
                item.group_id,
                item.status,
                item.reason,
                item.original_value,
                item.grouping_value,
                item.match_key,
                item.column_id,
                item.field_header,
                item.cell_id,
                item.coordinate,
                1 if item.override_applied else 0,
                item.asset_id,
            )
            for item in compiled["members"]
        ]
        for offset in range(0, len(rows), 1000):
            connection.executemany(
                """
                INSERT INTO p2_membership (
                    run_id, dataset_id, source_row_id, region_id, sheet_id, row_number, group_id,
                    membership_status, ungroup_reason, original_equipment_value, grouping_value, match_key,
                    column_id, field_header, equipment_cell_id, coordinate, override_applied, asset_id
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                rows[offset : offset + 1000],
            )
        connection.execute("COMMIT")
        body = _run_body(connection, dataset_id, run_id, group_offset=0, group_limit=50)
    except L1Error:
        if connection.in_transaction:
            connection.rollback()
        raise
    except sqlite3.Error as exc:
        if connection.in_transaction:
            connection.rollback()
        raise L1Error("storage_failed", f"P2 分组索引没有写入：{exc}", 500) from exc
    finally:
        connection.close()
    body["persisted"] = True
    return body


def list_equipment_groups(dataset_id: str, run_id: str | None, offset: int, limit: int) -> dict:
    _require(dataset_id)
    _check_page(offset, limit)
    connection = _connect()
    try:
        chosen = _resolve_run(connection, dataset_id, run_id)
        if chosen is None:
            return {
                "dataset_id": dataset_id,
                "applied": False,
                "persisted": False,
                "reused": False,
                "status": "not_started",
                "run": None,
                "groups": [],
                "sheet_links": [],
                "balance": None,
                "summary": None,
                "warnings": [],
                "potential_cross_sheet_matches": [],
                "downstream_ready": False,
                "cross_sheet_decision_required": False,
            }
        return _run_body(connection, dataset_id, chosen, offset, limit)
    finally:
        connection.close()


def grouping_summary(dataset_id: str, run_id: str | None = None) -> dict:
    """轻量摘要：不含设备组全量与成员明细。"""
    _require(dataset_id)
    connection = _connect()
    try:
        chosen = _resolve_run(connection, dataset_id, run_id)
        if chosen is None:
            return {
                "dataset_id": dataset_id,
                "applied": False,
                "status": "not_started",
                "summary": None,
                "run": None,
                "downstream_ready": False,
            }
        body = _run_body(connection, dataset_id, chosen, group_offset=0, group_limit=1)
    finally:
        connection.close()
    return {
        "dataset_id": dataset_id,
        "applied": True,
        "status": body["status"],
        "run": body["run"],
        "summary": body["summary"],
        "balance": body["balance"],
        "warnings": body.get("warnings") or [],
        "potential_cross_sheet_matches": body.get("potential_cross_sheet_matches") or [],
        "region_stats": body.get("region_stats") or [],
        "sheet_links": body.get("sheet_links") or [],
        "ungroup_reasons": body.get("ungroup_reasons") or {},
        "downstream_ready": body.get("downstream_ready", False),
        "rule_id": body.get("rule_id"),
        "p0_sha256": body.get("p0_sha256"),
    }


def list_group_rows(group_id: str, offset: int, limit: int) -> dict:
    if not group_id.startswith("EG-") or len(group_id) != 15:
        raise L1Error("group_not_found", "没有这个设备分组。", 404)
    _check_page(offset, limit)
    connection = _connect()
    try:
        group = connection.execute("SELECT * FROM p2_groups WHERE group_id = ?", (group_id,)).fetchone()
        if group is None:
            raise L1Error("group_not_found", "没有这个设备分组。", 404)
        total = int(group["record_count"])
        rows = _membership_page(connection, group["run_id"], offset, limit, group_id=group_id)
        run = connection.execute("SELECT * FROM p2_runs WHERE run_id = ?", (group["run_id"],)).fetchone()
    finally:
        connection.close()
    return {
        "group": _public_stored_group(group),
        "run_id": group["run_id"],
        "mapping_rule_id": run["rule_id"],
        "total_records": total,
        "offset": offset,
        "limit": limit,
        "rows": rows,
    }


def list_ungrouped_rows(dataset_id: str, run_id: str | None, offset: int, limit: int, reason: str | None) -> dict:
    _require(dataset_id)
    _check_page(offset, limit)
    connection = _connect()
    try:
        chosen = _resolve_run(connection, dataset_id, run_id)
        if chosen is None:
            raise L1Error("grouping_not_found", "还没有执行 P2。", 404)
        where = "run_id = ? AND membership_status = 'ungrouped'"
        params: list = [chosen]
        if reason:
            where += " AND ungroup_reason = ?"
            params.append(reason)
        total = connection.execute(f"SELECT COUNT(*) AS total FROM p2_membership WHERE {where}", params).fetchone()
        rows = _membership_page(connection, chosen, offset, limit, status="ungrouped", reason=reason)
    finally:
        connection.close()
    return {
        "dataset_id": dataset_id,
        "run_id": chosen,
        "total_records": int(total["total"]),
        "offset": offset,
        "limit": limit,
        "rows": rows,
    }


def list_grouping_rows(dataset_id: str, run_id: str | None, offset: int, limit: int, sheet_id: str | None, region_id: str | None) -> dict:
    _require(dataset_id)
    _check_page(offset, limit)
    connection = _connect()
    try:
        chosen = _resolve_run(connection, dataset_id, run_id)
        if chosen is None:
            raise L1Error("grouping_not_found", "还没有执行 P2。", 404)
        where = "run_id = ?"
        params: list = [chosen]
        if sheet_id:
            where += " AND sheet_id = ?"
            params.append(sheet_id)
        if region_id:
            where += " AND region_id = ?"
            params.append(region_id)
        total = connection.execute(f"SELECT COUNT(*) AS total FROM p2_membership WHERE {where}", params).fetchone()
        rows = _membership_page(connection, chosen, offset, limit, sheet_id=sheet_id, region_id=region_id)
    finally:
        connection.close()
    return {
        "dataset_id": dataset_id,
        "run_id": chosen,
        "total_records": int(total["total"]),
        "offset": offset,
        "limit": limit,
        "rows": rows,
    }


def _ready(dataset_id: str) -> sqlite3.Row:
    dataset = _require(dataset_id)
    _require_indexed(dataset)
    return dataset


def _compile(connection: sqlite3.Connection, dataset: sqlite3.Row, config: dict) -> dict:
    if not isinstance(config, dict):
        raise L1Error("invalid_config", "分组配置必须是 JSON 对象。", 422)
    p1 = _latest_p1(connection, dataset["dataset_id"])
    if config.get("p1_run_id") and config["p1_run_id"] != p1["run_id"]:
        raise L1Error("p1_revision_mismatch", "P2 只能读取当前最新的 P1 执行结果。", 409)
    regions = _field_regions(connection, p1["run_id"])
    choices = _choices(config, regions)
    match_mode = _choice(config.get("match_mode"), _MATCH_MODES, "exact", "匹配规则只支持原始文本，或忽略首尾空格和大小写。")
    cross_sheet = config.get("cross_sheet")
    explicit_cross = cross_sheet in _CROSS
    if cross_sheet is None or cross_sheet == "":
        cross_sheet = "separate"
    elif cross_sheet not in _CROSS:
        raise L1Error("invalid_config", "跨工作表策略只支持不合并、相同文本合并或台账映射。", 422)
    ledger = _ledger(config.get("ledger"), match_mode) if cross_sheet == "ledger" else []
    ledger_warnings = _validate_ledger(ledger) if ledger else []
    overrides = _overrides(config.get("overrides"))
    members: list[_Member] = []
    for region in regions:
        choice = choices[region.region_id]
        column = _selected_column(region, choice)
        for source in _business_rows(connection, dataset["dataset_id"], region, column):
            members.append(_classify_row(region, source, column, choice, match_mode))
    _apply_overrides(members, overrides, match_mode)
    collisions = _collision_keys(members, match_mode)
    groups = _build_groups(members, cross_sheet, ledger, match_mode)
    region_row_keys = {(item.region_id, item.source_row_id) for item in members}
    if len(region_row_keys) != len(members):
        raise L1Error("balance_failed", "同一区域源记录被重复计入多个分组。", 500)
    # P3/P0 契约以 source_row_id 为全局键；跨区域重复会破坏下游与主键
    if len({item.source_row_id for item in members}) != len(members):
        raise L1Error("balance_failed", "同一条源记录被重复分组。", 500)
    review_regions = [region for region in regions if choices[region.region_id]["blocking"]]
    balance = {
        "business_count": len(members),
        "grouped_count": sum(1 for item in members if item.status == "grouped"),
        "ungrouped_count": sum(1 for item in members if item.status == "ungrouped"),
        "equipment_count": len(groups),
        "sheet_count": len({item.sheet_id for item in members}),
        "ok": True,
    }
    balance["ok"] = balance["grouped_count"] + balance["ungrouped_count"] == balance["business_count"]
    ungroup_reasons = _ungroup_reason_stats(members)
    warnings = _collect_warnings(regions, choices, collisions, ledger_warnings, balance, ungroup_reasons)
    potential_matches = [
        {
            "relation": "potential_cross_sheet_match",
            "status": "candidate",
            "match_key": item["match_key"],
            "normalized_code": item["match_key"],
            "sheet_count": item["sheet_count"],
            "match_mode": item.get("match_mode", match_mode),
        }
        for item in collisions
    ]
    status = _run_status(balance, review_regions, warnings, ledger_warnings)
    stored_config = {
        "rule_id": RULE_ID,
        "rule_summary": RULE_SUMMARY,
        "p1_run_id": p1["run_id"],
        "p1_revision": int(p1["revision"]),
        "match_mode": match_mode,
        "cross_sheet": cross_sheet,
        "cross_sheet_explicit": explicit_cross,
        "ungrouped_policy": "keep",
        "source": config.get("source") if isinstance(config.get("source"), str) else "manual",
        "regions": [
            {
                "region_id": region.region_id,
                "column_id": choices[region.region_id]["column_id"],
                "confirmed": choices[region.region_id]["confirmed"],
                "recognition_source": region.recognition_source,
                "confidence_level": region.confidence_level,
                "warning_codes": region.warning_codes,
            }
            for region in regions
        ],
        "ledger": config.get("ledger") if isinstance(config.get("ledger"), list) else [],
        "overrides": config.get("overrides") if isinstance(config.get("overrides"), list) else [],
        "potential_cross_sheet_matches": potential_matches,
        "warnings": warnings,
        "ungroup_reasons": ungroup_reasons,
        "region_stats": [],  # 下方填入，避免签名循环依赖
    }
    region_stats = _region_stats(regions, members, choices)
    stored_config["region_stats"] = region_stats
    stored_config["execution_signature"] = _signature(dataset, p1, stored_config)
    return {
        "run_id": None,
        "p1_run_id": p1["run_id"],
        "config": stored_config,
        "match_mode": match_mode,
        "cross_sheet": cross_sheet,
        "members": members,
        "groups": groups,
        "balance": balance,
        "needs_confirmation": [region.region_id for region in review_regions],
        "cross_sheet_keys": collisions,
        "potential_cross_sheet_matches": potential_matches,
        "cross_sheet_decision_required": False,
        "apply_allowed": balance["ok"] and status != "failed",
        "status": status,
        "warnings": warnings,
        "ungroup_reasons": ungroup_reasons,
        "sheet_links": _sheet_links(groups, members),
        "region_stats": region_stats,
    }


def _choices(config: dict, regions: list[_FieldRegion]) -> dict[str, dict]:
    supplied = {}
    use_suggestions = bool(config.get("use_suggestions")) or config.get("source") == "auto"
    if not use_suggestions and isinstance(config.get("regions"), list):
        for item in config["regions"]:
            if isinstance(item, dict) and isinstance(item.get("region_id"), str):
                supplied[item["region_id"]] = item
    # 即使提供了 regions，auto / use_suggestions 仍以建议为主，仅覆盖显式项
    if use_suggestions and isinstance(config.get("regions"), list):
        for item in config["regions"]:
            if isinstance(item, dict) and isinstance(item.get("region_id"), str) and item.get("column_id"):
                supplied[item["region_id"]] = item
    choices = {}
    for region in regions:
        item = supplied.get(region.region_id)
        if item is None:
            column_id = region.suggested_column_id
            confirmed = not region.requires_review and region.confirmation_status != "needs_confirmation"
        else:
            column_id = item.get("column_id")
            if column_id is not None and not isinstance(column_id, str):
                raise L1Error("invalid_config", "设备字段必须是列标识。", 422)
            confirmed = bool(item.get("confirmed", True))
        needs = region.requires_review or region.confirmation_status == "needs_confirmation"
        blocking = needs and not confirmed
        if column_id:
            known = {column["column_id"] for column in region.columns}
            if column_id not in known:
                raise L1Error("invalid_field", f"区域 {region.range_label} 没有这个字段。", 422)
            # 人工确认后即便曾歧义也可放行
            if confirmed:
                blocking = False
        choices[region.region_id] = {"column_id": column_id or None, "confirmed": confirmed, "blocking": blocking}
    return choices


def _selected_column(region: _FieldRegion, choice: dict) -> dict | None:
    if not choice["column_id"]:
        return None
    for column in region.columns:
        if column["column_id"] == choice["column_id"]:
            return column
    return None


def _classify_row(region: _FieldRegion, source: dict, column: dict | None, choice: dict, match_mode: str) -> _Member:
    original = "" if source["raw_value"] is None else str(source["raw_value"])
    header = "" if column is None else " / ".join(column.get("header") or [])
    if column is None:
        status, reason, grouping, key = "ungrouped", "no_field" if region.confirmation_status == "no_field" else "unconfirmed_field", "", ""
        if region.confirmation_status == "needs_confirmation" and not choice["confirmed"]:
            reason = "unconfirmed_field"
        elif region.confirmation_status == "no_field" or not choice["column_id"]:
            reason = "no_field"
    else:
        kind, grouping, key = _value_kind(original, match_mode)
        if kind == "empty":
            status, reason = "ungrouped", "empty"
        elif kind == "invalid":
            status, reason = "ungrouped", "invalid"
        else:
            status, reason = "grouped", ""
    return _Member(
        source_row_id=source["source_row_id"],
        region_id=region.region_id,
        sheet_id=region.sheet_id,
        sheet_name=region.sheet_name,
        row_number=source["row_number"],
        status=status,
        reason=reason,
        original_value=original,
        grouping_value=grouping,
        match_key=key,
        column_id="" if column is None else column["column_id"],
        field_header=header,
        cell_id=source["cell_id"],
        coordinate=source["coordinate"] or "",
    )


def _value_kind(original: str, match_mode: str) -> tuple[str, str, str]:
    if original == "" or original.strip() == "":
        return "empty", "", ""
    if original.strip().casefold() in _PLACEHOLDERS:
        return "invalid", original, ""
    if match_mode == "trim_casefold":
        return "value", original, original.strip().casefold()
    return "value", original, original


def _apply_overrides(members: list[_Member], overrides: dict[str, dict], match_mode: str) -> None:
    by_row = {item.source_row_id: item for item in members}
    for source_row_id, item in overrides.items():
        member = by_row.get(source_row_id)
        if member is None:
            raise L1Error("override_not_found", "修正引用的记录不在当前 P1 业务记录中。", 422)
        member.override_applied = True
        if item["action"] == "ungroup":
            member.status = "ungrouped"
            member.reason = "override"
            member.grouping_value = ""
            member.match_key = ""
            member.asset_id = None
            continue
        value = item["equipment_value"]
        kind, grouping, key = _value_kind(value, match_mode)
        member.grouping_value = value
        member.match_key = key
        if kind == "empty":
            member.status, member.reason = "ungrouped", "empty"
        elif kind == "invalid":
            member.status, member.reason = "ungrouped", "invalid"
        else:
            member.status, member.reason = "grouped", ""


def _build_groups(members: list[_Member], cross_sheet: str, ledger: list[dict], match_mode: str) -> list[_Group]:
    buckets: dict[tuple, list[_Member]] = {}
    for member in members:
        if member.status != "grouped":
            member.group_id = None
            member.asset_id = None
            continue
        asset_id = None
        scope = "sheet"
        sheet_key = member.sheet_id
        if cross_sheet == "ledger":
            asset_id = _ledger_asset(ledger, member)
        if cross_sheet == "same_text" or asset_id:
            scope = "dataset"
            sheet_key = ""
        elif cross_sheet == "separate":
            scope = "sheet"
        member.asset_id = asset_id
        buckets.setdefault((scope, sheet_key, member.match_key, asset_id or ""), []).append(member)
    groups = []
    for key, rows in buckets.items():
        scope, sheet_key, match_key, asset_id = key
        originals = []
        for row in rows:
            shown = row.grouping_value
            if shown not in originals:
                originals.append(shown)
            if len(originals) == 8:
                break
        equipment_id = originals[0] if len(originals) == 1 else match_key
        sheet_name = rows[0].sheet_name if scope == "sheet" else ""
        label = f"{sheet_name} / {equipment_id}" if scope == "sheet" else equipment_id
        if asset_id:
            label = f"{label} → {asset_id}"
        group = _Group(
            group_id="",
            equipment_id=equipment_id,
            match_key=match_key,
            scope=scope,
            sheet_id=sheet_key or None,
            sheet_name=sheet_name,
            asset_id=asset_id or None,
            record_count=len(rows),
            original_values=originals,
            label=label,
            members=rows,
        )
        groups.append(group)
    groups.sort(key=lambda item: (item.sheet_name, item.equipment_id, item.match_key))
    return groups


def _ledger_asset(ledger: list[dict], member: _Member) -> str | None:
    hits = []
    for item in ledger:
        if item["match_key"] != member.match_key:
            continue
        if item["sheet_id"] and item["sheet_id"] != member.sheet_id:
            continue
        hits.append(item["asset_id"])
    unique = list(dict.fromkeys(hits))
    if len(unique) == 1:
        return unique[0]
    # 一对多 / 冲突时不自动写入 asset_id
    return None


def _validate_ledger(ledger: list[dict]) -> list[dict]:
    warnings = []
    by_key: dict[str, set[str]] = {}
    by_asset: dict[str, set[str]] = {}
    for item in ledger:
        scope = f"{item['match_key']}::{item['sheet_id'] or '*'}"
        by_key.setdefault(scope, set()).add(item["asset_id"])
        by_asset.setdefault(item["asset_id"], set()).add(item["match_key"])
    for scope, assets in by_key.items():
        if len(assets) > 1:
            warnings.append({"code": "ledger_ambiguous_asset", "scope": scope, "asset_ids": sorted(assets)})
    for asset_id, keys in by_asset.items():
        if len(keys) > 1:
            warnings.append({"code": "ledger_one_to_many", "asset_id": asset_id, "match_keys": sorted(keys)})
    return warnings


def _ungroup_reason_stats(members: list[_Member]) -> dict[str, int]:
    stats: dict[str, int] = {}
    for member in members:
        if member.status != "ungrouped":
            continue
        stats[member.reason or "unknown"] = stats.get(member.reason or "unknown", 0) + 1
    return stats


def _collect_warnings(
    regions: list[_FieldRegion],
    choices: dict[str, dict],
    collisions: list[dict],
    ledger_warnings: list[dict],
    balance: dict,
    ungroup_reasons: dict[str, int],
) -> list[dict]:
    warnings: list[dict] = []
    for region in regions:
        for code in region.warning_codes:
            warnings.append(
                {
                    "code": code,
                    "severity": "region",
                    "region_id": region.region_id,
                    "sheet_id": region.sheet_id,
                    "sheet_name": region.sheet_name,
                    "range_label": region.range_label,
                }
            )
        if region.requires_review and choices[region.region_id]["blocking"]:
            warnings.append(
                {
                    "code": "region_needs_review",
                    "severity": "region",
                    "region_id": region.region_id,
                    "sheet_id": region.sheet_id,
                    "sheet_name": region.sheet_name,
                    "range_label": region.range_label,
                }
            )
    for item in collisions:
        warnings.append(
            {
                "code": "potential_cross_sheet_match",
                "severity": "cross_sheet",
                "match_key": item["match_key"],
                "sheet_count": item["sheet_count"],
            }
        )
    for item in ledger_warnings:
        warnings.append({"code": item["code"], "severity": "ledger", **{k: v for k, v in item.items() if k != "code"}})
    if balance["ungrouped_count"] and balance["business_count"]:
        ratio = balance["ungrouped_count"] / max(balance["business_count"], 1)
        warnings.append(
            {
                "code": "ungrouped_records",
                "severity": "balance",
                "ungrouped_count": balance["ungrouped_count"],
                "ungrouped_ratio": round(ratio, 4),
                "reasons": ungroup_reasons,
            }
        )
    return warnings


def _run_status(
    balance: dict,
    review_regions: list[_FieldRegion],
    warnings: list[dict],
    ledger_warnings: list[dict],
) -> str:
    if not balance["ok"]:
        return "failed"
    if review_regions:
        return "needs_review"
    if any(item["code"] in {"ledger_ambiguous_asset"} for item in ledger_warnings):
        return "needs_review"
    # 全部业务记录未归组且存在候选字段歧义以外的“无字段”——不默认阻断，但若占比过高标 review
    ungrouped_ratio = balance["ungrouped_count"] / max(balance["business_count"], 1) if balance["business_count"] else 0
    if balance["business_count"] > 0 and ungrouped_ratio >= 0.5 and balance["grouped_count"] == 0:
        return "needs_review"
    soft = {item["code"] for item in warnings} - {"region_needs_review"}
    if soft:
        return "completed_with_warnings"
    return "completed"


def _region_stats(regions: list[_FieldRegion], members: list[_Member], choices: dict[str, dict]) -> list[dict]:
    by_region: dict[str, list[_Member]] = {}
    for member in members:
        by_region.setdefault(member.region_id, []).append(member)
    stats = []
    for region in regions:
        rows = by_region.get(region.region_id, [])
        grouped = sum(1 for item in rows if item.status == "grouped")
        ungrouped = sum(1 for item in rows if item.status == "ungrouped")
        choice = choices[region.region_id]
        selected = next((c for c in region.columns if c["column_id"] == choice["column_id"]), None)
        header = " / ".join(part for part in (selected.get("header") if selected else []) or []) if selected else ""
        stats.append(
            {
                "region_id": region.region_id,
                "sheet_id": region.sheet_id,
                "sheet_name": region.sheet_name,
                "range_label": region.range_label,
                "selected_field": choice["column_id"],
                "selected_header": header,
                "recognition_source": region.recognition_source,
                "confidence_level": region.confidence_level,
                "requires_review": region.requires_review and choice["blocking"],
                "grouped_count": grouped,
                "ungrouped_count": ungrouped,
                "record_count": len(rows),
                "warning_codes": region.warning_codes,
            }
        )
    return stats


def _signature(dataset: sqlite3.Row, p1: sqlite3.Row, config: dict) -> str:
    payload = {
        "rule_id": RULE_ID,
        "p0_sha256": dataset["sha256"],
        "p1_run_id": p1["run_id"],
        "p1_revision": int(p1["revision"]),
        "match_mode": config.get("match_mode"),
        "cross_sheet": config.get("cross_sheet"),
        "regions": sorted(
            [
                {
                    "region_id": item["region_id"],
                    "column_id": item.get("column_id"),
                    "confirmed": bool(item.get("confirmed")),
                }
                for item in (config.get("regions") or [])
            ],
            key=lambda item: item["region_id"],
        ),
        "ledger": config.get("ledger") or [],
        "overrides": config.get("overrides") or [],
    }
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _find_signature(connection: sqlite3.Connection, dataset_id: str, digest: str, signature: str | None) -> sqlite3.Row | None:
    if not signature:
        return None
    rows = connection.execute(
        """
        SELECT * FROM p2_runs
        WHERE dataset_id = ? AND p0_sha256 = ? AND rule_id = ?
        ORDER BY revision DESC
        """,
        (dataset_id, digest, RULE_ID),
    ).fetchall()
    for row in rows:
        try:
            config = json.loads(row["config_json"])
        except json.JSONDecodeError:
            continue
        if config.get("execution_signature") == signature and row["status"] in _PERSISTABLE:
            return row
    return None


def _collision_keys(members: list[_Member], match_mode: str) -> list[dict]:
    found: dict[str, set[str]] = {}
    for member in members:
        if member.status != "grouped" or not member.match_key:
            continue
        found.setdefault(member.match_key, set()).add(member.sheet_id)
    collisions = []
    for key, sheets in found.items():
        if len(sheets) > 1:
            collisions.append({"match_key": key, "match_mode": match_mode, "sheet_count": len(sheets)})
    collisions.sort(key=lambda item: item["match_key"])
    return collisions[:50]


def _cross_sheet_keys(connection: sqlite3.Connection, regions: list[_FieldRegion], match_mode: str) -> list[dict]:
    found: dict[str, set[str]] = {}
    for region in regions:
        if not region.suggested_column_id:
            continue
        column = next(item for item in region.columns if item["column_id"] == region.suggested_column_id)
        for source in _business_rows(connection, region_dataset(connection, region), region, column):
            original = "" if source["raw_value"] is None else str(source["raw_value"])
            kind, _grouping, key = _value_kind(original, match_mode)
            if kind == "value":
                found.setdefault(key, set()).add(region.sheet_id)
    return [{"match_key": key, "sheet_count": len(sheets)} for key, sheets in sorted(found.items()) if len(sheets) > 1][:50]


def region_dataset(connection: sqlite3.Connection, region: _FieldRegion) -> str:
    row = connection.execute("SELECT dataset_id FROM p1_regions WHERE region_id = ?", (region.region_id,)).fetchone()
    return str(row["dataset_id"])


def _sheet_links(groups: list[_Group], members: list[_Member]) -> list[dict]:
    sheets: dict[str, dict] = {}
    for member in members:
        item = sheets.setdefault(member.sheet_id, {"sheet_id": member.sheet_id, "sheet_name": member.sheet_name, "grouped_count": 0, "ungrouped_count": 0, "equipment": set()})
        if member.status == "grouped":
            item["grouped_count"] += 1
        else:
            item["ungrouped_count"] += 1
    for group in groups:
        if group.scope == "sheet" and group.sheet_id in sheets:
            sheets[group.sheet_id]["equipment"].add(group.equipment_id)
        else:
            for member in group.members:
                if member.sheet_id in sheets:
                    sheets[member.sheet_id]["equipment"].add(group.equipment_id)
    links = []
    for item in sheets.values():
        names = sorted(item["equipment"])
        links.append(
            {
                "sheet_id": item["sheet_id"],
                "sheet_name": item["sheet_name"],
                "grouped_count": item["grouped_count"],
                "ungrouped_count": item["ungrouped_count"],
                "equipment_count": len(names),
                "equipment_ids": names[:30],
            }
        )
    links.sort(key=lambda item: item["sheet_name"])
    return links


def _field_regions(connection: sqlite3.Connection, run_id: str) -> list[_FieldRegion]:
    rows = connection.execute(
        """
        SELECT r.*, s.name AS sheet_name
        FROM p1_regions r
        JOIN sheets s ON s.dataset_id = r.dataset_id AND s.sheet_id = r.sheet_id
        WHERE r.run_id = ? AND r.role = 'data_table' AND r.sheet_enabled = 1 AND r.end_row >= r.start_row
        ORDER BY r.ordinal
        """,
        (run_id,),
    ).fetchall()
    regions = []
    for row in rows:
        columns = json.loads(row["column_json"])
        scores = score_columns(
            connection,
            str(row["dataset_id"]),
            str(row["sheet_id"]),
            int(row["start_row"]),
            int(row["end_row"]),
            row["header_start_row"],
            int(row["header_row_count"]),
            columns,
        )
        decision = select_equipment_field(scores)
        candidates = [
            _Candidate(
                column_number=item.column_number,
                column_id=item.column_id,
                header=item.header,
                match=item.match,
                alias=item.alias,
                score=item.score,
                confidence_level=item.confidence_level,
                recognition_source=item.recognition_source,
                warning_codes=item.warning_codes,
            )
            for item in scores
            if item.match in {"exact", "fuzzy"} or item.score >= 0.35
        ]
        regions.append(
            _FieldRegion(
                region_id=row["region_id"],
                sheet_id=row["sheet_id"],
                sheet_name=row["sheet_name"],
                range_label=_range_label(int(row["start_column"]), int(row["start_row"]), int(row["end_column"]), int(row["end_row"])),
                record_count=int(row["record_count"]),
                columns=columns,
                candidates=candidates,
                suggested_column_id=decision["selected_field"],
                confirmation_status=decision["confirmation_status"],
                start_row=int(row["start_row"]),
                end_row=int(row["end_row"]),
                start_column=int(row["start_column"]),
                end_column=int(row["end_column"]),
                header_start_row=row["header_start_row"],
                header_row_count=int(row["header_row_count"]),
                confidence_level=decision["confidence_level"],
                recognition_source=decision["recognition_source"],
                warning_codes=list(decision["warning_codes"]),
                requires_review=bool(decision["requires_review"]),
                candidate_fields=list(decision["candidate_fields"]),
            )
        )
    return regions


def _business_rows(connection: sqlite3.Connection, dataset_id: str, region: _FieldRegion, column: dict | None) -> list[dict]:
    column_number = int(column["column_number"]) if column else -1
    header_count = region.header_row_count
    header_start = int(region.header_start_row or 0)
    header_end = header_start + header_count - 1 if header_count else 0
    rows = connection.execute(
        """
        SELECT s.source_row_id, s.row_number, c.raw_value, c.cell_id, c.coordinate
        FROM source_rows s
        LEFT JOIN raw_cells c
          ON c.dataset_id = s.dataset_id AND c.sheet_id = s.sheet_id
         AND c.row_number = s.row_number AND c.column_number = ?
        WHERE s.dataset_id = ? AND s.sheet_id = ? AND s.row_number BETWEEN ? AND ?
          AND (? = 0 OR s.row_number < ? OR s.row_number > ?)
        ORDER BY s.row_number
        """,
        (
            column_number,
            dataset_id,
            region.sheet_id,
            region.start_row,
            region.end_row,
            header_count,
            header_start,
            header_end,
        ),
    ).fetchall()
    return [
        {
            "source_row_id": row["source_row_id"],
            "row_number": int(row["row_number"]),
            "raw_value": row["raw_value"],
            "cell_id": row["cell_id"],
            "coordinate": row["coordinate"],
        }
        for row in rows
    ]


def _latest_p1(connection: sqlite3.Connection, dataset_id: str) -> sqlite3.Row:
    row = connection.execute(
        "SELECT * FROM p1_runs WHERE dataset_id = ? ORDER BY revision DESC LIMIT 1",
        (dataset_id,),
    ).fetchone()
    if row is None:
        raise L1Error("p1_required", "请先执行 P1。P2 只读取已确认的数据区域。", 409)
    status = str(row["status"])
    if status == "needs_review":
        raise L1Error("p1_needs_review", "P1 结果待审核，确认区域后再进入设备分组。", 409)
    if status not in {"completed", "completed_with_warnings"}:
        raise L1Error("p1_required", "请先执行 P1。P2 只读取已确认的数据区域。", 409)
    return row


def _ledger(payload: object, match_mode: str) -> list[dict]:
    if payload is None:
        return []
    if not isinstance(payload, list):
        raise L1Error("invalid_config", "台账映射必须是数组。", 422)
    items = []
    for item in payload:
        if not isinstance(item, dict) or not isinstance(item.get("match_key"), str) or not isinstance(item.get("asset_id"), str):
            raise L1Error("invalid_config", "台账映射需要 match_key 和 asset_id。", 422)
        if not item["asset_id"].strip():
            raise L1Error("invalid_config", "资产编号不能为空。", 422)
        key = item["match_key"].strip().casefold() if match_mode == "trim_casefold" else item["match_key"]
        sheet_id = item.get("sheet_id")
        if sheet_id is not None and not isinstance(sheet_id, str):
            raise L1Error("invalid_config", "台账映射的工作表标识无效。", 422)
        items.append({"match_key": key, "asset_id": item["asset_id"], "sheet_id": sheet_id or None})
    return items


def _overrides(payload: object) -> dict[str, dict]:
    if payload is None:
        return {}
    if not isinstance(payload, list):
        raise L1Error("invalid_config", "单条修正必须是数组。", 422)
    found = {}
    for item in payload:
        if not isinstance(item, dict) or not isinstance(item.get("source_row_id"), str):
            raise L1Error("invalid_config", "单条修正缺少源记录。", 422)
        action = item.get("action")
        if action not in {"assign", "ungroup"}:
            raise L1Error("invalid_config", "单条修正只支持指定设备或移出分组。", 422)
        if action == "assign" and not isinstance(item.get("equipment_value"), str):
            raise L1Error("invalid_config", "指定设备时要给出原始设备编号文本。", 422)
        found[item["source_row_id"]] = {"action": action, "equipment_value": item.get("equipment_value") or ""}
    return found


def _choice(value: object, allowed: set[str], default: str, message: str) -> str:
    if value is None or value == "":
        return default
    if not isinstance(value, str) or value not in allowed:
        raise L1Error("invalid_config", message, 422)
    return value


def _diff(connection: sqlite3.Connection, dataset_id: str, compiled: dict) -> dict:
    current = connection.execute(
        "SELECT run_id, revision, equipment_count, grouped_count, ungrouped_count FROM p2_runs WHERE dataset_id = ? ORDER BY revision DESC LIMIT 1",
        (dataset_id,),
    ).fetchone()
    balance = compiled["balance"]
    if current is None:
        return {
            "baseline_run_id": None,
            "baseline_revision": None,
            "equipment_count_before": 0,
            "equipment_count_after": balance["equipment_count"],
            "grouped_count_before": 0,
            "grouped_count_after": balance["grouped_count"],
            "ungrouped_count_before": 0,
            "ungrouped_count_after": balance["ungrouped_count"],
            "changed_record_count": balance["business_count"],
        }
    previous = {
        row["source_row_id"]: (row["membership_status"], row["match_key"], row["sheet_id"] if row["asset_id"] is None else "")
        for row in connection.execute(
            "SELECT source_row_id, membership_status, match_key, sheet_id, asset_id, group_id FROM p2_membership WHERE run_id = ?",
            (current["run_id"],),
        ).fetchall()
    }
    changed = 0
    for member in compiled["members"]:
        signature = (member.status, member.match_key, member.sheet_id if member.asset_id is None and compiled["cross_sheet"] == "separate" else "")
        if previous.get(member.source_row_id) != signature:
            changed += 1
    return {
        "baseline_run_id": current["run_id"],
        "baseline_revision": int(current["revision"]),
        "equipment_count_before": int(current["equipment_count"]),
        "equipment_count_after": balance["equipment_count"],
        "grouped_count_before": int(current["grouped_count"]),
        "grouped_count_after": balance["grouped_count"],
        "ungrouped_count_before": int(current["ungrouped_count"]),
        "ungrouped_count_after": balance["ungrouped_count"],
        "changed_record_count": changed,
    }


def _public_compiled(dataset: sqlite3.Row, compiled: dict, persisted: bool) -> dict:
    return {
        "dataset_id": dataset["dataset_id"],
        "persisted": persisted,
        "reused": False,
        "p1_run_id": compiled["p1_run_id"],
        "p0_sha256": dataset["sha256"],
        "rule_id": RULE_ID,
        "rule_summary": RULE_SUMMARY,
        "status": compiled["status"],
        "config": compiled["config"],
        "balance": compiled["balance"],
        "summary": _summary_from_compiled(compiled),
        "apply_allowed": compiled["apply_allowed"],
        "needs_confirmation": compiled["needs_confirmation"],
        "cross_sheet_keys": compiled["cross_sheet_keys"],
        "potential_cross_sheet_matches": compiled.get("potential_cross_sheet_matches") or [],
        "cross_sheet_decision_required": False,
        "warnings": compiled.get("warnings") or [],
        "ungroup_reasons": compiled.get("ungroup_reasons") or {},
        "region_stats": compiled.get("region_stats") or [],
        "sheet_links": compiled["sheet_links"],
        "groups": [_public_group(group) for group in compiled["groups"][:50]],
        "downstream_ready": compiled["status"] in _READY,
    }


def _public_group(group: _Group) -> dict:
    return {
        "group_id": group.group_id or None,
        "equipment_id": group.equipment_id,
        "equipment_code": group.equipment_id,
        "normalized_code": group.match_key,
        "match_key": group.match_key,
        "scope": group.scope,
        "source_scope": group.scope,
        "sheet_id": group.sheet_id,
        "sheet_name": group.sheet_name,
        "asset_id": group.asset_id,
        "record_count": group.record_count,
        "member_count": group.record_count,
        "original_values": group.original_values,
        "label": group.label,
        "grouping_method": "normalized_code",
        "grouping_rule": RULE_ID,
        "mapping_rule_id": RULE_ID,
        "region_ids": sorted({member.region_id for member in group.members}),
    }


def _public_member(member: _Member, run_id: str | None) -> dict:
    return {
        "run_id": run_id,
        "source_row_id": member.source_row_id,
        "region_id": member.region_id,
        "sheet_id": member.sheet_id,
        "sheet_name": member.sheet_name,
        "row_number": member.row_number,
        "membership_status": member.status,
        "ungroup_reason": member.reason,
        "original_equipment_value": member.original_value,
        "grouping_value": member.grouping_value,
        "equipment_id": member.grouping_value if member.status == "grouped" else "",
        "match_key": member.match_key,
        "column_id": member.column_id,
        "field_header": member.field_header,
        "equipment_cell_id": member.cell_id,
        "coordinate": member.coordinate,
        "override_applied": member.override_applied,
        "asset_id": member.asset_id,
        "mapping_rule_id": RULE_ID,
        "group_id": member.group_id,
        "fields": [],
    }


def _public_field_region(region: _FieldRegion) -> dict:
    return {
        "region_id": region.region_id,
        "sheet_id": region.sheet_id,
        "sheet_name": region.sheet_name,
        "range_label": region.range_label,
        "record_count": region.record_count,
        "selected_field": region.suggested_column_id,
        "candidate_fields": region.candidate_fields,
        "confidence_level": region.confidence_level,
        "recognition_source": region.recognition_source,
        "warning_codes": region.warning_codes,
        "requires_review": region.requires_review,
        "candidates": [
            {
                "column_number": item.column_number,
                "column_id": item.column_id,
                "header": item.header,
                "match": item.match,
                "alias": item.alias,
                "score": item.score,
                "confidence_level": item.confidence_level,
                "recognition_source": item.recognition_source,
                "warning_codes": item.warning_codes,
            }
            for item in region.candidates
        ],
        "columns": [
            {"column_number": column["column_number"], "column_id": column["column_id"], "header": column.get("header") or [], "letter": column.get("letter")}
            for column in region.columns
        ],
        "suggested_column_id": region.suggested_column_id,
        "confirmation_status": region.confirmation_status,
    }


def _run_body(connection: sqlite3.Connection, dataset_id: str, run_id: str, group_offset: int, group_limit: int) -> dict:
    run = connection.execute("SELECT * FROM p2_runs WHERE run_id = ?", (run_id,)).fetchone()
    total = connection.execute("SELECT COUNT(*) AS total FROM p2_groups WHERE run_id = ?", (run_id,)).fetchone()
    groups = connection.execute(
        """
        SELECT * FROM p2_groups WHERE run_id = ?
        ORDER BY label, group_id
        LIMIT ? OFFSET ?
        """,
        (run_id, group_limit, group_offset),
    ).fetchall()
    config = json.loads(run["config_json"])
    balance = {
        "business_count": int(run["business_count"]),
        "grouped_count": int(run["grouped_count"]),
        "ungrouped_count": int(run["ungrouped_count"]),
        "equipment_count": int(run["equipment_count"]),
        "sheet_count": int(run["sheet_count"]),
        "ok": bool(run["balance_ok"]),
    }
    sheet_links = _stored_sheet_links(connection, run_id)
    summary = {
        "status": run["status"],
        "equipment_count": balance["equipment_count"],
        "grouped_count": balance["grouped_count"],
        "ungrouped_count": balance["ungrouped_count"],
        "ungrouped_ratio": round(balance["ungrouped_count"] / max(balance["business_count"], 1), 4) if balance["business_count"] else 0.0,
        "business_count": balance["business_count"],
        "sheet_count": balance["sheet_count"],
        "field_summary": [
            {
                "region_id": item.get("region_id"),
                "column_id": item.get("column_id"),
                "recognition_source": item.get("recognition_source"),
                "confidence_level": item.get("confidence_level"),
            }
            for item in (config.get("regions") or [])
        ],
        "sheet_stats": sheet_links,
        "region_stats": config.get("region_stats") or [],
        "warning_count": len(config.get("warnings") or []),
        "warning_codes": sorted({item.get("code") for item in (config.get("warnings") or []) if item.get("code")}),
        "ungroup_reasons": config.get("ungroup_reasons") or {},
        "potential_cross_sheet_matches": config.get("potential_cross_sheet_matches") or [],
        "active_revision": int(run["revision"]),
        "run_id": run["run_id"],
        "rule_id": run["rule_id"],
        "downstream_ready": run["status"] in _READY,
    }
    # region_stats 在执行时写入 config；旧 v1 修订可能没有
    if not summary["region_stats"] and config.get("regions"):
        summary["region_stats"] = []
    return {
        "dataset_id": dataset_id,
        "applied": True,
        "persisted": True,
        "reused": False,
        "status": run["status"],
        "run": _public_run(run),
        "config": config,
        "summary": summary,
        "balance": balance,
        "warnings": config.get("warnings") or [],
        "potential_cross_sheet_matches": config.get("potential_cross_sheet_matches") or [],
        "ungroup_reasons": config.get("ungroup_reasons") or {},
        "region_stats": config.get("region_stats") or [],
        "group_total": int(total["total"]),
        "group_offset": group_offset,
        "group_limit": group_limit,
        "groups": [_public_stored_group(group) for group in groups],
        "sheet_links": sheet_links,
        "p0_sha256": run["p0_sha256"],
        "rule_id": run["rule_id"],
        "rule_summary": RULE_SUMMARY if run["rule_id"] == RULE_ID else "历史 P2 修订（只读）。",
        "downstream_ready": run["status"] in _READY,
        "cross_sheet_decision_required": False,
    }


def _summary_from_compiled(compiled: dict) -> dict:
    balance = compiled["balance"]
    return {
        "status": compiled["status"],
        "equipment_count": balance["equipment_count"],
        "grouped_count": balance["grouped_count"],
        "ungrouped_count": balance["ungrouped_count"],
        "ungrouped_ratio": round(balance["ungrouped_count"] / max(balance["business_count"], 1), 4) if balance["business_count"] else 0.0,
        "business_count": balance["business_count"],
        "sheet_count": balance["sheet_count"],
        "field_summary": [
            {
                "region_id": item.get("region_id"),
                "column_id": item.get("column_id"),
                "recognition_source": item.get("recognition_source"),
                "confidence_level": item.get("confidence_level"),
            }
            for item in (compiled["config"].get("regions") or [])
        ],
        "sheet_stats": compiled.get("sheet_links") or [],
        "region_stats": compiled.get("region_stats") or [],
        "warning_count": len(compiled.get("warnings") or []),
        "warning_codes": sorted({item.get("code") for item in (compiled.get("warnings") or []) if item.get("code")}),
        "ungroup_reasons": compiled.get("ungroup_reasons") or {},
        "potential_cross_sheet_matches": compiled.get("potential_cross_sheet_matches") or [],
        "active_revision": None,
        "run_id": compiled.get("run_id"),
        "rule_id": RULE_ID,
        "downstream_ready": compiled["status"] in _READY,
    }


def _public_run(run: sqlite3.Row) -> dict:
    return {
        "run_id": run["run_id"],
        "p1_run_id": run["p1_run_id"],
        "revision": int(run["revision"]),
        "status": run["status"],
        "created_at": run["created_at"],
        "rule_id": run["rule_id"],
        "p0_sha256": run["p0_sha256"],
        "equipment_count": int(run["equipment_count"]),
        "grouped_count": int(run["grouped_count"]),
        "ungrouped_count": int(run["ungrouped_count"]),
        "business_count": int(run["business_count"]),
        "sheet_count": int(run["sheet_count"]),
    }


def _public_stored_group(group: sqlite3.Row) -> dict:
    return {
        "group_id": group["group_id"],
        "equipment_id": group["equipment_id"],
        "equipment_code": group["equipment_id"],
        "normalized_code": group["match_key"],
        "match_key": group["match_key"],
        "scope": group["scope"],
        "source_scope": group["scope"],
        "sheet_id": group["sheet_id"],
        "asset_id": group["asset_id"],
        "record_count": int(group["record_count"]),
        "member_count": int(group["record_count"]),
        "original_values": json.loads(group["original_values_json"]),
        "label": group["label"],
        "grouping_method": "normalized_code",
        "grouping_rule": group["run_id"] and RULE_ID,
        "mapping_rule_id": RULE_ID,
    }


def _stored_sheet_links(connection: sqlite3.Connection, run_id: str) -> list[dict]:
    rows = connection.execute(
        """
        SELECT m.sheet_id, s.name AS sheet_name,
               SUM(CASE WHEN m.membership_status = 'grouped' THEN 1 ELSE 0 END) AS grouped_count,
               SUM(CASE WHEN m.membership_status = 'ungrouped' THEN 1 ELSE 0 END) AS ungrouped_count
        FROM p2_membership m
        JOIN sheets s ON s.dataset_id = m.dataset_id AND s.sheet_id = m.sheet_id
        WHERE m.run_id = ?
        GROUP BY m.sheet_id
        ORDER BY s.name
        """,
        (run_id,),
    ).fetchall()
    links = []
    for row in rows:
        equipment = connection.execute(
            """
            SELECT DISTINCT g.equipment_id
            FROM p2_membership m
            JOIN p2_groups g ON g.group_id = m.group_id
            WHERE m.run_id = ? AND m.sheet_id = ? AND m.membership_status = 'grouped'
            ORDER BY g.equipment_id
            LIMIT 30
            """,
            (run_id, row["sheet_id"]),
        ).fetchall()
        links.append(
            {
                "sheet_id": row["sheet_id"],
                "sheet_name": row["sheet_name"],
                "grouped_count": int(row["grouped_count"]),
                "ungrouped_count": int(row["ungrouped_count"]),
                "equipment_count": len(equipment),
                "equipment_ids": [item["equipment_id"] for item in equipment],
            }
        )
    return links


def _membership_page(
    connection: sqlite3.Connection,
    run_id: str,
    offset: int,
    limit: int,
    group_id: str | None = None,
    status: str | None = None,
    reason: str | None = None,
    sheet_id: str | None = None,
    region_id: str | None = None,
) -> list[dict]:
    where = ["m.run_id = ?"]
    params: list = [run_id]
    if group_id:
        where.append("m.group_id = ?")
        params.append(group_id)
    if status:
        where.append("m.membership_status = ?")
        params.append(status)
    if reason:
        where.append("m.ungroup_reason = ?")
        params.append(reason)
    if sheet_id:
        where.append("m.sheet_id = ?")
        params.append(sheet_id)
    if region_id:
        where.append("m.region_id = ?")
        params.append(region_id)
    params.extend([limit, offset])
    rows = connection.execute(
        f"""
        SELECT m.*, s.name AS sheet_name, g.equipment_id, g.label AS group_label, r.column_json, r.start_column, r.end_column
        FROM p2_membership m
        JOIN sheets s ON s.dataset_id = m.dataset_id AND s.sheet_id = m.sheet_id
        JOIN p1_regions r ON r.region_id = m.region_id
        LEFT JOIN p2_groups g ON g.group_id = m.group_id
        WHERE {" AND ".join(where)}
        ORDER BY m.sheet_id, m.row_number
        LIMIT ? OFFSET ?
        """,
        params,
    ).fetchall()
    if not rows:
        return []
    fields_by_row = _page_fields(connection, rows)
    page = []
    for row in rows:
        fields = fields_by_row[row["source_row_id"]]
        page.append(
            {
                "run_id": row["run_id"],
                "group_id": row["group_id"],
                "equipment_id": row["equipment_id"] or "",
                "group_label": row["group_label"] or "",
                "source_row_id": row["source_row_id"],
                "region_id": row["region_id"],
                "sheet_id": row["sheet_id"],
                "sheet_name": row["sheet_name"],
                "row_number": int(row["row_number"]),
                "membership_status": row["membership_status"],
                "ungroup_reason": row["ungroup_reason"],
                "original_equipment_value": row["original_equipment_value"],
                "grouping_value": row["grouping_value"],
                "match_key": row["match_key"],
                "column_id": row["column_id"],
                "field_header": row["field_header"],
                "equipment_cell_id": row["equipment_cell_id"],
                "coordinate": row["coordinate"],
                "override_applied": bool(row["override_applied"]),
                "asset_id": row["asset_id"],
                "mapping_rule_id": RULE_ID,
                "fields": fields,
            }
        )
    return page


def _page_fields(connection: sqlite3.Connection, memberships: list[sqlite3.Row]) -> dict[str, list[dict]]:
    columns_by_region: dict[str, list] = {}
    buckets: dict[tuple, list[sqlite3.Row]] = {}
    for membership in memberships:
        region_id = membership["region_id"]
        if region_id not in columns_by_region:
            columns_by_region[region_id] = json.loads(membership["column_json"])
        key = (membership["dataset_id"], membership["sheet_id"], int(membership["start_column"]), int(membership["end_column"]))
        buckets.setdefault(key, []).append(membership)
    cells_by_row: dict[str, dict[int, sqlite3.Row]] = {}
    for (dataset_id, sheet_id, start_column, end_column), members in buckets.items():
        numbers = [int(item["row_number"]) for item in members]
        marks = ",".join("?" for _ in numbers)
        found = connection.execute(
            f"""
            SELECT source_row_id, cell_id, column_number, coordinate, raw_value, cell_type
            FROM raw_cells
            WHERE dataset_id = ? AND sheet_id = ? AND column_number BETWEEN ? AND ?
              AND row_number IN ({marks})
            """,
            (dataset_id, sheet_id, start_column, end_column, *numbers),
        ).fetchall()
        for cell in found:
            cells_by_row.setdefault(cell["source_row_id"], {})[int(cell["column_number"])] = cell
    page: dict[str, list[dict]] = {}
    for membership in memberships:
        by_column = cells_by_row.get(membership["source_row_id"], {})
        fields = []
        for column in columns_by_region[membership["region_id"]]:
            cell = by_column.get(int(column["column_number"]))
            header = " / ".join(part for part in column.get("header") or [] if part) or column.get("letter") or column["column_id"]
            fields.append(
                {
                    "column_id": column["column_id"],
                    "column_number": int(column["column_number"]),
                    "header": header,
                    "raw_value": "" if cell is None else cell["raw_value"],
                    "cell_id": None if cell is None else cell["cell_id"],
                    "coordinate": None if cell is None else cell["coordinate"],
                    "cell_type": None if cell is None else cell["cell_type"],
                    "present": cell is not None,
                }
            )
        page[membership["source_row_id"]] = fields
    return page


def _resolve_run(connection: sqlite3.Connection, dataset_id: str, run_id: str | None) -> str | None:
    if run_id:
        found = connection.execute(
            "SELECT run_id FROM p2_runs WHERE dataset_id = ? AND run_id = ?",
            (dataset_id, run_id),
        ).fetchone()
        if found is None:
            raise L1Error("run_not_found", "没有这个 P2 执行版本。", 404)
        return run_id
    found = connection.execute(
        "SELECT run_id FROM p2_runs WHERE dataset_id = ? ORDER BY revision DESC LIMIT 1",
        (dataset_id,),
    ).fetchone()
    return None if found is None else str(found["run_id"])


def _block_message(compiled: dict) -> str:
    if compiled.get("status") == "failed":
        return "分组执行失败：数据完整性校验未通过。"
    if compiled.get("needs_confirmation"):
        return "存在设备编号字段歧义，结果已标记待审核。"
    return "当前分组配置还不能执行。"


def _check_page(offset: int, limit: int) -> None:
    if offset < 0:
        raise L1Error("invalid_offset", "offset 必须是大于等于 0 的整数。", 422)
    if limit < 1 or limit > 200:
        raise L1Error("invalid_limit", "limit 必须是 1 到 200 的整数。", 422)


def _range_label(start_column: int, start_row: int, end_column: int, end_row: int) -> str:
    return f"{_letters(start_column)}{start_row}:{_letters(end_column)}{end_row}"


def _letters(column: int) -> str:
    letters = ""
    number = column
    while number:
        number, remainder = divmod(number - 1, 26)
        letters = chr(65 + remainder) + letters
    return letters
