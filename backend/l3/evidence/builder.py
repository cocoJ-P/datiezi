"""组装不可变 EvidenceBundle。"""

from __future__ import annotations

from typing import Any

from l3.core.analyzer_base import AnalyzerResult
from l3.core.context import AnalysisContext
from l3.evidence.reasoning_trace import build_reasoning_trace_safe


def build_evidence_bundle(
    context: AnalysisContext,
    analyzer_results: list[AnalyzerResult],
    review: dict[str, Any],
) -> dict[str, Any]:
    # Trace 复用同一批 analyzer_results / evidence_items，不重跑数值分析。
    reasoning_trace = build_reasoning_trace_safe(context, analyzer_results, review)
    return {
        "schema": "L3EvidenceBundle",
        "schema_version": "l3_evidence_v1",
        "analysis_run_id": context.analysis_run_id,
        "record_id": context.record_id,
        "equipment_type": context.equipment_type,
        "equipment_id": context.equipment_id,
        "prepared_dataset_id": context.prepared_dataset_id,
        "dataset_revision_id": context.dataset_revision_id,
        "diagnosis_run_id": context.diagnosis_run_id,
        "model_bundle_id": context.model_bundle_id,
        "analyzer_config_version": context.analyzer_config_version,
        "routing_policy_version": context.routing_policy_version,
        "trigger_reason": context.trigger_reason,
        "collection_time": context.collection_time,
        "l2_context": context.to_l2_slice(),
        "analyzer_results": [r.to_dict() for r in analyzer_results],
        "fault_type_review": review.get("fault_type_review"),
        "severity_assessment": review.get("severity_assessment"),
        "location_candidates": review.get("location_candidates"),
        "spectral_feature_assessment": review.get("spectral_feature_assessment"),
        "evidence_sufficiency": review.get("evidence_sufficiency"),
        "evidence_items": review.get("evidence_items") or [],
        "support_evidence": review.get("support_evidence") or [],
        "contradict_evidence": review.get("contradict_evidence") or [],
        "missing_evidence": review.get("missing_evidence") or [],
        "analysis_limitations": review.get("analysis_limitations") or [],
        "skipped_analyzers": review.get("skipped_analyzers") or [],
        "evidence_dependencies": review.get("evidence_dependencies") or [],
        "covering_analyzers": review.get("covering_analyzers") or [],
        "history_note": context.history_note,
        "history_sample_count": len(context.history_before),
        "reasoning_trace": reasoning_trace,
        "knowledge_extension_hooks": review.get("knowledge_extension_hooks"),
        "l4_handoff_stub": {
            "ready": False,
            "note": "L4 根因推理未实现；可消费本 EvidenceBundle。",
            "evidence_ref": {
                "analysis_run_id": context.analysis_run_id,
                "record_id": context.record_id,
            },
        },
        # 明确空缺业务字段
        "deferred_fields": {
            "root_cause": None,
            "recommended_actions": None,
            "priority": None,
            "impact": None,
        },
    }
