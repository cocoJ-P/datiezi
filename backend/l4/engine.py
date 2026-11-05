"""L4 确定性引擎 v1.1：Eligibility → Candidates → Validate → Impact → Assessment。"""

from __future__ import annotations

from typing import Any

from l4.candidate_builder import build_root_cause_candidates
from l4.causal_trace import build_causal_trace
from l4.impact_analyzer import analyze_impact
from l4.input_adapter import L4InputError, build_l4_input
from l4.path_validator import validate_candidates
from l4.schema import empty_assessment_skeleton


def analyze_record(
    *,
    l4_run_id: str,
    analysis_run_id: str,
    record_id: str,
    reasoning_run_id: str | None = None,
    enrichment_run_id: str | None = None,
    ctx: dict[str, Any] | None = None,
) -> dict[str, Any]:
    try:
        context = ctx or build_l4_input(
            analysis_run_id=analysis_run_id,
            record_id=record_id,
            reasoning_run_id=reasoning_run_id,
            enrichment_run_id=enrichment_run_id,
        )
    except L4InputError as exc:
        skeleton = empty_assessment_skeleton(
            l4_run_id=l4_run_id,
            analysis_run_id=analysis_run_id,
            record_id=record_id,
            input_fingerprint="input_error",
            causal_knowledge_version="n/a",
        )
        skeleton["outcome"] = "analysis_failed"
        skeleton["limitations"] = [exc.message]
        skeleton["notes"] = [exc.code]
        return skeleton

    built = build_root_cause_candidates(context)
    active = built.get("root_cause_candidates") or []
    reference = built.get("reference_candidates") or []
    all_for_validate = active + reference
    paths = built.get("causal_paths") or []

    validated = validate_candidates(
        context,
        candidates=all_for_validate,
        paths=paths,
    )
    validated_all = validated.get("root_cause_candidates") or []
    active_ids = {c.get("candidate_id") for c in active}
    candidates = [c for c in validated_all if c.get("candidate_id") in active_ids]
    reference_out = [c for c in validated_all if c.get("candidate_id") not in active_ids]
    paths_out = validated.get("causal_paths") or []

    impact = analyze_impact(context)
    trace = build_causal_trace(
        candidates=candidates,
        paths=paths_out,
        impact=impact,
        evidence_refs=context.get("evidence_references") or [],
    )

    assessment = empty_assessment_skeleton(
        l4_run_id=l4_run_id,
        analysis_run_id=analysis_run_id,
        record_id=record_id,
        input_fingerprint=context.get("input_fingerprint") or "",
        causal_knowledge_version=built.get("causal_knowledge_version") or "l4_causal_knowledge_v1_2",
    )
    from l4 import APPLICABILITY_VERSION

    assessment.update(
        {
            "rules_version": built.get("rules_version"),
            "applicability_version": APPLICABILITY_VERSION,
            "source_reasoning_run_id": context.get("source_reasoning_run_id"),
            "source_enrichment_run_id": context.get("source_enrichment_run_id"),
            "equipment_id": context.get("equipment_id"),
            "equipment_type": context.get("equipment_type"),
            "knowledge_snapshot_id": context.get("knowledge_snapshot_id"),
            "l3_fault_candidates": context.get("l3_fault_candidates") or [],
            "l3_evidence_sufficiency": context.get("evidence_sufficiency"),
            "excluded_faults": built.get("excluded_faults") or [],
            "excluded_by_applicability": built.get("excluded_by_applicability") or [],
            "eligibility_summary": built.get("eligibility_summary"),
            "root_cause_candidates": candidates,
            "reference_candidates": reference_out,
            "causal_paths": paths_out,
            "impact_assessment": impact,
            "evidence_references": context.get("evidence_references") or [],
            "missing_evidence": validated.get("missing_evidence") or context.get("missing_evidence") or [],
            "limitations": list(context.get("limitations") or [])
            + [
                "根因确认状态固定为 unconfirmed。",
                "不读取 Excel 答案列，不调用 LLM。",
                "拓扑潜在关联 ≠ 已发生传播。",
                "CONTRADICTED/NOT_APPLICABLE 故障不展开有效根因。",
                "supporting_evidence_ids 仅含结构化 cause_specific。",
                "Unknown≠Absent：未知设备结构标 unresolved。",
            ],
            "causal_trace": trace,
            "notes": list(built.get("notes") or []),
            "input_context_summary": {
                "has_reasoning": bool(context.get("reasoning_assessment")),
                "has_enrichment": bool(context.get("knowledge_context")),
                "analysis_record_status": context.get("analysis_record_status"),
                "input_fingerprint": context.get("input_fingerprint"),
                "eligible_fault_count": (built.get("eligibility_summary") or {}).get("eligible_count"),
                "excluded_fault_count": (built.get("eligibility_summary") or {}).get("excluded_count"),
            },
        }
    )

    mapping_level = (impact.get("equipment_mapping") or {}).get("mapping_level")
    empty_reason = built.get("empty_reason")
    if empty_reason in {
        "no_applicable_causal_knowledge",
        "all_faults_excluded_by_eligibility",
        "no_l3_fault_candidates",
        "only_reference_candidates",
    } and not candidates:
        assessment["outcome"] = "no_applicable_causal_knowledge"
    elif context.get("evidence_sufficiency") in {"insufficient", "limited"} and not any(
        c.get("supporting_evidence_ids") for c in candidates
    ):
        assessment["outcome"] = "insufficient_evidence"
    elif mapping_level == "unresolved" and not candidates:
        assessment["outcome"] = "mapping_unresolved"
    elif candidates:
        assessment["outcome"] = "candidates_generated"
    else:
        assessment["outcome"] = "no_applicable_causal_knowledge"

    if mapping_level == "unresolved":
        assessment["notes"] = list(assessment.get("notes") or []) + [
            "设备映射 unresolved：不生成具体设备级影响结论。"
        ]

    return assessment
