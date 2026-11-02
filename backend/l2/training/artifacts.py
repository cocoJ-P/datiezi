"""模型包保存、加载、验证与最小预测接口。"""

from __future__ import annotations

import hashlib
import json
import shutil
import tempfile
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import joblib
import numpy as np
import pandas as pd
from catboost import CatBoostClassifier

from l2.training.calibration import PlattCalibrator, apply_temperature
from l2.training.schema import (
    BINARY_LABELS,
    FEATURE_COLUMNS,
    MULTICLASS_LABELS,
    default_feature_schema,
)

# FEATURE_COLUMNS / BINARY_LABELS / MULTICLASS_LABELS 仅作旧调用兼容默认值


@dataclass
class ModelBundle:
    path: Path
    manifest: dict[str, Any]
    binary_model: Any
    binary_calibrator: Any | None
    multiclass_model: CatBoostClassifier
    multiclass_calibration: dict[str, Any]
    feature_schema: dict[str, Any]
    label_map: dict[str, Any]
    training_config: dict[str, Any]
    metrics: dict[str, Any]


REQUIRED_FILES = (
    "binary_model.joblib",
    "multiclass_model.cbm",
    "multiclass_calibration.json",
    "feature_schema.json",
    "label_map.json",
    "training_config.json",
    "metrics.json",
    "split_ids.json",
    "manifest.json",
)


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def save_model_bundle(
    output_root: Path,
    *,
    equipment_type: str,
    binary_model: Any,
    binary_calibration: Any,
    multiclass_model: CatBoostClassifier,
    multiclass_calibration: Any,
    training_config: dict[str, Any],
    metrics: dict[str, Any],
    split_ids: dict[str, Any],
    test_predictions: pd.DataFrame,
    snapshot_meta: dict[str, Any],
    dependency_versions: dict[str, str],
    feature_schema: dict[str, Any] | None = None,
    binary_labels: list[str] | None = None,
    multiclass_labels: list[str] | None = None,
    normal_label: str = "正常",
) -> Path:
    output_root = Path(output_root)
    output_root.mkdir(parents=True, exist_ok=True)
    run_id = "TR-" + uuid.uuid4().hex[:12]
    bundle_dir = output_root / run_id
    if bundle_dir.exists():
        raise RuntimeError(f"模型版本目录已存在，拒绝覆盖：{bundle_dir}")

    schema = feature_schema or default_feature_schema().to_dict()
    bin_labels = list(binary_labels) if binary_labels is not None else list(BINARY_LABELS)
    multi_labels = list(multiclass_labels) if multiclass_labels is not None else list(MULTICLASS_LABELS)
    feature_columns = list(schema.get("feature_columns") or FEATURE_COLUMNS)

    tmp = Path(tempfile.mkdtemp(prefix="l2-bundle-"))
    try:
        joblib.dump(binary_model, tmp / "binary_model.joblib")
        if binary_calibration.calibrator is not None:
            joblib.dump(binary_calibration.calibrator, tmp / "binary_calibrator.joblib")
        multiclass_model.save_model(str(tmp / "multiclass_model.cbm"))
        _write_json(
            tmp / "multiclass_calibration.json",
            {
                "method": multiclass_calibration.method,
                "status": multiclass_calibration.status,
                "reason": multiclass_calibration.reason,
                "temperature": multiclass_calibration.temperature,
                "params": multiclass_calibration.params,
            },
        )
        _write_json(tmp / "feature_schema.json", schema)
        _write_json(
            tmp / "label_map.json",
            {
                "binary_labels": bin_labels,
                "multiclass_labels": multi_labels,
                "normal_label": normal_label,
                "binary_index": {name: i for i, name in enumerate(bin_labels)},
                "multiclass_index": {name: i for i, name in enumerate(multi_labels)},
            },
        )
        _write_json(tmp / "training_config.json", training_config)
        _write_json(tmp / "metrics.json", metrics)
        _write_json(tmp / "split_ids.json", split_ids)
        test_predictions.to_csv(tmp / "test_predictions.csv", index=False, encoding="utf-8-sig")

        artifact_hashes = {
            item.name: _sha256_file(item)
            for item in tmp.iterdir()
            if item.is_file()
        }
        created_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        config_hash = hashlib.sha256(
            json.dumps(training_config, ensure_ascii=False, sort_keys=True).encode("utf-8")
        ).hexdigest()
        binary_status = binary_calibration.status
        multi_status = multiclass_calibration.status
        calibration_status = {
            "binary": binary_status,
            "multiclass": multi_status,
            "overall": "applied"
            if binary_status == "applied" and multi_status == "applied"
            else "partial"
            if "applied" in {binary_status, multi_status}
            else "unverified",
        }
        manifest = {
            "model_bundle_id": run_id,
            "equipment_type": equipment_type,
            "dataset_id": snapshot_meta.get("dataset_id"),
            "dataset_revision_id": snapshot_meta.get("dataset_revision_id"),
            "feature_schema_version": schema["version"],
            "training_run_id": run_id,
            "created_at": created_at,
            "random_seed": training_config.get("random_seed"),
            "class_labels": {
                "binary": bin_labels,
                "multiclass": multi_labels,
                "normal_label": normal_label,
            },
            "training_config_hash": config_hash,
            "source_data_hash": snapshot_meta.get("source_data_hash"),
            "dependency_versions": dependency_versions,
            "calibration_status": calibration_status,
            "evaluation_summary": {
                "binary_accuracy": metrics.get("binary", {}).get("final", {}).get("accuracy"),
                "multiclass_accuracy": metrics.get("multiclass", {}).get("final", {}).get("accuracy"),
                "binary_macro_f1": metrics.get("binary", {}).get("final", {}).get("macro_f1"),
                "multiclass_macro_f1": metrics.get("multiclass", {}).get("final", {}).get("macro_f1"),
                "conflict_count": metrics.get("consistency", {}).get("conflict_count"),
            },
            "artifact_hashes": artifact_hashes,
            "status": "candidate",
            "binary_calibration": {
                "method": binary_calibration.method,
                "status": binary_calibration.status,
                "reason": binary_calibration.reason,
                "params": binary_calibration.params,
            },
        }
        _write_json(tmp / "manifest.json", manifest)

        # 加载自检后再发布
        probe = load_model_bundle(tmp)
        sample = {col: float(test_predictions.iloc[0][col]) for col in feature_columns if col in test_predictions.columns}
        if sample:
            predict_bundle(probe, [sample])

        shutil.move(str(tmp), str(bundle_dir))
        return bundle_dir
    except Exception:
        shutil.rmtree(tmp, ignore_errors=True)
        raise


def load_model_bundle(path: str | Path) -> ModelBundle:
    root = Path(path)
    if not root.is_dir():
        raise FileNotFoundError(f"模型包目录不存在：{root}")
    missing = [name for name in REQUIRED_FILES if not (root / name).is_file()]
    if missing:
        raise FileNotFoundError(f"模型包缺少文件：{missing}")

    binary_model = joblib.load(root / "binary_model.joblib")
    calibrator = None
    if (root / "binary_calibrator.joblib").is_file():
        calibrator = joblib.load(root / "binary_calibrator.joblib")
    multiclass = CatBoostClassifier()
    multiclass.load_model(str(root / "multiclass_model.cbm"))
    multi_cal = json.loads((root / "multiclass_calibration.json").read_text(encoding="utf-8"))
    feature_schema = json.loads((root / "feature_schema.json").read_text(encoding="utf-8"))
    label_map = json.loads((root / "label_map.json").read_text(encoding="utf-8"))
    training_config = json.loads((root / "training_config.json").read_text(encoding="utf-8"))
    metrics = json.loads((root / "metrics.json").read_text(encoding="utf-8"))
    manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    if not feature_schema.get("feature_columns"):
        raise ValueError("feature_schema 缺少 feature_columns，拒绝推理。")
    if not label_map.get("multiclass_labels"):
        raise ValueError("label_map 缺少 multiclass_labels，拒绝推理。")
    return ModelBundle(
        path=root.resolve(),
        manifest=manifest,
        binary_model=binary_model,
        binary_calibrator=calibrator,
        multiclass_model=multiclass,
        multiclass_calibration=multi_cal,
        feature_schema=feature_schema,
        label_map=label_map,
        training_config=training_config,
        metrics=metrics,
    )


def _records_to_matrix(bundle: ModelBundle, feature_records: list[dict[str, Any]]) -> np.ndarray:
    columns = bundle.feature_schema["feature_columns"]
    rows = []
    for item in feature_records:
        row = []
        for name in columns:
            if name not in item:
                raise ValueError(f"缺少特征：{name}")
            value = float(item[name])
            if not np.isfinite(value):
                raise ValueError(f"特征 {name} 非有限值：{value}")
            row.append(value)
        rows.append(row)
    return np.asarray(rows, dtype=float)


def predict_bundle(bundle: ModelBundle, feature_records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not feature_records:
        return []
    x = _records_to_matrix(bundle, feature_records)
    if bundle.binary_calibrator is not None:
        binary_proba = np.asarray(bundle.binary_calibrator.predict_proba(x), dtype=float)
    else:
        binary_proba = np.asarray(bundle.binary_model.predict_proba(x), dtype=float)
    multi_raw = np.asarray(bundle.multiclass_model.predict_proba(x), dtype=float)
    temperature = float(bundle.multiclass_calibration.get("temperature", 1.0))
    multi_final = apply_temperature(multi_raw, temperature)
    binary_names = bundle.label_map.get("binary_labels") or list(BINARY_LABELS)
    multi_names = bundle.label_map.get("multiclass_labels") or list(MULTICLASS_LABELS)
    normal_label = str(
        bundle.label_map.get("normal_label")
        or (bundle.manifest.get("class_labels") or {}).get("normal_label")
        or "正常"
    )
    try:
        normal_idx = list(multi_names).index(normal_label)
    except ValueError:
        normal_idx = 0
    outputs = []
    for i in range(len(feature_records)):
        b_prob = binary_proba[i]
        m_prob = multi_final[i]
        p_a = float(b_prob[1])
        p_b = float(1.0 - m_prob[normal_idx])
        outputs.append(
            {
                "binary_label": binary_names[int(np.argmax(b_prob))],
                "binary_proba": {binary_names[j]: float(b_prob[j]) for j in range(len(binary_names))},
                "multiclass_label": multi_names[int(np.argmax(m_prob))],
                "multiclass_proba": {multi_names[j]: float(m_prob[j]) for j in range(len(multi_names))},
                "probability_gap": abs(p_a - p_b),
                "p_a_abnormal": p_a,
                "p_b_abnormal": p_b,
                "model_bundle_id": bundle.manifest.get("model_bundle_id"),
                "calibration_status": bundle.manifest.get("calibration_status"),
            }
        )
    return outputs


def verify_bundle(path: str | Path) -> dict[str, Any]:
    bundle = load_model_bundle(path)
    columns = bundle.feature_schema["feature_columns"]
    sample = {name: 0.0 for name in columns}
    # 用轻微非零避免全零退化
    for i, name in enumerate(columns):
        sample[name] = float(i + 1) * 0.01
    pred = predict_bundle(bundle, [sample])[0]
    for key in ("binary_proba", "multiclass_proba"):
        values = list(pred[key].values())
        if any((v < 0 or v > 1 or not np.isfinite(v)) for v in values):
            raise ValueError(f"{key} 概率非法：{values}")
        if abs(sum(values) - 1.0) > 1e-5:
            raise ValueError(f"{key} 概率和不为 1：{sum(values)}")
    return {
        "ok": True,
        "model_bundle_id": bundle.manifest.get("model_bundle_id"),
        "path": str(bundle.path),
        "sample_prediction": pred,
    }
