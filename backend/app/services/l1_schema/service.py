"""P3 映射编排。预览不落库；执行只追加版本，不改 P0 单元格和 P2 分组。"""

from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import datetime, timezone

from app.services.l1_schema.definitions import FIELD_BY_ID, ROLES, RULE_ID, TEMPLATE_ID, TEMPLATE_VERSION
from app.services.l1_schema.matcher import match_header, parse_unit
from app.services.l1_schema.repository import ensure_tables, list_templates, load_template
from app.services.l1_schema.validator import validate
from app.services.l1_store import L1Error, _connect, _require, _require_indexed

_SAMPLE_LIMIT = 5


def list_schema_templates() -> dict:
    connection = _open()
    try:
        return {"templates": list_templates(connection)}
    finally:
        connection.close()


def get_schema_template(template_id: str, version: str | None = None) -> dict:
    connection = _open()
    try:
        found = load_template(connection, template_id, version)
    finally:
        connection.close()
    if found is None:
        raise L1Error("template_not_found", "没有这个 Schema 模板版本。", 404)
    return found


def suggest_mappings(dataset_id: str) -> dict:
    dataset = _ready(dataset_id)
    connection = _open()
    try:
        compiled = _compile(connection, dataset, {"use_suggestions": True, "batch_confirm": False})
    finally:
        connection.close()
    body = _public_plan(dataset, compiled, persisted=False)
    body["suggestions"] = True
    return body


def get_mappings(dataset_id: str, run_id: str | None = None) -> dict:
    _require(dataset_id)
    connection = _open()
    try:
        chosen = _resolve_run(connection, dataset_id, run_id)
        if chosen is None:
            return {"dataset_id": dataset_id, "applied": False, "run": None, "mappings": [], "issues": [], "summary": None}
        return _stored_plan(connection, dataset_id, chosen)
    finally:
        connection.close()


def preview_mapping(dataset_id: str, config: dict) -> dict:
    dataset = _ready(dataset_id)
    connection = _open()
    try:
        compiled = _compile(connection, dataset, config)
        diff = _diff(connection, dataset_id, compiled)
        samples = _sample_records(connection, dataset["dataset_id"], compiled["mappings"], compiled["regions"], 0, 5)
    finally:
        connection.close()
    body = _public_plan(dataset, compiled, persisted=False)
    body["preview"] = True
    body["diff"] = diff
    body["sample_rows"] = samples["rows"]
    body["sample_limited"] = True
    return body


def validate_mapping(dataset_id: str, config: dict) -> dict:
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
        "summary": compiled["summary"],
    }


def apply_mapping(dataset_id: str, config: dict) -> dict:
    dataset = _ready(dataset_id)
    connection = _open()
    try:
        compiled = _compile(connection, dataset, config)
        if not compiled["apply_allowed"]:
            raise L1Error("mapping_blocked", _block_message(compiled["issues"]), 409)
        now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        connection.execute("BEGIN IMMEDIATE")
        current = connection.execute("SELECT MAX(revision) AS revision FROM p3_runs WHERE dataset_id = ?", (dataset_id,)).fetchone()
        revision = int(current["revision"] or 0) + 1
        run_id = "P3-" + uuid.uuid4().hex[:12]
        connection.execute(
            """
            INSERT INTO p3_runs (
                run_id, dataset_id, revision, status, created_at, p1_run_id, p2_run_id, template_id,
                template_version, rule_id, p0_sha256, config_json, summary_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                run_id,
                dataset_id,
                revision,
                "completed",
                now,
                compiled["p1_run_id"],
                compiled["p2_run_id"],
                compiled["template_id"],
                compiled["template_version"],
                RULE_ID,
                dataset["sha256"],
                json.dumps(compiled["config"], ensure_ascii=False, separators=(",", ":")),
                json.dumps(compiled["summary"], ensure_ascii=False, separators=(",", ":")),
            ),
        )
        if compiled["mappings"]:
            connection.executemany(
                """
                INSERT INTO p3_mappings (
                    run_id, source_column_id, dataset_id, region_id, sheet_id, sheet_name, column_id,
                    column_number, header, standard_field_id, role, eligibility, source_unit, target_unit,
                    target_type, match_method, match_reason, confirmation_status, candidates_json, sample_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        run_id,
                        item["source_column_id"],
                        dataset_id,
                        item["region_id"],
                        item["sheet_id"],
                        item["sheet_name"],
                        item["column_id"],
                        item["column_number"],
                        item["header"],
                        item["standard_field_id"],
                        item["role"],
                        item["eligibility"],
                        item["source_unit"],
                        item["target_unit"],
                        item["target_type"],
                        item["match_method"],
                        item["match_reason"],
                        item["confirmation_status"],
                        json.dumps(item["candidates"], ensure_ascii=False, separators=(",", ":")),
                        json.dumps(item["samples"], ensure_ascii=False, separators=(",", ":")),
                    )
                    for item in compiled["mappings"]
                ],
            )
        if compiled["issues"]:
            connection.executemany(
                """
                INSERT INTO p3_issues (run_id, severity, code, message, source_column_id, standard_field_id, region_id)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (run_id, item["severity"], item["code"], item["message"], item["source_column_id"], item["standard_field_id"], item["region_id"])
                    for item in compiled["issues"]
                ],
            )
        connection.execute("COMMIT")
        body = _stored_plan(connection, dataset_id, run_id)
    except L1Error:
        if connection.in_transaction:
            connection.rollback()
        raise
    except sqlite3.Error as exc:
        if connection.in_transaction:
            connection.rollback()
        raise L1Error("storage_failed", f"P3 映射没有写入：{exc}", 500) from exc
    finally:
        connection.close()
    body["persisted"] = True
    return body


def get_schema_run(run_id: str) -> dict:
    if not run_id.startswith("P3-") or len(run_id) != 15:
        raise L1Error("run_not_found", "没有这个 P3 执行版本。", 404)
    connection = _open()
    try:
        row = connection.execute("SELECT dataset_id FROM p3_runs WHERE run_id = ?", (run_id,)).fetchone()
        if row is None:
            raise L1Error("run_not_found", "没有这个 P3 执行版本。", 404)
        return _artifact(connection, str(row["dataset_id"]), run_id)
    finally:
        connection.close()


def list_schema_rows(run_id: str, offset: int, limit: int, region_id: str | None, view: str) -> dict:
    if not run_id.startswith("P3-") or len(run_id) != 15:
        raise L1Error("run_not_found", "没有这个 P3 执行版本。", 404)
    _check_page(offset, limit)
    connection = _open()
    try:
        run = connection.execute("SELECT * FROM p3_runs WHERE run_id = ?", (run_id,)).fetchone()
        if run is None:
            raise L1Error("run_not_found", "没有这个 P3 执行版本。", 404)
        mappings = _load_mapping_rows(connection, run_id)
        regions = _regions_for_run(connection, run, region_id)
        page = _sample_records(connection, str(run["dataset_id"]), mappings, regions, offset, limit, run_id=run_id, p2_run_id=run["p2_run_id"])
    finally:
        connection.close()
    page["view"] = view if view in {"input", "output"} else "output"
    page["run_id"] = run_id
    page["values_are_raw_text"] = True
    return page


def diff_schema_run(run_id: str) -> dict:
    connection = _open()
    try:
        run = connection.execute("SELECT * FROM p3_runs WHERE run_id = ?", (run_id,)).fetchone()
        if run is None:
            raise L1Error("run_not_found", "没有这个 P3 执行版本。", 404)
        previous = connection.execute(
            """
            SELECT run_id, revision FROM p3_runs
            WHERE dataset_id = ? AND revision < ?
            ORDER BY revision DESC LIMIT 1
            """,
            (run["dataset_id"], run["revision"]),
        ).fetchone()
        current = _load_mapping_rows(connection, run_id)
        before = _load_mapping_rows(connection, str(previous["run_id"])) if previous else []
    finally:
        connection.close()
    return _mapping_diff(before, current, None if previous is None else int(previous["revision"]), None if previous is None else str(previous["run_id"]))


def _compile(connection: sqlite3.Connection, dataset: sqlite3.Row, config: dict) -> dict:
    if not isinstance(config, dict):
        raise L1Error("invalid_config", "映射配置必须是 JSON 对象。", 422)
    template_id = config.get("template_id") or TEMPLATE_ID
    template_version = config.get("template_version") or TEMPLATE_VERSION
    if not isinstance(template_id, str) or not isinstance(template_version, str):
        raise L1Error("invalid_config", "模板标识无效。", 422)
    template = load_template(connection, template_id, template_version)
    if template is None:
        raise L1Error("template_not_found", "没有这个 Schema 模板版本。", 404)
    p1 = _latest_p1(connection, dataset["dataset_id"])
    regions = _field_regions(connection, p1["run_id"])
    if not regions:
        raise L1Error("p1_required", "没有已确认的数据区域可以映射。", 409)
    p2_latest = _peek_p2(connection, dataset["dataset_id"])
    p2 = _latest_p2(connection, dataset["dataset_id"])
    overrides = _overrides(config.get("mappings"))
    batch_confirm = bool(config.get("batch_confirm", True))
    mappings = []
    for region in regions:
        for column in region["columns"]:
            header = " / ".join(part for part in column.get("header") or [] if part) or column["column_id"]
            source_column_id = f"{region['region_id']}:{column['column_id']}"
            suggestion = match_header(header)
            mapping = _apply_override(suggestion, overrides.get(source_column_id), batch_confirm, region, column, header, source_column_id)
            mapping["samples"] = _column_samples(connection, dataset["dataset_id"], region, int(column["column_number"]))
            mapping["sample_limited"] = True
            mappings.append(mapping)
    p2_columns = {}
    if p2 is not None:
        stored = json.loads(p2["config_json"])
        for item in stored.get("regions") or []:
            if isinstance(item, dict) and item.get("region_id") and item.get("column_id"):
                p2_columns[str(item["region_id"])] = str(item["column_id"])
    issues = validate(mappings, p2_columns)
    if p2_latest is not None and str(p2_latest["status"]) == "needs_review":
        try:
            p2_cfg = json.loads(p2_latest["config_json"] or "{}")
        except json.JSONDecodeError:
            p2_cfg = {}
        warning_codes = {item.get("code") for item in (p2_cfg.get("warnings") or []) if isinstance(item, dict)}
        # 仅字段歧义 / 台账冲突阻断；纯无设备字段或未归组过高只警告，避免破坏既有 P3 契约
        if warning_codes & {"region_needs_review", "conflicting_equipment_candidates", "ledger_ambiguous_asset"}:
            issues.append(
                {
                    "severity": "blocking",
                    "code": "p2_needs_review",
                    "message": "P2 设备编号字段存在歧义或台账冲突，确认后再进入字段映射。",
                    "source_column_id": "",
                    "standard_field_id": "equipment_id",
                    "region_id": "",
                }
            )
        else:
            issues.append(
                {
                    "severity": "warning",
                    "code": "p2_needs_review",
                    "message": "P2 分组待审核（例如缺少设备编号或未归组比例过高），字段映射仍可继续，但设备索引可能不完整。",
                    "source_column_id": "",
                    "standard_field_id": "equipment_id",
                    "region_id": "",
                }
            )
    elif p2 is None:
        issues.append(
            {
                "severity": "warning",
                "code": "p2_missing",
                "message": "还没有 P2 设备分组。设备标识映射不会改写分组，但暂时不能核对两边是否一致。",
                "source_column_id": "",
                "standard_field_id": "equipment_id",
                "region_id": "",
            }
        )
    summary = _summary(mappings, issues)
    return {
        "template_id": template["template_id"],
        "template_version": template["version"],
        "template": template,
        "p1_run_id": p1["run_id"],
        "p2_run_id": None if p2 is None else p2["run_id"],
        "regions": regions,
        "mappings": mappings,
        "issues": issues,
        "summary": summary,
        "apply_allowed": not any(item["severity"] == "blocking" for item in issues),
        "config": {
            "template_id": template["template_id"],
            "template_version": template["version"],
            "rule_id": RULE_ID,
            "p1_run_id": p1["run_id"],
            "p2_run_id": None if p2 is None else p2["run_id"],
            "batch_confirm": batch_confirm,
            "mappings": [
                {
                    "source_column_id": item["source_column_id"],
                    "standard_field_id": item["standard_field_id"],
                    "role": item["role"],
                    "eligibility": item["eligibility"],
                    "source_unit": item["source_unit"],
                    "target_unit": item["target_unit"],
                    "target_type": item["target_type"],
                    "confirmation_status": item["confirmation_status"],
                }
                for item in mappings
            ],
        },
    }


def _apply_override(suggestion: dict, override: dict | None, batch_confirm: bool, region: dict, column: dict, header: str, source_column_id: str) -> dict:
    field_id = suggestion["standard_field_id"]
    role = suggestion["role"]
    eligibility = suggestion["eligibility"]
    source_unit = suggestion["source_unit"] or parse_unit(header)
    target_unit = suggestion["target_unit"]
    target_type = suggestion["target_type"]
    method = suggestion["match_method"]
    reason = suggestion["match_reason"]
    confirmation = suggestion["confirmation_status"]
    candidates = list(suggestion["candidates"])
    if override:
        if "standard_field_id" in override and override["standard_field_id"] != field_id:
            field_id = override["standard_field_id"]
            method = "manual"
            reason = "用户指定了标准字段。"
            field = FIELD_BY_ID.get(field_id) if field_id else None
            if field is None:
                role, eligibility, target_unit, target_type = "unmapped", "excluded", "", ""
            else:
                role, eligibility, target_unit, target_type = field.role, field.eligibility, field.target_unit, field.target_type
        if override.get("role"):
            role = override["role"]
        if override.get("eligibility"):
            eligibility = override["eligibility"]
        if "source_unit" in override:
            source_unit = override["source_unit"]
        if "target_unit" in override:
            target_unit = override["target_unit"]
        if override.get("target_type"):
            target_type = override["target_type"]
        if override.get("confirmation_status"):
            confirmation = override["confirmation_status"]
    if role not in ROLES:
        raise L1Error("invalid_config", "字段角色不在已定义的角色里。", 422)
    if confirmation not in {"suggested", "needs_confirmation", "confirmed"}:
        raise L1Error("invalid_config", "确认状态无效。", 422)
    if batch_confirm and confirmation == "suggested" and field_id:
        confirmation = "confirmed"
    label = FIELD_BY_ID[field_id].label if field_id in FIELD_BY_ID else ""
    meaning = FIELD_BY_ID[field_id].physical_meaning if field_id in FIELD_BY_ID else ""
    description = FIELD_BY_ID[field_id].description if field_id in FIELD_BY_ID else ""
    return {
        "source_column_id": source_column_id,
        "region_id": region["region_id"],
        "sheet_id": region["sheet_id"],
        "sheet_name": region["sheet_name"],
        "range_label": region["range_label"],
        "column_id": column["column_id"],
        "column_number": int(column["column_number"]),
        "header": header,
        "standard_field_id": field_id,
        "standard_label": label,
        "description": description,
        "physical_meaning": meaning,
        "role": role if field_id else "unmapped",
        "eligibility": "excluded" if not field_id else eligibility,
        "source_unit": source_unit,
        "target_unit": target_unit,
        "target_type": target_type,
        "match_method": method if field_id or method == "ambiguous" else "unmapped",
        "match_reason": reason,
        "confirmation_status": confirmation,
        "candidates": candidates,
        "raw_type_seen": "text",
    }


def _overrides(payload: object) -> dict[str, dict]:
    if payload is None:
        return {}
    if not isinstance(payload, list):
        raise L1Error("invalid_config", "字段映射必须是数组。", 422)
    found = {}
    for item in payload:
        if not isinstance(item, dict) or not isinstance(item.get("source_column_id"), str):
            raise L1Error("invalid_config", "字段映射缺少 source_column_id。", 422)
        override = {}
        if "standard_field_id" in item:
            field_id = item.get("standard_field_id") or ""
            if not isinstance(field_id, str):
                raise L1Error("invalid_config", "标准字段必须是字符串。", 422)
            override["standard_field_id"] = field_id
        for key in ("role", "eligibility", "source_unit", "target_unit", "target_type", "confirmation_status"):
            if isinstance(item.get(key), str):
                override[key] = item[key]
        found[item["source_column_id"]] = override
    return found


def _summary(mappings: list[dict], issues: list[dict]) -> dict:
    matched = [item for item in mappings if item["standard_field_id"]]
    return {
        "column_count": len(mappings),
        "matched_count": len(matched),
        "auto_matched_count": sum(1 for item in matched if item["match_method"] in {"exact_match", "alias_match", "normalized_match", "similarity"}),
        "confirmed_count": sum(1 for item in mappings if item["confirmation_status"] == "confirmed"),
        "pending_count": sum(1 for item in mappings if item["confirmation_status"] == "needs_confirmation"),
        "unmapped_count": sum(1 for item in mappings if not item["standard_field_id"]),
        "conflict_count": sum(1 for item in issues if item["severity"] == "blocking"),
        "warning_count": sum(1 for item in issues if item["severity"] == "warning"),
        "label_fields": [item["header"] for item in mappings if item["role"] == "target_label"],
        "diagnostic_fields": [item["header"] for item in mappings if item["role"] == "diagnostic_output"],
    }


def _public_plan(dataset: sqlite3.Row, compiled: dict, persisted: bool) -> dict:
    return {
        "dataset_id": dataset["dataset_id"],
        "persisted": persisted,
        "applied": False,
        "template_id": compiled["template_id"],
        "template_version": compiled["template_version"],
        "template": compiled["template"],
        "p1_run_id": compiled["p1_run_id"],
        "p2_run_id": compiled["p2_run_id"],
        "rule_id": RULE_ID,
        "apply_allowed": compiled["apply_allowed"],
        "summary": compiled["summary"],
        "mappings": compiled["mappings"],
        "issues": compiled["issues"],
        "p0_sha256": dataset["sha256"],
    }


def _stored_plan(connection: sqlite3.Connection, dataset_id: str, run_id: str) -> dict:
    run = connection.execute("SELECT * FROM p3_runs WHERE run_id = ?", (run_id,)).fetchone()
    template = load_template(connection, run["template_id"], run["template_version"])
    mappings = _load_mapping_rows(connection, run_id)
    issues = [
        {
            "severity": row["severity"],
            "code": row["code"],
            "message": row["message"],
            "source_column_id": row["source_column_id"],
            "standard_field_id": row["standard_field_id"],
            "region_id": row["region_id"],
        }
        for row in connection.execute("SELECT * FROM p3_issues WHERE run_id = ? ORDER BY issue_id", (run_id,)).fetchall()
    ]
    summary = json.loads(run["summary_json"])
    return {
        "dataset_id": dataset_id,
        "persisted": True,
        "applied": True,
        "run": _public_run(run),
        "template_id": run["template_id"],
        "template_version": run["template_version"],
        "template": template,
        "p1_run_id": run["p1_run_id"],
        "p2_run_id": run["p2_run_id"],
        "rule_id": run["rule_id"],
        "apply_allowed": True,
        "summary": summary,
        "mappings": mappings,
        "issues": issues,
        "p0_sha256": run["p0_sha256"],
        "artifact": _artifact_body(run, mappings, issues),
    }


def _artifact(connection: sqlite3.Connection, dataset_id: str, run_id: str) -> dict:
    plan = _stored_plan(connection, dataset_id, run_id)
    return plan["artifact"]


def _artifact_body(run: sqlite3.Row, mappings: list[dict], issues: list[dict]) -> dict:
    return {
        "dataset_id": run["dataset_id"],
        "schema_template_id": run["template_id"],
        "schema_template_version": run["template_version"],
        "p3_run_id": run["run_id"],
        "upstream_references": {"p0_sha256": run["p0_sha256"], "p1_run_id": run["p1_run_id"], "p2_run_id": run["p2_run_id"]},
        "field_mappings": [
            {
                "source_column_id": item["source_column_id"],
                "region_id": item["region_id"],
                "sheet_id": item["sheet_id"],
                "column_id": item["column_id"],
                "header": item["header"],
                "standard_field_id": item["standard_field_id"],
                "role": item["role"],
                "eligibility": item["eligibility"],
                "source_unit": item["source_unit"],
                "target_unit": item["target_unit"],
                "target_type": item["target_type"],
            }
            for item in mappings
            if item["standard_field_id"]
        ],
        "unmapped_columns": [item["source_column_id"] for item in mappings if not item["standard_field_id"]],
        "validation_issues": issues,
        "status": "confirmed",
    }


def _load_mapping_rows(connection: sqlite3.Connection, run_id: str) -> list[dict]:
    rows = connection.execute(
        "SELECT * FROM p3_mappings WHERE run_id = ? ORDER BY sheet_name, column_number",
        (run_id,),
    ).fetchall()
    items = []
    for row in rows:
        field = FIELD_BY_ID.get(row["standard_field_id"])
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
                "standard_label": "" if field is None else field.label,
                "description": "" if field is None else field.description,
                "physical_meaning": "" if field is None else field.physical_meaning,
                "role": row["role"],
                "eligibility": row["eligibility"],
                "source_unit": row["source_unit"],
                "target_unit": row["target_unit"],
                "target_type": row["target_type"],
                "match_method": row["match_method"],
                "match_reason": row["match_reason"],
                "confirmation_status": row["confirmation_status"],
                "candidates": json.loads(row["candidates_json"]),
                "samples": json.loads(row["sample_json"]),
                "sample_limited": True,
            }
        )
    return items


def _diff(connection: sqlite3.Connection, dataset_id: str, compiled: dict) -> dict:
    current = connection.execute(
        "SELECT run_id, revision FROM p3_runs WHERE dataset_id = ? ORDER BY revision DESC LIMIT 1",
        (dataset_id,),
    ).fetchone()
    before = _load_mapping_rows(connection, str(current["run_id"])) if current else []
    return _mapping_diff(
        before,
        compiled["mappings"],
        None if current is None else int(current["revision"]),
        None if current is None else str(current["run_id"]),
    )


def _mapping_diff(before: list[dict], after: list[dict], revision: int | None, run_id: str | None) -> dict:
    old = {item["source_column_id"]: item for item in before}
    changes = []
    for item in after:
        previous = old.get(item["source_column_id"])
        if previous is None:
            kind = "added"
        else:
            kind = _change_kind(previous, item)
        if kind:
            changes.append(
                {
                    "source_column_id": item["source_column_id"],
                    "header": item["header"],
                    "change": kind,
                    "before": None
                    if previous is None
                    else {
                        "standard_field_id": previous["standard_field_id"],
                        "role": previous["role"],
                        "source_unit": previous["source_unit"],
                        "target_unit": previous["target_unit"],
                        "target_type": previous["target_type"],
                    },
                    "after": {
                        "standard_field_id": item["standard_field_id"],
                        "role": item["role"],
                        "source_unit": item["source_unit"],
                        "target_unit": item["target_unit"],
                        "target_type": item["target_type"],
                    },
                }
            )
    return {
        "baseline_run_id": run_id,
        "baseline_revision": revision,
        "changed_count": len(changes),
        "changes": changes[:200],
        "note": "这里只比较字段语义和声明，不表示数值已经被清洗或换算。",
    }


def _change_kind(before: dict, after: dict) -> str:
    if before["standard_field_id"] != after["standard_field_id"]:
        return "field"
    if before["role"] != after["role"]:
        return "role"
    if before["source_unit"] != after["source_unit"] or before["target_unit"] != after["target_unit"]:
        return "unit"
    if before["target_type"] != after["target_type"]:
        return "type"
    if (not before["standard_field_id"]) != (not after["standard_field_id"]):
        return "unmapped"
    return ""


def _sample_records(
    connection: sqlite3.Connection,
    dataset_id: str,
    mappings: list[dict],
    regions: list[dict],
    offset: int,
    limit: int,
    run_id: str | None = None,
    p2_run_id: str | None = None,
) -> dict:
    windows = []
    total = 0
    for region in regions:
        count = _business_count(connection, dataset_id, region)
        windows.append((region, total, count))
        total += count
    selected: list[tuple[dict, sqlite3.Row]] = []
    cursor = offset
    remaining = limit
    for region, start, count in windows:
        if remaining <= 0:
            break
        local_end = start + count
        if cursor >= local_end:
            continue
        local_offset = max(0, cursor - start)
        take = min(remaining, count - local_offset)
        rows = _business_page(connection, dataset_id, region, local_offset, take)
        selected.extend((region, row) for row in rows)
        remaining -= len(rows)
        cursor = local_end
    grouped: dict[tuple[str, int, int], list[dict]] = {}
    for region, row in selected:
        key = (region["sheet_id"], int(region["start_column"]), int(region["end_column"]))
        grouped.setdefault(key, []).append(row)
    cells: dict[tuple[str, int], dict[int, sqlite3.Row]] = {}
    for (sheet_id, start_column, end_column), rows in grouped.items():
        numbers = [int(item["row_number"]) for item in rows]
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
            cells.setdefault((cell["source_row_id"], int(cell["column_number"])), cell)
    by_region_columns: dict[str, list[dict]] = {}
    for item in mappings:
        by_region_columns.setdefault(item["region_id"], []).append(item)
    membership = _p2_membership(connection, p2_run_id, [row["source_row_id"] for _region, row in selected])
    records = []
    for region, row in selected:
        source_row_id = row["source_row_id"]
        fields = []
        for mapping in by_region_columns.get(region["region_id"], []):
            cell = cells.get((source_row_id, int(mapping["column_number"])))
            fields.append(
                {
                    "source_column_id": mapping["source_column_id"],
                    "column_id": mapping["column_id"],
                    "column_number": mapping["column_number"],
                    "header": mapping["header"],
                    "standard_field_id": mapping["standard_field_id"],
                    "standard_label": mapping.get("standard_label") or "",
                    "role": mapping["role"],
                    "raw_value": "" if cell is None else cell["raw_value"],
                    "cell_id": None if cell is None else cell["cell_id"],
                    "coordinate": None if cell is None else cell["coordinate"],
                    "cell_type": None if cell is None else cell["cell_type"],
                }
            )
        group = membership.get(source_row_id)
        records.append(
            {
                "source_row_id": source_row_id,
                "region_id": region["region_id"],
                "sheet_id": region["sheet_id"],
                "sheet_name": region["sheet_name"],
                "row_number": int(row["row_number"]),
                "p3_run_id": run_id,
                "p2_run_id": None if group is None else p2_run_id,
                "equipment_group_id": None if group is None else group["group_id"],
                "equipment_value": "" if group is None else group["original_equipment_value"],
                "fields": fields,
            }
        )
    return {"total_records": total, "offset": offset, "limit": limit, "rows": records}


def _p2_membership(connection: sqlite3.Connection, run_id: str | None, source_row_ids: list[str]) -> dict[str, sqlite3.Row]:
    if not run_id or not source_row_ids:
        return {}
    marks = ",".join("?" for _ in source_row_ids)
    rows = connection.execute(
        f"""
        SELECT source_row_id, group_id, original_equipment_value
        FROM p2_membership
        WHERE run_id = ? AND source_row_id IN ({marks})
        """,
        (run_id, *source_row_ids),
    ).fetchall()
    return {row["source_row_id"]: row for row in rows}


def _column_samples(connection: sqlite3.Connection, dataset_id: str, region: dict, column_number: int) -> list[str]:
    header_count = int(region["header_row_count"])
    header_start = int(region["header_start_row"] or 0)
    header_end = header_start + header_count - 1 if header_count else 0
    rows = connection.execute(
        """
        SELECT c.raw_value
        FROM source_rows s
        JOIN raw_cells c
          ON c.dataset_id = s.dataset_id AND c.sheet_id = s.sheet_id
         AND c.row_number = s.row_number AND c.column_number = ?
        WHERE s.dataset_id = ? AND s.sheet_id = ? AND s.row_number BETWEEN ? AND ?
          AND (? = 0 OR s.row_number < ? OR s.row_number > ?)
          AND c.raw_value <> ''
        ORDER BY s.row_number
        LIMIT ?
        """,
        (
            column_number,
            dataset_id,
            region["sheet_id"],
            region["start_row"],
            region["end_row"],
            header_count,
            header_start,
            header_end,
            _SAMPLE_LIMIT,
        ),
    ).fetchall()
    return [row["raw_value"] for row in rows]


def _business_count(connection: sqlite3.Connection, dataset_id: str, region: dict) -> int:
    header_count = int(region["header_row_count"])
    header_start = int(region["header_start_row"] or 0)
    header_end = header_start + header_count - 1 if header_count else 0
    row = connection.execute(
        """
        SELECT COUNT(*) AS total FROM source_rows
        WHERE dataset_id = ? AND sheet_id = ? AND row_number BETWEEN ? AND ?
          AND (? = 0 OR row_number < ? OR row_number > ?)
        """,
        (dataset_id, region["sheet_id"], region["start_row"], region["end_row"], header_count, header_start, header_end),
    ).fetchone()
    return int(row["total"])


def _business_page(connection: sqlite3.Connection, dataset_id: str, region: dict, offset: int, limit: int) -> list[sqlite3.Row]:
    header_count = int(region["header_row_count"])
    header_start = int(region["header_start_row"] or 0)
    header_end = header_start + header_count - 1 if header_count else 0
    return connection.execute(
        """
        SELECT source_row_id, row_number FROM source_rows
        WHERE dataset_id = ? AND sheet_id = ? AND row_number BETWEEN ? AND ?
          AND (? = 0 OR row_number < ? OR row_number > ?)
        ORDER BY row_number
        LIMIT ? OFFSET ?
        """,
        (dataset_id, region["sheet_id"], region["start_row"], region["end_row"], header_count, header_start, header_end, limit, offset),
    ).fetchall()


def _field_regions(connection: sqlite3.Connection, run_id: str) -> list[dict]:
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
    regions = []
    for row in rows:
        start_column = int(row["start_column"])
        end_column = int(row["end_column"])
        regions.append(
            {
                "region_id": row["region_id"],
                "sheet_id": row["sheet_id"],
                "sheet_name": row["sheet_name"],
                "start_row": int(row["start_row"]),
                "end_row": int(row["end_row"]),
                "start_column": start_column,
                "end_column": end_column,
                "header_start_row": row["header_start_row"],
                "header_row_count": int(row["header_row_count"]),
                "range_label": f"{_letters(start_column)}{row['start_row']}:{_letters(end_column)}{row['end_row']}",
                "columns": json.loads(row["column_json"]),
            }
        )
    return regions


def _regions_for_run(connection: sqlite3.Connection, run: sqlite3.Row, region_id: str | None) -> list[dict]:
    regions = _field_regions(connection, run["p1_run_id"])
    if region_id:
        regions = [item for item in regions if item["region_id"] == region_id]
        if not regions:
            raise L1Error("region_not_found", "这个 P3 版本里没有该数据区域。", 404)
    return regions


def _latest_p1(connection: sqlite3.Connection, dataset_id: str) -> sqlite3.Row:
    row = connection.execute(
        "SELECT * FROM p1_runs WHERE dataset_id = ? ORDER BY revision DESC LIMIT 1",
        (dataset_id,),
    ).fetchone()
    if row is None:
        raise L1Error("p1_required", "请先执行 P1。P3 只读取已确认的数据区域。", 409)
    status = str(row["status"])
    if status == "needs_review":
        raise L1Error("p1_needs_review", "P1 结果待审核，确认区域后再进入字段映射。", 409)
    if status not in {"completed", "completed_with_warnings"}:
        raise L1Error("p1_required", "请先执行 P1。P3 只读取已确认的数据区域。", 409)
    return row


def _peek_p2(connection: sqlite3.Connection, dataset_id: str) -> sqlite3.Row | None:
    try:
        return connection.execute(
            "SELECT * FROM p2_runs WHERE dataset_id = ? ORDER BY revision DESC LIMIT 1",
            (dataset_id,),
        ).fetchone()
    except sqlite3.OperationalError:
        return None


def _latest_p2(connection: sqlite3.Connection, dataset_id: str) -> sqlite3.Row | None:
    try:
        rows = connection.execute(
            "SELECT * FROM p2_runs WHERE dataset_id = ? ORDER BY revision DESC",
            (dataset_id,),
        ).fetchall()
    except sqlite3.OperationalError:
        return None
    for row in rows:
        if str(row["status"]) in {"completed", "completed_with_warnings"}:
            return row
    return None


def _resolve_run(connection: sqlite3.Connection, dataset_id: str, run_id: str | None) -> str | None:
    if run_id:
        found = connection.execute("SELECT run_id FROM p3_runs WHERE dataset_id = ? AND run_id = ?", (dataset_id, run_id)).fetchone()
        if found is None:
            raise L1Error("run_not_found", "没有这个 P3 执行版本。", 404)
        return run_id
    found = connection.execute("SELECT run_id FROM p3_runs WHERE dataset_id = ? ORDER BY revision DESC LIMIT 1", (dataset_id,)).fetchone()
    return None if found is None else str(found["run_id"])


def _public_run(run: sqlite3.Row) -> dict:
    return {
        "run_id": run["run_id"],
        "revision": int(run["revision"]),
        "status": run["status"],
        "created_at": run["created_at"],
        "p1_run_id": run["p1_run_id"],
        "p2_run_id": run["p2_run_id"],
        "template_id": run["template_id"],
        "template_version": run["template_version"],
        "rule_id": run["rule_id"],
        "p0_sha256": run["p0_sha256"],
    }


def _block_message(issues: list[dict]) -> str:
    blocking = [item for item in issues if item["severity"] == "blocking"]
    if not blocking:
        return "当前映射不能执行。"
    return blocking[0]["message"]


def _check_page(offset: int, limit: int) -> None:
    if offset < 0 or limit < 1 or limit > 200:
        raise L1Error("invalid_page", "offset 必须大于等于 0，limit 必须是 1 到 200 的整数。", 422)


def _letters(column: int) -> str:
    letters = ""
    number = column
    while number:
        number, remainder = divmod(number - 1, 26)
        letters = chr(65 + remainder) + letters
    return letters


def _ready(dataset_id: str) -> sqlite3.Row:
    row = _require(dataset_id)
    _require_indexed(row)
    return row


def _open() -> sqlite3.Connection:
    connection = _connect()
    ensure_tables(connection)
    return connection
