from __future__ import annotations

import json

from l2.management.database import connect
from l2.management.errors import ManagementError
from l2.management.jobs import create_training_job, get_job
from l2.management.registry import register_bundle


def test_create_training_job_records_retrain_lineage(tmp_mgmt):
    config = tmp_mgmt["config"]
    service = tmp_mgmt["service"]
    bundle_a = tmp_mgmt["bundle_a"]

    connection = connect(config.db_path)
    try:
        registered = register_bundle(
            connection,
            config,
            bundle_a,
            expected_equipment_type="main_mill_motor",
            force_status="candidate",
        )
        connection.commit()
        parent_id = registered["model_bundle_id"]

        job = create_training_job(
            connection,
            config,
            equipment_type="main_mill_motor",
            dataset_id="main_mill_motor_dev",
            parent_model_bundle_id=parent_id,
        )
        connection.commit()
        assert job["train_mode"] == "retrain"
        assert job["parent_model_bundle_id"] == parent_id
        cfg = json.loads(job["training_config_json"])
        assert cfg["train_mode"] == "retrain"
        assert cfg["parent_model_bundle_id"] == parent_id

        fetched = get_job(connection, job["job_id"])
        assert fetched["train_mode"] == "retrain"
        assert fetched["parent_model_bundle_id"] == parent_id
    finally:
        connection.close()

    created = service.submit_train_job(
        equipment_type="main_mill_motor",
        dataset_id="main_mill_motor_dev",
        parent_model_bundle_id=parent_id,
    )
    assert created["train_mode"] == "retrain"
    assert created["parent_model_bundle_id"] == parent_id
    assert created["status"] == "queued"

    plain = service.submit_train_job(
        equipment_type="main_mill_motor",
        dataset_id="main_mill_motor_dev",
    )
    assert plain["train_mode"] == "new"
    assert plain["parent_model_bundle_id"] is None


def test_retrain_rejects_missing_or_mismatched_parent(tmp_mgmt):
    service = tmp_mgmt["service"]
    config = tmp_mgmt["config"]
    bundle_a = tmp_mgmt["bundle_a"]

    connection = connect(config.db_path)
    try:
        registered = register_bundle(
            connection,
            config,
            bundle_a,
            expected_equipment_type="main_mill_motor",
            force_status="candidate",
        )
        connection.commit()
        parent_id = registered["model_bundle_id"]
    finally:
        connection.close()

    try:
        service.submit_train_job(
            equipment_type="main_mill_motor",
            dataset_id="main_mill_motor_dev",
            parent_model_bundle_id="TR-does-not-exist",
        )
        assert False, "missing parent should fail"
    except ManagementError as exc:
        assert exc.status_code == 400
        assert exc.error_code == "PARENT_MODEL_INVALID"

    try:
        service.submit_train_job(
            equipment_type="high_pressure_descaling_pump",
            dataset_id="main_mill_motor_dev",
            parent_model_bundle_id=parent_id,
        )
        assert False, "equipment mismatch should fail"
    except ManagementError as exc:
        assert exc.status_code == 400
        assert exc.error_code == "PARENT_MODEL_INVALID"
