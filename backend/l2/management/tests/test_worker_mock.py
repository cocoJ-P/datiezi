from __future__ import annotations

import os
from unittest.mock import patch

from l2.management.database import connect
from l2.management.jobs import claim_next_job, create_training_job, get_job
from l2.management.registry import list_models
from l2.management.worker import process_one_job


def test_failed_training_does_not_register(tmp_mgmt):
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
        claimed = claim_next_job(connection, worker_pid=os.getpid())
        assert claimed is not None
    finally:
        connection.close()

    with patch("l2.management.worker._run_training_subprocess", return_value=(1, "", "boom")):
        connection = connect(config.db_path)
        try:
            process_one_job(connection, config, claimed)
        finally:
            connection.close()

    connection = connect(config.db_path)
    try:
        final = get_job(connection, claimed["job_id"])
        assert final["status"] == "failed"
        assert final["error_code"] == "TRAINING_FAILED"
        models = list_models(connection)
        # bootstrap 未调用，失败训练不得新增模型
        assert models["total"] == 0
    finally:
        connection.close()
