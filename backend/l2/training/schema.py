"""训练特征、标签与泄漏字段约束（兼容旧常量 + EquipmentSpec）。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

from l2.equipment.registry import get_equipment_spec
from l2.equipment.schema import EquipmentSpec

# ---- 兼容常量：主轧机电机默认 Schema（旧测试与旧模型包依赖）----
_DEFAULT = get_equipment_spec("main_mill_motor")

FEATURE_COLUMNS: tuple[str, ...] = _DEFAULT.feature_names
FEATURE_IDS: tuple[str, ...] = _DEFAULT.feature_ids
RECORD_ID_COLUMN = _DEFAULT.record_id_field
EQUIPMENT_ID_COLUMN = _DEFAULT.equipment_id_field
TIMESTAMP_COLUMN = _DEFAULT.timestamp_field
EQUIPMENT_STATUS_COLUMN = _DEFAULT.status_field
FAULT_TYPE_COLUMN = _DEFAULT.target_field
MULTICLASS_LABELS: tuple[str, ...] = _DEFAULT.class_labels
BINARY_LABELS: tuple[str, ...] = _DEFAULT.binary_labels
LEAKAGE_COLUMNS: frozenset[str] = frozenset(_DEFAULT.leakage_columns)
FEATURE_SCHEMA_VERSION = _DEFAULT.schema_version


@dataclass(frozen=True)
class FeatureSchema:
    version: str
    feature_columns: tuple[str, ...]
    feature_ids: tuple[str, ...]
    multiclass_labels: tuple[str, ...]
    binary_labels: tuple[str, ...]
    leakage_columns: tuple[str, ...]
    equipment_type: str = "main_mill_motor"
    normal_label: str = "正常"

    def to_dict(self) -> dict:
        return {
            "version": self.version,
            "equipment_type": self.equipment_type,
            "feature_columns": list(self.feature_columns),
            "feature_ids": list(self.feature_ids),
            "multiclass_labels": list(self.multiclass_labels),
            "binary_labels": list(self.binary_labels),
            "normal_label": self.normal_label,
            "leakage_columns": list(self.leakage_columns),
            "record_id_column": RECORD_ID_COLUMN,
            "fault_type_column": FAULT_TYPE_COLUMN,
        }


def feature_schema_from_spec(spec: EquipmentSpec) -> FeatureSchema:
    return FeatureSchema(
        version=spec.schema_version,
        feature_columns=spec.feature_names,
        feature_ids=spec.feature_ids,
        multiclass_labels=spec.class_labels,
        binary_labels=spec.binary_labels,
        leakage_columns=tuple(sorted(set(spec.leakage_columns))),
        equipment_type=spec.equipment_type,
        normal_label=spec.normal_label,
    )


def default_feature_schema() -> FeatureSchema:
    return feature_schema_from_spec(_DEFAULT)


def assert_no_leakage(columns: Sequence[str], spec: EquipmentSpec | None = None) -> None:
    target = spec or _DEFAULT
    target.assert_no_leakage(columns)


def fault_to_binary(label: str, *, normal_label: str = "正常") -> str:
    return "正常" if str(label) == normal_label else "非正常"


def status_to_binary(status: str) -> str:
    """设备状态映射为正常/非正常。仅「正常」为正常，预警/报警/危险等均为非正常。"""
    return "正常" if str(status) == "正常" else "非正常"


def multiclass_index(label: str, labels: Sequence[str] | None = None) -> int:
    names = tuple(labels) if labels is not None else MULTICLASS_LABELS
    try:
        return names.index(label)
    except ValueError as exc:
        raise ValueError(f"非法故障类型：{label}") from exc


def binary_index(label: str, labels: Sequence[str] | None = None) -> int:
    names = tuple(labels) if labels is not None else BINARY_LABELS
    try:
        return names.index(label)
    except ValueError as exc:
        raise ValueError(f"非法二分类标签：{label}") from exc
