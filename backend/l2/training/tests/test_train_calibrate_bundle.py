from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest

from l2.training.artifacts import load_model_bundle, predict_bundle, verify_bundle
from l2.training.calibration import apply_temperature, calibrate_multiclass_temperature
from l2.training.pipeline import load_config, run_training
from l2.training.schema import FEATURE_COLUMNS, MULTICLASS_LABELS, fault_to_binary

ROOT = Path(__file__).resolve().parents[4]
DEV = ROOT / "data" / "l2_dev" / "main_mill_motor"
CONFIG = ROOT / "backend" / "l2" / "training" / "config" / "main_mill_motor.yaml"


def test_fault_to_binary_mapping():
    assert fault_to_binary("正常") == "正常"
    assert fault_to_binary("轴承磨损") == "非正常"
    assert fault_to_binary("齿轮异常") == "非正常"


def test_temperature_scaling_safe_on_perfect():
    proba = np.eye(5)
    y = np.arange(5)
    result = calibrate_multiclass_temperature(proba, y)
    assert result.temperature > 0
    assert np.isfinite(result.temperature)
    calibrated = apply_temperature(proba, result.temperature)
    assert np.allclose(calibrated.sum(axis=1), 1.0, atol=1e-6)
    assert np.all((calibrated >= 0) & (calibrated <= 1))


def test_end_to_end_train_reload_predict(tmp_path: Path):
    config = load_config(CONFIG)
    # 加速测试：降低树数量，但保持流程完整
    config["binary_params"] = {
        "n_estimators": 40,
        "max_depth": 8,
        "min_samples_leaf": 2,
        "random_state": 42,
        "n_jobs": -1,
    }
    config["multiclass_params"] = {
        "iterations": 40,
        "depth": 4,
        "learning_rate": 0.1,
        "loss_function": "MultiClass",
        "random_seed": 42,
        "task_type": "CPU",
        "verbose": False,
        "allow_writing_files": False,
    }
    result = run_training(
        config=config,
        features_path=DEV / "features.csv",
        labels_path=DEV / "labels.csv",
        output_root=tmp_path / "models",
    )
    bundle_path = Path(result["bundle_path"])
    assert bundle_path.is_dir()
    assert (bundle_path / "manifest.json").is_file()
    assert result["split"]["counts"]["train"] + result["split"]["counts"]["calib"] + result["split"]["counts"]["test"] == 4000

    report = verify_bundle(bundle_path)
    assert report["ok"] is True

    bundle = load_model_bundle(bundle_path)
    sample = {name: 1.0 for name in FEATURE_COLUMNS}
    pred1 = predict_bundle(bundle, [sample])[0]
    pred2 = predict_bundle(load_model_bundle(bundle_path), [sample])[0]
    assert pred1["binary_label"] in {"正常", "非正常"}
    assert pred1["multiclass_label"] in MULTICLASS_LABELS
    assert abs(sum(pred1["binary_proba"].values()) - 1.0) < 1e-6
    assert abs(sum(pred1["multiclass_proba"].values()) - 1.0) < 1e-6
    assert pred1["binary_proba"] == pytest.approx(pred2["binary_proba"], rel=1e-9, abs=1e-9)
    assert pred1["multiclass_proba"] == pytest.approx(pred2["multiclass_proba"], rel=1e-9, abs=1e-9)

    with pytest.raises(FileNotFoundError):
        load_model_bundle(tmp_path / "missing")

    # 缺少 schema 应拒绝
    bad = tmp_path / "bad_bundle"
    bad.mkdir()
    for name in [
        "binary_model.joblib",
        "multiclass_model.cbm",
        "multiclass_calibration.json",
        "label_map.json",
        "training_config.json",
        "metrics.json",
        "split_ids.json",
        "manifest.json",
    ]:
        (bad / name).write_text("{}", encoding="utf-8")
    with pytest.raises(FileNotFoundError, match="缺少文件"):
        load_model_bundle(bad)

    manifest = json.loads((bundle_path / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["status"] == "candidate"
