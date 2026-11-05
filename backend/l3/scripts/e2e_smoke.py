"""对最新完成的 DiagnosisRun 执行一次 L3 入队与分析冒烟。"""

from __future__ import annotations

from l2.handoff.database import connect
from l2.handoff.store import HANDOFF_STORE
from l3.runtime.database import init_db
from l3.runtime.jobs import enqueue_from_diagnosis_run, execute_analysis_run
from l3.runtime.store import ANALYSIS_STORE


def main() -> int:
    init_db()
    connection = connect()
    try:
        row = connection.execute(
            """
            SELECT diagnosis_run_id, status, equipment_type
            FROM diagnosis_runs
            WHERE status LIKE 'completed%'
            ORDER BY finished_at DESC
            LIMIT 1
            """
        ).fetchone()
    finally:
        connection.close()
    if not row:
        print("NO_DIAGNOSIS_RUN")
        return 0
    diagnosis_run_id = row["diagnosis_run_id"]
    targets = HANDOFF_STORE.iter_l3_target_records(diagnosis_run_id)
    print("DR", diagnosis_run_id, row["equipment_type"], "targets", len(targets))
    body = enqueue_from_diagnosis_run(diagnosis_run_id)
    print(
        "enqueue",
        {
            k: body.get(k)
            for k in ("analysis_run_id", "selected_record_count", "status", "created", "reused", "skipped")
        },
    )
    if body.get("skipped"):
        return 0
    run = execute_analysis_run(body["analysis_run_id"])
    print(
        "done",
        {
            k: run.get(k)
            for k in (
                "analysis_run_id",
                "status",
                "selected_record_count",
                "processed_record_count",
                "succeeded_record_count",
                "failed_record_count",
                "inconclusive_record_count",
                "result_dir",
            )
        },
    )
    page = ANALYSIS_STORE.list_results(run["analysis_run_id"], limit=5)
    for item in page["items"]:
        evidence = item.get("evidence") or {}
        print(
            "record",
            item["record_id"],
            item.get("screening_result"),
            "sufficiency",
            evidence.get("evidence_sufficiency"),
            "analyzers",
            evidence.get("covering_analyzers"),
            "severity",
            (evidence.get("severity_assessment") or {}).get("severity_level"),
            "support",
            len(evidence.get("support_evidence") or []),
            "contradict",
            len(evidence.get("contradict_evidence") or []),
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
