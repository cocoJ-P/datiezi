"""L2 Management 端到端冒烟：激活 → 训练 → 激活新模型 → 回滚。"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "backend") if (ROOT / "backend").is_dir() else str(ROOT))
# 当脚本位于 backend/l2/... 时 parents[3]=backend；兼容从项目根执行
if not (ROOT / "l2").is_dir() and (ROOT.parent / "backend" / "l2").is_dir():
    sys.path.insert(0, str(ROOT.parent / "backend"))

os.environ.setdefault("L2_MANAGEMENT_TOKEN", "local-dev-token")
os.environ.setdefault("PYTHONUTF8", "1")

from l2.inference.config import load_config
from l2.inference.model_manager import MODEL_MANAGER
from l2.management.service import ManagementService
from l2.management.worker import run_worker
from l2.training import predict_bundle


def main() -> int:
    service = ManagementService()
    cfg = load_config()
    MODEL_MANAGER.load(cfg)

    boot = service.bootstrap_models()
    print("bootstrap_count", boot["count"])

    act1 = service.activate("TR-0b062172078e")
    print("activate_first", json.dumps(act1, ensure_ascii=False))
    assert MODEL_MANAGER.require().bundle.manifest["model_bundle_id"] == "TR-0b062172078e"

    job = service.submit_train_job(equipment_type="main_mill_motor", dataset_id="main_mill_motor_dev")
    print("job_submitted", job)
    started = time.time()
    run_worker(once=True)
    elapsed = time.time() - started
    done = service.get_train_job(job["job_id"])
    print("job_finished", done["status"], "bundle", done.get("model_bundle_id"), f"elapsed={elapsed:.1f}s")
    if done["status"] != "succeeded":
        print("logs", service.get_train_job_logs(job["job_id"], max_lines=50))
        return 1

    new_id = done["model_bundle_id"]
    assert new_id and new_id != "TR-0b062172078e"

    cols = MODEL_MANAGER.require().bundle.feature_schema["feature_columns"]
    sample = {name: float(i + 1) * 0.01 for i, name in enumerate(cols)}
    before = predict_bundle(MODEL_MANAGER.require().bundle, [sample])[0]

    act2 = service.activate(new_id)
    print("activate_new", json.dumps(act2, ensure_ascii=False))
    assert MODEL_MANAGER.require().bundle.manifest["model_bundle_id"] == new_id
    after_new = predict_bundle(MODEL_MANAGER.require().bundle, [sample])[0]
    assert after_new["model_bundle_id"] == new_id

    rolled = service.rollback(equipment_type="main_mill_motor", target_model_bundle_id="TR-0b062172078e")
    print("rollback", json.dumps(rolled, ensure_ascii=False))
    assert MODEL_MANAGER.require().bundle.manifest["model_bundle_id"] == "TR-0b062172078e"
    after_roll = predict_bundle(MODEL_MANAGER.require().bundle, [sample])[0]
    assert after_roll["model_bundle_id"] == "TR-0b062172078e"
    assert before["model_bundle_id"] == "TR-0b062172078e"

    print("ACTIVE", service.active_status())
    print("E2E_OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
