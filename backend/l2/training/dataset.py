"""训练前检查、分层划分与划分记录。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np
import pandas as pd
from sklearn.model_selection import StratifiedShuffleSplit

from l2.equipment.schema import EquipmentSpec
from l2.training.schema import (
    BINARY_LABELS,
    EQUIPMENT_ID_COLUMN,
    EQUIPMENT_STATUS_COLUMN,
    FAULT_TYPE_COLUMN,
    FEATURE_COLUMNS,
    MULTICLASS_LABELS,
    RECORD_ID_COLUMN,
    fault_to_binary,
    multiclass_index,
    status_to_binary,
)


@dataclass
class StatusFaultAudit:
    """设备状态 vs 故障类型：区分原始字面不同与正常/非正常语义冲突。"""

    both_normal: int
    both_abnormal: int
    status_abnormal_fault_normal: int
    status_normal_fault_abnormal: int
    semantic_conflict_count: int
    raw_value_different_count: int

    def to_dict(self) -> dict[str, int]:
        return {
            "both_normal": self.both_normal,
            "both_abnormal": self.both_abnormal,
            "status_abnormal_fault_normal": self.status_abnormal_fault_normal,
            "status_normal_fault_abnormal": self.status_normal_fault_abnormal,
            "semantic_conflict_count": self.semantic_conflict_count,
            "raw_value_different_count": self.raw_value_different_count,
        }


@dataclass
class DataAudit:
    record_count: int
    class_counts: dict[str, int]
    binary_counts: dict[str, int]
    warnings: list[str]
    status_mismatch_count: int
    status_fault_audit: StatusFaultAudit | None = None


@dataclass
class SplitBundle:
    train_ids: list[str]
    calib_ids: list[str]
    test_ids: list[str]
    x_train: np.ndarray
    y_multi_train: np.ndarray
    y_bin_train: np.ndarray
    x_calib: np.ndarray
    y_multi_calib: np.ndarray
    y_bin_calib: np.ndarray
    x_test: np.ndarray
    y_multi_test: np.ndarray
    y_bin_test: np.ndarray
    equipment_train: list[str]
    equipment_calib: list[str]
    equipment_test: list[str]
    random_seed: int
    ratios: dict[str, float]


def audit_training_frame(frame: pd.DataFrame, spec: EquipmentSpec | None = None) -> DataAudit:
    warnings: list[str] = []
    feature_columns = FEATURE_COLUMNS if spec is None else spec.feature_names
    multiclass_labels = MULTICLASS_LABELS if spec is None else spec.class_labels
    binary_labels = BINARY_LABELS if spec is None else spec.binary_labels
    record_id = RECORD_ID_COLUMN if spec is None else spec.record_id_field
    fault_col = FAULT_TYPE_COLUMN if spec is None else spec.target_field
    status_col = EQUIPMENT_STATUS_COLUMN if spec is None else spec.status_field
    normal_label = "正常" if spec is None else spec.normal_label

    if frame.empty:
        raise ValueError("训练数据为空。")
    if frame[record_id].duplicated().any():
        raise ValueError("存在重复记录编号。")

    for column in feature_columns:
        series = pd.to_numeric(frame[column], errors="coerce")
        if series.isna().any():
            bad = int(series.isna().sum())
            raise ValueError(f"特征 {column} 存在 {bad} 个缺失或非数值。")
        values = series.to_numpy(dtype=float)
        if not np.isfinite(values).all():
            raise ValueError(f"特征 {column} 存在非有限值。")
        if float(np.nanstd(values)) == 0.0:
            warnings.append(f"特征 {column} 为常数列。")

    labels = frame[fault_col].astype(str)
    illegal = sorted(set(labels) - set(multiclass_labels))
    if illegal:
        raise ValueError(f"存在非法故障类型：{illegal}")

    class_counts = {name: int((labels == name).sum()) for name in multiclass_labels}
    if any(count == 0 for count in class_counts.values()):
        missing = [name for name, count in class_counts.items() if count == 0]
        raise ValueError(f"以下故障类别样本数为 0，无法分层划分：{missing}")

    binary = labels.map(lambda v: fault_to_binary(v, normal_label=normal_label))
    # 兼容：fault_to_binary 返回固定「正常/非正常」文案；映射到 binary_labels
    if set(binary_labels) != {"正常", "非正常"}:
        binary = binary.map(
            lambda v: binary_labels[0] if v == "正常" else binary_labels[1]
        )
    binary_counts = {name: int((binary == name).sum()) for name in binary_labels}
    if any(count == 0 for count in binary_counts.values()):
        raise ValueError(f"二分类样本不足：{binary_counts}")

    status_fault_audit: StatusFaultAudit | None = None
    mismatch = 0
    if status_col in frame.columns:
        status_fault_audit = compare_status_and_fault(
            frame[status_col].astype(str),
            labels,
            normal_label=normal_label,
        )
        mismatch = status_fault_audit.semantic_conflict_count
        if status_fault_audit.raw_value_different_count:
            warnings.append(
                f"设备状态与故障类型原始字段值不同：{status_fault_audit.raw_value_different_count}"
            )
        if mismatch:
            warnings.append(
                "设备状态与故障类型正常/非正常语义冲突："
                f"{mismatch}"
                f"（状态非正常&故障正常={status_fault_audit.status_abnormal_fault_normal}，"
                f"状态正常&故障非正常={status_fault_audit.status_normal_fault_abnormal}）"
            )

    for name, count in class_counts.items():
        if count < 10:
            warnings.append(f"类别 {name} 样本较少：{count}")

    return DataAudit(
        record_count=int(len(frame)),
        class_counts=class_counts,
        binary_counts=binary_counts,
        warnings=warnings,
        status_mismatch_count=mismatch,
        status_fault_audit=status_fault_audit,
    )


def compare_status_and_fault(
    status: pd.Series,
    fault_type: pd.Series,
    *,
    normal_label: str = "正常",
) -> StatusFaultAudit:
    """将设备状态、故障类型分别映射为正常/非正常后再比较。"""
    status_bin = status.astype(str).map(status_to_binary)
    fault_bin = fault_type.astype(str).map(lambda v: fault_to_binary(v, normal_label=normal_label))
    both_normal = int(((status_bin == "正常") & (fault_bin == "正常")).sum())
    both_abnormal = int(((status_bin == "非正常") & (fault_bin == "非正常")).sum())
    status_abnormal_fault_normal = int(((status_bin == "非正常") & (fault_bin == "正常")).sum())
    status_normal_fault_abnormal = int(((status_bin == "正常") & (fault_bin == "非正常")).sum())
    semantic_conflict = status_abnormal_fault_normal + status_normal_fault_abnormal
    raw_different = int((status.astype(str) != fault_type.astype(str)).sum())
    return StatusFaultAudit(
        both_normal=both_normal,
        both_abnormal=both_abnormal,
        status_abnormal_fault_normal=status_abnormal_fault_normal,
        status_normal_fault_abnormal=status_normal_fault_abnormal,
        semantic_conflict_count=semantic_conflict,
        raw_value_different_count=raw_different,
    )


def _matrix(
    frame: pd.DataFrame,
    spec: EquipmentSpec | None = None,
) -> tuple[np.ndarray, np.ndarray, np.ndarray, list[str], list[str]]:
    feature_columns = FEATURE_COLUMNS if spec is None else spec.feature_names
    multiclass_labels = MULTICLASS_LABELS if spec is None else spec.class_labels
    fault_col = FAULT_TYPE_COLUMN if spec is None else spec.target_field
    record_id = RECORD_ID_COLUMN if spec is None else spec.record_id_field
    equipment_id = EQUIPMENT_ID_COLUMN if spec is None else spec.equipment_id_field
    normal_label = "正常" if spec is None else spec.normal_label
    normal_idx = multiclass_labels.index(normal_label)

    x = frame[list(feature_columns)].apply(pd.to_numeric, errors="raise").to_numpy(dtype=float)
    y_multi = np.array(
        [multiclass_index(str(v), multiclass_labels) for v in frame[fault_col]],
        dtype=int,
    )
    y_bin = np.array([0 if int(v) == normal_idx else 1 for v in y_multi], dtype=int)
    record_ids = frame[record_id].astype(str).tolist()
    if equipment_id in frame.columns:
        equipment = frame[equipment_id].astype(str).tolist()
    else:
        equipment = [""] * len(frame)
    return x, y_multi, y_bin, record_ids, equipment


def make_splits(
    frame: pd.DataFrame,
    *,
    random_seed: int = 42,
    train_ratio: float = 0.70,
    calib_ratio: float = 0.15,
    test_ratio: float = 0.15,
    spec: EquipmentSpec | None = None,
) -> SplitBundle:
    if abs(train_ratio + calib_ratio + test_ratio - 1.0) > 1e-9:
        raise ValueError("划分比例之和必须为 1。")
    x, y_multi, y_bin, record_ids, equipment = _matrix(frame, spec=spec)
    indices = np.arange(len(frame))

    first = StratifiedShuffleSplit(n_splits=1, test_size=test_ratio, random_state=random_seed)
    train_calib_idx, test_idx = next(first.split(indices, y_multi))

    relative_calib = calib_ratio / (train_ratio + calib_ratio)
    second = StratifiedShuffleSplit(n_splits=1, test_size=relative_calib, random_state=random_seed)
    train_rel, calib_rel = next(second.split(train_calib_idx, y_multi[train_calib_idx]))
    train_idx = train_calib_idx[train_rel]
    calib_idx = train_calib_idx[calib_rel]

    used = set(train_idx.tolist()) | set(calib_idx.tolist()) | set(test_idx.tolist())
    if len(used) != len(frame):
        raise ValueError("划分后记录数与原始不一致。")
    if len(set(train_idx) & set(calib_idx)) or len(set(train_idx) & set(test_idx)) or len(set(calib_idx) & set(test_idx)):
        raise ValueError("同一记录出现在多个划分中。")

    def pick(ids: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray, list[str], list[str]]:
        return (
            x[ids],
            y_multi[ids],
            y_bin[ids],
            [record_ids[i] for i in ids],
            [equipment[i] for i in ids],
        )

    x_tr, ym_tr, yb_tr, id_tr, eq_tr = pick(train_idx)
    x_ca, ym_ca, yb_ca, id_ca, eq_ca = pick(calib_idx)
    x_te, ym_te, yb_te, id_te, eq_te = pick(test_idx)

    return SplitBundle(
        train_ids=id_tr,
        calib_ids=id_ca,
        test_ids=id_te,
        x_train=x_tr,
        y_multi_train=ym_tr,
        y_bin_train=yb_tr,
        x_calib=x_ca,
        y_multi_calib=ym_ca,
        y_bin_calib=yb_ca,
        x_test=x_te,
        y_multi_test=ym_te,
        y_bin_test=yb_te,
        equipment_train=eq_tr,
        equipment_calib=eq_ca,
        equipment_test=eq_te,
        random_seed=random_seed,
        ratios={"train": train_ratio, "calib": calib_ratio, "test": test_ratio},
    )


def group_by_equipment_holdout(
    frame: pd.DataFrame,
    *,
    holdout_equipment: str,
) -> dict[str, Any]:
    """可选补充验证：按设备留组。不替代主划分。"""
    if EQUIPMENT_ID_COLUMN not in frame.columns:
        raise ValueError("缺少设备编号，无法按设备留组。")
    train_frame = frame[frame[EQUIPMENT_ID_COLUMN].astype(str) != holdout_equipment]
    test_frame = frame[frame[EQUIPMENT_ID_COLUMN].astype(str) == holdout_equipment]
    if train_frame.empty or test_frame.empty:
        raise ValueError(f"设备留组不可用：holdout={holdout_equipment}")
    train_labels = set(train_frame[FAULT_TYPE_COLUMN].astype(str))
    test_labels = set(test_frame[FAULT_TYPE_COLUMN].astype(str))
    missing = sorted(test_labels - train_labels)
    warning = None
    if missing:
        warning = f"留组设备 {holdout_equipment} 含训练未见类别：{missing}，指标可能误导。"
    return {
        "train_ids": train_frame[RECORD_ID_COLUMN].astype(str).tolist(),
        "test_ids": test_frame[RECORD_ID_COLUMN].astype(str).tolist(),
        "warning": warning,
    }


def split_to_dict(split: SplitBundle) -> dict[str, Any]:
    return {
        "random_seed": split.random_seed,
        "ratios": split.ratios,
        "train_ids": split.train_ids,
        "calib_ids": split.calib_ids,
        "test_ids": split.test_ids,
        "counts": {
            "train": len(split.train_ids),
            "calib": len(split.calib_ids),
            "test": len(split.test_ids),
        },
    }
