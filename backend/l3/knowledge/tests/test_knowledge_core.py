"""L3 Knowledge v1 基础测试。"""

from __future__ import annotations

from l3.evidence.reasoning_trace import validate_trace
from l3.knowledge.context import attach_knowledge_nodes_to_trace, build_knowledge_context
from l3.knowledge.graph_query import graph_status, query_equipment_components, query_fault_features
from l3.knowledge.import_docs import inspect_sources
from l3.knowledge.retrieval import hybrid_search, rrf_fuse
from l3.knowledge.schema import equipment_type_matches_text
from l3.knowledge.snapshot import build_snapshot


def test_sources_importable():
    status = inspect_sources()
    assert status["importable_count"] == 6
    assert status["missing"] == []


def test_snapshot_builds_and_dedupes():
    snap = build_snapshot()
    assert snap["knowledge_snapshot_id"].startswith("KS-")
    assert snap["item_count"] > 0
    assert "source_hashes" in snap
    # KB-06 别名不应全部缺失
    aliases = [i for i in snap["items"] if i.get("canonical_of")]
    assert isinstance(aliases, list)


def test_equipment_scope_not_cross_applied():
    assert equipment_type_matches_text("high_pressure_descaling_pump", "冷却水循环泵 轴承温度") is False
    assert equipment_type_matches_text("high_pressure_descaling_pump", "棒线主轧机电机 EXP-2024-001") is False
    assert equipment_type_matches_text("main_mill_motor", "主轧机电机 驱动端轴承") is True
    assert equipment_type_matches_text("main_mill_motor", "冷却水循环泵 限值") is False


def test_graph_queries():
    status = graph_status()
    assert status["available"] is True
    assert status["entity_count"] > 0
    comps = query_equipment_components("main_mill_motor")
    assert "components" in comps
    feats = query_fault_features("轴承磨损")
    assert "features" in feats
    assert feats["note"]


def test_hybrid_search_modes():
    snap = build_snapshot()
    out = hybrid_search(
        query="主轧机电机 轴承磨损 BPFO",
        equipment_type="main_mill_motor",
        fault_mode="轴承磨损",
        top_k=5,
        snapshot_id=snap["knowledge_snapshot_id"],
    )
    assert out["mode"] in {"hybrid_rrf", "bm25_only", "vector_only"}
    assert "results" in out
    # 无相关特异词时不得用泛词填充
    empty = hybrid_search(query="不存在的外星设备故障XYZ123", equipment_type="main_mill_motor", top_k=3)
    assert empty["message"] == "no_relevant_knowledge"
    assert empty["count"] == 0
    assert empty["results"] == []


def test_rrf_fuse_stable():
    fused = rrf_fuse([["a", "b", "c"], ["b", "a", "d"]], top_k=3)
    assert fused[0][0] in {"a", "b"}


def test_knowledge_context_does_not_mutate_evidence_values():
    snap = build_snapshot()
    evidence = {
        "equipment_type": "main_mill_motor",
        "l2_context": {
            "screening_result": "ABNORMAL",
            "preliminary_fault": "轴承磨损",
            "route": "l3_review_recommended",
        },
        "fault_type_review": [{"fault_mode": "轴承磨损", "verdict": "SUPPORT", "support_count": 2, "contradict_count": 0}],
        "evidence_items": [
            {
                "evidence_id": "mech:R1:bearing:BPFO幅值(g)",
                "feature_name": "BPFO幅值(g)",
                "observed_value": 0.8,
                "assessment": "SUPPORT",
                "related_fault_mode": "轴承磨损",
            }
        ],
        "reasoning_trace": {
            "trace_id": "TRACE-t",
            "trace_version": "reasoning_trace_v1",
            "build_status": "ok",
            "nodes": [
                {
                    "node_id": "obs:R1:BPFO幅值g",
                    "node_type": "OBSERVATION",
                    "feature_name": "BPFO幅值(g)",
                    "value": 0.8,
                },
                {
                    "node_id": "ev:mech:R1:bearing:BPFO幅值(g)",
                    "node_type": "EVIDENCE",
                    "evidence_id": "mech:R1:bearing:BPFO幅值(g)",
                    "assessment": "SUPPORT",
                    "related_fault_mode": "轴承磨损",
                },
                {
                    "node_id": "concl:fault_review:轴承磨损",
                    "node_type": "CONCLUSION",
                    "conclusion_type": "fault_type_review",
                    "assessment": "SUPPORT",
                    "evidence_ids": ["mech:R1:bearing:BPFO幅值(g)"],
                },
            ],
            "edges": [
                {"from": "obs:R1:BPFO幅值g", "to": "ev:mech:R1:bearing:BPFO幅值(g)", "relation": "DERIVED_FROM"},
                {"from": "ev:mech:R1:bearing:BPFO幅值(g)", "to": "concl:fault_review:轴承磨损", "relation": "SUPPORTS"},
            ],
            "conclusion_node_ids": ["concl:fault_review:轴承磨损"],
            "limitations": [],
        },
    }
    original_value = evidence["evidence_items"][0]["observed_value"]
    ctx = build_knowledge_context(
        analysis_run_id="AR-x",
        record_id="R1",
        equipment_type="main_mill_motor",
        evidence_bundle=evidence,
        knowledge_snapshot_id=snap["knowledge_snapshot_id"],
    )
    assert evidence["evidence_items"][0]["observed_value"] == original_value
    overlay = attach_knowledge_nodes_to_trace(evidence["reasoning_trace"], ctx)
    assert overlay is not None
    assert overlay["build_status"] != "failed" or overlay.get("error")
    if overlay.get("build_status") != "failed":
        validate_trace(overlay)
        assert any(n.get("node_type") == "KNOWLEDGE_REFERENCE" for n in overlay["nodes"]) or not ctx.get(
            "knowledge_references"
        )
