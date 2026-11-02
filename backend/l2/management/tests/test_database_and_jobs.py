from __future__ import annotations

import os

from l2.management.database import connect, init_db
from l2.management.jobs import (
    ALLOWED_TRANSITIONS,
    claim_next_job,
    create_training_job,
    get_job,
    mark_stale_running_jobs,
    update_job_status,
)


def test_init_db_idempotent(tmp_mgmt):
    config = tmp_mgmt["config"]
    init_db(config.db_path)
    connection = connect(config.db_path)
    try:
        create_training_job(
            connection,
            config,
            equipment_type="main_mill_motor",
            dataset_id="main_mill_motor_dev",
        )
        connection.commit()
        count1 = connection.execute("SELECT COUNT(*) AS c FROM training_jobs").fetchone()["c"]
    finally:
        connection.close()

    init_db(config.db_path)
    connection = connect(config.db_path)
    try:
        count2 = connection.execute("SELECT COUNT(*) AS c FROM training_jobs").fetchone()["c"]
    finally:
        connection.close()
    assert count1 == count2 == 1


def test_job_status_transitions(tmp_mgmt):
    config = tmp_mgmt["config"]
    connection = connect(config.db_path)
    try:
        job = create_training_job(
            connection,
            config,
            equipment_type="main_mill_motor",
            dataset_id="main_mill_motor_dev",
        )
        connection.commit()
        assert job["status"] == "queued"
        claimed = claim_next_job(connection, worker_pid=os.getpid())
        assert claimed is not None
        assert claimed["status"] == "running"
        # 并发：已有 running 时不能再领取
        again = claim_next_job(connection, worker_pid=os.getpid())
        assert again is None
        update_job_status(connection, claimed["job_id"], new_status="succeeded", model_bundle_id="TR-x")
        connection.commit()
        final = get_job(connection, claimed["job_id"])
        assert final["status"] == "succeeded"
        assert final["model_bundle_id"] == "TR-x"
        try:
            update_job_status(connection, claimed["job_id"], new_status="running")
            assert False, "不应允许 succeeded -> running"
        except ValueError:
            pass
        assert "running" in ALLOWED_TRANSITIONS["queued"]
    finally:
        connection.close()


def test_worker_interrupt_recovery(tmp_mgmt):
    config = tmp_mgmt["config"]
    connection = connect(config.db_path)
    try:
        job = create_training_job(
            connection,
            config,
            equipment_type="main_mill_motor",
            dataset_id="main_mill_motor_dev",
        )
        connection.commit()
        claimed = claim_next_job(connection, worker_pid=12345)
        assert claimed["status"] == "running"
        n = mark_stale_running_jobs(connection)
        connection.commit()
        assert n == 1
        recovered = get_job(connection, job["job_id"])
        assert recovered["status"] == "interrupted"
        assert recovered["error_code"] == "WORKER_INTERRUPTED"
    finally:
        connection.close()
