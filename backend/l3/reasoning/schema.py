"""ReasoningAssessment / Hypothesis 结构化 Schema（dict 契约，便于 JSON 校验）。"""

from __future__ import annotations

from typing import Any, Literal

HypothesisAssessment = Literal["SUPPORTED", "CONTRADICTED", "INCONCLUSIVE", "NOT_APPLICABLE"]
HypothesisOrigin = Literal[
    "l2_preliminary",
    "l2_topk",
    "l3_core",
    "knowledge_fault_mode",
    "knowledge_case",
    "graph",
    "exploratory_hypothesis",
]

VALID_HYPOTHESIS_ORIGINS = {
    "l2_preliminary",
    "l2_topk",
    "l3_core",
    "knowledge_fault_mode",
    "knowledge_case",
    "graph",
    "exploratory_hypothesis",
}

# 模型常见别名 → 合法枚举（仅白名单映射，其余拒绝）
HYPOTHESIS_ORIGIN_ALIASES = {
    "l2_training_class": "l2_topk",
    "L2_training_class": "l2_topk",
    "l2": "l2_topk",
    "exploratory": "exploratory_hypothesis",
    "knowledge": "knowledge_fault_mode",
    "case": "knowledge_case",
}

VALID_OUTCOME_CLASSES = {
    "live_llm_success",
    "insufficient_evidence",
    "llm_invalid_json_fallback",
    "llm_failed_fallback",
    "dry_run",
    "validation_failed",
}

EVIDENCE_ROLE_ANOMALY = "anomaly_evidence"
EVIDENCE_ROLE_FAULT_SPECIFIC = "fault_specific_evidence"
EVIDENCE_ROLE_KNOWLEDGE = "knowledge_reference"

FORBIDDEN_FIELDS = (
    "final_root_cause",
    "root_cause",
    "repair_action",
    "recommended_actions",
    "business_priority",
    "priority",
    "impact_assessment",
    "impact",
)

# 故障特异特征关键字（用于证据语义分类）
_FAULT_SPECIFIC_MARKERS = (
    "bpfo",
    "bpfi",
    "bsf",
    "1x",
    "2x",
    "转频",
    "包络",
    "峭度",
    "峰值因子",
    "啮合",
    "边带",
    "unbalance",
    "bearing",
    "envelope",
    "kurtosis",
)
_ANOMALY_MARKERS = (
    "alarm",
    "thr:",
    "vib_x",
    "vib_y",
    "振动总值",
    "上限",
    "报警",
    "iso10816",
    "mill_vib",
)


def normalize_hypothesis_origin(raw: str | None) -> tuple[str | None, str | None]:
    """返回 (normalized_or_none, error_or_none)。"""
    if raw is None or str(raw).strip() == "":
        return None, "missing_hypothesis_origin"
    text = str(raw).strip()
    if text in VALID_HYPOTHESIS_ORIGINS:
        return text, None
    mapped = HYPOTHESIS_ORIGIN_ALIASES.get(text) or HYPOTHESIS_ORIGIN_ALIASES.get(text.lower())
    if mapped:
        return mapped, None
    return None, f"invalid_hypothesis_origin:{text}"


def classify_evidence_role(
    *,
    evidence_id: str | None = None,
    feature_name: str | None = None,
    related_fault_mode: str | None = None,
) -> str:
    """区分通用异常证据与故障特异证据（基于现有 EvidenceItem 字段，不另建证据库）。"""
    blob = f"{evidence_id or ''} {feature_name or ''} {related_fault_mode or ''}".lower()
    if any(m in blob for m in _FAULT_SPECIFIC_MARKERS):
        return EVIDENCE_ROLE_FAULT_SPECIFIC
    if any(m in blob for m in _ANOMALY_MARKERS):
        return EVIDENCE_ROLE_ANOMALY
    # 机械特征但未命中特异关键字时，若带 related_fault_mode 仍视为特异倾向
    if related_fault_mode and str(evidence_id or "").startswith("mech:"):
        return EVIDENCE_ROLE_FAULT_SPECIFIC
    if str(evidence_id or "").startswith("thr:"):
        return EVIDENCE_ROLE_ANOMALY
    return EVIDENCE_ROLE_ANOMALY


def empty_hypothesis(
    *,
    hypothesis_id: str,
    fault_type: str,
    hypothesis_origin: str,
    related_component: str | None = None,
) -> dict[str, Any]:
    origin, _err = normalize_hypothesis_origin(hypothesis_origin)
    return {
        "hypothesis_id": hypothesis_id,
        "fault_type": fault_type,
        "hypothesis_origin": origin or "exploratory_hypothesis",
        "related_component": related_component,
        "supporting_evidence_ids": [],
        "contradicting_evidence_ids": [],
        "knowledge_reference_ids": [],
        "missing_evidence": [],
        "assessment": "INCONCLUSIVE",
    }


def assessment_skeleton(
    *,
    reasoning_run_id: str,
    analysis_run_id: str,
    record_id: str,
    knowledge_snapshot_id: str | None,
    llm_model: str,
    prompt_version: str,
) -> dict[str, Any]:
    return {
        "schema": "ReasoningAssessment",
        "schema_version": "reasoning_assessment_v1",
        "reasoning_run_id": reasoning_run_id,
        "analysis_run_id": analysis_run_id,
        "record_id": record_id,
        "knowledge_snapshot_id": knowledge_snapshot_id,
        "llm_model": llm_model,
        "prompt_version": prompt_version,
        "status": "pending",
        "outcome_class": None,
        "l2_diagnosis_reference": {},
        "fault_type_review": {},
        "candidate_hypotheses": [],
        "severity_interpretation": {
            "note": "仅解释 Core 已有越限/迹象，不补齐正式严重度等级",
            "core_signals": [],
        },
        "location_interpretation": {
            "note": "无测点关联时不得定位具体轴承",
            "candidates": [],
            "limitations": [],
        },
        "spectral_interpretation": {
            "observed_scalars": [],
            "knowledge_references": [],
            "note": "必须区分实测标量与知识参考",
        },
        "evidence_sufficiency": "insufficient",
        "evidence_references": [],
        "knowledge_references": [],
        "missing_evidence": [],
        "limitations": [],
        "explanation": "",
        "tool_calls": [],
        "validation": {"ok": False, "errors": []},
        "audit": {},
    }
