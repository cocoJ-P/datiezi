"""T0–T7 训练流水线（设备配置驱动）。"""

from __future__ import annotations

import logging
from importlib import metadata
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import yaml

from l2.equipment.registry import get_equipment_spec
from l2.equipment.schema import EquipmentSpec
from l2.training.artifacts import save_model_bundle
from l2.training.calibration import (
    apply_temperature,
    binary_proba,
    calibrate_binary,
    calibrate_multiclass_temperature,
)
from l2.training.data_adapter import load_csv_snapshot, snapshot_meta
from l2.training.dataset import audit_training_frame, make_splits, split_to_dict
from l2.training.evaluation import evaluate_pair
from l2.training.schema import feature_schema_from_spec
from l2.training.trainers import (
    predict_proba_binary,
    predict_proba_multiclass,
    train_binary_classifier,
    train_multiclass_classifier,
)

LOG = logging.getLogger("l2.training")


def _dep_versions() -> dict[str, str]:
    names = ["scikit-learn", "catboost", "numpy", "scipy", "joblib", "pandas"]
    out: dict[str, str] = {}
    for name in names:
        try:
            out[name] = metadata.version(name)
        except metadata.PackageNotFoundError:
            out[name] = "missing"
    return out


def load_config(path: str | Path) -> dict[str, Any]:
    payload = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("配置文件必须是 YAML 对象。")
    return payload


def run_training(
    *,
    config: dict[str, Any],
    features_path: str | Path,
    labels_path: str | Path,
    output_root: str | Path,
    equipment_spec: EquipmentSpec | None = None,
) -> dict[str, Any]:
    LOG.info("任务开始")
    equipment_type = str(config.get("equipment_type") or "main_mill_motor")
    spec = equipment_spec or get_equipment_spec(equipment_type)
    if spec.equipment_type != equipment_type:
        raise ValueError(
            f"训练配置设备类型 {equipment_type} 与 EquipmentSpec {spec.equipment_type} 不一致。"
        )

    random_seed = int(config.get("random_seed", 42))
    split_cfg = config.get("split") or {}
    dataset_id = str(config.get("dataset_id") or f"{equipment_type}_dev")
    dataset_revision_id = str(config.get("dataset_revision_id") or "rev1")

    snapshot = load_csv_snapshot(
        features_path,
        labels_path,
        dataset_id=dataset_id,
        dataset_revision_id=dataset_revision_id,
        equipment_spec=spec,
    )
    LOG.info("数据读取完成 record_count=%s", len(snapshot.frame))

    audit = audit_training_frame(snapshot.frame, spec=spec)
    LOG.info(
        "数据检查结果 classes=%s warnings=%s",
        audit.class_counts,
        audit.warnings + list(snapshot.warnings),
    )

    split = make_splits(
        snapshot.frame,
        random_seed=random_seed,
        train_ratio=float(split_cfg.get("train_ratio", 0.70)),
        calib_ratio=float(split_cfg.get("calib_ratio", 0.15)),
        test_ratio=float(split_cfg.get("test_ratio", 0.15)),
        spec=spec,
    )
    LOG.info(
        "数据划分完成 train=%s calib=%s test=%s",
        len(split.train_ids),
        len(split.calib_ids),
        len(split.test_ids),
    )

    binary = train_binary_classifier(
        split.x_train,
        split.y_bin_train,
        config.get("binary_params"),
        class_names=spec.binary_labels,
    )
    LOG.info("分类器 A 训练完成")
    multiclass = train_multiclass_classifier(
        split.x_train,
        split.y_multi_train,
        config.get("multiclass_params"),
        class_names=spec.class_labels,
    )
    LOG.info("分类器 B 训练完成 class_count=%s", len(spec.class_labels))

    binary_cal = calibrate_binary(binary.model, split.x_calib, split.y_bin_calib)
    multi_raw_calib = predict_proba_multiclass(multiclass.model, split.x_calib)
    multi_cal = calibrate_multiclass_temperature(multi_raw_calib, split.y_multi_calib)
    LOG.info(
        "概率校准完成 binary=%s multiclass=%s temp=%s",
        binary_cal.status,
        multi_cal.status,
        multi_cal.temperature,
    )

    binary_raw_test = predict_proba_binary(binary.model, split.x_test)
    binary_final_test = binary_proba(binary.model, binary_cal, split.x_test)
    multi_raw_test = predict_proba_multiclass(multiclass.model, split.x_test)
    multi_final_test = apply_temperature(multi_raw_test, multi_cal.temperature)

    metrics = evaluate_pair(
        record_ids=split.test_ids,
        y_bin_true=split.y_bin_test,
        y_multi_true=split.y_multi_test,
        binary_raw=binary_raw_test,
        binary_final=binary_final_test,
        multi_raw=multi_raw_test,
        multi_final=multi_final_test,
        binary_labels=list(spec.binary_labels),
        multiclass_labels=list(spec.class_labels),
        normal_label=spec.normal_label,
    )
    metrics["data_audit"] = {
        "record_count": audit.record_count,
        "class_counts": audit.class_counts,
        "binary_counts": audit.binary_counts,
        "warnings": audit.warnings + list(snapshot.warnings),
        "status_mismatch_count": audit.status_mismatch_count,
        "status_fault_audit": None if audit.status_fault_audit is None else audit.status_fault_audit.to_dict(),
    }
    LOG.info(
        "评测完成 binary_acc=%.4f multi_acc=%.4f conflicts=%s",
        metrics["binary"]["final"]["accuracy"],
        metrics["multiclass"]["final"]["accuracy"],
        metrics["consistency"]["conflict_count"],
    )

    normal_idx = list(spec.class_labels).index(spec.normal_label)
    pred_rows = []
    for i, record_id in enumerate(split.test_ids):
        row = {
            spec.record_id_field: record_id,
            "y_bin_true": int(split.y_bin_test[i]),
            "y_multi_true": int(split.y_multi_test[i]),
            "y_bin_pred": int(np.argmax(binary_final_test[i])),
            "y_multi_pred": int(np.argmax(multi_final_test[i])),
            "p_a_abnormal": float(binary_final_test[i, 1]),
            "p_b_abnormal": float(1.0 - multi_final_test[i, normal_idx]),
        }
        for j, name in enumerate(spec.feature_names):
            row[name] = float(split.x_test[i, j])
        for j, name in enumerate(spec.class_labels):
            row[f"multi_p_{name}"] = float(multi_final_test[i, j])
        pred_rows.append(row)
    predictions = pd.DataFrame(pred_rows)

    schema = feature_schema_from_spec(spec).to_dict()
    training_config = {
        "equipment_type": equipment_type,
        "random_seed": random_seed,
        "split": split.ratios,
        "binary_params": binary.params,
        "multiclass_params": multiclass.params,
        "feature_columns": list(spec.feature_names),
        "class_labels": list(spec.class_labels),
        "schema_version": spec.schema_version,
        "dataset_id": dataset_id,
        "dataset_revision_id": dataset_revision_id,
    }
    bundle_path = save_model_bundle(
        Path(output_root) / equipment_type,
        equipment_type=equipment_type,
        binary_model=binary.model,
        binary_calibration=binary_cal,
        multiclass_model=multiclass.model,
        multiclass_calibration=multi_cal,
        training_config=training_config,
        metrics=metrics,
        split_ids=split_to_dict(split),
        test_predictions=predictions,
        snapshot_meta=snapshot_meta(snapshot),
        dependency_versions=_dep_versions(),
        feature_schema=schema,
        binary_labels=list(spec.binary_labels),
        multiclass_labels=list(spec.class_labels),
        normal_label=spec.normal_label,
    )
    LOG.info("模型包保存完成 path=%s", bundle_path)
    LOG.info("训练结束")
    return {
        "bundle_path": str(bundle_path),
        "metrics": metrics,
        "split": split_to_dict(split),
        "calibration": {
            "binary": {
                "method": binary_cal.method,
                "status": binary_cal.status,
                "reason": binary_cal.reason,
            },
            "multiclass": {
                "method": multi_cal.method,
                "status": multi_cal.status,
                "reason": multi_cal.reason,
                "temperature": multi_cal.temperature,
            },
        },
        "snapshot": snapshot_meta(snapshot),
        "equipment_type": equipment_type,
        "schema_version": spec.schema_version,
    }
