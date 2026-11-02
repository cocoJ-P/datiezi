"""设备类型注册表（YAML 配置驱动）。"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from l2.equipment.schema import EquipmentSpec, FeatureDef

CONFIG_DIR = Path(__file__).resolve().parent / "config"
_CACHE: dict[str, EquipmentSpec] | None = None


def _parse_spec(payload: dict[str, Any]) -> EquipmentSpec:
    features = tuple(
        FeatureDef(
            name=str(item["name"]),
            dtype=str(item.get("dtype") or "float"),
            unit=None if item.get("unit") in (None, "null") else str(item.get("unit")),
            required=bool(item.get("required", True)),
            feature_id=None if item.get("feature_id") in (None, "") else str(item.get("feature_id")),
        )
        for item in (payload.get("features") or [])
    )
    class_labels = tuple(str(x) for x in (payload.get("class_labels") or []))
    binary_labels = tuple(str(x) for x in (payload.get("binary_labels") or ["正常", "非正常"]))
    normal_label = str(payload.get("normal_label") or "正常")
    abnormal_label = str(payload.get("abnormal_label") or "非正常")
    if not features:
        raise ValueError("EquipmentSpec 缺少 features。")
    if not class_labels:
        raise ValueError("EquipmentSpec 缺少 class_labels。")
    if normal_label not in class_labels:
        raise ValueError(f"normal_label={normal_label} 必须出现在 class_labels 中。")
    if normal_label not in binary_labels or abnormal_label not in binary_labels:
        raise ValueError("binary_labels 必须包含 normal_label 与 abnormal_label。")
    if len(class_labels) < 2:
        raise ValueError("至少需要正常 + 一种故障类别。")
    return EquipmentSpec(
        equipment_type=str(payload["equipment_type"]),
        display_name=str(payload.get("display_name") or payload["equipment_type"]),
        schema_version=str(payload["schema_version"]),
        record_id_field=str(payload.get("record_id_field") or "记录编号"),
        normal_label=normal_label,
        abnormal_label=abnormal_label,
        target_field=str(payload.get("target_field") or "故障类型"),
        features=features,
        class_labels=class_labels,
        binary_labels=binary_labels,
        training_config=str(payload["training_config"]),
        equipment_id_field=str(payload.get("equipment_id_field") or "设备编号"),
        timestamp_field=str(payload.get("timestamp_field") or "采集时间"),
        status_field=str(payload.get("status_field") or "设备状态"),
        leakage_columns=tuple(str(x) for x in (payload.get("leakage_columns") or [])),
    )


def load_all_specs(*, refresh: bool = False) -> dict[str, EquipmentSpec]:
    global _CACHE
    if _CACHE is not None and not refresh:
        return dict(_CACHE)
    specs: dict[str, EquipmentSpec] = {}
    if CONFIG_DIR.is_dir():
        for path in sorted(CONFIG_DIR.glob("*.yaml")):
            payload = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
            if not isinstance(payload, dict):
                raise ValueError(f"设备配置必须是对象：{path}")
            spec = _parse_spec(payload)
            if spec.equipment_type in specs:
                raise ValueError(f"重复设备类型：{spec.equipment_type}")
            specs[spec.equipment_type] = spec
    _CACHE = specs
    return dict(specs)


def list_equipment_types(*, refresh: bool = False) -> list[EquipmentSpec]:
    return list(load_all_specs(refresh=refresh).values())


def get_equipment_spec(equipment_type: str, *, refresh: bool = False) -> EquipmentSpec:
    specs = load_all_specs(refresh=refresh)
    if equipment_type not in specs:
        raise KeyError(equipment_type)
    return specs[equipment_type]


def has_equipment(equipment_type: str) -> bool:
    return equipment_type in load_all_specs()
