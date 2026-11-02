"""确定性字段匹配。相似度只作为低优先级建议，不能压过别名和单位约束。"""

from __future__ import annotations

import re
from difflib import SequenceMatcher

from app.services.l1_schema.definitions import BARE_AMBIGUOUS, FIELD_BY_ID, FIELDS, StandardField

_UNIT = re.compile(r"\(([^()]*)\)\s*$")
_UNIT_DIMENSION = {
    "rpm": "rotational_speed",
    "hz": "frequency",
    "a": "current",
    "mm/s": "velocity",
    "g": "acceleration",
    "°c": "temperature",
    "℃": "temperature",
    "mm": "displacement",
    "um": "displacement",
    "μm": "displacement",
}


def normalize_name(value: str) -> str:
    text = value.strip().replace("（", "(").replace("）", ")").replace("℃", "°C")
    return "".join(text.split()).casefold()


def strip_unit(value: str) -> str:
    return _UNIT.sub("", normalize_name(value))


def parse_unit(value: str) -> str:
    match = _UNIT.search(value.strip().replace("（", "(").replace("）", ")"))
    if not match:
        return ""
    return match.group(1).strip()


def unit_dimension(unit: str) -> str:
    key = "".join(unit.strip().split()).casefold().replace("℃", "°c")
    return _UNIT_DIMENSION.get(key, "")


def match_header(header: str) -> dict:
    norm = normalize_name(header)
    bare = strip_unit(norm)
    unit = parse_unit(header)
    source_dimension = unit_dimension(unit)
    if bare in BARE_AMBIGUOUS:
        choices = [FIELD_BY_ID[item] for item in BARE_AMBIGUOUS[bare]]
        return _ambiguous(header, unit, choices, "只有特征代号，没有标明幅值或频率。")
    ranked: list[tuple[int, str, StandardField, str, float]] = []
    for field in FIELDS:
        if _rejected(norm, field, source_dimension):
            continue
        exact = _alias_hit(norm, bare, field)
        if exact is not None:
            ranked.append(exact)
            continue
        score = _similarity(bare, field)
        if score >= 0.9:
            ranked.append((3, "similarity", field, f"与「{field.label}」的相似度是 {score:.2f}。", score))
    if not ranked:
        return _unmapped(header, unit, "没有达到别名或相似度门槛。振动位移、速度和加速度不会自动当成同一个量。")
    best = min(item[0] for item in ranked)
    top = [item for item in ranked if item[0] == best]
    chosen_fields = {}
    for item in top:
        chosen_fields.setdefault(item[2].field_id, item)
    if len(chosen_fields) > 1:
        return _ambiguous(header, unit, [item[2] for item in chosen_fields.values()], "多个标准字段的匹配优先级相同。")
    _rank, method, field, reason, _score = next(iter(chosen_fields.values()))
    review = _needs_review(method, field, unit, source_dimension)
    return {
        "header": header,
        "standard_field_id": field.field_id,
        "match_method": method,
        "match_reason": reason,
        "confirmation_status": "needs_confirmation" if review else "suggested",
        "role": field.role,
        "eligibility": field.eligibility,
        "source_unit": unit,
        "target_unit": field.target_unit,
        "target_type": field.target_type,
        "candidates": [field.field_id],
    }


def _alias_hit(norm: str, bare: str, field: StandardField) -> tuple[int, str, StandardField, str, float] | None:
    label = normalize_name(field.label)
    field_id = normalize_name(field.field_id)
    for alias in field.aliases:
        alias_norm = normalize_name(alias)
        if norm == alias_norm or norm == field_id:
            method = "exact_match" if norm in {label, field_id} else "alias_match"
            return (1, method, field, f"列名与「{alias}」一致。", 1.0)
    for alias in field.aliases:
        alias_norm = normalize_name(alias)
        if bare and bare == strip_unit(alias_norm) and bare != norm:
            return (2, "normalized_match", field, f"去掉单位后与「{alias}」一致。", 0.95)
    return None


def _similarity(bare: str, field: StandardField) -> float:
    if not bare:
        return 0.0
    values = [strip_unit(normalize_name(field.label)), *[strip_unit(normalize_name(item)) for item in field.aliases]]
    return max(SequenceMatcher(None, bare, item).ratio() for item in values if item)


def _rejected(norm: str, field: StandardField, source_dimension: str) -> bool:
    if source_dimension and field.dimension not in {"", "text", "identity", "category", "time"} and source_dimension != field.dimension:
        return True
    if "加速度" in norm and field.dimension == "velocity":
        return True
    if "位移" in norm and field.dimension == "velocity":
        return True
    if "速度" in norm and field.dimension == "acceleration":
        return True
    if "幅值" in norm and field.quantity == "frequency":
        return True
    if "频率" in norm and "幅值" not in norm and field.quantity == "amplitude":
        return True
    return False


def _needs_review(method: str, field: StandardField, unit: str, source_dimension: str) -> bool:
    if field.uncertain or method in {"similarity", "normalized_match"}:
        return True
    if field.unit_sensitive and field.dimension not in {"", "text"} and not source_dimension:
        return True
    if unit and source_dimension and field.dimension not in {"", "text"} and source_dimension != field.dimension:
        return True
    return False


def _ambiguous(header: str, unit: str, fields: list[StandardField], reason: str) -> dict:
    return {
        "header": header,
        "standard_field_id": "",
        "match_method": "ambiguous",
        "match_reason": reason,
        "confirmation_status": "needs_confirmation",
        "role": "unmapped",
        "eligibility": "excluded",
        "source_unit": unit,
        "target_unit": "",
        "target_type": "",
        "candidates": [item.field_id for item in fields],
    }


def _unmapped(header: str, unit: str, reason: str) -> dict:
    return {
        "header": header,
        "standard_field_id": "",
        "match_method": "unmapped",
        "match_reason": reason,
        "confirmation_status": "suggested",
        "role": "unmapped",
        "eligibility": "excluded",
        "source_unit": unit,
        "target_unit": "",
        "target_type": "",
        "candidates": [],
    }
