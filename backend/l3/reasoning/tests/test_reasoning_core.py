"""L3 Reasoning v1 单元测试（默认 dry_run，不强制真实 LLM）。"""

from __future__ import annotations

from l3.evidence.reasoning_trace import validate_trace
from l3.reasoning.orchestrator import build_deterministic_assessment, run_llm_tool_loop
from l3.reasoning.policy import apply_selection, select_reason
from l3.reasoning.schema import FORBIDDEN_FIELDS
from l3.reasoning.tools import ToolContext, execute_tool
from l3.reasoning.trace_overlay import attach_reasoning_nodes
from l3.reasoning.validator import validate_assessment


def _sample_input() -> dict:
    evidence = {
        "record_id": "R1",
        "equipment_type": "main_mill_motor",
        "equipment_id": "MM-01",
        "l2_context": {
            "screening_result": "CONFLICT",
            "preliminary_fault": "转子不平衡",
            "binary_probability": 0.9,
            "fault_probabilities": {"转子不平衡": 0.55, "轴承磨损": 0.4},
        },
        "fault_type_review": [
            {"fault_mode": "转子不平衡", "verdict": "SUPPORT"},
            {"fault_mode": "轴承磨损", "verdict": "INCONCLUSIVE"},
        ],
        "evidence_sufficiency": "insufficient",
        "evidence_items": [
            {
                "evidence_id": "mech:R1:1X",
                "feature_name": "1X转频幅值(mm/s)",
                "observed_value": 4.2,
                "assessment": "SUPPORT",
                "related_fault_mode": "转子不平衡",
            },
            {
                "evidence_id": "mech:R1:BPFO",
                "feature_name": "BPFO幅值(g)",
                "observed_value": 0.1,
                "assessment": "SUPPORT",
                "related_fault_mode": "轴承磨损",
            },
        ],
        "support_evidence": [{"evidence_id": "mech:R1:1X"}],
        "contradict_evidence": [],
        "missing_evidence": ["相位稳定性", "2X幅值"],
        "analysis_limitations": ["no_phase"],
        "severity_assessment": {},
        "location_candidates": {"limitations": ["无测点映射"]},
        "spectral_feature_assessment": {"observed_scalar_features": ["1X转频幅值(mm/s)", "BPFO幅值(g)"]},
        "reasoning_trace": {
            "trace_id": "TRACE-t",
            "trace_version": "reasoning_trace_v1",
            "build_status": "ok",
            "nodes": [
                {"node_id": "obs:1", "node_type": "OBSERVATION", "feature_name": "1X转频幅值(mm/s)", "value": 4.2},
                {
                    "node_id": "ev:mech:R1:1X",
                    "node_type": "EVIDENCE",
                    "evidence_id": "mech:R1:1X",
                    "assessment": "SUPPORT",
                    "related_fault_mode": "转子不平衡",
                },
                {
                    "node_id": "concl:fault_review:转子不平衡",
                    "node_type": "CONCLUSION",
                    "conclusion_type": "fault_type_review",
                    "assessment": "SUPPORT",
                },
            ],
            "edges": [
                {"from": "obs:1", "to": "ev:mech:R1:1X", "relation": "DERIVED_FROM"},
                {"from": "ev:mech:R1:1X", "to": "concl:fault_review:转子不平衡", "relation": "SUPPORTS"},
            ],
            "conclusion_node_ids": ["concl:fault_review:转子不平衡"],
            "limitations": [],
        },
    }
    return {
        "analysis_run_id": "AR-test",
        "record_id": "R1",
        "equipment_type": "main_mill_motor",
        "knowledge_snapshot_id": "KS-test",
        "evidence_summary": {
            "l2_context": evidence["l2_context"],
            "fault_type_review": evidence["fault_type_review"],
            "evidence_sufficiency": "insufficient",
            "evidence_items": evidence["evidence_items"],
            "missing_evidence": evidence["missing_evidence"],
            "analysis_limitations": evidence["analysis_limitations"],
            "severity_assessment": {},
            "location_candidates": evidence["location_candidates"],
            "spectral_feature_assessment": evidence["spectral_feature_assessment"],
        },
        "knowledge_summary": {
            "knowledge_references": [{"knowledge_id": "K-demo", "canonical_name": "转子不平衡"}],
            "fault_candidates": ["转子不平衡", "轴承磨损"],
        },
        "allowed_evidence_ids": ["mech:R1:1X", "mech:R1:BPFO"],
        "allowed_knowledge_ids": ["K-demo"],
        "_evidence_bundle": evidence,
        "_knowledge_context": {"knowledge_references": [{"knowledge_id": "K-demo"}]},
    }


def test_policy_prefers_conflict_and_uncertain():
    ok, reason = select_reason(screening_result="CONFLICT", evidence={"evidence_sufficiency": "sufficient"})
    assert ok and reason == "l2_conflict"
    ok2, reason2 = select_reason(screening_result="UNCERTAIN", evidence={})
    assert ok2 and reason2 == "l2_uncertain"
    ok3, reason3 = select_reason(
        screening_result="ABNORMAL",
        evidence={
            "evidence_sufficiency": "sufficient",
            "fault_type_review": [{"fault_mode": "转子不平衡", "verdict": "SUPPORT"}],
            "contradict_evidence": [],
        },
    )
    assert not ok3
    assert "skip" in reason3 or "optional" in reason3


def test_selection_budget_defers_overflow():
    rows = []
    for i in range(5):
        rows.append(
            {
                "record_id": f"R{i}",
                "screening_result": "CONFLICT",
                "evidence": {"evidence_sufficiency": "insufficient", "fault_type_review": [{"fault_mode": "x", "verdict": "SUPPORT"}]},
            }
        )
    out = apply_selection(rows, automatic_selection=True, max_auto=2)
    assert len(out["selected"]) == 2
    assert len(out["deferred"]) == 3
    assert all(d.get("note") == "budget_exceeded_pending" for d in out["deferred"])


def test_tools_readonly_and_whitelist():
    ctx = ToolContext(_sample_input())
    bad = execute_tool("rm_rf", {}, ctx)
    assert bad["ok"] is False
    good = execute_tool("get_l2_diagnosis", {}, ctx)
    assert good["ok"] is True
    assert good["l2"]["screening_result"] == "CONFLICT"
    ev = execute_tool("get_l3_evidence", {"include_items": True}, ctx)
    assert "mech:R1:1X" in ev["allowed_evidence_ids"]


def test_deterministic_assessment_and_validation():
    rin = _sample_input()
    assessment = build_deterministic_assessment(rin, reasoning_run_id="RR-x")
    assert assessment["candidate_hypotheses"]
    assert "final_root_cause" not in assessment
    for f in FORBIDDEN_FIELDS:
        assert f not in assessment
    validation = validate_assessment(assessment, reasoning_input=rin)
    assert validation["ok"] is True


def test_validator_rejects_fake_ids_and_forbidden():
    rin = _sample_input()
    assessment = build_deterministic_assessment(rin, reasoning_run_id="RR-x")
    assessment["candidate_hypotheses"][0]["supporting_evidence_ids"].append("fake-ev")
    assessment["knowledge_references"] = ["K-not-exist"]
    assessment["final_root_cause"] = "禁止"
    validation = validate_assessment(assessment, reasoning_input=rin)
    assert validation["ok"] is False
    assert any("unknown_evidence_id" in e for e in validation["errors"])
    assert any("unknown_knowledge_id" in e for e in validation["errors"])
    assert any("forbidden_field" in e for e in validation["errors"])


def test_anomaly_only_cannot_support_fault():
    from l3.reasoning.outcome import finalize_assessment

    rin = _sample_input()
    rin["allowed_evidence_ids"] = ["thr:R1:vib_x_alarm"]
    rin["evidence_summary"]["evidence_items"] = [
        {
            "evidence_id": "thr:R1:vib_x_alarm",
            "feature_name": "X向振动报警",
            "observed_value": 8.0,
            "assessment": "SUPPORT",
            "related_fault_mode": "轴承磨损",
        }
    ]
    rin["_evidence_bundle"]["evidence_items"] = rin["evidence_summary"]["evidence_items"]
    assessment = build_deterministic_assessment(rin, reasoning_run_id="RR-anom")
    assessment["candidate_hypotheses"] = [
        {
            "hypothesis_id": "H1",
            "fault_type": "轴承磨损",
            "hypothesis_origin": "l2_preliminary",
            "supporting_evidence_ids": ["thr:R1:vib_x_alarm"],
            "contradicting_evidence_ids": [],
            "knowledge_reference_ids": [],
            "missing_evidence": [],
            "assessment": "SUPPORTED",
        }
    ]
    assessment, mode = finalize_assessment(
        assessment, reasoning_input=rin, base_mode="live_llm_success", audit={"llm_call_count": 1}
    )
    assert assessment["candidate_hypotheses"][0]["assessment"] == "INCONCLUSIVE"
    assert mode in {"insufficient_evidence", "live_llm_success"}
    assert assessment["outcome_class"] == mode


def test_invalid_origin_rejected_or_sanitized():
    from l3.reasoning.outcome import finalize_assessment

    rin = _sample_input()
    assessment = build_deterministic_assessment(rin, reasoning_run_id="RR-origin")
    assessment["candidate_hypotheses"][0]["hypothesis_origin"] = "not_a_real_origin"
    assessment, mode = finalize_assessment(
        assessment, reasoning_input=rin, base_mode="live_llm_success", audit={}
    )
    assert assessment["candidate_hypotheses"][0]["hypothesis_origin"] == "exploratory_hypothesis"
    validation = validate_assessment(assessment, reasoning_input=rin)
    assert validation["ok"] is True


def test_graph_view_param_validation():
    ctx = ToolContext(_sample_input())
    bad = execute_tool("query_equipment_graph", {"view": "nope"}, ctx)
    assert bad["ok"] is False
    assert "非法 view" in bad["error"]
    need = execute_tool("query_equipment_graph", {"view": "fault_features"}, ctx)
    assert need["ok"] is False
    ok = execute_tool("query_equipment_graph", {"view": "components"}, ctx)
    assert ok["ok"] is True


def test_trace_overlay_hypothesis_and_tool():
    rin = _sample_input()
    outcome = run_llm_tool_loop(rin, reasoning_run_id="RR-dry", dry_run=True)
    assessment = outcome["assessment"]
    overlay = attach_reasoning_nodes(rin["_evidence_bundle"]["reasoning_trace"], assessment, outcome["tool_history"])
    assert overlay is not None
    assert overlay.get("build_status") != "failed"
    validate_trace(overlay)
    types = {n.get("node_type") for n in overlay["nodes"]}
    assert "HYPOTHESIS" in types
    assert "TOOL_RESULT" in types


def test_dry_run_loop_keeps_core_safe():
    rin = _sample_input()
    original = rin["_evidence_bundle"]["evidence_items"][0]["observed_value"]
    outcome = run_llm_tool_loop(rin, reasoning_run_id="RR-dry2", dry_run=True)
    assert rin["_evidence_bundle"]["evidence_items"][0]["observed_value"] == original
    assert outcome["mode"] == "dry_run"
    assert outcome["assessment"]["outcome_class"] == "dry_run"
    assert outcome["assessment"]["validation"]["ok"] is True
    assert "audit" in outcome["assessment"]
