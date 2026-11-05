"""L1→L2 Automatic Handoff v1 测试。"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from l2.handoff.diagnosis_batch import run_batch_diagnosis
from l2.handoff.matcher import MATCHED, NO_ACTIVE_MODEL, match_active_for_ped
from l2.handoff.orchestrator import L1L2HandoffOrchestrator
from l2.handoff.store import HandoffStore
from l2.inference.config import L2InferenceConfig
from l2.inference.model_manager import MODEL_MANAGER, ModelManager
from l2.management.config import ManagementConfig
from l2.management.database import init_db
from l2.management.registry import get_active_model, list_models, register_bundle
from l2.management.service import ManagementService
from l2.prepared.publisher import publish_from_l1_prepared

ROOT = Path(__file__).resolve().parents[4]
PUMP_PD = "PD-4c2a075ce5f8"
PUMP_EQ = "high_pressure_descaling_pump"
PUMP_BUNDLE_SRC = ROOT / "models" / "high_pressure_descaling_pump" / "TR-0410fbe70876"
TRAINING_CONFIG = ROOT / "backend" / "l2" / "training" / "config" / "high_pressure_descaling_pump.yaml"


@pytest.fixture()
def handoff_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    prepared = tmp_path / "l2_prepared"
    monkeypatch.setenv("L2_PREPARED_ROOT", str(prepared))
    db_path = tmp_path / "handoff.sqlite3"
    store = HandoffStore(db_path)

    models_root = tmp_path / "models"
    eq_dir = models_root / PUMP_EQ
    eq_dir.mkdir(parents=True)
    bundle_dir = eq_dir / "TR-0410fbe70876"
    if PUMP_BUNDLE_SRC.is_dir():
        shutil.copytree(PUMP_BUNDLE_SRC, bundle_dir)

    mgmt_db = tmp_path / "mgmt.sqlite3"
    config = ManagementConfig(
        db_path=mgmt_db,
        models_root=models_root,
        logs_root=tmp_path / "logs",
        datasets={},
        training_config_path=TRAINING_CONFIG,
        poll_interval_sec=0.1,
    )
    init_db(mgmt_db)
    manager = ModelManager()
    service = ManagementService(config=config, manager=manager)
    monkeypatch.setattr("l2.management.service.MANAGEMENT_SERVICE", service)
    monkeypatch.setattr("l2.handoff.matcher.MANAGEMENT_SERVICE", service)
    monkeypatch.setattr("l2.handoff.matcher.MODEL_MANAGER", manager)
    monkeypatch.setattr("l2.handoff.diagnosis_batch.MODEL_MANAGER", manager)
    monkeypatch.setattr("l2.inference.model_manager.MODEL_MANAGER", manager)

    orch = L1L2HandoffOrchestrator(store=store)
    return {
        "store": store,
        "orch": orch,
        "service": service,
        "manager": manager,
        "config": config,
        "bundle_dir": bundle_dir,
        "tmp_path": tmp_path,
    }


def test_enqueue_only_after_successful_prepare(handoff_env):
    orch = handoff_env["orch"]
    blocked = orch.enqueue_after_prepare(
        dataset_id="ds-blocked",
        pd_version_id="PD-blocked00001",
        prepare_run_id="prep-1",
        prepare_status="needs_review",
    )
    assert blocked is not None
    assert blocked["status"] == "dataset_not_ready"

    job = orch.enqueue_after_prepare(
        dataset_id="ds-ok",
        pd_version_id="PD-ok0000000001",
        prepare_run_id="prep-2",
        prepare_status="completed",
    )
    assert job["status"] == "queued"
    again = orch.enqueue_after_prepare(
        dataset_id="ds-ok",
        pd_version_id="PD-ok0000000001",
        prepare_run_id="prep-2",
        prepare_status="completed",
    )
    assert again["handoff_id"] == job["handoff_id"]


def test_recoverable_jobs_survive_restart(handoff_env):
    orch = handoff_env["orch"]
    store = handoff_env["store"]
    job = orch.enqueue_after_prepare(
        dataset_id="ds-r",
        pd_version_id="PD-recover00001",
        prepare_run_id="prep-r",
        prepare_status="completed",
    )
    store.update_job(job["handoff_id"], status="inference_running")
    # 新 store 实例模拟重启
    store2 = HandoffStore(handoff_env["tmp_path"] / "handoff.sqlite3")
    orch2 = L1L2HandoffOrchestrator(store=store2)
    n = orch2.recover_pending()
    assert n >= 1
    recovered = store2.get_job(job["handoff_id"])
    assert recovered["status"] == "inference_queued"


@pytest.mark.skipif(not PUMP_BUNDLE_SRC.is_dir(), reason="缺少泵模型包")
def test_publish_idempotent_and_match_active(handoff_env):
    service = handoff_env["service"]
    manager = handoff_env["manager"]
    bundle_dir = handoff_env["bundle_dir"]
    connection = service.connection()
    try:
        registered = register_bundle(connection, service.config, bundle_dir)
        connection.commit()
        model_id = registered["model_bundle_id"]
    finally:
        connection.close()
    service.activate(model_id)

    first = publish_from_l1_prepared(PUMP_PD, equipment_type=PUMP_EQ)
    second = publish_from_l1_prepared(PUMP_PD, equipment_type=PUMP_EQ)
    assert second.get("reused") is True
    assert second["prepared_dataset_id"] == first["prepared_dataset_id"]

    match = match_active_for_ped(first)
    assert match["match_status"] == MATCHED
    assert match["model_bundle_id"] == model_id


@pytest.mark.skipif(not PUMP_BUNDLE_SRC.is_dir(), reason="缺少泵模型包")
def test_candidate_not_auto_inference(handoff_env):
    service = handoff_env["service"]
    bundle_dir = handoff_env["bundle_dir"]
    connection = service.connection()
    try:
        register_bundle(connection, service.config, bundle_dir)
        connection.commit()
        active = get_active_model(connection, PUMP_EQ)
        assert active is None
        models = list_models(connection, equipment_type=PUMP_EQ)
        assert models["total"] >= 1
        assert models["models"][0]["status"] == "candidate"
    finally:
        connection.close()

    published = publish_from_l1_prepared(PUMP_PD, equipment_type=PUMP_EQ)
    match = match_active_for_ped(published)
    assert match["match_status"] == NO_ACTIVE_MODEL
    assert match["candidates"]


@pytest.mark.skipif(not PUMP_BUNDLE_SRC.is_dir(), reason="缺少泵模型包")
def test_batch_diagnosis_reuses_and_counts(handoff_env):
    service = handoff_env["service"]
    manager = handoff_env["manager"]
    store = handoff_env["store"]
    bundle_dir = handoff_env["bundle_dir"]
    connection = service.connection()
    try:
        registered = register_bundle(connection, service.config, bundle_dir)
        connection.commit()
        model_id = registered["model_bundle_id"]
    finally:
        connection.close()
    service.activate(model_id)
    published = publish_from_l1_prepared(PUMP_PD, equipment_type=PUMP_EQ)

    # 缩小批次上限，验证分批
    cfg = L2InferenceConfig(
        equipment_type=PUMP_EQ,
        model_bundle_id=model_id,
        model_bundle_path=bundle_dir,
        max_batch_size=50,
        clear_probability_threshold=0.80,
        fault_top1_threshold=0.70,
        max_probability_gap=0.20,
        enabled=True,
    )
    manager.load_equipment(PUMP_EQ, cfg)

    run1 = run_batch_diagnosis(
        store,
        prepared_dataset_id=published["prepared_dataset_id"],
        model_bundle_id=model_id,
    )
    assert run1["status"] in {"completed", "completed_with_errors"}
    assert run1["total_records"] == run1["processed_records"]
    assert run1["model_bundle_id"] == model_id
    assert sum(run1["counts"].get(k, 0) for k in ("NORMAL", "ABNORMAL", "CONFLICT", "UNCERTAIN", "FAILED")) == run1[
        "processed_records"
    ]

    run2 = run_batch_diagnosis(
        store,
        prepared_dataset_id=published["prepared_dataset_id"],
        model_bundle_id=model_id,
    )
    assert run2.get("reused") is True
    assert run2["diagnosis_run_id"] == run1["diagnosis_run_id"]

    page = store.list_diagnosis_records(run1["diagnosis_run_id"], offset=0, limit=5)
    assert page["total"] == run1["total_records"]
    assert page["items"][0]["result"]["model_bundle_id"] == model_id
    # 推理结果不含标签泄漏字段
    assert "fault_type" not in (page["items"][0]["result"] or {})
    assert "labels" not in (page["items"][0]["result"] or {})


@pytest.mark.skipif(not PUMP_BUNDLE_SRC.is_dir(), reason="缺少泵模型包")
def test_no_auto_train_requires_confirm(handoff_env):
    orch = handoff_env["orch"]
    service = handoff_env["service"]
    bundle_dir = handoff_env["bundle_dir"]
    # 仅登记 Candidate，不激活
    connection = service.connection()
    try:
        register_bundle(connection, service.config, bundle_dir)
        connection.commit()
    finally:
        connection.close()

    # 使用真实 PD 走完整编排
    from l2.prepared.l1_source import get_l1_prepared_meta

    meta = get_l1_prepared_meta(PUMP_PD)
    job = orch.enqueue_after_prepare(
        dataset_id=meta.dataset_id,
        pd_version_id=PUMP_PD,
        prepare_run_id="prep-handoff",
        prepare_status="completed",
    )
    result = orch.process_job(job["handoff_id"])
    assert result["status"] == "training_confirmation_required"
    assert any(e["status"] == "training_confirmation_required" for e in result["equipment_items"])
    # 确认前不应有 training_job
    assert all(not e.get("training_job_id") for e in result["equipment_items"])

    confirmed = orch.confirm_train(result["handoff_id"])
    assert any(e.get("training_job_id") for e in confirmed["equipment_items"])
    assert confirmed["status"] in {"training_queued", "training_running", "dataset_not_ready", "failed"}


@pytest.mark.skipif(not PUMP_BUNDLE_SRC.is_dir(), reason="缺少泵模型包")
def test_skip_training_persists(handoff_env):
    orch = handoff_env["orch"]
    from l2.prepared.l1_source import get_l1_prepared_meta

    meta = get_l1_prepared_meta(PUMP_PD)
    job = orch.enqueue_after_prepare(
        dataset_id=meta.dataset_id,
        pd_version_id=PUMP_PD,
        prepare_run_id="prep-skip",
        prepare_status="completed",
    )
    result = orch.process_job(job["handoff_id"])
    assert result["status"] == "training_confirmation_required"
    skipped = orch.skip_training(result["handoff_id"])
    assert skipped["status"] == "skipped"
    # 再次 process 不应反复改回 confirmation（completed/skipped 早退）
    again = orch.process_job(skipped["handoff_id"])
    assert again["status"] == "skipped"


@pytest.mark.skipif(not PUMP_BUNDLE_SRC.is_dir(), reason="缺少泵模型包")
def test_end_to_end_with_active_auto_diagnose(handoff_env):
    orch = handoff_env["orch"]
    service = handoff_env["service"]
    manager = handoff_env["manager"]
    bundle_dir = handoff_env["bundle_dir"]
    connection = service.connection()
    try:
        registered = register_bundle(connection, service.config, bundle_dir)
        connection.commit()
        model_id = registered["model_bundle_id"]
    finally:
        connection.close()
    service.activate(model_id)

    from l2.prepared.l1_source import get_l1_prepared_meta

    meta = get_l1_prepared_meta(PUMP_PD)
    job = orch.enqueue_after_prepare(
        dataset_id=meta.dataset_id,
        pd_version_id=PUMP_PD,
        prepare_run_id="prep-e2e",
        prepare_status="completed",
    )
    result = orch.process_job(job["handoff_id"])
    assert result["status"] == "completed"
    item = result["equipment_items"][0]
    assert item["ped_id"]
    assert item["diagnosis_run_id"]
    assert item["model_bundle_id"] == model_id
    run = handoff_env["store"].get_diagnosis_run(item["diagnosis_run_id"])
    assert run["succeeded_records"] == run["total_records"]
    assert run["total_records"] > 0
