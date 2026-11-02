"""Active 模型版本一致性：普通诊断与 PED 诊断必须共用同一 Runtime ModelBundle。"""

from __future__ import annotations

import json
import math
import shutil
from pathlib import Path

import pandas as pd
import pytest

from l2.equipment.registry import get_equipment_spec
from l2.inference.config import L2InferenceConfig
from l2.inference.model_manager import ModelManager
from l2.inference.service import InferenceService
from l2.management.config import ManagementConfig
from l2.management.database import connect, init_db
from l2.management.errors import ManagementError
from l2.management.registry import get_active_model, register_bundle
from l2.management.service import ManagementService
from l2.prepared.adapter import PreparedDatasetAdapter
from l2.prepared.contract import PreparedManifest, sha256_file, utc_now

ROOT = Path(__file__).resolve().parents[4]
EQ = "high_pressure_descaling_pump"
SRC_A = ROOT / "models" / EQ / "TR-afb202b407bf"
SRC_B = ROOT / "models" / EQ / "TR-0410fbe70876"
SRC_PED = ROOT / "data" / "runtime" / "l2_prepared" / EQ / "PED-862bf41ee218"
RECORD_ID = "REC-DP-20260314-00001"
TRAINING_CONFIG = ROOT / "backend" / "l2" / "training" / "config" / "high_pressure_descaling_pump.yaml"


def _approx_equal(a: dict, b: dict) -> bool:
    keys = set(a) | set(b)
    return all(math.isclose(float(a[k]), float(b[k]), abs_tol=1e-10, rel_tol=1e-9) for k in keys)


def _max_diff(a: dict, b: dict) -> float:
    return max(abs(float(a[k]) - float(b[k])) for k in set(a) | set(b))


def _write_minimal_ped(prepared_root: Path, *, source_ped: Path, record_id: str) -> str:
    """从真实 PED 抽一条记录，写入临时不可变快照。"""
    ped_id = "PED-testactive01"
    out = prepared_root / EQ / ped_id
    out.mkdir(parents=True)
    spec = get_equipment_spec(EQ)
    features = pd.read_csv(source_ped / "features.csv", encoding="utf-8-sig")
    labels = pd.read_csv(source_ped / "labels.csv", encoding="utf-8-sig")
    metadata = pd.read_csv(source_ped / "metadata.csv", encoding="utf-8-sig")
    id_col = spec.record_id_field
    features = features[features[id_col].astype(str) == record_id].copy()
    labels = labels[labels[id_col].astype(str) == record_id].copy()
    metadata = metadata[metadata["record_id"].astype(str) == record_id].copy()
    assert len(features) == 1, f"源 PED 缺少记录 {record_id}"
    features.to_csv(out / "features.csv", index=False, encoding="utf-8-sig")
    labels.to_csv(out / "labels.csv", index=False, encoding="utf-8-sig")
    metadata.to_csv(out / "metadata.csv", index=False, encoding="utf-8-sig")
    schema = json.loads((source_ped / "feature_schema.json").read_text(encoding="utf-8"))
    (out / "feature_schema.json").write_text(json.dumps(schema, ensure_ascii=False, indent=2), encoding="utf-8")
    validation = {"excluded_record_count": 0, "warnings": [], "content_key": "test-active-consistency"}
    (out / "validation_report.json").write_text(json.dumps(validation, ensure_ascii=False, indent=2), encoding="utf-8")
    manifest = PreparedManifest(
        prepared_dataset_id=ped_id,
        dataset_id="DS-test-active",
        revision_id="rev-test-active",
        equipment_type=EQ,
        equipment_group_id=None,
        schema_version=spec.schema_version,
        source_stage="P8",
        source_revision_ids={"l1_prepared_version_id": "PD-test"},
        created_at=utc_now(),
        record_count=1,
        trainable_record_count=1,
        feature_count=spec.feature_count,
        label_count=1,
        class_labels=list(spec.class_labels),
        release_status="ready",
        release_level="development",
        training_ready=True,
        inference_ready=True,
        source_type="l1_prepared",
        l1_prepared_version_id="PD-test",
        validation_summary=validation,
        content_hashes={
            "features.csv": sha256_file(out / "features.csv"),
            "labels.csv": sha256_file(out / "labels.csv"),
            "metadata.csv": sha256_file(out / "metadata.csv"),
            "content_key": "test-active-consistency",
        },
        artifact_files={
            "features": "features.csv",
            "labels": "labels.csv",
            "metadata": "metadata.csv",
        },
    )
    manifest.write(out / "manifest.json")
    index = [
        {
            **manifest.to_dict(),
            "manifest_hash": manifest.manifest_hash(),
            "bundle_dir": str(out),
        }
    ]
    (prepared_root / "index.json").write_text(json.dumps(index, ensure_ascii=False, indent=2), encoding="utf-8")
    return ped_id


@pytest.fixture()
def active_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    if not SRC_A.is_dir() or not SRC_B.is_dir():
        pytest.skip("缺少泵模型包副本")
    if not SRC_PED.is_dir():
        pytest.skip("缺少真实 PED-862bf41ee218 快照")

    prepared_root = tmp_path / "l2_prepared"
    monkeypatch.setenv("L2_PREPARED_ROOT", str(prepared_root))
    ped_id = _write_minimal_ped(prepared_root, source_ped=SRC_PED, record_id=RECORD_ID)

    models_root = tmp_path / "models"
    eq_dir = models_root / EQ
    eq_dir.mkdir(parents=True)
    bundle_a = eq_dir / "TR-afb202b407bf"
    bundle_b = eq_dir / "TR-0410fbe70876"
    shutil.copytree(SRC_A, bundle_a)
    shutil.copytree(SRC_B, bundle_b)

    config = ManagementConfig(
        db_path=tmp_path / "mgmt.sqlite3",
        models_root=models_root,
        logs_root=tmp_path / "logs",
        datasets={},
        training_config_path=TRAINING_CONFIG,
        poll_interval_sec=0.1,
        management_token="tok",
    )
    init_db(config.db_path)
    manager = ModelManager()
    service = ManagementService(config=config, manager=manager)
    inference = InferenceService(manager=manager)

    connection = connect(config.db_path)
    try:
        register_bundle(connection, config, bundle_a, expected_equipment_type=EQ, force_status="candidate")
        register_bundle(connection, config, bundle_b, expected_equipment_type=EQ, force_status="candidate")
        connection.commit()
    finally:
        connection.close()

    # 初始加载 A（与首次激活一致）
    manager.load_equipment(
        EQ,
        L2InferenceConfig(
            equipment_type=EQ,
            model_bundle_id="TR-afb202b407bf",
            model_bundle_path=bundle_a,
            max_batch_size=200,
            clear_probability_threshold=0.80,
            fault_top1_threshold=0.70,
            max_probability_gap=0.20,
            enabled=True,
        ),
    )
    service.activate("TR-afb202b407bf")

    features = PreparedDatasetAdapter(ped_id).get_record(RECORD_ID)["features"]
    return {
        "service": service,
        "manager": manager,
        "inference": inference,
        "config": config,
        "ped_id": ped_id,
        "features": features,
        "bundle_a": bundle_a,
        "bundle_b": bundle_b,
    }


def _assert_paths_agree(env, *, expected_id: str) -> tuple[dict, dict]:
    service = env["service"]
    manager = env["manager"]
    inference = env["inference"]
    ped_id = env["ped_id"]
    features = env["features"]

    active = service.active_status(EQ)
    runtime_id = manager.require(EQ).bundle.manifest["model_bundle_id"]
    model_api_id = manager.model_info(EQ)["model_bundle_id"]
    direct = inference.diagnose_one(equipment_type=EQ, features=features, record_id=RECORD_ID)
    ped = inference.diagnose_prepared_records(prepared_dataset_id=ped_id, record_ids=[RECORD_ID])["results"][0]

    assert active["active_model_bundle_id"] == expected_id
    assert active["runtime_model_bundle_id"] == expected_id
    assert runtime_id == expected_id
    assert model_api_id == expected_id
    assert direct["model_bundle_id"] == expected_id
    assert ped["model_bundle_id"] == expected_id
    assert _approx_equal(
        direct["binary_classifier"]["probabilities"],
        ped["binary_classifier"]["probabilities"],
    )
    assert _approx_equal(
        direct["multiclass_classifier"]["probabilities"],
        ped["multiclass_classifier"]["probabilities"],
    )
    assert direct["binary_classifier"]["predicted_label"] == ped["binary_classifier"]["predicted_label"]
    assert (
        direct["multiclass_classifier"]["predicted_label"]
        == ped["multiclass_classifier"]["predicted_label"]
    )
    assert direct["consistency"] == ped["consistency"]
    assert direct["decision"] == ped["decision"]
    assert direct.get("calibration_status") == ped.get("calibration_status")
    # PED 可附加来源字段
    assert ped["prepared_dataset_id"] == ped_id
    assert ped["record_id"] == RECORD_ID
    return direct, ped


def test_same_active_direct_and_ped_agree(active_env):
    _assert_paths_agree(active_env, expected_id="TR-afb202b407bf")


def test_hot_switch_and_rollback_both_paths_follow_active(active_env):
    # A
    direct_a, _ = _assert_paths_agree(active_env, expected_id="TR-afb202b407bf")

    # 切换到 B
    result_b = active_env["service"].activate("TR-0410fbe70876")
    assert result_b["activated_model_bundle_id"] == "TR-0410fbe70876"
    direct_b, ped_b = _assert_paths_agree(active_env, expected_id="TR-0410fbe70876")
    assert direct_b["model_bundle_id"] != direct_a["model_bundle_id"]
    assert ped_b["model_bundle_id"] == "TR-0410fbe70876"

    # 回滚到 A
    rolled = active_env["service"].rollback(
        equipment_type=EQ,
        target_model_bundle_id="TR-afb202b407bf",
    )
    assert rolled["activated_model_bundle_id"] == "TR-afb202b407bf"
    direct_roll, ped_roll = _assert_paths_agree(active_env, expected_id="TR-afb202b407bf")
    assert direct_roll["model_bundle_id"] == "TR-afb202b407bf"
    assert ped_roll["model_bundle_id"] == "TR-afb202b407bf"
    # 即使概率偶然相同，也必须以 bundle id 证明切换成功
    assert {direct_a["model_bundle_id"], direct_b["model_bundle_id"], direct_roll["model_bundle_id"]} == {
        "TR-afb202b407bf",
        "TR-0410fbe70876",
    }


def test_ped_does_not_cache_stale_bundle_after_switch(active_env):
    inference = active_env["inference"]
    ped_id = active_env["ped_id"]
    before = inference.diagnose_prepared_records(prepared_dataset_id=ped_id, record_ids=[RECORD_ID])["results"][0]
    assert before["model_bundle_id"] == "TR-afb202b407bf"

    active_env["service"].activate("TR-0410fbe70876")
    after = inference.diagnose_prepared_records(prepared_dataset_id=ped_id, record_ids=[RECORD_ID])["results"][0]
    assert after["model_bundle_id"] == "TR-0410fbe70876"
    # 不应继续返回旧版本
    assert after["model_bundle_id"] != before["model_bundle_id"]


def test_failed_activation_keeps_old_on_both_paths(active_env):
    service = active_env["service"]
    manager = active_env["manager"]
    old_id = manager.require(EQ).bundle.manifest["model_bundle_id"]
    assert old_id == "TR-afb202b407bf"

    # 破坏 B 后激活应失败
    (active_env["bundle_b"] / "binary_model.joblib").write_bytes(b"broken")
    with pytest.raises(ManagementError) as exc:
        service.activate("TR-0410fbe70876")
    assert exc.value.error_code in {"MODEL_ACTIVATION_FAILED", "MODEL_BUNDLE_INVALID"}

    connection = connect(active_env["config"].db_path)
    try:
        assert get_active_model(connection, EQ)["model_bundle_id"] == old_id
    finally:
        connection.close()
    _assert_paths_agree(active_env, expected_id=old_id)


def test_probability_diffs_zero_under_same_active(active_env):
    direct, ped = _assert_paths_agree(active_env, expected_id="TR-afb202b407bf")
    assert _max_diff(direct["binary_classifier"]["probabilities"], ped["binary_classifier"]["probabilities"]) == 0.0
    assert (
        _max_diff(
            direct["multiclass_classifier"]["probabilities"],
            ped["multiclass_classifier"]["probabilities"],
        )
        == 0.0
    )
