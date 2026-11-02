"""EquipmentSpec 与 PreparedEquipmentDataset 校验。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Sequence

import pandas as pd

from l2.equipment.schema import EquipmentSpec


@dataclass(frozen=True)
class PreparedEquipmentDataset:
    dataset_id: str
    dataset_revision_id: str
    equipment_type: str
    schema_version: str
    feature_schema: dict[str, Any]
    features: pd.DataFrame
    labels: pd.DataFrame
    record_ids: tuple[str, ...]
    metadata: dict[str, Any]


def validate_equipment_spec(spec: EquipmentSpec) -> None:
    if not spec.equipment_type.strip():
        raise ValueError("equipment_type 不能为空。")
    if not spec.schema_version.strip():
        raise ValueError("schema_version 不能为空。")
    names = [item.name for item in spec.features]
    if len(names) != len(set(names)):
        raise ValueError("特征名称存在重复。")
    if spec.normal_label not in spec.class_labels:
        raise ValueError("normal_label 必须属于 class_labels。")
    if len(spec.class_labels) < 2:
        raise ValueError("class_labels 至少包含正常与一种故障。")
    spec.assert_no_leakage(spec.feature_names)


def validate_prepared_dataset(dataset: PreparedEquipmentDataset, spec: EquipmentSpec) -> None:
    if dataset.equipment_type != spec.equipment_type:
        raise ValueError(
            f"数据集设备类型 {dataset.equipment_type} 与配置 {spec.equipment_type} 不一致。"
        )
    if dataset.schema_version != spec.schema_version:
        raise ValueError(
            f"数据集 Schema {dataset.schema_version} 与配置 {spec.schema_version} 不一致。"
        )
    missing = [name for name in spec.feature_names if name not in dataset.features.columns]
    if missing:
        raise ValueError(f"PreparedEquipmentDataset 缺少特征：{missing}")
    if spec.record_id_field not in dataset.features.columns:
        raise ValueError(f"特征表缺少 {spec.record_id_field}")
    if spec.target_field not in dataset.labels.columns:
        raise ValueError(f"标签表缺少 {spec.target_field}")
    if list(dataset.record_ids) != list(dataset.features[spec.record_id_field].astype(str)):
        raise ValueError("record_ids 必须与特征表记录编号顺序一致。")
