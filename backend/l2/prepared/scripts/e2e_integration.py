"""L1→L2 PreparedEquipmentDataset 端到端验收脚本。"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
PROJECT = ROOT.parent

from l2.equipment.registry import get_equipment_spec
from l2.inference.config import L2InferenceConfig
from l2.inference.model_manager import ModelManager
from l2.inference.service import InferenceService
from l2.management.config import load_management_config
from l2.management.database import connect, init_db
from l2.management.jobs import create_training_job, get_job
from l2.management.registry import get_active_model, list_models
from l2.management.service import ManagementService
from l2.management.worker import claim_next_job, process_one_job
from l2.prepared.adapter import PreparedDatasetAdapter
from l2.prepared.catalog import list_published_datasets
from l2.prepared.publisher import publish_from_l1_prepared
from l2.training import predict_bundle


def main() -> int:
    os.environ.setdefault("PYTHONUTF8", "1")
    l1_version = os.getenv("L1_PREPARED_VERSION", "PD-4c2a075ce5f8")
    print("=== Step1 publish ===")
    published = publish_from_l1_prepared(l1_version)
    print(json.dumps(published, ensure_ascii=False, indent=2))
    assert published["training_ready"]
    ped = published["prepared_dataset_id"]

    print("=== Step2 catalog ===")
    catalog = list_published_datasets(equipment_type="high_pressure_descaling_pump")
    assert any(x["prepared_dataset_id"] == ped for x in catalog)

    print("=== Step3 management list ===")
    service = ManagementService()
    datasets = service.list_datasets()["datasets"]
    assert any(d.get("prepared_dataset_id") == ped or d.get("dataset_id") == ped for d in datasets)
    assert any(d.get("source_type") == "legacy_csv" for d in datasets)

    print("=== Step4 train job + worker ===")
    config = load_management_config()
    init_db(config.db_path)
    connection = connect(config.db_path)
    try:
        job = create_training_job(
            connection,
            config,
            equipment_type="high_pressure_descaling_pump",
            dataset_id=ped,
            prepared_dataset_id=ped,
        )
        connection.commit()
        job_id = job["job_id"]
        print("job", job_id, job["dataset_revision_id"])
    finally:
        connection.close()

    connection = connect(config.db_path)
    try:
        claimed = claim_next_job(connection, worker_pid=os.getpid())
    finally:
        connection.close()
    assert claimed is not None and claimed["job_id"] == job_id
    connection = connect(config.db_path)
    try:
        process_one_job(connection, config, claimed)
    finally:
        connection.close()

    connection = connect(config.db_path)
    try:
        finished = get_job(connection, job_id)
        print("train status", finished["status"], finished.get("model_bundle_id"), finished.get("error_message"))
        assert finished["status"] == "succeeded", finished
        model_bundle_id = finished["model_bundle_id"]
        models = list_models(connection, equipment_type="high_pressure_descaling_pump", status="candidate")
        assert any(m["model_bundle_id"] == model_bundle_id for m in models["models"])
        active_before = get_active_model(connection, "high_pressure_descaling_pump")
        print("active unchanged", None if active_before is None else active_before["model_bundle_id"])
    finally:
        connection.close()

    print("=== Step5 diagnose by dataset ===")
    adapter = PreparedDatasetAdapter(ped)
    spec = get_equipment_spec("high_pressure_descaling_pump")
    rid = str(adapter.load_features().iloc[0][spec.record_id_field])
    record = adapter.get_record(rid)

    # 与 Management Active 对齐后，用同一 ModelManager 比较普通诊断 / PED 诊断
    # （禁止再单独加载固定历史包，否则会与 Registry Active 误报不一致）
    connection = connect(config.db_path)
    try:
        active = get_active_model(connection, "high_pressure_descaling_pump")
    finally:
        connection.close()
    if active is None:
        raise RuntimeError("泵设备没有 Active 模型，无法验收诊断一致性。")
    active_id = active["model_bundle_id"]
    bundle_path = Path(active["bundle_path"])
    manager = ModelManager()
    manager.load(
        L2InferenceConfig(
            equipment_type="high_pressure_descaling_pump",
            model_bundle_id=active_id,
            model_bundle_path=bundle_path,
            max_batch_size=200,
            clear_probability_threshold=0.80,
            fault_top1_threshold=0.70,
            max_probability_gap=0.20,
            enabled=True,
        )
    )
    inference = InferenceService(manager=manager)
    diagnosed = inference.diagnose_prepared_records(prepared_dataset_id=ped, record_ids=[rid])
    direct_api = inference.diagnose_one(
        equipment_type="high_pressure_descaling_pump",
        features=record["features"],
        record_id=rid,
    )
    direct = predict_bundle(manager.require("high_pressure_descaling_pump").bundle, [record["features"]])[0]
    assert diagnosed["results"][0]["model_bundle_id"] == active_id
    assert direct_api["model_bundle_id"] == active_id
    assert diagnosed["results"][0]["model_bundle_id"] == direct_api["model_bundle_id"]
    assert diagnosed["results"][0]["binary_classifier"]["probabilities"] == direct_api["binary_classifier"]["probabilities"]
    assert diagnosed["results"][0]["multiclass_classifier"]["probabilities"] == direct_api["multiclass_classifier"]["probabilities"]
    result = diagnosed["results"][0]
    print(
        json.dumps(
            {
                "record_id": result["record_id"],
                "equipment_type": result["equipment_type"],
                "model_bundle_id": result["model_bundle_id"],
                "prepared_dataset_id": diagnosed["prepared_dataset_id"],
                "dataset_revision_id": diagnosed["dataset_revision_id"],
                "binary": result["binary_classifier"]["probabilities"],
                "multiclass": result["multiclass_classifier"]["probabilities"],
                "consistency": result["consistency"],
                "decision": result["decision"],
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    assert result["binary_classifier"]["probabilities"] == {
        k: float(v) for k, v in direct["binary_proba"].items()
    }
    assert result["multiclass_classifier"]["probabilities"] == {
        k: float(v) for k, v in direct["multiclass_proba"].items()
    }

    print("=== Step6 idempotent republish ===")
    again = publish_from_l1_prepared(l1_version)
    assert again["reused"] is True
    assert again["prepared_dataset_id"] == ped
    print("OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
