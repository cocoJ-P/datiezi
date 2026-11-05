from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[4]
BUNDLE = ROOT / "models" / "main_mill_motor" / "TR-0b062172078e"


@pytest.fixture()
def client(tmp_path, monkeypatch):
    db_path = tmp_path / "api_mgmt.sqlite3"
    logs = tmp_path / "logs"
    logs.mkdir()
    monkeypatch.setenv("STARTUP_TOOL_SELFTEST", "0")
    monkeypatch.setenv("L2_INFERENCE_ENABLED", "1")
    monkeypatch.setenv("L2_EQUIPMENT_TYPE", "main_mill_motor")
    monkeypatch.setenv("L2_MODEL_BUNDLE_ID", "TR-0b062172078e")
    monkeypatch.setenv("L2_MODEL_BUNDLE_PATH", str(BUNDLE))
    monkeypatch.setenv("L2_MANAGEMENT_DB", str(db_path))
    monkeypatch.setenv("L2_TRAINING_LOGS_ROOT", str(logs))
    monkeypatch.setenv("L2_MODELS_ROOT", str(ROOT / "models"))

    from l2.inference.model_manager import MODEL_MANAGER
    from l2.management.config import load_management_config
    from l2.management.service import ManagementService
    import l2.management.router as router_mod
    import l2.management.service as svc_mod

    svc = ManagementService(config=load_management_config(), manager=MODEL_MANAGER)
    svc_mod.MANAGEMENT_SERVICE = svc
    router_mod.MANAGEMENT_SERVICE = svc

    from app.main import app

    with TestClient(app) as test_client:
        yield test_client, svc


def test_datasets_and_write_without_token(client):
    test_client, _svc = client
    datasets = test_client.get("/l2/management/datasets")
    assert datasets.status_code == 200
    assert any(item["dataset_id"] == "main_mill_motor_dev" for item in datasets.json()["datasets"])

    bad_dataset = test_client.post(
        "/l2/management/train/jobs",
        json={"equipment_type": "main_mill_motor", "dataset_id": "not_exists"},
    )
    assert bad_dataset.status_code == 404
    assert bad_dataset.json()["error_code"] == "DATASET_NOT_FOUND"

    ok = test_client.post(
        "/l2/management/train/jobs",
        json={"equipment_type": "main_mill_motor", "dataset_id": "main_mill_motor_dev"},
    )
    assert ok.status_code == 200
    body = ok.json()
    assert body["status"] == "queued"
    assert body["job_id"].startswith("JOB-")

    missing = test_client.get("/l2/management/models/TR-does-not-exist")
    assert missing.status_code == 404
    assert missing.json()["error_code"] == "MODEL_NOT_FOUND"

    bootstrap = test_client.post("/l2/management/bootstrap")
    assert bootstrap.status_code == 200
    assert bootstrap.json()["count"] >= 1

    activate = test_client.post("/l2/management/models/TR-0b062172078e/activate")
    assert activate.status_code == 200
    assert activate.json()["activated_model_bundle_id"] == "TR-0b062172078e"

    active = test_client.get("/l2/management/active?equipment_type=main_mill_motor")
    assert active.status_code == 200
    assert active.json()["active_model_bundle_id"] == "TR-0b062172078e"
    assert active.json()["consistent"] is True
    all_active = test_client.get("/l2/management/active")
    assert all_active.status_code == 200
    assert "equipment" in all_active.json()

    logs = test_client.get(f"/l2/management/train/jobs/{body['job_id']}/logs?max_lines=10")
    assert logs.status_code == 200
    assert logs.json()["max_lines"] == 10
