"""P4 配置检查。不修改 P3 的角色、资格和已确认语义。"""

from __future__ import annotations

import re

from app.services.l1_standardization.datetime_parser import parse_offset
from app.services.l1_standardization.definitions import DECIMAL_STYLES, FAILURE_POLICIES, THOUSANDS
from app.services.l1_standardization.unit_converter import convert_unit
from decimal import Decimal


def validate_config(fields: list[dict], options: dict) -> list[dict]:
    issues = []
    decimal = options.get("decimal_style") or "point"
    thousands = options.get("thousands_separator") or ""
    if decimal not in DECIMAL_STYLES:
        issues.append(_issue("blocking", "invalid_decimal", "小数点样式只接受 point 或 comma。", ""))
    if thousands not in THOUSANDS:
        issues.append(_issue("blocking", "invalid_thousands", "千位分隔符只接受空、逗号、点或空格。", ""))
    point = "." if decimal == "point" else ","
    if thousands and thousands == point:
        issues.append(_issue("blocking", "invalid_thousands", "千位分隔符不能和小数点相同。", ""))
    if options.get("failure_policy", "keep_record") not in FAILURE_POLICIES:
        issues.append(_issue("blocking", "invalid_failure_policy", "P4 只保留记录，不能删除无法解析的行。", ""))
    if not isinstance(options.get("missing_tokens", []), list) or any(not isinstance(item, str) for item in options.get("missing_tokens", [])):
        issues.append(_issue("blocking", "invalid_missing_tokens", "缺失标识必须是字符串列表。", ""))
    for key in ("source_timezone", "target_timezone"):
        value = options.get(key) or ""
        if value and parse_offset(value) is None:
            issues.append(_issue("blocking", "invalid_timezone", "时区请使用 Z 或 ±HH:MM，不能使用服务器时区名称来猜测。", ""))
    for fmt in options.get("datetime_formats") or []:
        if not isinstance(fmt, str) or re.search(r"%[^aAbBcdHIjmMpSUwWxXyYf%]", fmt):
            issues.append(_issue("blocking", "invalid_datetime_format", "日期格式含有不能识别的指令。", ""))
    unconfirmed = 0
    for field in fields:
        column = field["source_column_id"]
        if field.get("confirmation_status") != "confirmed":
            unconfirmed += 1
            if field.get("enabled"):
                issues.append(_issue("blocking", "unconfirmed_enabled", f"{field.get('header') or column} 尚未在 P3 确认，不能在 P4 启用转换。", column))
            else:
                issues.append(_issue("warning", "mapping_unconfirmed", f"{field.get('header') or column} 映射未确认，本阶段保留原始值。", column))
            continue
        if not field.get("enabled", True):
            issues.append(_issue("info", "conversion_disabled", f"{field.get('header') or column} 的转换已停用。", column))
            continue
        declared = field.get("declared_target_type")
        if declared and declared != field.get("target_type"):
            issues.append(_issue("blocking", "p3_type_locked", f"{field.get('header') or column} 的目标类型属于 P3。要修改请回到 P3 生成新修订。", column))
        if field.get("target_type") in {"float64", "integer"} and field.get("target_unit"):
            _value, status, _rule = convert_unit(Decimal(1), field.get("source_unit") or "", field.get("target_unit") or "", field.get("dimension") or "")
            if status == "unit_incompatible":
                issues.append(_issue("blocking", "unit_incompatible", f"{field.get('header') or column} 的单位 {field.get('source_unit') or '空'} 不能换算到 {field.get('target_unit')}。", column))
            elif status == "unit_unconfirmed":
                issues.append(_issue("warning", "unit_unconfirmed", f"{field.get('header') or column} 的来源单位未确认，不会猜测单位或执行换算。", column))
        if field.get("role") in {"target_label", "diagnostic_output"} and field.get("eligibility") != "excluded":
            issues.append(_issue("blocking", "label_leakage", f"{field.get('header') or column} 是标签或诊断输出，不能改变 P3 的排除资格。", column))
    if unconfirmed:
        issues.append(_issue("warning", "partial_standardization", f"有 {unconfirmed} 个字段尚未确认，P4 结果只能是部分标准化。", ""))
    return issues


def _issue(severity: str, code: str, message: str, source_column_id: str) -> dict:
    return {"severity": severity, "code": code, "message": message, "source_column_id": source_column_id}
