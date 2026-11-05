"""L3KnowledgeContext：按 AnalysisRun/记录检索并关联知识（不重跑数值分析）。"""

from __future__ import annotations

from typing import Any

from l3.knowledge.graph_query import (
    query_equipment_components,
    query_fault_cases,
    query_fault_features,
    query_rules_for_equipment,
)
from l3.knowledge.retrieval import hybrid_search
from l3.knowledge.snapshot import load_snapshot


def build_knowledge_context(
    *,
    analysis_run_id: str,
    record_id: str,
    equipment_type: str,
    evidence_bundle: dict[str, Any],
    knowledge_snapshot_id: str | None = None,
    top_k: int = 6,
) -> dict[str, Any]:
    snap = load_snapshot(knowledge_snapshot_id)
    l2 = evidence_bundle.get("l2_context") or {}
    preliminary = l2.get("preliminary_fault")
    screening = l2.get("screening_result")
    reviews = evidence_bundle.get("fault_type_review") or []
    candidates = []
    if preliminary and preliminary not in {"正常", None}:
        candidates.append(str(preliminary))
    for row in reviews:
        mode = row.get("fault_mode")
        if mode and mode not in candidates:
            candidates.append(str(mode))

    observed = []
    for item in evidence_bundle.get("evidence_items") or []:
        name = item.get("feature_name")
        if name:
            observed.append(str(name))
    observed = sorted(set(observed))[:12]

    query_parts = [equipment_type or "", screening or "", " ".join(candidates), " ".join(observed[:5])]
    query_text = " ".join(p for p in query_parts if p).strip() or (candidates[0] if candidates else equipment_type)

    # 按候选故障复用检索（同组缓存由调用方负责）；此处对主候选 + 全查询各一次
    search_primary = hybrid_search(
        query=query_text,
        equipment_type=equipment_type,
        fault_mode=candidates[0] if candidates else None,
        top_k=top_k,
        snapshot_id=snap["knowledge_snapshot_id"],
    )
    knowledge_refs = search_primary.get("results") or []

    components = query_equipment_components(equipment_type)
    rules = query_rules_for_equipment(equipment_type)
    related_features = []
    matching_cases = []
    for mode in candidates[:2]:
        related_features.append(query_fault_features(mode))
        matching_cases.append(query_fault_cases(mode))

    # 知识与当前证据的一致性说明（不改写数值）
    consistency_notes = []
    for ref in knowledge_refs:
        if ref.get("applicability_status") == "applicable":
            consistency_notes.append(
                {
                    "knowledge_id": ref["knowledge_id"],
                    "relation": "mechanistic_reference",
                    "note": "知识为机理/规程参考；仅当对应 EvidenceItem 已存在时视为一致，不得单独构成 SUPPORT",
                }
            )

    missing = []
    if not knowledge_refs:
        missing.append("no_relevant_knowledge")
    if not components.get("components"):
        missing.append("no_component_graph_match")
    if not rules.get("parameters"):
        missing.append("no_equipment_scoped_parameter_nodes")

    return {
        "schema": "L3KnowledgeContext",
        "schema_version": "l3_knowledge_context_v1",
        "analysis_run_id": analysis_run_id,
        "record_id": record_id,
        "equipment_type": equipment_type,
        "knowledge_snapshot_id": snap["knowledge_snapshot_id"],
        "l2_screening_result": screening,
        "fault_candidates": candidates,
        "observed_features": observed,
        "applicable_knowledge": knowledge_refs,
        "diagnostic_rules": rules,
        "related_components": components,
        "matching_cases": matching_cases,
        "related_features": related_features,
        "knowledge_references": knowledge_refs,
        "consistency_notes": consistency_notes,
        "conflicts": snap.get("conflicts") or [],
        "missing_knowledge": missing,
        "retrieval": {
            "mode": search_primary.get("mode"),
            "bm25": search_primary.get("bm25"),
            "semantic": search_primary.get("semantic"),
            "query": search_primary.get("effective_query"),
        },
        "immutable_core_note": "未修改 L2 概率与 L3 EvidenceItem 计算值",
    }


def attach_knowledge_nodes_to_trace(
    reasoning_trace: dict[str, Any] | None,
    knowledge_context: dict[str, Any],
) -> dict[str, Any] | None:
    """生成带 KNOWLEDGE_REFERENCE 的 Trace 覆盖副本，不修改原 Trace 文件语义。"""
    if not reasoning_trace or reasoning_trace.get("build_status") == "failed":
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

    for ref in knowledge_context.get("knowledge_references") or []:
        nid = f"know:{ref['knowledge_id']}"
        if nid in existing:
            continue
        trace["nodes"].append(
            {
                "node_id": nid,
                "node_type": "KNOWLEDGE_REFERENCE",
                "knowledge_id": ref["knowledge_id"],
                "knowledge_snapshot_id": knowledge_context.get("knowledge_snapshot_id"),
                "knowledge_type": ref.get("knowledge_type"),
                "source_document": ref.get("source_document_id"),
                "source_location": ref.get("source_location"),
                "content_summary": ref.get("content_summary"),
                "applicability_status": ref.get("applicability_status"),
                "note": "知识引用不是当前设备已验证的数值 SUPPORT 证据",
            }
        )
        existing.add(nid)
        # 连接到相关结论（机理一致性说明）
        for concl in conclusion_nodes[:3]:
            trace["edges"].append(
                {
                    "from": nid,
                    "to": concl["node_id"],
                    "relation": "CONTRIBUTES_TO",
                }
            )
        # 若有同故障证据，连接到证据节点
        fault_scope = " ".join(ref.get("fault_mode_scope") or [])
        for ev in evidence_nodes:
            if fault_scope and str(ev.get("related_fault_mode") or "") in fault_scope:
                trace["edges"].append({"from": nid, "to": ev["node_id"], "relation": "CONTRIBUTES_TO"})
                break

    trace["limitations"] = sorted(
        set(
            trace["limitations"]
            + [
                "knowledge_reference_is_not_numerical_support",
                "enrichment_overlay_does_not_mutate_core_evidence_bundle",
            ]
        )
    )
    trace["knowledge_enrichment"] = {
        "knowledge_snapshot_id": knowledge_context.get("knowledge_snapshot_id"),
        "reference_count": len([n for n in trace["nodes"] if n.get("node_type") == "KNOWLEDGE_REFERENCE"]),
    }
    # 校验无环
    from l3.evidence.reasoning_trace import validate_trace

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
            "limitations": [f"knowledge_trace_overlay_failed: {exc}"],
            "build_status": "failed",
            "error": str(exc),
        }
    return trace
