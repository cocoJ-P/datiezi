"""EquipmentSpec：设备类别配置契约。"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Sequence


@dataclass(frozen=True)
class FeatureDef:
    name: str
    dtype: str = "float"
    unit: str | None = None
    required: bool = True
    feature_id: str | None = None


@dataclass(frozen=True)
class EquipmentSpec:
    equipment_type: str
    display_name: str
    schema_version: str
    record_id_field: str
    normal_label: str
    abnormal_label: str
    target_field: str
    features: tuple[FeatureDef, ...]
    class_labels: tuple[str, ...]
    binary_labels: tuple[str, ...]
    training_config: str
    equipment_id_field: str = "设备编号"
    timestamp_field: str = "采集时间"
    status_field: str = "设备状态"
    leakage_columns: tuple[str, ...] = ()

    @property
    def feature_names(self) -> tuple[str, ...]:
        return tuple(item.name for item in self.features)

    @property
    def feature_ids(self) -> tuple[str, ...]:
        return tuple(item.feature_id or item.name for item in self.features)

    @property
    def feature_count(self) -> int:
        return len(self.features)

    @property
    def class_count(self) -> int:
        return len(self.class_labels)

    def feature_schema_dict(self) -> dict[str, Any]:
        return {
            "version": self.schema_version,
            "equipment_type": self.equipment_type,
            "feature_columns": list(self.feature_names),
            "feature_ids": list(self.feature_ids),
            "feature_defs": [
                {
                    "name": item.name,
                    "dtype": item.dtype,
                    "unit": item.unit,
                    "required": item.required,
                    "feature_id": item.feature_id,
                }
                for item in self.features
            ],
            "multiclass_labels": list(self.class_labels),
            "binary_labels": list(self.binary_labels),
            "normal_label": self.normal_label,
            "leakage_columns": list(self.leakage_columns),
            "record_id_column": self.record_id_field,
            "fault_type_column": self.target_field,
        }

    def resolve_training_config(self, project_root: Path) -> Path:
        path = Path(self.training_config)
        if not path.is_absolute():
            path = (project_root / path).resolve()
        return path

    def fault_to_binary(self, label: str) -> str:
        return self.normal_label if str(label) == self.normal_label else self.abnormal_label

    def multiclass_index(self, label: str) -> int:
        try:
            return self.class_labels.index(str(label))
        except ValueError as exc:
            raise ValueError(f"非法故障类型：{label}") from exc

    def binary_index(self, label: str) -> int:
        try:
            return self.binary_labels.index(str(label))
        except ValueError as exc:
            raise ValueError(f"非法二分类标签：{label}") from exc

    def assert_no_leakage(self, columns: Sequence[str]) -> None:
        blocked = set(self.leakage_columns) | {
            self.record_id_field,
            self.equipment_id_field,
            self.timestamp_field,
            self.status_field,
            self.target_field,
        }
        overlap = [name for name in columns if name in blocked]
        if overlap:
            raise ValueError(f"特征列包含泄漏字段：{overlap}")

    def to_summary(self) -> dict[str, Any]:
        return {
            "equipment_type": self.equipment_type,
            "display_name": self.display_name,
            "schema_version": self.schema_version,
            "feature_count": self.feature_count,
            "class_count": self.class_count,
            "class_labels": list(self.class_labels),
            "normal_label": self.normal_label,
            "binary_labels": list(self.binary_labels),
            "feature_columns": list(self.feature_names),
            "training_config": self.training_config,
        }
