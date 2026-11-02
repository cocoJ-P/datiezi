"""单个单元格的标准化。原始值只读，失败时规范化值为空。"""

from __future__ import annotations

from decimal import Decimal

from app.services.l1_standardization.datetime_parser import parse_datetime
from app.services.l1_standardization.definitions import DATETIME_TYPES, NUMERIC_TYPES, TEXT_TYPES
from app.services.l1_standardization.numeric_parser import as_integer, decimal_text, parse_number
from app.services.l1_standardization.unit_converter import convert_unit


def convert_cell(raw: str | None, cell_type: str | None, number_format: str | None, field: dict, options: dict, excel_system: str) -> dict:
    raw_text = "" if raw is None else str(raw)
    base = {
        "raw_value": raw_text,
        "cell_type": cell_type or "",
        "target_type": field.get("target_type") or "",
        "source_unit": field.get("source_unit") or "",
        "target_unit": field.get("target_unit") or "",
        "role": field.get("role") or "",
        "eligibility": field.get("eligibility") or "",
        "normalized_value": None,
        "normalized_text": "",
        "status": "not_executed",
        "rule_id": "",
        "unit_rule": "",
        "timezone_status": "",
        "precision": "",
        "message": "",
    }
    if not field.get("standard_field_id"):
        base["status"] = "not_applicable"
        base["message"] = "未映射字段不进入标准化。"
        return base
    if field.get("confirmation_status") != "confirmed":
        base["status"] = "mapping_unconfirmed"
        base["message"] = "映射尚未确认，不执行类型和单位转换。"
        return base
    if not field.get("enabled", True):
        base["status"] = "not_executed"
        base["message"] = "这个字段的转换已停用。"
        return base
    target = field.get("target_type") or "string"
    tokens = tuple(field.get("missing_tokens") or options.get("missing_tokens") or [])
    if target in TEXT_TYPES:
        return _text(base, raw_text, tokens)
    if target in DATETIME_TYPES:
        return _time(base, raw_text, cell_type or "", number_format, field, options, tokens, excel_system)
    if target in NUMERIC_TYPES or target == "boolean":
        return _number(base, raw_text, cell_type or "", field, options, tokens, target)
    base["status"] = "parse_failed"
    base["rule_id"] = "TYPE_UNSUPPORTED"
    base["message"] = "当前没有这种目标类型的转换规则。"
    return base


def _text(base: dict, raw: str, tokens: tuple[str, ...]) -> dict:
    if raw == "":
        base["status"] = "missing_empty"
    elif raw.strip() == "":
        base["status"] = "missing_blank"
        base["normalized_text"] = raw
        base["normalized_value"] = raw
    elif raw.strip().casefold() in {token.casefold() for token in tokens if token}:
        base["status"] = "missing_marker"
    else:
        base["status"] = "success"
        base["normalized_text"] = raw
        base["normalized_value"] = raw
    base["rule_id"] = "STRING_PRESERVE"
    if base["status"] == "success" and not base["message"]:
        base["message"] = "按原始文本保留，没有修剪空格，也没有改写标签资格。"
    return base


def _time(base: dict, raw: str, cell_type: str, number_format: str | None, field: dict, options: dict, tokens: tuple[str, ...], excel_system: str) -> dict:
    formats = tuple(field.get("datetime_formats") or options.get("datetime_formats") or [])
    parsed = parse_datetime(
        raw,
        cell_type=cell_type,
        number_format=number_format,
        formats=formats,
        missing_tokens=tokens,
        source_timezone=field.get("source_timezone") or options.get("source_timezone") or "",
        target_timezone=field.get("target_timezone") or options.get("target_timezone") or "",
        excel_system=excel_system,
    )
    base.update(parsed)
    base["normalized_value"] = parsed["normalized_text"] or None
    return base


def _number(base: dict, raw: str, cell_type: str, field: dict, options: dict, tokens: tuple[str, ...], target: str) -> dict:
    if target == "boolean":
        return _boolean(base, raw, tokens)
    source_unit = field.get("source_unit") or ""
    target_unit = field.get("target_unit") or ""
    if target_unit and not source_unit.strip():
        base["status"] = "unit_unconfirmed"
        base["rule_id"] = "UNIT_UNCONFIRMED"
        base["message"] = "来源单位未确认，不解析为已发布的标准数值，也不猜测单位。"
        return base
    decimal = field.get("decimal_style") or options.get("decimal_style") or "point"
    thousands = field.get("thousands_separator") if field.get("thousands_separator") is not None else options.get("thousands_separator") or ""
    value, status = parse_number(raw, decimal=decimal, thousands=thousands, missing_tokens=tokens, excel_number=cell_type == "number")
    if status != "success" or value is None:
        base["status"] = status
        base["rule_id"] = "NUMERIC_PARSE"
        base["message"] = _numeric_message(status)
        return base
    converted, unit_status, unit_rule = convert_unit(value, source_unit, target_unit, field.get("dimension") or "")
    base["unit_rule"] = unit_rule
    if unit_status != "success" or converted is None:
        base["status"] = unit_status
        base["rule_id"] = unit_rule
        base["message"] = "来源单位和目标单位的物理维度不兼容，没有换算。" if unit_status == "unit_incompatible" else "单位未确认，没有换算。"
        return base
    if target == "integer":
        whole = as_integer(converted)
        if whole is None:
            base["status"] = "parse_failed"
            base["rule_id"] = "NUMERIC_PARSE"
            base["message"] = "这个值不是整数。"
            return base
        base["normalized_value"] = whole
        base["normalized_text"] = str(whole)
    else:
        base["normalized_text"] = decimal_text(converted)
        base["normalized_value"] = float(converted)
    base["status"] = "success"
    base["rule_id"] = "NUMERIC_PARSE" if unit_rule in {"UNIT_IDENTITY", "UNIT_NONE"} else unit_rule
    base["message"] = "数值已解析，单位相同，没有换算。" if unit_rule == "UNIT_IDENTITY" else "已按确认的单位关系换算。" if unit_rule == "UNIT_CONVERT" else "数值已解析。"
    return base


def _boolean(base: dict, raw: str, tokens: tuple[str, ...]) -> dict:
    if raw == "":
        base["status"] = "missing_empty"
    elif raw.strip() == "":
        base["status"] = "missing_blank"
    elif raw.strip().casefold() in {token.casefold() for token in tokens if token}:
        base["status"] = "missing_marker"
    elif raw.strip().casefold() in {"1", "true"}:
        base["status"] = "success"
        base["normalized_value"] = True
        base["normalized_text"] = "true"
    elif raw.strip().casefold() in {"0", "false"}:
        base["status"] = "success"
        base["normalized_value"] = False
        base["normalized_text"] = "false"
    else:
        base["status"] = "parse_failed"
        base["message"] = "不能把这个文本当成布尔值。只接受 1、0、true、false。"
    base["rule_id"] = "BOOLEAN_PARSE"
    return base


def _numeric_message(status: str) -> str:
    if status == "numeric_ambiguous":
        return "小数点或千位分隔符不明确。请先确认数字格式，不能自动猜测。"
    if status in {"missing_empty", "missing_blank", "missing_marker"}:
        return "这个单元格是缺失，不是数值 0。"
    return "文本不能按目标数值类型解析，原值保持不变。"
