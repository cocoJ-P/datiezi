from __future__ import annotations

from pathlib import Path

from l2.equipment.registry import get_equipment_spec
from l2.training import load_model_bundle, predict_bundle
from l2.training.pipeline import load_config, run_training

ROOT = Path(__file__).resolve().parents[4]
PUMP_FEATURES = ROOT / "data" / "l2_dev" / "high_pressure_descaling_pump" / "features.csv"
PUMP_LABELS = ROOT / "data" / "l2_dev" / "high_pressure_descaling_pump" / "labels.csv"
PUMP_CFG = ROOT / "backend" / "l2" / "training" / "config" / "high_pressure_descaling_pump.yaml"
MILL_BUNDLE = ROOT / "models" / "main_mill_motor" / "TR-0b062172078e"


def test_train_pump_dynamic_classes(tmp_path: Path):
    spec = get_equipment_spec("high_pressure_descaling_pump")
    assert spec.class_count == 3
    assert spec.feature_count == 14
    result = run_training(
        config=load_config(PUMP_CFG),
        features_path=PUMP_FEATURES,
        labels_path=PUMP_LABELS,
        output_root=tmp_path / "models",
        equipment_spec=spec,
    )
    bundle = load_model_bundle(result["bundle_path"])
    assert bundle.manifest["equipment_type"] == "high_pressure_descaling_pump"
    assert len(bundle.label_map["multiclass_labels"]) == 3
    assert len(bundle.feature_schema["feature_columns"]) == 14
    sample = {name: 0.01 * (i + 1) for i, name in enumerate(spec.feature_names)}
    pred = predict_bundle(bundle, [sample])[0]
    assert len(pred["multiclass_proba"]) == 3
    assert abs(sum(pred["multiclass_proba"].values()) - 1.0) < 1e-5
    assert abs(sum(pred["binary_proba"].values()) - 1.0) < 1e-5


def test_old_mill_bundle_unchanged():
    bundle = load_model_bundle(MILL_BUNDLE)
    assert bundle.manifest["model_bundle_id"] == "TR-0b062172078e"
    assert len(bundle.feature_schema["feature_columns"]) == 12
    assert len(bundle.label_map["multiclass_labels"]) == 5
    sample = {name: 0.01 * (i + 1) for i, name in enumerate(bundle.feature_schema["feature_columns"])}
    pred = predict_bundle(bundle, [sample])[0]
    assert pred["model_bundle_id"] == "TR-0b062172078e"
    assert len(pred["multiclass_proba"]) == 5
