"""模拟 L2 Workspace UI 关键读路径的真实 API 联调。"""

from __future__ import annotations

import json
import urllib.request

BASE = "http://127.0.0.1:8000"


def get(path: str):
    with urllib.request.urlopen(BASE + path) as resp:
        return json.loads(resp.read().decode("utf-8"))


def post(path: str, body: dict):
    req = urllib.request.Request(
        BASE + path,
        data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req) as resp:
        return json.loads(resp.read().decode("utf-8"))


def main() -> None:
    equipment = get("/l2/management/equipment-types")["equipment_types"]
    assert any(item["equipment_type"] == "high_pressure_descaling_pump" for item in equipment)
    assert any(item["equipment_type"] == "main_mill_motor" for item in equipment)
    datasets = get("/l2/management/datasets")["datasets"]
    ped = next(item for item in datasets if item.get("prepared_dataset_id") == "PED-862bf41ee218")
    rows = get("/api/l1/prepared-datasets/PED-862bf41ee218/rows?offset=0&limit=1")
    record_id = rows["rows"][0]["record_id"]
    diagnosed = post(
        "/l2/datasets/PED-862bf41ee218/diagnose",
        {"record_ids": [record_id], "revision_id": ped.get("revision_id")},
    )
    one = diagnosed["results"][0]
    active = get("/l2/management/active?equipment_type=high_pressure_descaling_pump")
    assert one["model_bundle_id"] == active["active_model_bundle_id"] == active["runtime_model_bundle_id"]
    print(
        json.dumps(
            {
                "equipment_count": len(equipment),
                "ped": ped["prepared_dataset_id"],
                "record_id": record_id,
                "model_bundle_id": one["model_bundle_id"],
                "binary": one["binary_classifier"]["probabilities"],
                "multiclass": one["multiclass_classifier"]["probabilities"],
                "decision": one["decision"],
                "calibration_status": one.get("calibration_status"),
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
