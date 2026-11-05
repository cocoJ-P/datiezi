"""ReasoningTrace 覆盖层：增加 HYPOTHESIS / TOOL_RESULT，不修改 Core Trace 落盘。"""

from __future__ import annotations

from typing import Any

from l3.evidence.reasoning_trace import validate_trace


def attach_reasoning_nodes(
    reasoning_trace: dict[str, Any] | None,
    assessment: dict[str, Any],
    tool_history: list[dict[str, Any]] | None = None,
) -> dict[str, Any] | None:
    if not reasoning_trace:
        # 最小空壳，便于前端展示
        reasoning_trace = {
            "trace_id": f"TRACE-r-{assessment.get('record_id')}",
            "trace_version": "reasoning_trace_v1",
            "build_status": "ok",
            "nodes": [],
            "edges": [],
            "conclusion_node_ids": [],
            "limitations": ["trace_created_for_reasoning_overlay"],
        }
    if reasoning_trace.get("build_status") == "failed":
        return reasoning_trace

    trace = {
        **reasoning_trace,
        "nodes": list(reasoning_trace.get("nodes") or []),
        "edges": list(reasoning_trace.get("edges") or []),
        "limitations": list(reasoning_trace.get("limitations") or []),
    }
    existing = {n["node_id"] for n in trace["nodes"]}
    evidence_nodes = [n for n in trace["nodes"] if n.get("node_type") == "EVIDENCE"]
    conclusion_nodes = [n for n in trace["nodes"] if n.get("node_type") == "CONCLUSION"]

    for call in tool_history or assessment.get("tool_calls") or []:
        tid = str(call.get("result_ref") or f"tool:{call.get('tool')}")
        nid = f"tool:{tid}"
        if nid in existing:
            continue
        trace["nodes"].append(
            {
                "node_id": nid,
                "node_type": "TOOL_RESULT",
                "tool_call_id": tid,
                "tool_name": call.get("tool"),
                "validated_arguments": call.get("arguments") or {},
                "result_reference": tid,
                "execution_status": "ok" if call.get("ok") else "failed",
            }
        )
        existing.add(nid)

    for hyp in assessment.get("candidate_hypotheses") or []:
        hid = str(hyp.get("hypothesis_id") or hyp.get("fault_type"))
        nid = f"hyp:{hid}"
        if nid in existing:
            continue
        trace["nodes"].append(
            {
                "node_id": nid,
                "node_type": "HYPOTHESIS",
                "hypothesis_id": hid,
                "fault_type": hyp.get("fault_type"),
                "origin": hyp.get("hypothesis_origin"),
                "supporting_evidence_ids": hyp.get("supporting_evidence_ids") or [],
                "contradicting_evidence_ids": hyp.get("contradicting_evidence_ids") or [],
                "knowledge_reference_ids": hyp.get("knowledge_reference_ids") or [],
                "verification_status": hyp.get("assessment"),
                "note": "假设支持≠确诊根因",
            }
        )
        existing.add(nid)
        for eid in hyp.get("supporting_evidence_ids") or []:
            for ev in evidence_nodes:
                if ev.get("evidence_id") == eid:
                    trace["edges"].append({"from": ev["node_id"], "to": nid, "relation": "SUPPORTS"})
                    break
        for eid in hyp.get("contradicting_evidence_ids") or []:
            for ev in evidence_nodes:
                if ev.get("evidence_id") == eid:
                    trace["edges"].append({"from": ev["node_id"], "to": nid, "relation": "CONTRADICTS"})
                    break
        for concl in conclusion_nodes[:2]:
            trace["edges"].append({"from": nid, "to": concl["node_id"], "relation": "CONTRIBUTES_TO"})
        for kid in (hyp.get("knowledge_reference_ids") or [])[:3]:
            knid = f"know:{kid}"
            if knid not in existing:
                trace["nodes"].append(
                    {
                        "node_id": knid,
                        "node_type": "KNOWLEDGE_REFERENCE",
                        "knowledge_id": kid,
                        "note": "知识引用不是数值 SUPPORT",
                    }
                )
                existing.add(knid)
            trace["edges"].append({"from": knid, "to": nid, "relation": "CONTRIBUTES_TO"})

    trace["limitations"] = sorted(
        set(
            trace["limitations"]
            + [
                "hypothesis_is_not_confirmed_root_cause",
                "reasoning_overlay_does_not_mutate_core_evidence",
            ]
        )
    )
    trace["reasoning_enrichment"] = {
        "reasoning_run_id": assessment.get("reasoning_run_id"),
        "hypothesis_count": len([n for n in trace["nodes"] if n.get("node_type") == "HYPOTHESIS"]),
        "tool_result_count": len([n for n in trace["nodes"] if n.get("node_type") == "TOOL_RESULT"]),
        "assessment_status": assessment.get("status"),
    }
    try:
        validate_trace(trace)
        trace["build_status"] = reasoning_trace.get("build_status") or "ok"
    except Exception as exc:  # noqa: BLE001
        return {
            "trace_id": reasoning_trace.get("trace_id"),
            "trace_version": reasoning_trace.get("trace_version"),
            "nodes": [],
            "edges": [],
            "conclusion_node_ids": [],
            "limitations": [f"reasoning_trace_overlay_failed: {exc}"],
            "build_status": "failed",
            "error": str(exc),
        }
    return trace
