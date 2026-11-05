"""对真实 DiagnosisRun 中一条 ABNORMAL 记录构建 ReasoningTrace 并打印摘要。"""

from __future__ import annotations

from l2.handoff.database import connect
from l2.handoff.store import HANDOFF_STORE
from l3.core.input_adapter import InputAdapter
from l3.core.registry import DEFAULT_REGISTRY
from l3.evidence.builder import build_evidence_bundle
from l3.evidence.reasoning_trace import validate_trace


def main() -> int:
    connection = connect()
    try:
        row = connection.execute(
            """
            SELECT diagnosis_run_id, equipment_type, prepared_dataset_id, dataset_revision_id, model_bundle_id, schema_version
            FROM diagnosis_runs
            WHERE status LIKE 'completed%'
            ORDER BY finished_at DESC
            LIMIT 1
            """
        ).fetchone()
    finally:
        connection.close()
    if not row:
        print("NO_RUN")
        return 0
    diagnosis = dict(row)
    targets = HANDOFF_STORE.iter_l3_target_records(diagnosis["diagnosis_run_id"])
    abnormal = next((t for t in targets if t.get("screening_result") == "ABNORMAL"), targets[0])
    adapter = InputAdapter()
    ped = adapter.load_ped_index(
        prepared_dataset_id=str(diagnosis["prepared_dataset_id"]),
        equipment_type=str(diagnosis["equipment_type"]),
    )
    ctx = adapter.build_context(
        analysis_run_id="AR-trace-smoke",
        diagnosis_run=diagnosis | {"diagnosis_run_id": diagnosis["diagnosis_run_id"]},
        record_row=abnormal,
        ped_index=ped,
    )
    results, review = DEFAULT_REGISTRY.run_all(ctx)
    bundle = build_evidence_bundle(ctx, results, review)
    trace = bundle["reasoning_trace"]
    validate_trace(trace)
    print("record", ctx.record_id, "equipment", ctx.equipment_type, ctx.equipment_id)
    print("l2", ctx.screening_result, ctx.preliminary_fault)
    print(
        "trace",
        trace["trace_id"],
        "status",
        trace["build_status"],
        "nodes",
        len(trace["nodes"]),
        "edges",
        len(trace["edges"]),
    )
    for ntype in ("OBSERVATION", "ANALYSIS", "RULE", "EVIDENCE", "CONCLUSION"):
        items = [n for n in trace["nodes"] if n["node_type"] == ntype]
        print(ntype, len(items))
        for node in items[:3]:
            if ntype == "OBSERVATION":
                print(" ", node.get("feature_name"), "=", node.get("value"))
            elif ntype == "RULE":
                print(" ", node.get("rule_id"), node.get("condition"))
            elif ntype == "EVIDENCE":
                print(" ", node.get("assessment"), node.get("feature_name"), node.get("related_fault_mode"))
            elif ntype == "CONCLUSION":
                print(" ", node.get("assessment"), node.get("summary"))
            else:
                print(" ", node.get("analyzer_id"), node.get("method"))
    print("limitations", trace.get("limitations"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
