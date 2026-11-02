from __future__ import annotations

import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[4]
BUNDLE = ROOT / "models" / "main_mill_motor" / "TR-0b062172078e"
FEATURES_CSV = ROOT / "data" / "l2_dev" / "main_mill_motor" / "features.csv"

FEATURE_COLUMNS = [
    "转速(rpm)",
    "电机电流(A)",
    "X向振动(mm/s)",
    "Y向振动(mm/s)",
    "驱动端轴承温度(°C)",
    "非驱动端轴承温度(°C)",
    "包络谱峰值(g)",
    "BPFO幅值(g)",
    "BPFI幅值(g)",
    "BSF幅值(g)",
    "啮合频率幅值(g)",
    "1X转频幅值(mm/s)",
]


@pytest.fixture(scope="module")
def client():
    os.environ["STARTUP_TOOL_SELFTEST"] = "0"
    os.environ["L2_INFERENCE_ENABLED"] = "1"
    os.environ["L2_EQUIPMENT_TYPE"] = "main_mill_motor"
    os.environ["L2_MODEL_BUNDLE_ID"] = "TR-0b062172078e"
    os.environ["L2_MODEL_BUNDLE_PATH"] = str(BUNDLE)
    from app.main import app
    from l2.inference.model_manager import MODEL_MANAGER

    with TestClient(app) as test_client:
        assert MODEL_MANAGER.is_ready()
        yield test_client


def _sample_features() -> dict[str, float]:
    import pandas as pd

    row = pd.read_csv(FEATURES_CSV, encoding="utf-8-sig").iloc[0]
    return {name: float(row[name]) for name in FEATURE_COLUMNS}


def test_health_and_model(client: TestClient):
    health = client.get("/l2/health")
    assert health.status_code == 200
    body = health.json()
    assert body["model_loaded"] is True
    assert body["model_bundle_id"] == "TR-0b062172078e"
    model = client.get("/l2/model")
    assert model.status_code == 200
    info = model.json()
    assert info["feature_count"] == 12
    assert info["model_status"] == "candidate"
    assert info["calibration_status"]["binary"] == "applied"


def test_model_loaded_once(client: TestClient):
    from l2.inference.model_manager import MODEL_MANAGER

    before = MODEL_MANAGER.load_count
    client.post(
        "/l2/diagnose",
        json={"equipment_type": "main_mill_motor", "record_id": "R1", "features": _sample_features()},
    )
    client.post(
        "/l2/diagnose",
        json={"equipment_type": "main_mill_motor", "record_id": "R2", "features": _sample_features()},
    )
    assert MODEL_MANAGER.load_count == before


def test_diagnose_ok_and_probabilities(client: TestClient):
    response = client.post(
        "/l2/diagnose",
        json={"equipment_type": "main_mill_motor", "record_id": "REC-1", "features": _sample_features()},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["record_id"] == "REC-1"
    assert body["model_bundle_id"] == "TR-0b062172078e"
    binary = body["binary_classifier"]["probabilities"]
    multi = body["multiclass_classifier"]["probabilities"]
    assert abs(sum(binary.values()) - 1.0) < 1e-5
    assert abs(sum(multi.values()) - 1.0) < 1e-5
    assert all(0.0 <= v <= 1.0 for v in binary.values())
    assert all(0.0 <= v <= 1.0 for v in multi.values())
    assert body["decision"]["screening_result"] in {"NORMAL", "ABNORMAL", "CONFLICT", "UNCERTAIN"}
    assert "inference_time_ms" in body


def test_validation_errors(client: TestClient):
    features = _sample_features()
    missing = dict(features)
    missing.pop("转速(rpm)")
    bad = client.post("/l2/diagnose", json={"equipment_type": "main_mill_motor", "features": missing})
    assert bad.status_code == 422
    assert bad.json()["error_code"] == "MISSING_FEATURES"

    extra = dict(features)
    extra["未知字段"] = 1.0
    bad2 = client.post("/l2/diagnose", json={"equipment_type": "main_mill_motor", "features": extra})
    assert bad2.status_code == 422
    assert bad2.json()["error_code"] == "UNKNOWN_FEATURES"

    stringed = dict(features)
    stringed["电机电流(A)"] = "abc"
    bad3 = client.post("/l2/diagnose", json={"equipment_type": "main_mill_motor", "features": stringed})
    assert bad3.status_code == 422

    leaked = dict(features)
    leaked["故障类型"] = "正常"
    bad4 = client.post("/l2/diagnose", json={"equipment_type": "main_mill_motor", "features": leaked})
    assert bad4.status_code == 422
    assert bad4.json()["error_code"] == "LEAKAGE_FIELDS_FORBIDDEN"

    naned = dict(features)
    naned["包络谱峰值(g)"] = float("nan")
    # JSON 不能直接传 nan，用字符串路径测 null
    nulled = dict(features)
    nulled["包络谱峰值(g)"] = None
    bad5 = client.post("/l2/diagnose", json={"equipment_type": "main_mill_motor", "features": nulled})
    assert bad5.status_code == 422

    unsupported = client.post(
        "/l2/diagnose",
        json={"equipment_type": "gearbox", "features": features},
    )
    assert unsupported.status_code == 400
    assert unsupported.json()["error_code"] == "UNSUPPORTED_EQUIPMENT_TYPE"


def test_feature_order_invariant(client: TestClient):
    features = _sample_features()
    shuffled = {name: features[name] for name in reversed(FEATURE_COLUMNS)}
    a = client.post("/l2/diagnose", json={"equipment_type": "main_mill_motor", "features": features}).json()
    b = client.post("/l2/diagnose", json={"equipment_type": "main_mill_motor", "features": shuffled}).json()
    assert a["binary_classifier"]["predicted_label"] == b["binary_classifier"]["predicted_label"]
    assert a["multiclass_classifier"]["predicted_label"] == b["multiclass_classifier"]["predicted_label"]
    for key, value in a["binary_classifier"]["probabilities"].items():
        assert value == pytest.approx(b["binary_classifier"]["probabilities"][key], abs=1e-9)


def test_batch_and_limit(client: TestClient):
    features = _sample_features()
    records = [{"record_id": f"R{i}", "features": features} for i in range(5)]
    response = client.post("/l2/diagnose/batch", json={"equipment_type": "main_mill_motor", "records": records})
    assert response.status_code == 200
    body = response.json()
    assert body["count"] == 5
    assert [item["record_id"] for item in body["results"]] == [f"R{i}" for i in range(5)]

    too_many = [{"record_id": f"R{i}", "features": features} for i in range(201)]
    overflow = client.post("/l2/diagnose/batch", json={"equipment_type": "main_mill_motor", "records": too_many})
    assert overflow.status_code == 400
    assert overflow.json()["error_code"] == "BATCH_TOO_LARGE"

    # 整批严格：一条坏记录导致整批失败
    bad_records = [
        {"record_id": "ok", "features": features},
        {"record_id": "bad", "features": {**features, "转速(rpm)": None}},
    ]
    strict = client.post("/l2/diagnose/batch", json={"equipment_type": "main_mill_motor", "records": bad_records})
    assert strict.status_code == 422
    assert strict.json()["details"].get("index") == 1


def test_api_matches_training_core(client: TestClient):
    from l2.training import load_model_bundle, predict_bundle

    features = _sample_features()
    direct = predict_bundle(load_model_bundle(BUNDLE), [features])[0]
    api = client.post(
        "/l2/diagnose",
        json={"equipment_type": "main_mill_motor", "record_id": "CMP", "features": features},
    ).json()
    assert api["model_bundle_id"] == direct["model_bundle_id"]
    assert api["binary_classifier"]["predicted_label"] == direct["binary_label"]
    assert api["multiclass_classifier"]["predicted_label"] == direct["multiclass_label"]
    for key, value in direct["binary_proba"].items():
        assert api["binary_classifier"]["probabilities"][key] == pytest.approx(value, abs=1e-9)
    for key, value in direct["multiclass_proba"].items():
        assert api["multiclass_classifier"]["probabilities"][key] == pytest.approx(value, abs=1e-9)
    assert api["calibration_status"] == direct["calibration_status"]


def test_batch_matches_single(client: TestClient):
    import pandas as pd

    rows = pd.read_csv(FEATURES_CSV, encoding="utf-8-sig").head(8)
    records = []
    singles = []
    for _, row in rows.iterrows():
        features = {name: float(row[name]) for name in FEATURE_COLUMNS}
        record_id = str(row["记录编号"])
        records.append({"record_id": record_id, "features": features})
        singles.append(
            client.post(
                "/l2/diagnose",
                json={"equipment_type": "main_mill_motor", "record_id": record_id, "features": features},
            ).json()
        )
    batch = client.post("/l2/diagnose/batch", json={"equipment_type": "main_mill_motor", "records": records}).json()
    assert len(batch["results"]) == len(singles)
    for left, right in zip(batch["results"], singles):
        assert left["record_id"] == right["record_id"]
        assert left["binary_classifier"]["predicted_label"] == right["binary_classifier"]["predicted_label"]
        for key, value in left["binary_classifier"]["probabilities"].items():
            assert value == pytest.approx(right["binary_classifier"]["probabilities"][key], abs=1e-9)
        for key, value in left["multiclass_classifier"]["probabilities"].items():
            assert value == pytest.approx(right["multiclass_classifier"]["probabilities"][key], abs=1e-9)


def test_missing_bundle_fails_fast(tmp_path: Path):
    os.environ["STARTUP_TOOL_SELFTEST"] = "0"
    os.environ["L2_INFERENCE_ENABLED"] = "1"
    os.environ["L2_MODEL_BUNDLE_PATH"] = str(tmp_path / "missing-bundle")
    os.environ["L2_MODEL_BUNDLE_ID"] = "TR-missing"
    # 重新加载配置与管理器状态
    from l2.inference.config import load_config
    from l2.inference.model_manager import ModelManager

    manager = ModelManager()
    with pytest.raises(FileNotFoundError):
        manager.load(load_config())
