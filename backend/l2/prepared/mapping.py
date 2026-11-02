"""L1 设备分组 / 数据集 → L2 EquipmentSpec 映射。"""

from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from l2.equipment.registry import get_equipment_spec, has_equipment, list_equipment_types
from l2.equipment.schema import EquipmentSpec

_CONFIG = Path(__file__).resolve().parent / "config" / "equipment_mappings.yaml"


@dataclass(frozen=True)
class EquipmentMapping:
    equipment_type: str
    schema_version: str
    mapping_status: str
    mapping_source: str
    equipment_group_id: str | None = None
    display_name: str | None = None
    needs_confirmation: bool = False
    candidate_spec: dict[str, Any] | None = None


def _load_config() -> dict[str, Any]:
    if not _CONFIG.is_file():
        return {}
    return yaml.safe_load(_CONFIG.read_text(encoding="utf-8")) or {}


def _load_rules() -> list[dict[str, Any]]:
    return list(_load_config().get("mappings") or [])


def load_feature_bridges(equipment_type: str) -> list[dict[str, Any]]:
    bridges = _load_config().get("feature_bridges") or {}
    return list(bridges.get(equipment_type) or [])


def resolve_equipment_type(
    *,
    dataset_id: str,
    original_filename: str | None = None,
    equipment_category: str | None = None,
    sheet_name: str | None = None,
    equipment_group_id: str | None = None,
) -> EquipmentMapping:
    rules = _load_rules()
    filename = (original_filename or "").lower()
    category = (equipment_category or "").strip()
    sheet = (sheet_name or "").strip()

    for rule in rules:
        match = rule.get("match") or {}
        ok = True
        if "filename_contains" in match:
            ok = ok and str(match["filename_contains"]).lower() in filename
        if "category_equals" in match:
            ok = ok and category == str(match["category_equals"])
        if "sheet_contains" in match:
            ok = ok and str(match["sheet_contains"]) in sheet
        if "dataset_id" in match:
            ok = ok and dataset_id == str(match["dataset_id"])
        if not ok:
            continue
        equipment_type = str(rule["equipment_type"])
        if not has_equipment(equipment_type):
            return EquipmentMapping(
                equipment_type=equipment_type,
                schema_version="",
                mapping_status="needs_review",
                mapping_source="mapping_rule_unregistered",
                equipment_group_id=equipment_group_id,
                needs_confirmation=True,
            )
        spec = get_equipment_spec(equipment_type)
        return EquipmentMapping(
            equipment_type=spec.equipment_type,
            schema_version=spec.schema_version,
            mapping_status="confirmed",
            mapping_source="mapping_rule",
            equipment_group_id=equipment_group_id,
            display_name=spec.display_name,
        )

    # 回退：类别名命中已注册 display_name / equipment_type
    for spec in list_equipment_types():
        if category and category in {spec.display_name, spec.equipment_type}:
            return EquipmentMapping(
                equipment_type=spec.equipment_type,
                schema_version=spec.schema_version,
                mapping_status="confirmed",
                mapping_source="display_name_match",
                equipment_group_id=equipment_group_id,
                display_name=spec.display_name,
            )

    return EquipmentMapping(
        equipment_type="",
        schema_version="",
        mapping_status="needs_review",
        mapping_source="unmatched",
        equipment_group_id=equipment_group_id,
        needs_confirmation=True,
        candidate_spec={
            "dataset_id": dataset_id,
            "original_filename": original_filename,
            "equipment_category": equipment_category,
            "sheet_name": sheet_name,
            "hint": "请确认 EquipmentSpec 后写入 equipment_mappings.yaml",
        },
    )


def feature_id_to_name(spec: EquipmentSpec) -> dict[str, str]:
    return {item.feature_id or item.name: item.name for item in spec.features}


def _scalar_from_cell(cell: Any) -> float | None:
    if isinstance(cell, (int, float)) and not isinstance(cell, bool):
        number = float(cell)
        if number != number or number in {float("inf"), float("-inf")}:
            return None
        return number
    if not isinstance(cell, dict):
        return None
    if cell.get("status") not in {None, "success"} and cell.get("value") is None:
        return None
    value = cell.get("value")
    if value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number != number or number in {float("inf"), float("-inf")}:
        return None
    return number


def _apply_bridge(
    *,
    bridge: dict[str, Any],
    input_features: dict[str, Any],
    out: dict[str, float],
    spec: EquipmentSpec,
) -> str | None:
    """应用已确认桥接；成功返回 None，失败返回目标特征中文名。"""
    if not bridge.get("confirmed"):
        return None
    target_id = str(bridge.get("target_feature_id") or "")
    name_map = feature_id_to_name(spec)
    target_name = name_map.get(target_id)
    if not target_name or target_name in out:
        return None
    method = str(bridge.get("method") or "")
    sources = [str(x) for x in (bridge.get("source_feature_ids") or [])]
    if method == "rms_from_axes" and len(sources) >= 2:
        values = [_scalar_from_cell(input_features.get(src)) for src in sources]
        if any(v is None for v in values):
            return target_name
        assert values[0] is not None and values[1] is not None
        out[target_name] = float(math.sqrt((values[0] ** 2 + values[1] ** 2) / 2.0))
        return None
    if method == "alias" and sources:
        value = _scalar_from_cell(input_features.get(sources[0]))
        if value is None:
            return target_name
        out[target_name] = value
        return None
    return target_name


def map_l1_features_to_spec(
    input_features: dict[str, Any],
    spec: EquipmentSpec,
) -> tuple[dict[str, float], list[str], list[str]]:
    """将 L1 standard_field_id 特征映射为 EquipmentSpec 中文列。

    返回：(mapped, missing_names, applied_bridge_notes)
    """
    out: dict[str, float] = {}
    missing: list[str] = []
    for feature in spec.features:
        field_id = feature.feature_id or feature.name
        number = _scalar_from_cell(input_features.get(field_id))
        if number is None:
            number = _scalar_from_cell(input_features.get(feature.name))
        if number is None:
            missing.append(feature.name)
            continue
        out[feature.name] = number

    applied_notes: list[str] = []
    still_missing: list[str] = []
    bridges = load_feature_bridges(spec.equipment_type)
    bridged_targets = set()
    for bridge in bridges:
        target_id = str(bridge.get("target_feature_id") or "")
        name_map = feature_id_to_name(spec)
        target_name = name_map.get(target_id)
        if target_name and target_name in missing:
            failed = _apply_bridge(bridge=bridge, input_features=input_features, out=out, spec=spec)
            if failed is None and target_name in out:
                bridged_targets.add(target_name)
                applied_notes.append(str(bridge.get("note") or f"bridged:{target_id}"))
            elif failed:
                still_missing.append(failed)
    for name in missing:
        if name not in out and name not in still_missing:
            still_missing.append(name)
    return out, still_missing, applied_notes
