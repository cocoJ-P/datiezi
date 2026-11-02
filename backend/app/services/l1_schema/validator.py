"""映射校验。严重冲突阻止执行；缺列和未映射只警告。"""

from __future__ import annotations

from app.services.l1_schema.definitions import FIELD_BY_ID, FIELDS
from app.services.l1_schema.matcher import unit_dimension


def validate(mappings: list[dict], p2_columns: dict[str, str]) -> list[dict]:
    issues: list[dict] = []
    by_region: dict[str, dict[str, list[dict]]] = {}
    present = {item["standard_field_id"] for item in mappings if item.get("standard_field_id")}
    for item in mappings:
        field_id = item.get("standard_field_id") or ""
        field = FIELD_BY_ID.get(field_id) if field_id else None
        if field_id and field is None:
            issues.append(_issue("blocking", "unknown_field", f"标准字段 {field_id} 不在当前模板中。", item))
            continue
        if item["role"] == "unmapped" or not field_id:
            if item["eligibility"] == "candidate_input":
                issues.append(_issue("blocking", "unmapped_input", "未映射字段不能进入模型输入。", item))
            continue
        if field is None:
            continue
        if item["role"] != field.role and item["role"] not in {"unmapped"}:
            issues.append(_issue("info", "role_override", f"{item['header']} 的角色已改成 {item['role']}。", item))
        if item["role"] in {"target_label", "diagnostic_output"} and item["eligibility"] == "candidate_input":
            issues.append(_issue("blocking", "label_leakage", f"{item['header']} 是{ _role_name(item['role']) }，不能作为预测输入。", item))
        if item["eligibility"] not in {"candidate_input", "excluded", "requires_review"}:
            issues.append(_issue("blocking", "invalid_eligibility", "模型输入资格无效。", item))
        source_dimension = unit_dimension(item.get("source_unit") or "")
        if source_dimension and field.dimension not in {"", "text", "identity", "category", "time"} and source_dimension != field.dimension:
            issues.append(_issue("blocking", "unit_conflict", f"{item['header']} 的单位 {item['source_unit']} 与 {field.label} 的 {field.target_unit} 不是同一物理量。", item))
        elif field.unit_sensitive and field.dimension not in {"", "text"} and not source_dimension:
            issues.append(_issue("warning", "unit_unspecified", f"{item['header']} 没有识别出单位，目标单位暂记为 {field.target_unit or '空'}。", item))
        by_region.setdefault(item["region_id"], {}).setdefault(field_id, []).append(item)
        p2_column = p2_columns.get(item["region_id"])
        if field_id == "equipment_id" and p2_column and p2_column != item["column_id"]:
            issues.append(_issue("blocking", "p2_identifier_conflict", "这个设备标识和 P2 已确认的设备编号字段不一致。请先改 P2，或改回同一个字段。", item))
    for region_id, grouped in by_region.items():
        for field_id, columns in grouped.items():
            if len(columns) > 1:
                names = "、".join(item["header"] for item in columns[:4])
                issues.append(
                    {
                        "severity": "blocking",
                        "code": "duplicate_standard_field",
                        "message": f"同一数据区域里，{names} 都指向 {FIELD_BY_ID[field_id].label}。P3 不会合并这些列，请只保留一个映射。",
                        "source_column_id": columns[0]["source_column_id"],
                        "standard_field_id": field_id,
                        "region_id": region_id,
                    }
                )
    for field in FIELDS:
        if field.required and field.field_id not in present:
            issues.append(
                {
                    "severity": "warning",
                    "code": "missing_required",
                    "message": f"模板中的必需字段「{field.label}」在当前数据里没有映射。",
                    "source_column_id": "",
                    "standard_field_id": field.field_id,
                    "region_id": "",
                }
            )
    return issues


def _issue(severity: str, code: str, message: str, mapping: dict) -> dict:
    return {
        "severity": severity,
        "code": code,
        "message": message,
        "source_column_id": mapping.get("source_column_id") or "",
        "standard_field_id": mapping.get("standard_field_id") or "",
        "region_id": mapping.get("region_id") or "",
    }


def _role_name(role: str) -> str:
    names = {"target_label": "监督标签", "diagnostic_output": "已有诊断输出"}
    return names.get(role, role)
