"""L1→L2 Handoff 冒烟：对已有泵 PD 执行编排并打印摘要。"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT / "backend"))

from l2.handoff.orchestrator import ORCHESTRATOR
from l2.handoff.store import HANDOFF_STORE
from l2.prepared.l1_source import get_l1_prepared_meta


def main() -> int:
    pd = sys.argv[1] if len(sys.argv) > 1 else "PD-4c2a075ce5f8"
    meta = get_l1_prepared_meta(pd)
    job = ORCHESTRATOR.enqueue_after_prepare(
        dataset_id=meta.dataset_id,
        pd_version_id=pd,
        prepare_run_id=None,
        prepare_status="completed",
    )
    assert job is not None
    result = ORCHESTRATOR.process_job(job["handoff_id"])
    print(json.dumps({"handoff": result}, ensure_ascii=False, indent=2))
    for item in result.get("equipment_items") or []:
        run_id = item.get("diagnosis_run_id")
        if run_id:
            run = HANDOFF_STORE.get_diagnosis_run(run_id)
            print(json.dumps({"diagnosis_run": run}, ensure_ascii=False, indent=2))
    return 0 if result.get("status") in {"completed", "training_confirmation_required", "skipped"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
