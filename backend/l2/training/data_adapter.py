"""读取 L1 开发 CSV 视图或本地 features/labels，统一为训练表。"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pandas as pd

from l2.equipment.registry import get_equipment_spec
from l2.equipment.schema import EquipmentSpec
from l2.equipment.validation import PreparedEquipmentDataset
from l2.training.schema import (
    EQUIPMENT_ID_COLUMN,
    EQUIPMENT_STATUS_COLUMN,
    FAULT_TYPE_COLUMN,
    FEATURE_COLUMNS,
    RECORD_ID_COLUMN,
    TIMESTAMP_COLUMN,
    assert_no_leakage,
)


@dataclass(frozen=True)
class TrainingSnapshot:
    frame: pd.DataFrame
    source_kind: str
    features_path: str
    labels_path: str
    dataset_id: str
    dataset_revision_id: str
    source_data_hash: str
    warnings: tuple[str, ...]
    equipment_type: str = "main_mill_motor"
    schema_version: str = ""


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _read_csv(path: Path) -> pd.DataFrame:
    return pd.read_csv(path, encoding="utf-8-sig")


def load_csv_snapshot(
    features_path: str | Path,
    labels_path: str | Path,
    *,
    dataset_id: str = "csv_dev",
    dataset_revision_id: str = "rev1",
    equipment_spec: EquipmentSpec | None = None,
    equipment_type: str | None = None,
) -> TrainingSnapshot:
    features_file = Path(features_path)
    labels_file = Path(labels_path)
    if not features_file.is_file():
        raise FileNotFoundError(f"找不到特征文件：{features_file}")
    if not labels_file.is_file():
        raise FileNotFoundError(f"找不到标签文件：{labels_file}")

    spec = equipment_spec
    if spec is None and equipment_type:
        spec = get_equipment_spec(equipment_type)
    if spec is None:
        # 兼容旧调用：默认主轧机电机
        feature_columns = FEATURE_COLUMNS
        record_id = RECORD_ID_COLUMN
        fault_col = FAULT_TYPE_COLUMN
        equipment_type_value = "main_mill_motor"
        schema_version = ""
        assert_no_leakage(feature_columns)
    else:
        feature_columns = spec.feature_names
        record_id = spec.record_id_field
        fault_col = spec.target_field
        equipment_type_value = spec.equipment_type
        schema_version = spec.schema_version
        spec.assert_no_leakage(feature_columns)

    features = _read_csv(features_file)
    labels = _read_csv(labels_file)
    if record_id not in features.columns:
        raise ValueError(f"特征表缺少 {record_id}")
    if record_id not in labels.columns:
        raise ValueError(f"标签表缺少 {record_id}")
    if fault_col not in labels.columns:
        raise ValueError(f"标签表缺少 {fault_col}")

    missing_features = [name for name in feature_columns if name not in features.columns]
    if missing_features:
        raise ValueError(f"特征表缺少必要列：{missing_features}")

    feature_only = features[[record_id, *feature_columns]].copy()

    label_keep = [record_id, fault_col]
    for optional in (
        EQUIPMENT_ID_COLUMN if spec is None else spec.equipment_id_field,
        TIMESTAMP_COLUMN if spec is None else spec.timestamp_field,
        EQUIPMENT_STATUS_COLUMN if spec is None else spec.status_field,
    ):
        if optional in labels.columns and optional not in label_keep:
            label_keep.append(optional)
    label_view = labels[label_keep].copy()

    if feature_only[record_id].duplicated().any():
        dup = feature_only.loc[feature_only[record_id].duplicated(), record_id].tolist()[:10]
        raise ValueError(f"特征表存在重复记录编号：{dup}")
    if label_view[record_id].duplicated().any():
        dup = label_view.loc[label_view[record_id].duplicated(), record_id].tolist()[:10]
        raise ValueError(f"标签表存在重复记录编号：{dup}")

    merged = feature_only.merge(label_view, on=record_id, how="inner", validate="one_to_one")
    only_features = set(feature_only[record_id]) - set(label_view[record_id])
    only_labels = set(label_view[record_id]) - set(feature_only[record_id])
    if only_features or only_labels:
        raise ValueError(
            "特征与标签记录编号无法一一对应："
            f"仅特征={len(only_features)} 仅标签={len(only_labels)}"
        )

    source_hash = hashlib.sha256(
        (_sha256_file(features_file) + ":" + _sha256_file(labels_file)).encode("utf-8")
    ).hexdigest()

    return TrainingSnapshot(
        frame=merged.reset_index(drop=True),
        source_kind="csv_pair",
        features_path=str(features_file.resolve()),
        labels_path=str(labels_file.resolve()),
        dataset_id=dataset_id,
        dataset_revision_id=dataset_revision_id,
        source_data_hash=source_hash,
        warnings=tuple(),
        equipment_type=equipment_type_value,
        schema_version=schema_version,
    )


def snapshot_to_prepared(snapshot: TrainingSnapshot, spec: EquipmentSpec) -> PreparedEquipmentDataset:
    features = snapshot.frame[[spec.record_id_field, *spec.feature_names]].copy()
    label_cols = [spec.record_id_field, spec.target_field]
    for optional in (spec.equipment_id_field, spec.timestamp_field, spec.status_field):
        if optional in snapshot.frame.columns:
            label_cols.append(optional)
    labels = snapshot.frame[label_cols].copy()
    return PreparedEquipmentDataset(
        dataset_id=snapshot.dataset_id,
        dataset_revision_id=snapshot.dataset_revision_id,
        equipment_type=spec.equipment_type,
        schema_version=spec.schema_version,
        feature_schema=spec.feature_schema_dict(),
        features=features,
        labels=labels,
        record_ids=tuple(features[spec.record_id_field].astype(str).tolist()),
        metadata=snapshot_meta(snapshot),
    )


def snapshot_meta(snapshot: TrainingSnapshot) -> dict[str, Any]:
    return {
        "source_kind": snapshot.source_kind,
        "features_path": snapshot.features_path,
        "labels_path": snapshot.labels_path,
        "dataset_id": snapshot.dataset_id,
        "dataset_revision_id": snapshot.dataset_revision_id,
        "source_data_hash": snapshot.source_data_hash,
        "record_count": int(len(snapshot.frame)),
        "warnings": list(snapshot.warnings),
        "equipment_type": snapshot.equipment_type,
        "schema_version": snapshot.schema_version,
    }


def write_meta(path: Path, snapshot: TrainingSnapshot) -> None:
    path.write_text(json.dumps(snapshot_meta(snapshot), ensure_ascii=False, indent=2), encoding="utf-8")
