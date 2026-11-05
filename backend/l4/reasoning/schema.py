"""CausalReasoningAssessment 契约。"""

from __future__ import annotations

from typing import Any

VALID_VALIDITY = {
    "consistent_with_observations",
    "mechanism_only",
    "insufficient_upstream_evidence",
    "contradicted",
    "indistinguishable",
    "review_recommended",
    "not_applicable_kept_excluded",
}

VALID_OUTCOMES = {
    "live_llm_success",
    "deterministic_fallback",
    "dry_run",
    "insufficient_evidence",
    "llm_failed_fallback",
    "llm_invalid_json_fallback",
    "validation_failed",
}

FORBIDDEN_FIELDS = (
    "final_root_cause",
    "confirmed_root_cause",
    "root_cause_confirmation",  # 不得由 LLM 改写为 confirmed；由服务端强制 unconfirmed
    "repair_action",
    "recommended_actions",
    "business_priority",
    "priority",
    "impact_degree",
    "propagation_confirmed",
    "already_propagated",
)


def empty_candidate_review(*, candidate_id: str, cause_name: str) -> dict[str, Any]:
    return {
        "candidate_id": candidate_id,
        "cause_name": cause_name,
        "source_causal_knowledge_ids": [],
        "validity_status": "insufficient_upstream_evidence",
        "causal_mechanism_summary": "",
        "upstream_cause_evidence": [],
        "fault_manifestation_evidence": [],
        "contradicting_evidence": [],
        "unresolved_links": [],
        "alternative_explanations": [],
        "review_summary": "",
        "same_layer_as_resulting_fault": False,
    }


def assessment_skeleton(
    *,
    reasoning_run_id: str,
    source_l4_run_id: str,
    source_analysis_run_id: str,
    record_id: str,
    llm_model: str,
    prompt_version: str,
    causal_knowledge_version: str,
    applicability_version: str,
    evidence_match_version: str,
    knowledge_snapshot_id: str | None,
    input_fingerprint: str,
) -> dict[str, Any]:
    return {
        "schema": "CausalReasoningAssessment",
        "schema_version": "causal_reasoning_assessment_v1",
        "reasoning_run_id": reasoning_run_id,
        "source_l4_run_id": source_l4_run_id,
        "source_analysis_run_id": source_analysis_run_id,
        "record_id": record_id,
        "model_id": llm_model,
        "prompt_version": prompt_version,
        "causal_knowledge_version": causal_knowledge_version,
        "applicability_version": applicability_version,
        "evidence_match_version": evidence_match_version,
        "knowledge_snapshot_id": knowledge_snapshot_id,
        "input_fingerprint": input_fingerprint,
        "root_cause_confirmation": "unconfirmed",
        "candidate_reviews": [],
        "causal_path_reviews": [],
        "supporting_evidence_ids": [],
        "contradicting_evidence_ids": [],
        "missing_evidence": [],
        "excluded_review_recommendations": [],
        "impact_interpretation": {
            "propagation_status": "unknown",
            "notes": [],
            "impact_degree": None,
        },
        "limitations": [],
        "overall_assessment": "",
        "outcome": "insufficient_evidence",
        "audit": {},
        "status": "queued",
    }
