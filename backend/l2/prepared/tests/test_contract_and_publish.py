"""PreparedEquipmentDataset 契约与发布测试。"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pandas as pd
import pytest

from l2.equipment.registry import get_equipment_spec
from l2.prepared.adapter import PreparedDatasetAdapter
from l2.prepared.catalog import get_published_dataset, list_published_datasets
from l2.prepared.contract import read_manifest
from l2.prepared.mapping import map_l1_features_to_spec
from l2.prepared.publisher import publish_from_l1_prepared


PUMP_PD = "PD-4c2a075ce5f8"


@pytest.fixture()
def prepared_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    root = tmp_path / "l2_prepared"
    monkeypatch.setenv("L2_PREPARED_ROOT", str(root))
    return root


def test_feature_label_metadata_isolation(prepared_root: Path):
    result = publish_from_l1_prepared(PUMP_PD)
    assert result["training_ready"] is True
    bundle = Path(result["bundle_dir"])
    features = pd.read_csv(bundle / "features.csv", encoding="utf-8-sig")
    labels = pd.read_csv(bundle / "labels.csv", encoding="utf-8-sig")
    metadata = pd.read_csv(bundle / "metadata.csv", encoding="utf-8-sig")
    spec = get_equipment_spec("high_pressure_descaling_pump")
    assert spec.target_field not in features.columns
    assert "根因分析" not in features.columns
    assert "故障类型" in labels.columns
    assert set(spec.feature_names).issubset(set(features.columns))
    assert "record_id" in metadata.columns or spec.record_id_field in features.columns
    assert features[spec.record_id_field].is_unique
    assert len(features) == len(labels) == result["record_count"]


def test_publish_idempotent(prepared_root: Path):
    first = publish_from_l1_prepared(PUMP_PD)
    second = publish_from_l1_prepared(PUMP_PD)
    assert first["prepared_dataset_id"] == second["prepared_dataset_id"]
    assert second["reused"] is True


def test_manifest_and_hash(prepared_root: Path):
    result = publish_from_l1_prepared(PUMP_PD)
    manifest = read_manifest(Path(result["bundle_dir"]) / "manifest.json")
    assert manifest["prepared_dataset_id"] == result["prepared_dataset_id"]
    assert manifest["equipment_type"] == "high_pressure_descaling_pump"
    assert manifest["source_stage"] == "P8"
    assert "l1_prepared_version_id" in manifest["source_revision_ids"] or manifest.get(
        "l1_prepared_version_id"
    )
    assert manifest["content_hashes"]["features.csv"]
    adapter = PreparedDatasetAdapter(result["prepared_dataset_id"])
    assert adapter.validate_dataset()["ok"] is True


def test_catalog_lists_published(prepared_root: Path):
    result = publish_from_l1_prepared(PUMP_PD)
    items = list_published_datasets(equipment_type="high_pressure_descaling_pump")
    assert any(x["prepared_dataset_id"] == result["prepared_dataset_id"] for x in items)
    meta = get_published_dataset(result["prepared_dataset_id"])
    assert meta["source_type"] == "l1_prepared"


def test_training_snapshot_excludes_unlabeled_and_no_leakage(prepared_root: Path):
    result = publish_from_l1_prepared(PUMP_PD)
    adapter = PreparedDatasetAdapter(result["prepared_dataset_id"])
    snapshot = adapter.load_training_snapshot()
    spec = get_equipment_spec("high_pressure_descaling_pump")
    assert snapshot.source_kind == "l1_prepared"
    assert spec.target_field in snapshot.frame.columns
    for col in spec.feature_names:
        assert col in snapshot.frame.columns
    # X 侧不应混入 reference
    assert "根因分析" not in snapshot.frame.columns
    assert "频谱描述" not in snapshot.frame.columns


def test_inference_record_has_no_labels(prepared_root: Path):
    result = publish_from_l1_prepared(PUMP_PD)
    adapter = PreparedDatasetAdapter(result["prepared_dataset_id"])
    features = adapter.load_features()
    rid = str(features.iloc[0][get_equipment_spec("high_pressure_descaling_pump").record_id_field])
    record = adapter.get_record(rid)
    assert "features" in record
    assert "故障类型" not in record["features"]
    assert set(record["features"].keys()) == set(
        get_equipment_spec("high_pressure_descaling_pump").feature_names
    )


def test_bridge_fills_vibration_rms():
    spec = get_equipment_spec("high_pressure_descaling_pump")
    input_features = {
        "rotational_speed": {"value": 1.0, "status": "success"},
        "rotational_frequency": {"value": 1.0, "status": "success"},
        "motor_current": {"value": 1.0, "status": "success"},
        "vibration_x": {"value": 3.0, "status": "success"},
        "vibration_y": {"value": 4.0, "status": "success"},
        "vibration_max": {"value": 4.0, "status": "success"},
        "bearing_temperature_drive_end": {"value": 1.0, "status": "success"},
        "bearing_temperature_non_drive_end": {"value": 1.0, "status": "success"},
        "envelope_peak": {"value": 1.0, "status": "success"},
        "bpfo_amplitude": {"value": 1.0, "status": "success"},
        "bpfi_amplitude": {"value": 1.0, "status": "success"},
        "bsf_amplitude": {"value": 1.0, "status": "success"},
        "amplitude_1x": {"value": 1.0, "status": "success"},
    }
    mapped, missing, notes = map_l1_features_to_spec(input_features, spec)
    assert missing == []
    assert mapped["振动均方根"] == pytest.approx((0.5 * (3.0**2 + 4.0**2)) ** 0.5)
    assert notes
