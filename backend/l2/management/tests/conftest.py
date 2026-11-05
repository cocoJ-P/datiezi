from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from l2.inference.config import L2InferenceConfig
from l2.inference.model_manager import ModelManager
from l2.management.config import DatasetRef, ManagementConfig
from l2.management.database import init_db
from l2.management.service import ManagementService

ROOT = Path(__file__).resolve().parents[4]
SRC_BUNDLE = ROOT / "models" / "main_mill_motor" / "TR-0b062172078e"
FEATURES = ROOT / "data" / "l2_dev" / "main_mill_motor" / "features.csv"
LABELS = ROOT / "data" / "l2_dev" / "main_mill_motor" / "labels.csv"
TRAINING_CONFIG = ROOT / "backend" / "l2" / "training" / "config" / "main_mill_motor.yaml"


@pytest.fixture()
def tmp_mgmt(tmp_path: Path):
    models_root = tmp_path / "models"
    equipment_dir = models_root / "main_mill_motor"
    equipment_dir.mkdir(parents=True)
    bundle_a = equipment_dir / "TR-0b062172078e"
    shutil.copytree(SRC_BUNDLE, bundle_a)

    # 复制第二份作为“另一版本”，只改 manifest model_bundle_id / 目录名
    bundle_b = equipment_dir / "TR-testbundle02"
    shutil.copytree(SRC_BUNDLE, bundle_b)
    manifest_path = bundle_b / "manifest.json"
    text = manifest_path.read_text(encoding="utf-8")
    text = text.replace("TR-0b062172078e", "TR-testbundle02")
    manifest_path.write_text(text, encoding="utf-8")

    db_path = tmp_path / "mgmt.sqlite3"
    logs_root = tmp_path / "logs"
    logs_root.mkdir()
    config = ManagementConfig(
        db_path=db_path,
        models_root=models_root,
        logs_root=logs_root,
        datasets={
            "main_mill_motor_dev": DatasetRef(
                dataset_id="main_mill_motor_dev",
                equipment_type="main_mill_motor",
                features_path=FEATURES,
                labels_path=LABELS,
                dataset_revision_id="rev1",
            )
        },
        training_config_path=TRAINING_CONFIG,
        poll_interval_sec=0.1,
        max_concurrent_training_jobs=1,
    )
    init_db(db_path)
    manager = ModelManager()
    service = ManagementService(config=config, manager=manager)
    inference_cfg = L2InferenceConfig(
        equipment_type="main_mill_motor",
        model_bundle_id="TR-0b062172078e",
        model_bundle_path=bundle_a,
        max_batch_size=200,
        clear_probability_threshold=0.80,
        fault_top1_threshold=0.70,
        max_probability_gap=0.20,
        enabled=True,
    )
    manager.load(inference_cfg)
    return {
        "tmp_path": tmp_path,
        "config": config,
        "service": service,
        "manager": manager,
        "bundle_a": bundle_a,
        "bundle_b": bundle_b,
        "inference_cfg": inference_cfg,
    }
