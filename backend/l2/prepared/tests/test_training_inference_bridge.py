"""PreparedDataset → TrainingJob / Inference 桥接测试。"""

from __future__ import annotations

from pathlib import Path

import pytest

from l2.equipment.registry import get_equipment_spec
from l2.inference.model_manager import ModelManager
from l2.inference.config import L2InferenceConfig
from l2.inference.service import InferenceService
from l2.management.config import DatasetRef, ManagementConfig
from l2.management.database import init_db
from l2.management.jobs import create_training_job, get_job, verify_job_input_hashes
from l2.management.service import ManagementService
from l2.prepared.adapter import PreparedDatasetAdapter
from l2.prepared.publisher import publish_from_l1_prepared
from l2.training import predict_bundle

ROOT = Path(__file__).resolve().parents[4]
PUMP_PD = "PD-4c2a075ce5f8"
PUMP_BUNDLE = ROOT / "models" / "high_pressure_descaling_pump" / "TR-0410fbe70876"
TRAINING_CONFIG = ROOT / "backend" / "l2" / "training" / "config" / "high_pressure_descaling_pump.yaml"


@pytest.fixture()
def prepared_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    root = tmp_path / "l2_prepared"
    monkeypatch.setenv("L2_PREPARED_ROOT", str(root))
    return root


@pytest.fixture()
def published(prepared_root: Path):
    return publish_from_l1_prepared(PUMP_PD)


def test_create_training_job_binds_immutable_revision(published, tmp_path: Path):
    config = ManagementConfig(
        db_path=tmp_path / "mgmt.sqlite3",
        models_root=tmp_path / "models",
        logs_root=tmp_path / "logs",
        datasets={},
        training_config_path=TRAINING_CONFIG,
        poll_interval_sec=0.1,
    )
    init_db(config.db_path)
    connection = __import__("l2.management.database", fromlist=["connect"]).connect(config.db_path)
    try:
        job = create_training_job(
            connection,
            config,
            equipment_type="high_pressure_descaling_pump",
            dataset_id=published["prepared_dataset_id"],
            prepared_dataset_id=published["prepared_dataset_id"],
        )
        connection.commit()
        assert job["dataset_id"] == published["prepared_dataset_id"]
        assert job["dataset_revision_id"] == published["revision_id"]
        verify_job_input_hashes(job)
        # 篡改哈希应失败
        bad = dict(job)
        bad["source_data_hash"] = "0" * 64
        with pytest.raises(ValueError, match="哈希"):
            verify_job_input_hashes(bad)
    finally:
        connection.close()


def test_management_lists_both_sources(published, tmp_path: Path):
    features = ROOT / "data" / "l2_dev" / "main_mill_motor" / "features.csv"
    labels = ROOT / "data" / "l2_dev" / "main_mill_motor" / "labels.csv"
    config = ManagementConfig(
        db_path=tmp_path / "mgmt.sqlite3",
        models_root=tmp_path / "models",
        logs_root=tmp_path / "logs",
        datasets={
            "main_mill_motor_dev": DatasetRef(
                dataset_id="main_mill_motor_dev",
                equipment_type="main_mill_motor",
                features_path=features,
                labels_path=labels,
            )
        },
        training_config_path=TRAINING_CONFIG,
        poll_interval_sec=0.1,
    )
    init_db(config.db_path)
    service = ManagementService(config=config, manager=ModelManager())
    body = service.list_datasets()
    sources = {item["source_type"] for item in body["datasets"]}
    assert "legacy_csv" in sources
    assert "l1_prepared" in sources
    assert any(item.get("prepared_dataset_id") == published["prepared_dataset_id"] for item in body["datasets"])


@pytest.mark.skipif(not PUMP_BUNDLE.is_dir(), reason="缺少泵 Active/候选模型包")
def test_diagnose_prepared_matches_predict_bundle(published):
    adapter = PreparedDatasetAdapter(published["prepared_dataset_id"])
    features = adapter.load_features()
    spec = get_equipment_spec("high_pressure_descaling_pump")
    rid = str(features.iloc[0][spec.record_id_field])
    record = adapter.get_record(rid)

    manager = ModelManager()
    cfg = L2InferenceConfig(
        equipment_type="high_pressure_descaling_pump",
        model_bundle_id=PUMP_BUNDLE.name,
        model_bundle_path=PUMP_BUNDLE,
        max_batch_size=200,
        clear_probability_threshold=0.80,
        fault_top1_threshold=0.70,
        max_probability_gap=0.20,
        enabled=True,
    )
    manager.load(cfg)
    service = InferenceService(manager=manager)
    via_dataset = service.diagnose_prepared_records(
        prepared_dataset_id=published["prepared_dataset_id"],
        record_ids=[rid],
    )
    assert via_dataset["results"][0]["record_id"] == rid
    assert via_dataset["prepared_dataset_id"] == published["prepared_dataset_id"]
    assert via_dataset["dataset_revision_id"] == published["revision_id"]
    # 推理结果不含标签
    assert "故障类型" not in str(via_dataset["results"][0].get("features") or {})

    direct = predict_bundle(manager.require("high_pressure_descaling_pump").bundle, [record["features"]])
    assert via_dataset["results"][0]["binary_classifier"]["probabilities"] == pytest.approx(
        {k: float(v) for k, v in direct[0]["binary_proba"].items()},
        rel=1e-9,
        abs=1e-9,
    )
    assert via_dataset["results"][0]["multiclass_classifier"]["probabilities"] == pytest.approx(
        {k: float(v) for k, v in direct[0]["multiclass_proba"].items()},
        rel=1e-9,
        abs=1e-9,
    )


def test_unmatched_equipment_requires_config(prepared_root: Path, monkeypatch: pytest.MonkeyPatch):
    from l2.prepared.mapping import EquipmentMapping
    from l2.prepared.publisher import PreparedPublishError, publish_from_l1_prepared
    import l2.prepared.publisher as publisher_mod

    def fake_resolve(**kwargs):
        return EquipmentMapping(
            equipment_type="brand_new_pump",
            schema_version="",
            mapping_status="needs_review",
            mapping_source="test",
            needs_confirmation=True,
            candidate_spec={"hint": "need confirm"},
        )

    monkeypatch.setattr(publisher_mod, "resolve_equipment_type", fake_resolve)
    with pytest.raises(PreparedPublishError) as exc:
        publish_from_l1_prepared(PUMP_PD)
    assert exc.value.code == "EQUIPMENT_CONFIG_REQUIRED"
