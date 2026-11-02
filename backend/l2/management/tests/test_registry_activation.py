from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from l2.management.database import connect
from l2.management.errors import ManagementError
from l2.management.registry import get_active_model, get_model, list_models, register_bundle
from l2.training import predict_bundle


def test_register_existing_bundle_idempotent(tmp_mgmt):
    service = tmp_mgmt["service"]
    first = service.bootstrap_models()
    assert first["count"] >= 1
    ids = {row["model_bundle_id"] for row in first["registered"]}
    assert "TR-0b062172078e" in ids
    second = service.bootstrap_models()
    listed = service.list_models_api(equipment_type="main_mill_motor")
    assert listed["total"] == first["count"]
    assert second["count"] == first["count"]
    # 登记不自动激活
    active = service.active_status()
    assert active["active_model_bundle_id"] is None


def test_reject_incomplete_bundle(tmp_mgmt):
    config = tmp_mgmt["config"]
    bad = tmp_mgmt["tmp_path"] / "models" / "main_mill_motor" / "TR-badbundle001"
    shutil.copytree(tmp_mgmt["bundle_a"], bad)
    (bad / "manifest.json").unlink()
    connection = connect(config.db_path)
    try:
        with pytest.raises(ManagementError) as exc:
            register_bundle(connection, config, bad, expected_equipment_type="main_mill_motor")
        assert exc.value.error_code == "MODEL_BUNDLE_INVALID"
    finally:
        connection.close()


def test_activate_and_rollback_switches_runtime(tmp_mgmt):
    service = tmp_mgmt["service"]
    manager = tmp_mgmt["manager"]
    service.bootstrap_models()

    # 首次激活 A
    result_a = service.activate("TR-0b062172078e")
    assert result_a["activated_model_bundle_id"] == "TR-0b062172078e"
    assert result_a["runtime_matches_active"] is True
    assert manager.require().bundle.manifest["model_bundle_id"] == "TR-0b062172078e"

    # 登记并激活 B
    connection = connect(tmp_mgmt["config"].db_path)
    try:
        register_bundle(
            connection,
            tmp_mgmt["config"],
            tmp_mgmt["bundle_b"],
            expected_equipment_type="main_mill_motor",
        )
        connection.commit()
    finally:
        connection.close()

    pred_before = predict_bundle(manager.require().bundle, [_zero_features(manager)])[0]
    result_b = service.activate("TR-testbundle02")
    assert result_b["previous_model_bundle_id"] == "TR-0b062172078e"
    assert manager.require().bundle.manifest["model_bundle_id"] == "TR-testbundle02"

    connection = connect(tmp_mgmt["config"].db_path)
    try:
        assert get_model(connection, "TR-0b062172078e")["status"] == "archived"
        listed = list_models(connection, status="active")
        assert listed["total"] == 1
    finally:
        connection.close()

    # 回滚
    rolled = service.rollback(
        equipment_type="main_mill_motor",
        target_model_bundle_id="TR-0b062172078e",
    )
    assert rolled["activated_model_bundle_id"] == "TR-0b062172078e"
    assert manager.require().bundle.manifest["model_bundle_id"] == "TR-0b062172078e"
    pred_after = predict_bundle(manager.require().bundle, [_zero_features(manager)])[0]
    assert pred_after["model_bundle_id"] == "TR-0b062172078e"
    assert pred_before["model_bundle_id"] == "TR-0b062172078e"

    events = service.list_events_api()
    types = {item["event_type"] for item in events["events"]}
    assert "model_activated" in types
    assert "model_rollback" in types


def test_activation_load_failure_keeps_old(tmp_mgmt):
    service = tmp_mgmt["service"]
    manager = tmp_mgmt["manager"]
    service.bootstrap_models()
    service.activate("TR-0b062172078e")
    old_id = manager.require().bundle.manifest["model_bundle_id"]

    connection = connect(tmp_mgmt["config"].db_path)
    try:
        register_bundle(
            connection,
            tmp_mgmt["config"],
            tmp_mgmt["bundle_b"],
            expected_equipment_type="main_mill_motor",
        )
        connection.commit()
    finally:
        connection.close()

    # 破坏 B 的模型文件
    (tmp_mgmt["bundle_b"] / "binary_model.joblib").write_bytes(b"broken")

    with pytest.raises(ManagementError) as exc:
        service.activate("TR-testbundle02")
    assert exc.value.error_code in {"MODEL_ACTIVATION_FAILED", "MODEL_BUNDLE_INVALID"}
    assert manager.require().bundle.manifest["model_bundle_id"] == old_id
    connection = connect(tmp_mgmt["config"].db_path)
    try:
        assert get_active_model(connection, "main_mill_motor")["model_bundle_id"] == old_id
        assert get_model(connection, "TR-testbundle02")["status"] == "candidate"
    finally:
        connection.close()


def test_already_active(tmp_mgmt):
    service = tmp_mgmt["service"]
    service.bootstrap_models()
    service.activate("TR-0b062172078e")
    with pytest.raises(ManagementError) as exc:
        service.activate("TR-0b062172078e")
    assert exc.value.error_code == "MODEL_ALREADY_ACTIVE"


def _zero_features(manager) -> dict[str, float]:
    cols = manager.require().bundle.feature_schema["feature_columns"]
    return {name: float(i + 1) * 0.01 for i, name in enumerate(cols)}
